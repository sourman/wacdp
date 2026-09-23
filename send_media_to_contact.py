#!/usr/bin/env python3
"""Send photo(s) to a WhatsApp Web contact via box-chrome CDP.

Opens Attach → Photos & videos (never sticker tray), sets files via
Page.setInterceptFileChooserDialog + DOM.setFileInputFiles, optional caption
on the media composer, Send N selected, then verifies outgoing image-thumb
(not sticker-container).

Usage:
  WA_CDP_HTTP=http://127.0.0.1:9225 python3 send_media_to_contact.py \\
    --contact '+201092600692' --file /path/a.png [--file /path/b.png] \\
    [--caption 'optional']

Exit 0 only after verified photo bubble(s). Sticker detection → attempt
delete-for-everyone and exit nonzero with JSON error.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import websockets

from wa_cdp import (
    open_contact,
    resolve_cdp_http,
    wa_ws_url,
)

CDP = resolve_cdp_http()


def die(msg, code: int = 1, **extra):
    out = {"ok": False, "error": msg, **extra}
    print(json.dumps(out, ensure_ascii=False))
    sys.exit(code)


class CdpSession:
    """CDP websocket that routes responses by id and buffers events."""

    def __init__(self, ws):
        self.w = ws
        self._pending: dict[int, asyncio.Future] = {}
        self._events: asyncio.Queue = asyncio.Queue()
        self._mid = 1
        self._reader: asyncio.Task | None = None

    def start(self):
        self._reader = asyncio.create_task(self._read_loop())

    async def close(self):
        if self._reader:
            self._reader.cancel()
            try:
                await self._reader
            except asyncio.CancelledError:
                pass

    async def _read_loop(self):
        try:
            while True:
                raw = json.loads(await self.w.recv())
                if "id" in raw:
                    fut = self._pending.pop(raw["id"], None)
                    if fut and not fut.done():
                        fut.set_result(raw)
                elif "method" in raw:
                    await self._events.put(raw)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(e)
            await self._events.put({"method": "_session_error", "params": {"error": str(e)}})

    def next_id(self) -> int:
        mid = self._mid
        self._mid += 1
        return mid

    async def call(self, method: str, params=None, timeout: float = 45):
        mid = self.next_id()
        fut = asyncio.get_event_loop().create_future()
        self._pending[mid] = fut
        await self.w.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        return await asyncio.wait_for(fut, timeout=timeout)

    async def evaluate(self, expr: str, await_promise: bool = False):
        raw = await self.call(
            "Runtime.evaluate",
            {
                "expression": expr,
                "returnByValue": True,
                "awaitPromise": await_promise,
                "userGesture": True,
            },
        )
        if raw.get("result", {}).get("exceptionDetails"):
            raise RuntimeError(str(raw["result"]["exceptionDetails"])[:800])
        return raw["result"]["result"].get("value")

    async def mouse(self, x, y, button="left", click_count=1):
        await self.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        await self.call(
            "Input.dispatchMouseEvent",
            {
                "type": "mousePressed",
                "x": x,
                "y": y,
                "button": button,
                "clickCount": click_count,
            },
        )
        await self.call(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseReleased",
                "x": x,
                "y": y,
                "button": button,
                "clickCount": click_count,
            },
        )

    async def key(self, name, code, vk):
        await self.call(
            "Input.dispatchKeyEvent",
            {
                "type": "keyDown",
                "key": name,
                "code": code,
                "windowsVirtualKeyCode": vk,
                "nativeVirtualKeyCode": vk,
            },
        )
        await self.call(
            "Input.dispatchKeyEvent",
            {
                "type": "keyUp",
                "key": name,
                "code": code,
                "windowsVirtualKeyCode": vk,
                "nativeVirtualKeyCode": vk,
            },
        )

    async def wait_event(self, method: str, timeout: float = 10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = deadline - time.time()
            try:
                ev = await asyncio.wait_for(self._events.get(), timeout=max(0.05, remaining))
            except asyncio.TimeoutError:
                break
            if ev.get("method") == method:
                return ev
            if ev.get("method") == "_session_error":
                raise RuntimeError(ev["params"]["error"])
            # re-queue unrelated? drop for now
        return None

    async def drain_events(self):
        while not self._events.empty():
            try:
                self._events.get_nowait()
            except asyncio.QueueEmpty:
                break


async def clear_compose(s: CdpSession):
    st = None
    for _ in range(6):
        st = await s.evaluate(
            """(() => {
              const c = document.querySelector('#main footer div[contenteditable="true"][data-tab="10"]')
                || document.querySelector('#main footer div[contenteditable="true"]');
              if (!c) return {ok:false, empty:false, text:''};
              c.focus();
              try {
                const sel = window.getSelection();
                const range = document.createRange();
                range.selectNodeContents(c);
                sel.removeAllRanges();
                sel.addRange(range);
              } catch (e) {}
              document.execCommand('selectAll');
              document.execCommand('delete');
              if ((c.innerText || '').trim()) {
                c.textContent = '';
                c.dispatchEvent(new InputEvent('input', {bubbles:true}));
              }
              const after = (c.innerText || '').trim();
              return {ok:true, empty:!after, text: after.slice(0, 80)};
            })()"""
        )
        if st and st.get("ok") and st.get("empty"):
            return st
        await asyncio.sleep(0.05)
    return st


async def find_btn(s: CdpSession, *patterns_exact_first):
    """Find visible button by exact aria/text then regex-ish includes."""
    pats = list(patterns_exact_first)
    return await s.evaluate(
        f"""(() => {{
          const pats = {json.dumps(pats)};
          const els = [...document.querySelectorAll('button,[role=button],li,div[role=button],span[role=button]')];
          const rows = els.map(el => {{
            const r = el.getBoundingClientRect();
            if (r.width < 6 || r.height < 6) return null;
            const aria = (el.getAttribute('aria-label')||'').trim();
            const txt = (el.innerText||'').trim().replace(/\\s+/g,' ');
            const tid = el.getAttribute('data-testid')||'';
            const iconEl = el.querySelector('[data-icon]');
            const icon = el.getAttribute('data-icon') || (iconEl ? iconEl.getAttribute('data-icon') : '') || '';
            return {{
              aria, txt, tid, icon,
              x: r.x + r.width/2, y: r.y + r.height/2,
              w: Math.round(r.width), h: Math.round(r.height)
            }};
          }}).filter(Boolean);
          const blob = (b) => (b.aria + ' ' + b.txt + ' ' + b.tid + ' ' + b.icon).trim();
          for (const p of pats) {{
            const hit = rows.find(b => b.aria === p || b.txt === p);
            if (hit) return hit;
          }}
          for (const p of pats) {{
            const re = new RegExp(p, 'i');
            const hit = rows.find(b => re.test(b.aria) || re.test(b.txt) || re.test(b.tid) || re.test(b.icon));
            if (hit) return hit;
          }}
          return null;
        }})()"""
    )


async def media_composer_state(s: CdpSession):
    return await s.evaluate(
        r"""(() => {
          const hint = document.body.innerText || '';
          const boxes = [...document.querySelectorAll('div[contenteditable="true"]')].map(el => {
            const r = el.getBoundingClientRect();
            return {
              aria: (el.getAttribute('aria-label')||'').trim(),
              w: Math.round(r.width), h: Math.round(r.height),
              x: r.x + r.width/2, y: r.y + r.height/2,
              text: (el.innerText||'').slice(0, 80),
              inFooter: !!el.closest('footer'),
            };
          }).filter(b => b.w > 40 && b.h > 8);
          const send = [...document.querySelectorAll('button,[role=button]')].map(b => {
            const r = b.getBoundingClientRect();
            return {
              aria: (b.getAttribute('aria-label')||'').trim(),
              tid: b.getAttribute('data-testid')||'',
              x: r.x + r.width/2, y: r.y + r.height/2,
              w: Math.round(r.width), h: Math.round(r.height),
            };
          }).filter(b => b.w > 5 && (
            /^Send$/i.test(b.aria) || /Send \d+ selected/i.test(b.aria)
            || b.tid === 'send' || /compose-btn-send/i.test(b.tid)
          ));
          const remove = [...document.querySelectorAll('[aria-label]')].filter(e =>
            /Remove attachment/i.test(e.getAttribute('aria-label')||'')
          ).map(e => e.getAttribute('aria-label'));
          const bigImgs = [...document.querySelectorAll('img')].filter(i => {
            const r = i.getBoundingClientRect();
            return r.width > 80 && r.height > 80;
          }).length;
          const sendSelectedMatch = hint.match(/Send (\d+) selected/i);
          return {
            addCaption: /Add a caption/i.test(hint),
            sendSelected: !!sendSelectedMatch,
            sendN: sendSelectedMatch ? parseInt(sendSelectedMatch[1], 10) : null,
            sendSelectedText: sendSelectedMatch ? sendSelectedMatch[0] : null,
            boxes, send, remove, bigImgs,
            ready: !!(remove.length || sendSelectedMatch || bigImgs > 0),
          };
        })()"""
    )


async def verify_outgoing(s: CdpSession, caption: str | None, n_files: int, since_ms: float):
    needle = (caption or "").strip()
    return await s.evaluate(
        f"""(() => {{
          const needle = {json.dumps(needle)};
          const nFiles = {json.dumps(n_files)};
          const nodes = [...document.querySelectorAll('#main [data-testid="msg-container"], #main div[data-id]')];
          const last = nodes.slice(-12).map(m => {{
            const t = (m.innerText||'').replace(/\\s+/g,' ').trim();
            const sticker = !!m.querySelector(
              '[data-testid="sticker-container"], .sticker-container, [data-testid="sticker"]'
            );
            const imageThumb = !!m.querySelector(
              '[data-testid="image-thumb"], [data-testid="image-thumb-body"]'
            );
            const imgs = [...m.querySelectorAll('img')].filter(i => {{
              const r = i.getBoundingClientRect();
              return r.width > 40 && r.height > 40;
            }}).map(i => ({{
              w: Math.round(i.getBoundingClientRect().width),
              h: Math.round(i.getBoundingClientRect().height),
            }}));
            const outgoing = !!(
              m.querySelector('[data-icon="tail-out"], [data-testid="tail-out"]')
              || (m.getAttribute('data-id')||'').startsWith('true_')
              || m.classList.contains('message-out')
              || [...m.querySelectorAll('[aria-label]')].some(e =>
                   /Sent|Delivered|Read/i.test(e.getAttribute('aria-label')||''))
              || m.querySelector('[data-testid="msg-check"],[data-testid="msg-dblcheck"],[data-icon="msg-check"],[data-icon="msg-dblcheck"]')
            );
            const tick = !!m.querySelector(
              '[data-testid="msg-check"],[data-testid="msg-dblcheck"],[data-icon="msg-check"],[data-icon="msg-dblcheck"],[data-icon="wds-ic-delivered"],[data-icon="wds-ic-read"],[data-icon="wds-ic-sent"]'
            );
            return {{
              t: t.slice(0, 240), sticker, imageThumb, imgs, outgoing, tick,
              dataId: (m.getAttribute('data-id')||'').slice(0, 80),
            }};
          }});
          const composerOpen = /Add a caption|Send \\d+ selected/i.test(document.body.innerText||'');
          const compose = ((document.querySelector('#main footer div[contenteditable="true"]')||{{}}).innerText||'').trim();
          const stickers = last.filter(x => x.outgoing && x.sticker);
          const photos = last.filter(x => x.outgoing && !x.sticker && (x.imageThumb || (x.imgs && x.imgs.length)));
          let hit = null;
          for (const p of photos.slice().reverse()) {{
            if (needle) {{
              if (!(p.t||'').includes(needle.slice(0, Math.min(40, needle.length)))) continue;
            }}
            hit = p;
            break;
          }}
          // multi: WA may send one album bubble or N bubbles; accept album or matching caption on latest photo
          return {{
            last, stickers, photos: photos.slice(-nFiles),
            hit, composerOpen, composeEmpty: !compose || compose === '\\n', compose,
            fail: !!document.querySelector('[data-testid="fail-container"]'),
          }};
        }})()"""
    )


async def try_delete_sticker(s: CdpSession, contact: str, title: str):
    """Best-effort: hover latest outgoing sticker → Delete for everyone."""
    loc = await s.evaluate(
        r"""(() => {
          const nodes = [...document.querySelectorAll('#main [data-testid="msg-container"], #main div[data-id]')];
          for (const m of nodes.slice(-10).reverse()) {
            const sticker = !!m.querySelector('[data-testid="sticker-container"], .sticker-container, [data-testid="sticker"]');
            if (!sticker) continue;
            m.scrollIntoView({block:'center'});
            const r = m.getBoundingClientRect();
            if (r.height < 8) continue;
            return {
              x: Math.min(r.right - 24, innerWidth - 24),
              y: r.y + Math.min(r.height / 2, 28),
            };
          }
          return null;
        })()"""
    )
    if not loc:
        return {"deleted": False, "reason": "no_sticker_bubble"}
    await s.call(
        "Input.dispatchMouseEvent",
        {"type": "mouseMoved", "x": loc["x"], "y": loc["y"]},
    )
    await asyncio.sleep(0.55)
    opts = await s.evaluate(
        r"""(() => {
          const nodes = [...document.querySelectorAll('#main [data-testid="msg-container"], #main div[data-id]')];
          const n = nodes.slice(-10).reverse().find(m =>
            m.querySelector('[data-testid="sticker-container"], .sticker-container, [data-testid="sticker"]')
          );
          if (!n) return {found:false};
          const btn = n.querySelector('[data-testid="icon-down-context"]')
            || [...n.querySelectorAll('[aria-label]')].find(e =>
                 /open message options/i.test(e.getAttribute('aria-label')||''));
          if (!btn) return {found:false};
          const r = btn.getBoundingClientRect();
          return {found:true, x:r.x+r.width/2, y:r.y+r.height/2};
        })()"""
    )
    if opts and opts.get("found"):
        await s.mouse(opts["x"], opts["y"])
    else:
        await s.call(
            "Input.dispatchMouseEvent",
            {
                "type": "mousePressed",
                "x": loc["x"],
                "y": loc["y"],
                "button": "right",
                "clickCount": 1,
            },
        )
        await s.call(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseReleased",
                "x": loc["x"],
                "y": loc["y"],
                "button": "right",
                "clickCount": 1,
            },
        )
    await asyncio.sleep(0.4)
    # JS click — mouse coords for Delete are often clipped under the footer.
    delete_item = await s.evaluate(
        """(() => {
          const el = [...document.querySelectorAll('[role="menuitem"]')]
            .find(e => (e.innerText||'').trim() === 'Delete'
              || (e.getAttribute('aria-label')||'') === 'Delete');
          if (!el) return {ok:false};
          el.scrollIntoView({block:'nearest'});
          el.click();
          return {ok:true};
        })()"""
    )
    if not delete_item or not delete_item.get("ok"):
        await s.key("Escape", "Escape", 27)
        return {"deleted": False, "reason": "no_delete_menu"}
    for _ in range(20):
        await asyncio.sleep(0.35)
        step = await s.evaluate(
            """(() => {
              const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
              let node; const texts = [];
              while (node = walker.nextNode()) {
                const v = (node.nodeValue||'').trim();
                if (v === 'Delete for everyone' || v === 'Delete for me') {
                  const range = document.createRange(); range.selectNodeContents(node);
                  const r = range.getBoundingClientRect();
                  if (r.width > 0 && r.height > 0)
                    texts.push({t:v, x:r.x+r.width/2, y:r.y+r.height/2});
                }
              }
              const aria = [...document.querySelectorAll('[aria-label]')]
                .filter(e => /^(Delete|Cancel delete)$/.test(e.getAttribute('aria-label')||''))
                .map(e => {
                  const r = e.getBoundingClientRect();
                  return {aria:e.getAttribute('aria-label'), x:r.x+r.width/2, y:r.y+r.height/2, w:r.width};
                }).filter(e => e.w > 8);
              return {texts, aria};
            })()"""
        )
        everyone = next((t for t in (step.get("texts") or []) if t["t"] == "Delete for everyone"), None)
        aria_del = next((a for a in (step.get("aria") or []) if a["aria"] == "Delete"), None)
        if everyone:
            await s.mouse(everyone["x"], everyone["y"])
            await asyncio.sleep(0.8)
            return {"deleted": True, "mode": "everyone"}
        if aria_del and not everyone:
            await s.mouse(aria_del["x"], aria_del["y"])
            await asyncio.sleep(0.5)
    await s.key("Escape", "Escape", 27)
    return {"deleted": False, "reason": "no_everyone_btn"}


async def set_files_via_chooser(s: CdpSession, paths: list[str], timeout: float = 8):
    await s.drain_events()
    await s.call("Page.setInterceptFileChooserDialog", {"enabled": True})

    attach = await find_btn(s, "Attach")
    if not attach:
        # icon fallback
        attach = await s.evaluate(
            r"""(() => {
              const hits = [...document.querySelectorAll('button,[role=button]')].map(b => {
                const a = (b.getAttribute('aria-label')||'').trim();
                const iconEl = b.querySelector('[data-icon]');
                const icon = b.getAttribute('data-icon') || (iconEl ? iconEl.getAttribute('data-icon') : '') || '';
                const r = b.getBoundingClientRect();
                if (r.width < 5) return null;
                if (a === 'Attach' || /attach/i.test(icon)) {
                  return {aria:a, icon, x:r.x+r.width/2, y:r.y+r.height/2};
                }
                return null;
              }).filter(Boolean);
              return hits[0] || null;
            })()"""
        )
    if not attach:
        die("no_attach_btn")
    await s.mouse(attach["x"], attach["y"])
    await asyncio.sleep(0.65)

    photo = await find_btn(s, "Photos & videos", r"Photos\s*&\s*videos", r"Photos\s*and\s*videos")
    if photo and "sticker" in f"{photo.get('aria','')} {photo.get('txt','')}".lower():
        photo = None
    if not photo:
        # refuse New sticker explicitly
        die("no_photos_videos_menu", attach=attach)

    # Click Photos & videos and wait for fileChooserOpened
    await s.drain_events()
    await s.mouse(photo["x"], photo["y"])
    chooser = await s.wait_event("Page.fileChooserOpened", timeout=timeout)
    if chooser:
        backend = chooser["params"].get("backendNodeId")
        mode = chooser["params"].get("mode")
        raw = await s.call(
            "DOM.setFileInputFiles",
            {"backendNodeId": backend, "files": paths},
        )
        if raw.get("error"):
            die("setFileInputFiles_chooser", detail=raw["error"], mode=mode)
        return {"via": "fileChooser", "mode": mode, "backendNodeId": backend, "n": len(paths)}

    # Fallback: set on image accept input (only after Photos menu was clicked)
    doc = await s.call("DOM.getDocument", {"depth": 0})
    root = doc["result"]["root"]["nodeId"]
    q = await s.call("DOM.querySelectorAll", {"nodeId": root, "selector": "input[type=file]"})
    nids = q.get("result", {}).get("nodeIds") or []
    chosen = None
    meta = []
    for nid in nids:
        desc = await s.call("DOM.describeNode", {"nodeId": nid})
        attrs = desc.get("result", {}).get("node", {}).get("attributes") or []
        ad = dict(zip(attrs[0::2], attrs[1::2]))
        acc = ad.get("accept") or ""
        meta.append({"accept": acc, "testid": ad.get("data-testid") or "", "nodeId": nid})
        if "image" in acc:
            chosen = nid
            break
        if chosen is None and acc in ("*", "image/*,video/*,image/webp"):
            chosen = nid
    if chosen is None and nids:
        # Prefer last input created after Photos click
        chosen = nids[-1]
    if chosen is None:
        die("no_file_input_after_photos", meta=meta, photo=photo)
    raw = await s.call("DOM.setFileInputFiles", {"nodeId": chosen, "files": paths})
    if raw.get("error"):
        die("setFileInputFiles_input", detail=raw["error"], meta=meta)
    return {"via": "input", "meta": meta, "n": len(paths)}


async def insert_caption(s: CdpSession, caption: str, composer: dict):
    if not caption:
        return
    boxes = composer.get("boxes") or []
    # Prefer non-footer caption / "Type a message" / Add a caption aria
    box = None
    for b in boxes:
        aria = (b.get("aria") or "").lower()
        if "caption" in aria:
            box = b
            break
    if not box:
        for b in boxes:
            aria = (b.get("aria") or "").lower()
            if "type a message" in aria and not b.get("inFooter"):
                box = b
                break
    if not box:
        for b in boxes:
            if not b.get("inFooter"):
                box = b
                break
    if not box and boxes:
        # last resort: first box that isn't the footer compose with contact name
        for b in boxes:
            aria = (b.get("aria") or "")
            if "to +" in aria.lower() or "to " in aria.lower():
                continue
            box = b
            break
        if not box:
            box = boxes[0]
    await s.mouse(box["x"], box["y"])
    await asyncio.sleep(0.12)
    # clear any leftover
    await s.evaluate(
        """(() => {
          const els = [...document.querySelectorAll('div[contenteditable="true"]')];
          let el = els.find(e => /caption/i.test(e.getAttribute('aria-label')||''));
          if (!el) el = els.find(e => {
            const a = (e.getAttribute('aria-label')||'');
            return /Type a message/i.test(a) && !/to \\+/i.test(a) && !e.closest('footer');
          });
          if (!el) el = els.find(e => !e.closest('footer'));
          if (!el) return false;
          el.focus();
          try {
            const sel = window.getSelection();
            const range = document.createRange();
            range.selectNodeContents(el);
            sel.removeAllRanges();
            sel.addRange(range);
          } catch (e) {}
          document.execCommand('selectAll');
          document.execCommand('delete');
          return el.getAttribute('aria-label')||'';
        })()"""
    )
    await s.call("Input.insertText", {"text": caption})
    await asyncio.sleep(0.25)
    draft = await s.evaluate(
        """(() => {
          const els = [...document.querySelectorAll('div[contenteditable="true"]')];
          let el = els.find(e => /caption/i.test(e.getAttribute('aria-label')||''));
          if (!el) el = els.find(e => {
            const a = (e.getAttribute('aria-label')||'');
            return /Type a message/i.test(a) && !e.closest('#main footer');
          });
          if (!el) el = document.activeElement;
          return (el && (el.innerText||'')).trim();
        })()"""
    )
    norm = lambda x: " ".join((x or "").replace("\n", " ").split())
    if norm(draft) != norm(caption):
        die("caption_draft_mismatch", draft=draft, want=caption)


async def main_async(contact: str, files: list[str], caption: str | None, allow_substring: bool, do_search: bool):
    t0 = time.time()
    paths = [str(Path(f).resolve()) for f in files]
    for p in paths:
        if not os.path.isfile(p):
            die("missing_file", path=p)
        # Only images for Photos & videos path
        ext = Path(p).suffix.lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            die("unsupported_image_ext", path=p, ext=ext)

    async with websockets.connect(wa_ws_url(), max_size=None) as ws:
        s = CdpSession(ws)
        s.start()
        try:
            await s.call("Runtime.enable")
            await s.call("Page.enable")
            await s.call("DOM.enable")

            # Bridge open_contact which uses raw mid-based API — use a side connection? 
            # Simpler: implement open via importing with a thin adapter.
            # open_contact expects (w, mid, ...) with call/evaluate that match wa_cdp signatures.
            # We'll open using a short-lived raw mid path on same socket carefully:
            # Actually CdpSession owns the reader — can't share. Reimplement open using s.

            # Use wa_cdp.open_contact by temporarily using its evaluate/call against our session:
            mid_holder = {"mid": 1}

            async def call_adapt(w, mid, method, params=None):
                # ignore mid; use session
                return await s.call(method, params)

            async def eval_adapt(w, mid, expr, await_promise=False):
                return await s.evaluate(expr, await_promise)

            async def mouse_adapt(w, mid0, x, y, button="left", click_count=1):
                await s.mouse(x, y, button=button, click_count=click_count)

            async def key_adapt(w, mid, name, code, vk):
                await s.key(name, code, vk)

            # Monkeypatch wa_cdp functions used by open_contact for this process
            import wa_cdp as W

            orig = (W.call, W.evaluate, W.mouse, W.key)
            W.call = call_adapt
            W.evaluate = eval_adapt
            W.mouse = mouse_adapt
            W.key = key_adapt
            try:
                # open_contact increments mid but we ignore it
                _mid, title = await open_contact(
                    ws, 10, contact, allow_substring=allow_substring, via_search_if_missing=do_search
                )
            finally:
                W.call, W.evaluate, W.mouse, W.key = orig

            # Do NOT Escape here — Escape closes #main / loses the open chat.

            prior = await clear_compose(s)
            if prior and prior.get("text"):
                print(
                    json.dumps(
                        {"info": "cleared_leftover_compose", "chars": len(prior.get("text") or "")},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )

            set_info = await set_files_via_chooser(s, paths)

            composer = None
            for _ in range(40):
                await asyncio.sleep(0.25)
                composer = await media_composer_state(s)
                if composer and composer.get("ready"):
                    break
            if not composer or not composer.get("ready"):
                die("no_media_composer", set_info=set_info, state=composer)

            # Confirm expected count when Send N selected visible
            if composer.get("sendN") is not None and composer["sendN"] != len(paths):
                # sometimes WA shows 1 while still loading; wait a bit more
                for _ in range(10):
                    await asyncio.sleep(0.3)
                    composer = await media_composer_state(s)
                    if composer.get("sendN") == len(paths):
                        break
                if composer.get("sendN") not in (None, len(paths)):
                    print(
                        json.dumps(
                            {
                                "warning": "sendN_mismatch",
                                "sendN": composer.get("sendN"),
                                "want": len(paths),
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )

            if caption:
                await insert_caption(s, caption, composer)

            # Click Send N selected / Send (prefer Send N selected)
            sends = await s.evaluate(
                r"""(() => {
                  return [...document.querySelectorAll('button,[role=button]')].map(b => {
                    const r = b.getBoundingClientRect();
                    return {
                      aria: (b.getAttribute('aria-label')||'').trim(),
                      tid: b.getAttribute('data-testid')||'',
                      x: r.x+r.width/2, y: r.y+r.height/2,
                      w: Math.round(r.width), h: Math.round(r.height),
                    };
                  }).filter(b => b.w > 5 && (
                    /^Send$/i.test(b.aria) || /Send \d+ selected/i.test(b.aria)
                    || b.tid === 'send' || /compose-btn-send/i.test(b.tid)
                  ));
                })()"""
            )
            send = None
            for b in sends or []:
                if f"Send {len(paths)} selected".lower() == (b.get("aria") or "").lower():
                    send = b
                    break
            if not send:
                for b in sends or []:
                    if "selected" in (b.get("aria") or "").lower():
                        send = b
                        break
            if not send and sends:
                send = sends[0]
            if not send:
                die("no_send_btn", composer=composer)
            await s.mouse(send["x"], send["y"])

            # Verify
            ver = None
            ok = False
            sticker_hit = False
            for _ in range(40):
                await asyncio.sleep(0.35)
                ver = await verify_outgoing(s, caption, len(paths), t0)
                if ver.get("fail"):
                    die("fail_container_after_send", verify=ver)
                if ver.get("stickers"):
                    sticker_hit = True
                    break
                if (
                    ver.get("hit")
                    and not ver.get("composerOpen")
                    and ver.get("composeEmpty")
                ):
                    # caption required?
                    if caption:
                        if caption.strip()[:40] not in (ver["hit"].get("t") or ""):
                            continue
                    ok = True
                    break
                # empty caption: accept latest photo without requiring text
                if (
                    not caption
                    and not ver.get("composerOpen")
                    and ver.get("composeEmpty")
                    and ver.get("photos")
                ):
                    ok = True
                    ver["hit"] = ver["photos"][-1]
                    break

            if sticker_hit:
                del_info = await try_delete_sticker(s, contact, title)
                die(
                    "sent_as_sticker",
                    verify=ver,
                    deleted_sticker=del_info,
                    set_info=set_info,
                )

            if not ok:
                die("send_not_verified", verify=ver, set_info=set_info, send=send)

            elapsed_ms = int((time.time() - t0) * 1000)
            out = {
                "ok": True,
                "contact": contact,
                "resolved_title": title,
                "files": paths,
                "n": len(paths),
                "caption": caption or "",
                "set": set_info,
                "send_aria": send.get("aria"),
                "verify": {
                    "text": (ver.get("hit") or {}).get("t"),
                    "imageThumb": (ver.get("hit") or {}).get("imageThumb"),
                    "imgs": (ver.get("hit") or {}).get("imgs"),
                    "sticker": False,
                    "tick": (ver.get("hit") or {}).get("tick"),
                },
                "elapsed_ms": elapsed_ms,
            }
            print(json.dumps(out, ensure_ascii=False))
        finally:
            await s.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Send WhatsApp Web photo(s) via CDP")
    ap.add_argument("--contact", required=True, help="Exact chat title OR phone (+20… / digits)")
    ap.add_argument(
        "--file",
        action="append",
        dest="files",
        required=True,
        help="Image path (repeatable for multi-send)",
    )
    ap.add_argument("--caption", default="", help="Optional caption on the media send")
    ap.add_argument("--allow-substring", action="store_true")
    ap.add_argument(
        "--no-search",
        action="store_true",
        help="Do not fall back to Search pane",
    )
    args = ap.parse_args()
    try:
        asyncio.run(
            main_async(
                args.contact,
                args.files,
                args.caption or None,
                args.allow_substring,
                not args.no_search,
            )
        )
    except SystemExit:
        raise
    except Exception as e:
        die(str(e))
