#!/usr/bin/env python3
"""Delete an outgoing WhatsApp Web message via Gate inoculum CDP.

Usage:
  WA_CDP_HTTP=http://127.0.0.1:9427 python3 delete_outgoing.py \\
    --contact '+201092600692' --needle 'unique substring'

Prefer Delete for everyone. Pass --for-me-ok to allow Delete for me.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.request

import websockets

CDP = os.environ.get("WA_CDP_HTTP", "http://127.0.0.1:9427")


def digits_only(s: str) -> str:
    return "".join(c for c in (s or "") if c.isdigit())


def phone_match(a: str, b: str) -> bool:
    td, qd = digits_only(a), digits_only(b)
    if not td or not qd or len(td) < 8 or len(qd) < 8:
        return False
    return td.endswith(qd[-9:]) or qd.endswith(td[-9:]) or td in qd or qd in td


async def call(w, mid, method, params=None):
    await w.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
    while True:
        raw = json.loads(await asyncio.wait_for(w.recv(), timeout=45))
        if raw.get("id") == mid:
            return raw


async def evaluate(w, mid, expr, await_promise=False):
    raw = await call(
        w,
        mid,
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


async def mouse(w, mid0, x, y, button="left"):
    await call(w, mid0, "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
    await call(
        w,
        mid0 + 1,
        "Input.dispatchMouseEvent",
        {"type": "mousePressed", "x": x, "y": y, "button": button, "clickCount": 1},
    )
    await call(
        w,
        mid0 + 2,
        "Input.dispatchMouseEvent",
        {"type": "mouseReleased", "x": x, "y": y, "button": button, "clickCount": 1},
    )


def die(msg: str, code: int = 1):
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
    sys.exit(code)


async def main(contact: str, needle: str, for_me_ok: bool, do_search: bool):
    # reuse send helper open by shelling out? keep self-contained; call send path patterns
    tabs = json.load(urllib.request.urlopen(f"{CDP}/json/list", timeout=5))
    pages = [
        t
        for t in tabs
        if t.get("type") == "page" and "whatsapp" in (t.get("url") or "").lower()
    ]
    if not pages:
        die("no whatsapp page on CDP")

    async with websockets.connect(pages[0]["webSocketDebuggerUrl"], max_size=None) as w:
        await call(w, 1, "Runtime.enable")
        mid = 10

        # If already on chat with matching header, skip reopen
        hdr = await evaluate(
            w,
            mid,
            """(() => {
              const t = ((document.querySelector('#main header')||{}).innerText||'');
              return {text: t.slice(0,160), dig: t.replace(/\\D/g,''), compose: !!document.querySelector('#main footer div[contenteditable="true"]')};
            })()""",
        )
        mid += 1
        already = hdr and hdr.get("compose") and (
            contact in (hdr.get("text") or "")
            or phone_match(hdr.get("text") or "", contact)
            or phone_match(hdr.get("dig") or "", contact)
        )

        if not already:
            # open via list/search (same strategy as send_to_contact)
            from pathlib import Path
            # inline minimal open: prefer span title / phone
            loc = await evaluate(
                w,
                mid,
                f"""(() => {{
                  const want = {json.dumps(contact)};
                  const phoneDigits = {json.dumps(digits_only(contact))};
                  const spans = [...document.querySelectorAll('#pane-side span[title], span[title]')];
                  let el = spans.find(s => s.getAttribute('title') === want);
                  if (!el && phoneDigits.length >= 8) {{
                    el = spans.find(s => {{
                      const dig = (s.getAttribute('title')||'').replace(/\\D/g,'');
                      return dig.length >= 8 && (dig.endsWith(phoneDigits.slice(-9)) || phoneDigits.endsWith(dig.slice(-9)));
                    }});
                  }}
                  if (!el) return {{found:false, titles: spans.map(s=>s.getAttribute('title')).filter(Boolean).slice(0,30)}};
                  const row = el.closest('[data-testid="cell-frame-container"]') || el.closest('[role="row"], [role="listitem"]') || el;
                  el.scrollIntoView({{block:'center'}});
                  const r = row.getBoundingClientRect();
                  return {{found:true, title: el.getAttribute('title'), x:r.x+r.width/2, y:r.y+r.height/2}};
                }})()""",
            )
            mid += 1
            if not loc or not loc.get("found"):
                if not do_search:
                    die(f"contact not found: {loc}")
                # search
                sloc = await evaluate(
                    w,
                    mid,
                    """(() => {
                      const inp = document.querySelector('input[aria-label="Search or start a new chat"]');
                      if (!inp) return null;
                      const r = inp.getBoundingClientRect();
                      return {x:r.x+r.width/2,y:r.y+r.height/2};
                    })()""",
                )
                mid += 1
                if not sloc:
                    die("not in list and no search")
                await mouse(w, mid, sloc["x"], sloc["y"])
                mid += 3
                q = digits_only(contact) or contact
                await call(w, mid, "Input.insertText", {"text": q})
                mid += 1
                await asyncio.sleep(1.5)
                loc = await evaluate(
                    w,
                    mid,
                    f"""(() => {{
                      const phoneDigits = {json.dumps(digits_only(contact))};
                      const want = {json.dumps(contact)};
                      const spans = [...document.querySelectorAll('span[title]')];
                      let el = spans.find(s => s.getAttribute('title') === want);
                      if (!el) el = spans.find(s => {{
                        const dig=(s.getAttribute('title')||'').replace(/\\D/g,'');
                        return dig.length>=8 && (dig.endsWith(phoneDigits.slice(-9))||phoneDigits.endsWith(dig.slice(-9)));
                      }});
                      if (!el) return {{found:false}};
                      const row = el.closest('[data-testid="cell-frame-container"]') || el;
                      const r = row.getBoundingClientRect();
                      return {{found:true, title: el.getAttribute('title'), x:r.x+r.width/2, y:r.y+r.height/2}};
                    }})()""",
                )
                mid += 1
                if not loc or not loc.get("found"):
                    die(f"contact not found after search: {loc}")
            await mouse(w, mid, loc["x"], loc["y"])
            mid += 3
            await asyncio.sleep(1.1)

        # find outgoing bubble
        bubble = await evaluate(
            w,
            mid,
            f"""(() => {{
              const needle = {json.dumps(needle)};
              const nodes = [...document.querySelectorAll('#main [data-testid="msg-container"]')];
              for (let i = nodes.length - 1; i >= 0; i--) {{
                const n = nodes[i];
                const t = n.innerText || '';
                if (!t.includes(needle)) continue;
                const _aria = [...n.querySelectorAll('[aria-label]')].map(e => e.getAttribute('aria-label') || '');
                const _icons = [...n.querySelectorAll('[data-icon], svg title')].map(e => e.getAttribute('data-icon') || e.textContent || '');
                const _out = !!(n.querySelector('[aria-label="You:"], [aria-label="You"]')
                  || _aria.some(a => /Sent|Delivered|Read/i.test(a || ''))
                  || n.querySelector('[data-testid="msg-dblcheck"], [data-testid="msg-check"], [data-icon="msg-dblcheck"], [data-icon="msg-check"]')
                  || _icons.some(x => /delivered|msg-check|msg-dblcheck|wds-ic-read|wds-ic-delivered|wds-ic-sent/i.test(x)));
                if (!_out) continue;
                n.scrollIntoView({{block:'center'}});
                const r = n.getBoundingClientRect();
                const x = Math.min(r.right - 24, window.innerWidth - 24);
                const y = r.y + Math.min(r.height / 2, 28);
                return {{found:true, x, y, raw: t.slice(0,120)}};
              }}
              return {{found:false}};
            }})()""",
        )
        mid += 1
        if not bubble or not bubble.get("found"):
            die(f"outgoing bubble not found: {bubble}")

        # hover → options
        await call(w, mid, "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": bubble["x"], "y": bubble["y"]})
        mid += 1
        await asyncio.sleep(0.55)
        opts = await evaluate(
            w,
            mid,
            f"""(() => {{
              const needle = {json.dumps(needle)};
              const n = [...document.querySelectorAll('#main [data-testid="msg-container"]')].reverse()
                .find(n => {{
                if (!(n.innerText||'').includes(needle)) return false;
                const _aria = [...n.querySelectorAll('[aria-label]')].map(e => e.getAttribute('aria-label') || '');
                const _icons = [...n.querySelectorAll('[data-icon], svg title')].map(e => e.getAttribute('data-icon') || e.textContent || '');
                return !!(n.querySelector('[aria-label="You:"], [aria-label="You"]')
                  || _aria.some(a => /Sent|Delivered|Read/i.test(a || ''))
                  || n.querySelector('[data-testid="msg-dblcheck"], [data-testid="msg-check"], [data-icon="msg-dblcheck"], [data-icon="msg-check"]')
                  || _icons.some(x => /delivered|msg-check|msg-dblcheck|wds-ic-read|wds-ic-delivered|wds-ic-sent/i.test(x)));
              }});
              const btn = n && (n.querySelector('[data-testid="icon-down-context"]')
                || [...n.querySelectorAll('[aria-label]')].find(e => /open message options/i.test(e.getAttribute('aria-label')||'')));
              if (!btn) return {{found:false}};
              const r = btn.getBoundingClientRect();
              return {{found:true, x:r.x+r.width/2, y:r.y+r.height/2}};
            }})()""",
        )
        mid += 1
        if opts and opts.get("found"):
            await mouse(w, mid, opts["x"], opts["y"])
            mid += 3
        else:
            await mouse(w, mid, bubble["x"], bubble["y"], button="right")
            mid += 3
        await asyncio.sleep(0.4)

        delete_item = await evaluate(
            w,
            mid,
            """(() => {
              const el = [...document.querySelectorAll('[role="menuitem"]')]
                .find(e => (e.innerText||'').trim() === 'Delete');
              if (!el) return {found:false, menu:[...document.querySelectorAll('[role="menuitem"]')].map(e=>(e.innerText||'').trim())};
              const r = el.getBoundingClientRect();
              return {found:true, x:r.x+r.width/2, y:r.y+r.height/2};
            })()""",
        )
        mid += 1
        if not delete_item or not delete_item.get("found"):
            die(f"Delete menu missing: {delete_item}")
        await mouse(w, mid, delete_item["x"], delete_item["y"])
        mid += 3

        mode = None
        # Wait for confirm: either bottom aria Delete, or Delete for everyone text
        for _ in range(20):
            await asyncio.sleep(0.35)
            step = await evaluate(
                w,
                mid,
                """(() => {
                  // text nodes
                  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                  let node; const texts = [];
                  while (node = walker.nextNode()) {
                    const v = (node.nodeValue||'').trim();
                    if (v === 'Delete for everyone' || v === 'Delete for me') {
                      const range = document.createRange(); range.selectNodeContents(node);
                      const r = range.getBoundingClientRect();
                      if (r.width > 0 && r.height > 0) texts.push({t:v, x:r.x+r.width/2, y:r.y+r.height/2});
                    }
                  }
                  const aria = [...document.querySelectorAll('[aria-label]')]
                    .filter(e => /^(Delete|Cancel delete)$/.test(e.getAttribute('aria-label')||''))
                    .map(e => {
                      const r = e.getBoundingClientRect();
                      return {aria:e.getAttribute('aria-label'), x:r.x+r.width/2, y:r.y+r.height/2, w:r.width, h:r.height};
                    }).filter(e => e.w > 8 && e.h > 8);
                  return {texts, aria};
                })()""",
            )
            mid += 1
            everyone = next((t for t in (step.get("texts") or []) if t["t"] == "Delete for everyone"), None)
            for_me = next((t for t in (step.get("texts") or []) if t["t"] == "Delete for me"), None)
            aria_del = next((a for a in (step.get("aria") or []) if a["aria"] == "Delete"), None)
            if everyone:
                await mouse(w, mid, everyone["x"], everyone["y"])
                mid += 3
                mode = "everyone"
                break
            if for_me and for_me_ok and not everyone:
                await mouse(w, mid, for_me["x"], for_me["y"])
                mid += 3
                mode = "for_me"
                break
            if aria_del and not everyone:
                # first-stage confirm bar — click Delete then keep looping for everyone
                await mouse(w, mid, aria_del["x"], aria_del["y"])
                mid += 3
                await asyncio.sleep(0.5)
                continue
        else:
            die(f"delete confirm never completed: last={step}")

        await asyncio.sleep(0.9)
        verify = await evaluate(
            w,
            mid,
            f"""(() => {{
              const needle = {json.dumps(needle)};
              const nodes = [...document.querySelectorAll('#main [data-testid="msg-container"]')];
              let still = false, recalled = false;
              for (const n of nodes) {{
                const t = n.innerText || '';
                if (t.includes(needle)) still = true;
                if (/You deleted this message/i.test(t) || n.querySelector('[data-icon="recalled"], [data-testid="recalled"]'))
                  recalled = true;
              }}
              return {{stillNeedle: still, recalled}};
            }})()""",
        )
        mid += 1
        if verify and verify.get("stillNeedle") and not verify.get("recalled"):
            die(f"delete not confirmed: {verify}")

        print(
            json.dumps(
                {"ok": True, "contact": contact, "needle": needle, "mode": mode, "verify": verify},
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--contact", required=True)
    ap.add_argument("--needle", required=True)
    ap.add_argument("--for-me-ok", action="store_true")
    ap.add_argument("--no-search", action="store_true")
    args = ap.parse_args()
    try:
        asyncio.run(main(args.contact, args.needle, args.for_me_ok, not args.no_search))
    except Exception as e:
        die(str(e))
