#!/usr/bin/env python3
"""Forward a WhatsApp Web message via Gate inoculum CDP.

Usage:
  WA_CDP_HTTP=http://127.0.0.1:9427 python3 forward_message.py \\
    --from-contact '+201092600692' --needle 'cdp-fwd-probe-…' \\
    --to-contact '+201092600692'

Flow:
  open from-contact → find bubble with needle → hover → message options
  → Forward (menu) → confirm Forward (multi-select bar) → forward picker
  → search/select to-contact → Send → verify needle in to-contact
  (forwarded indicator when present; same-chat ok — duplicate outgoing bubble).

Self-test note: WhatsApp allows forwarding into the same chat (Message yourself /
notes-to-self). Same-chat forwards may omit the "Forwarded" label; verification
then relies on a new outgoing bubble containing the needle (count increase).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.request

import websockets

CDP = os.environ.get("WA_CDP_HTTP", "http://127.0.0.1:9427")


def digits_only(s: str) -> str:
    return "".join(c for c in (s or "") if c.isdigit())


def phone_match(a: str, b: str) -> bool:
    td, qd = digits_only(a), digits_only(b)
    if not td or not qd or len(td) < 8 or len(qd) < 8:
        return False
    return (
        td.endswith(qd[-9:])
        or qd.endswith(td[-9:])
        or td in qd
        or qd in td
    )


def looks_like_phone(s: str) -> bool:
    d = digits_only(s)
    return len(d) >= 8 and sum(
        c.isdigit() or c in "+- ()" for c in (s or "")
    ) >= len((s or "").strip()) * 0.6


def same_contact(a: str, b: str) -> bool:
    if (a or "").strip() == (b or "").strip():
        return True
    return phone_match(a, b)


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


async def mouse_move(w, mid, x, y):
    await call(w, mid, "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})


def die(msg: str, code: int = 1):
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
    sys.exit(code)


async def find_row(w, mid, contact: str, allow_substring: bool, root_sel: str = ""):
    root_js = root_sel or "document"
    return mid + 1, await evaluate(
        w,
        mid,
        f"""(() => {{
          const want = {json.dumps(contact)};
          const allowSub = {json.dumps(allow_substring)};
          const phoneDigits = {json.dumps(digits_only(contact))};
          const root = {root_js} || document;
          const spans = [...root.querySelectorAll('span[title]')];
          const scored = spans.map(s => {{
            const title = s.getAttribute('title') || '';
            const dig = title.replace(/\\D/g, '');
            const exact = title === want;
            const sub = allowSub && title.includes(want);
            const phoneHit = phoneDigits.length >= 8 && dig.length >= 8 && (
              dig.endsWith(phoneDigits.slice(-9)) || phoneDigits.endsWith(dig.slice(-9))
              || dig.includes(phoneDigits) || phoneDigits.includes(dig)
            );
            if (!exact && !sub && !phoneHit) return null;
            const row = s.closest('[data-testid="cell-frame-container"]')
              || s.closest('[data-testid="checkbox-selectable-wrapper"]')
              || s.closest('[role="row"], [role="listitem"], label') || s;
            const r = row.getBoundingClientRect();
            if (r.width < 4 || r.height < 4) return null;
            return {{
              title, exact, sub, phoneHit,
              x: r.x + r.width / 2, y: r.y + r.height / 2,
              score: (exact ? 100 : 0) + (phoneHit ? 50 : 0) + (sub ? 10 : 0)
            }};
          }}).filter(Boolean).sort((a,b) => b.score - a.score);
          if (!scored.length) {{
            return {{found:false, titles: spans.map(s=>s.getAttribute('title')).filter(Boolean).slice(0,40)}};
          }}
          const hit = scored[0];
          const el = spans.find(s => s.getAttribute('title') === hit.title);
          if (el) el.scrollIntoView({{block:'center'}});
          return {{found:true, ...hit}};
        }})()""",
    )


async def open_via_search(w, mid, contact: str):
    loc = await evaluate(
        w,
        mid,
        """(() => {
          const inp = document.querySelector('input[aria-label="Search or start a new chat"]')
            || [...document.querySelectorAll('input')].find(i => /search/i.test(i.getAttribute('aria-label')||''));
          if (!inp) return null;
          const r = inp.getBoundingClientRect();
          return {x: r.x + r.width / 2, y: r.y + r.height / 2};
        })()""",
    )
    mid += 1
    if not loc:
        die("contact not in list and no search input")
    await mouse(w, mid, loc["x"], loc["y"])
    mid += 3
    await asyncio.sleep(0.2)
    dig = digits_only(contact)
    query = dig if looks_like_phone(contact) and dig else contact.strip()
    await call(
        w,
        mid,
        "Input.dispatchKeyEvent",
        {"type": "keyDown", "key": "a", "code": "KeyA", "windowsVirtualKeyCode": 65, "modifiers": 2},
    )
    mid += 1
    await call(
        w,
        mid,
        "Input.dispatchKeyEvent",
        {"type": "keyUp", "key": "a", "code": "KeyA", "windowsVirtualKeyCode": 65, "modifiers": 2},
    )
    mid += 1
    await call(
        w,
        mid,
        "Input.dispatchKeyEvent",
        {"type": "keyDown", "key": "Backspace", "code": "Backspace", "windowsVirtualKeyCode": 8},
    )
    mid += 1
    await call(
        w,
        mid,
        "Input.dispatchKeyEvent",
        {"type": "keyUp", "key": "Backspace", "code": "Backspace", "windowsVirtualKeyCode": 8},
    )
    mid += 1
    await call(w, mid, "Input.insertText", {"text": query})
    mid += 1
    await asyncio.sleep(1.6)
    mid, loc2 = await find_row(w, mid, contact, True)
    if not loc2 or not loc2.get("found"):
        die(f"contact not found after search: {loc2}")
    return mid, loc2


async def header_ok(w, mid, contact: str):
    st = await evaluate(
        w,
        mid,
        f"""(() => {{
          const contact = {json.dumps(contact)};
          const phoneDigits = {json.dumps(digits_only(contact))};
          const headerEl = document.querySelector('#main header');
          const headerText = (headerEl && headerEl.innerText) || '';
          const parts = [...document.querySelectorAll('#main header span[title], #main header [dir="auto"]')]
            .map(e => (e.getAttribute('title')||e.textContent||'').trim()).filter(Boolean);
          const blob = (parts.join(' ') + ' ' + headerText).trim();
          const dig = blob.replace(/\\D/g, '');
          const phoneHit = phoneDigits.length >= 8 && dig.length >= 8 && (
            dig.includes(phoneDigits) || phoneDigits.includes(dig)
            || dig.endsWith(phoneDigits.slice(-9)) || phoneDigits.endsWith(dig.slice(-9))
          );
          const titleHit = parts.some(h => h === contact || h.includes(contact)) || blob.includes(contact);
          return {{
            ok: !!(document.querySelector('#main footer div[contenteditable="true"]') && (titleHit || phoneHit)),
            parts, headerText: headerText.slice(0, 160), dig, phoneHit, titleHit
          }};
        }})()""",
    )
    return mid + 1, st


async def ensure_chat_open(w, mid, contact: str, do_search: bool):
    hdr = await evaluate(
        w,
        mid,
        """(() => {
          const t = ((document.querySelector('#main header')||{}).innerText||'');
          return {
            text: t.slice(0,160),
            dig: t.replace(/\\D/g,''),
            compose: !!document.querySelector('#main footer div[contenteditable="true"]')
          };
        })()""",
    )
    mid += 1
    already = hdr and hdr.get("compose") and (
        contact in (hdr.get("text") or "")
        or phone_match(hdr.get("text") or "", contact)
        or phone_match(hdr.get("dig") or "", contact)
    )
    if already:
        return mid, {"skipped_open": True, "title": (hdr.get("text") or "").split("\n")[0]}

    mid, loc = await find_row(w, mid, contact, False)
    if not loc or not loc.get("found"):
        if not do_search:
            die(f"contact not found: {loc}")
        mid, loc = await open_via_search(w, mid, contact)

    if (
        loc.get("title") != contact
        and not loc.get("phoneHit")
        and not phone_match(loc.get("title") or "", contact)
    ):
        die(f"title mismatch {loc.get('title')!r} != {contact!r}")

    await mouse(w, mid, loc["x"], loc["y"])
    mid += 3
    await asyncio.sleep(0.35)
    await evaluate(
        w,
        mid,
        f"""(() => {{
          const want = {json.dumps(loc.get('title') or contact)};
          const el = [...document.querySelectorAll('span[title]')].find(s => s.getAttribute('title') === want);
          if (!el) return false;
          const row = el.closest('[data-testid="cell-frame-container"]')
            || el.closest('[role="row"], [role="listitem"]') || el;
          row.click();
          return true;
        }})()""",
    )
    mid += 1
    await asyncio.sleep(1.1)
    mid, h = await header_ok(w, mid, contact)
    if not h or not h.get("ok"):
        die(f"refusing forward; header does not match contact={contact!r} state={h}")
    return mid, loc


async def find_bubble(w, mid, needle: str):
    """Prefer outgoing bubble containing needle (forward often used on own msgs); else any."""
    return mid + 1, await evaluate(
        w,
        mid,
        f"""(() => {{
          const needle = {json.dumps(needle)};
          const nodes = [...document.querySelectorAll('#main [data-testid="msg-container"]')];
          const hits = [];
          for (let i = nodes.length - 1; i >= 0; i--) {{
            const n = nodes[i];
            const t = (n.innerText || '').replace(/\\s+/g, ' ').trim();
            if (!t.includes(needle)) continue;
            const outgoing = !!n.querySelector('[aria-label="You:"]');
            n.scrollIntoView({{block: 'center'}});
            const r = n.getBoundingClientRect();
            hits.push({{
              outgoing,
              text: t.slice(0, 160),
              x: r.x + r.width / 2,
              y: r.y + Math.min(r.height / 2, 28),
              left: r.x, top: r.y, right: r.right, bottom: r.bottom,
              w: r.width, h: r.height,
            }});
          }}
          if (!hits.length) return {{found: false}};
          // Prefer most recent; slight preference for outgoing
          hits.sort((a, b) => (b.outgoing ? 1 : 0) - (a.outgoing ? 1 : 0));
          return {{found: true, ...hits[0], candidates: hits.length, before_count: hits.length}};
        }})()""",
    )


async def count_needle_bubbles(w, mid, needle: str):
    return mid + 1, await evaluate(
        w,
        mid,
        f"""(() => {{
          const needle = {json.dumps(needle)};
          const nodes = [...document.querySelectorAll('#main [data-testid="msg-container"]')];
          let count = 0;
          let latest = null;
          for (const n of nodes) {{
            const t = (n.innerText || '').replace(/\\s+/g, ' ').trim();
            if (!t.includes(needle)) continue;
            count += 1;
            const outgoing = !!n.querySelector('[aria-label="You:"]');
            const forwarded = !!(
              n.querySelector('[data-testid="forwarded"], [data-icon="forwarded"], [data-icon="status-forwarded"]')
              || [...n.querySelectorAll('[aria-label], span')].some(e =>
                /Forwarded/i.test(e.getAttribute('aria-label') || '')
                || /^Forwarded$/i.test((e.textContent || '').trim()))
              || /\\bForwarded\\b/i.test(t)
            );
            latest = {{ text: t.slice(0, 200), outgoing, forwarded }};
          }}
          return {{ count, latest }};
        }})()""",
    )


async def locate_options_btn(w, mid, needle: str):
    return mid + 1, await evaluate(
        w,
        mid,
        f"""(() => {{
          const needle = {json.dumps(needle)};
          const n = [...document.querySelectorAll('#main [data-testid="msg-container"]')]
            .reverse().find(n => (n.innerText || '').includes(needle));
          if (!n) return {{found: false, reason: 'no bubble'}};
          const btn = n.querySelector('[data-testid="icon-down-context"]')
            || [...n.querySelectorAll('[aria-label]')].find(e =>
              /Context menu|open message options|message options/i.test(e.getAttribute('aria-label') || ''));
          if (!btn) {{
            const aria = [...n.querySelectorAll('[aria-label]')].map(e => e.getAttribute('aria-label'));
            return {{found: false, reason: 'no options btn', aria}};
          }}
          const r = btn.getBoundingClientRect();
          if (r.width < 4 || r.height < 4) return {{found: false, reason: 'options btn not visible'}};
          return {{
            found: true,
            x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width, h: r.height,
            aria: btn.getAttribute('aria-label')
          }};
        }})()""",
    )


async def locate_forward_menuitem(w, mid):
    return mid + 1, await evaluate(
        w,
        mid,
        """(() => {
          const items = [...document.querySelectorAll('[role="menuitem"]')]
            .map(e => (e.innerText || '').trim());
          const el = [...document.querySelectorAll('[role="menuitem"]')]
            .find(e => /^Forward$/i.test((e.innerText || '').trim())
              || /^Forward$/i.test(e.getAttribute('aria-label') || ''));
          if (!el) return {found: false, items};
          const r = el.getBoundingClientRect();
          if (r.width < 4 || r.height < 4) return {found: false, reason: 'Forward not visible', items};
          return {
            found: true,
            x: r.x + r.width / 2, y: r.y + r.height / 2,
            w: r.width, h: r.height, items
          };
        })()""",
    )


async def locate_forward_bar_btn(w, mid):
    """After menu Forward: multi-select bar with Cancel forward + Forward confirm."""
    return mid + 1, await evaluate(
        w,
        mid,
        """(() => {
          const cancel = !!document.querySelector('[aria-label="Cancel forward"]');
          const b = [...document.querySelectorAll('button, [role="button"]')]
            .find(e => e.getAttribute('aria-label') === 'Forward');
          if (!b) return {found: false, cancel, reason: 'no Forward bar button'};
          const r = b.getBoundingClientRect();
          if (r.width < 4 || r.height < 4) return {found: false, cancel, reason: 'Forward bar not visible'};
          return {
            found: true, cancel,
            x: r.x + r.width / 2, y: r.y + r.height / 2,
            w: r.width, h: r.height
          };
        })()""",
    )


async def forward_picker_open(w, mid):
    return mid + 1, await evaluate(
        w,
        mid,
        """(() => {
          const popup = document.querySelector('[data-testid="confirm-popup"]')
            || document.querySelector('[data-testid="chat-modal"]');
          const dialog = document.querySelector('[role="dialog"]');
          const search = document.querySelector('input[aria-label="Search name, number or @username"]')
            || [...document.querySelectorAll('input')].find(i =>
              /search name|number|@username/i.test(i.getAttribute('aria-label') || '')
              || /search name|number|@username/i.test(i.getAttribute('placeholder') || ''));
          const text = ((popup || dialog || {}).innerText || '').slice(0, 120);
          const ok = !!(search && (popup || dialog) && /Forward message to/i.test(
            ((popup || dialog || {}).innerText || '') + text));
          return {
            ok: !!(search && (popup || dialog)),
            hasSearch: !!search,
            hasPopup: !!popup,
            hasDialog: !!dialog,
            text,
          };
        })()""",
    )


async def search_forward_dest(w, mid, contact: str):
    loc = await evaluate(
        w,
        mid,
        """(() => {
          const inp = document.querySelector('input[aria-label="Search name, number or @username"]')
            || [...document.querySelectorAll('input')].find(i =>
              /search name|number|@username/i.test(i.getAttribute('aria-label') || '')
              || /search name|number|@username/i.test(i.getAttribute('placeholder') || ''));
          if (!inp) return null;
          const r = inp.getBoundingClientRect();
          inp.focus();
          return {x: r.x + r.width / 2, y: r.y + r.height / 2};
        })()""",
    )
    mid += 1
    if not loc:
        die("forward picker search input missing")
    await mouse(w, mid, loc["x"], loc["y"])
    mid += 3
    await asyncio.sleep(0.15)
    dig = digits_only(contact)
    query = dig if looks_like_phone(contact) and dig else contact.strip()
    await call(
        w,
        mid,
        "Input.dispatchKeyEvent",
        {"type": "keyDown", "key": "a", "code": "KeyA", "windowsVirtualKeyCode": 65, "modifiers": 2},
    )
    mid += 1
    await call(
        w,
        mid,
        "Input.dispatchKeyEvent",
        {"type": "keyUp", "key": "a", "code": "KeyA", "windowsVirtualKeyCode": 65, "modifiers": 2},
    )
    mid += 1
    await call(
        w,
        mid,
        "Input.dispatchKeyEvent",
        {"type": "keyDown", "key": "Backspace", "code": "Backspace", "windowsVirtualKeyCode": 8},
    )
    mid += 1
    await call(
        w,
        mid,
        "Input.dispatchKeyEvent",
        {"type": "keyUp", "key": "Backspace", "code": "Backspace", "windowsVirtualKeyCode": 8},
    )
    mid += 1
    await call(w, mid, "Input.insertText", {"text": query})
    mid += 1
    await asyncio.sleep(1.2)
    return mid, query


async def select_forward_dest(w, mid, contact: str):
    root = (
        'document.querySelector(\'[data-testid="confirm-popup"]\') '
        '|| document.querySelector(\'[data-testid="chat-modal"]\') '
        '|| document.querySelector(\'[role="dialog"]\')'
    )
    mid, hit = await find_row(w, mid, contact, True, root_sel=root)
    if not hit or not hit.get("found"):
        die(f"to-contact not found in forward picker: {hit}")
    await mouse(w, mid, hit["x"], hit["y"])
    mid += 3
    await asyncio.sleep(0.45)
    return mid, hit


async def locate_send_btn(w, mid):
    return mid + 1, await evaluate(
        w,
        mid,
        """(() => {
          const b = [...document.querySelectorAll('button, [role="button"]')]
            .find(e => e.getAttribute('aria-label') === 'Send');
          if (!b) {
            const ic = document.querySelector('[data-icon="wds-ic-send-filled"], [data-testid="wds-ic-send-filled"]');
            if (ic) {
              const r = ic.getBoundingClientRect();
              if (r.width >= 4) return {found: true, kind: 'icon', x: r.x + r.width / 2, y: r.y + r.height / 2};
            }
            return {found: false};
          }
          const r = b.getBoundingClientRect();
          if (r.width < 4 || r.height < 4) return {found: false, reason: 'Send not visible'};
          return {found: true, kind: 'aria', x: r.x + r.width / 2, y: r.y + r.height / 2};
        })()""",
    )


async def dismiss_forward_ui(w, mid):
    """Best-effort Escape / Cancel forward so pane stays healthy on failure paths."""
    cancel = await evaluate(
        w,
        mid,
        """(() => {
          const b = document.querySelector('[aria-label="Cancel forward"]');
          if (!b) return null;
          const r = b.getBoundingClientRect();
          return {x: r.x + r.width / 2, y: r.y + r.height / 2};
        })()""",
    )
    mid += 1
    if cancel:
        await mouse(w, mid, cancel["x"], cancel["y"])
        mid += 3
        await asyncio.sleep(0.3)
    for typ in ("keyDown", "keyUp"):
        await call(
            w,
            mid,
            "Input.dispatchKeyEvent",
            {
                "type": typ,
                "key": "Escape",
                "code": "Escape",
                "windowsVirtualKeyCode": 27,
                "nativeVirtualKeyCode": 27,
            },
        )
        mid += 1
    await asyncio.sleep(0.2)
    return mid


async def verify_forwarded(w, mid, needle: str, before_count: int, same: bool):
    return mid + 1, await evaluate(
        w,
        mid,
        f"""(() => {{
          const needle = {json.dumps(needle)};
          const before = {json.dumps(before_count)};
          const sameChat = {json.dumps(same)};
          const dialogGone = !document.querySelector('[data-testid="confirm-popup"]');
          const cancelGone = !document.querySelector('[aria-label="Cancel forward"]');
          const nodes = [...document.querySelectorAll('#main [data-testid="msg-container"]')];
          const matches = [];
          for (const b of nodes) {{
            const t = (b.innerText || '').replace(/\\s+/g, ' ').trim();
            if (!t.includes(needle)) continue;
            const outgoing = !!b.querySelector('[aria-label="You:"]');
            const forwarded = !!(
              b.querySelector('[data-testid="forwarded"], [data-icon="forwarded"], [data-icon="status-forwarded"]')
              || [...b.querySelectorAll('[aria-label], span')].some(e =>
                /Forwarded/i.test(e.getAttribute('aria-label') || '')
                || /^Forwarded$/i.test((e.textContent || '').trim()))
              || /\\bForwarded\\b/i.test(t)
            );
            const tick = b.querySelector('[data-testid="msg-dblcheck"], [data-testid="msg-check"], [data-icon="msg-dblcheck"], [data-icon="msg-check"]');
            const svgs = [...b.querySelectorAll('svg title, [data-icon]')].map(e => e.getAttribute('data-icon') || e.textContent || '');
            const aria = [...b.querySelectorAll('[aria-label]')].map(e => e.getAttribute('aria-label') || '');
            matches.push({{
              text: t.slice(0, 200),
              outgoing,
              forwarded,
              tickTestId: tick && (tick.getAttribute('data-testid') || tick.getAttribute('data-icon')),
              aria: (tick && tick.getAttribute('aria-label')) || aria.find(a => /Sent|Delivered|Read/i.test(a)) || null,
              svgs, ariaAll: aria.slice(0, 8),
            }});
          }}
          const latest = matches.length ? matches[matches.length - 1] : null;
          const countOk = matches.length > before;
          const forwardedPresent = matches.some(m => m.forwarded);
          const newOutgoing = sameChat
            ? (countOk && latest && (latest.outgoing || true))
            : (matches.length >= 1 && (forwardedPresent || latest));
          return {{
            dialogGone,
            cancelGone,
            before,
            count: matches.length,
            countOk,
            forwardedPresent,
            latest,
            matches: matches.slice(-4),
            ok: !!(dialogGone && cancelGone && matches.length && (countOk || forwardedPresent || (!sameChat && matches.length >= 1))),
          }};
        }})()""",
    )


async def main(from_contact: str, needle: str, to_contact: str, do_search: bool):
    tabs = json.load(urllib.request.urlopen(f"{CDP}/json/list", timeout=5))
    pages = [
        t
        for t in tabs
        if t.get("type") == "page" and "whatsapp" in (t.get("url") or "").lower()
    ]
    if not pages:
        die("no whatsapp page on CDP")
    ws_url = pages[0]["webSocketDebuggerUrl"]
    same = same_contact(from_contact, to_contact)

    async with websockets.connect(ws_url, max_size=None) as w:
        await call(w, 1, "Runtime.enable")
        mid = 10

        ready = await evaluate(
            w,
            mid,
            """(() => ({
          pane: !!document.querySelector('#pane-side'),
          qr: !!(document.querySelector('div[data-testid="qrcode"]') || document.querySelector('canvas[aria-label*="Scan"]'))
            || /Scan.*?QR|Link with phone|Log into WhatsApp/i.test((document.body&&document.body.innerText)||'')
        }))()""",
        )
        mid += 1
        if not ready or not ready.get("pane") or ready.get("qr"):
            die(f"whatsapp not ready: {ready}")

        mid, open_info = await ensure_chat_open(w, mid, from_contact, do_search)

        mid, before = await count_needle_bubbles(w, mid, needle)
        before_count = (before or {}).get("count") or 0

        mid, bubble = await find_bubble(w, mid, needle)
        if not bubble or not bubble.get("found"):
            die(f"bubble not found for needle={needle!r}")

        await mouse_move(w, mid, bubble["x"], bubble["y"])
        mid += 1
        await asyncio.sleep(0.55)

        mid, aff = await locate_options_btn(w, mid, needle)
        if not aff or not aff.get("found"):
            await mouse_move(w, mid, bubble["x"] + 8, bubble["y"])
            mid += 1
            await asyncio.sleep(0.55)
            mid, aff = await locate_options_btn(w, mid, needle)
        if not aff or not aff.get("found"):
            die(f"message options not found: {aff}")

        await mouse(w, mid, aff["x"], aff["y"])
        mid += 3
        await asyncio.sleep(0.5)

        mid, menuitem = await locate_forward_menuitem(w, mid)
        if not menuitem or not menuitem.get("found"):
            await mouse_move(w, mid, bubble["x"], bubble["y"])
            mid += 1
            await asyncio.sleep(0.45)
            mid, aff2 = await locate_options_btn(w, mid, needle)
            if aff2 and aff2.get("found"):
                await mouse(w, mid, aff2["x"], aff2["y"])
                mid += 3
                await asyncio.sleep(0.5)
            mid, menuitem = await locate_forward_menuitem(w, mid)
        if not menuitem or not menuitem.get("found"):
            die(f"Forward menu item missing: {menuitem}")

        await mouse(w, mid, menuitem["x"], menuitem["y"])
        mid += 3
        await asyncio.sleep(0.55)

        mid, bar = await locate_forward_bar_btn(w, mid)
        if not bar or not bar.get("found"):
            await asyncio.sleep(0.4)
            mid, bar = await locate_forward_bar_btn(w, mid)
        if not bar or not bar.get("found"):
            # Some builds may jump straight to picker
            mid, picker = await forward_picker_open(w, mid)
            if not picker or not picker.get("ok"):
                mid = await dismiss_forward_ui(w, mid)
                die(f"Forward bar / picker not active: bar={bar} picker={picker}")
        else:
            await mouse(w, mid, bar["x"], bar["y"])
            mid += 3
            await asyncio.sleep(1.0)
            mid, picker = await forward_picker_open(w, mid)
            if not picker or not picker.get("ok"):
                await asyncio.sleep(0.5)
                mid, picker = await forward_picker_open(w, mid)
            if not picker or not picker.get("ok"):
                mid = await dismiss_forward_ui(w, mid)
                die(f"forward contact picker not open: {picker}")

        mid, query = await search_forward_dest(w, mid, to_contact)
        mid, dest = await select_forward_dest(w, mid, to_contact)

        mid, send = await locate_send_btn(w, mid)
        if not send or not send.get("found"):
            await asyncio.sleep(0.4)
            mid, send = await locate_send_btn(w, mid)
        if not send or not send.get("found"):
            mid = await dismiss_forward_ui(w, mid)
            die(f"Send button missing after selecting dest: {send}")

        await mouse(w, mid, send["x"], send["y"])
        mid += 3
        await asyncio.sleep(1.2)

        if not same:
            mid, to_open = await ensure_chat_open(w, mid, to_contact, do_search)
        else:
            to_open = open_info

        deadline = time.time() + 14
        last = None
        while time.time() < deadline:
            mid, last = await verify_forwarded(w, mid, needle, before_count, same)
            if last and last.get("ok"):
                break
            await asyncio.sleep(0.5)

        if not last or not last.get("ok"):
            mid = await dismiss_forward_ui(w, mid)
            die(f"forward not verified in to-contact: {last}")

        latest = last.get("latest") or {}
        tick_blob = " ".join(
            [
                latest.get("tickTestId") or "",
                latest.get("aria") or "",
                " ".join(latest.get("svgs") or []),
                " ".join(latest.get("ariaAll") or []),
            ]
        ).lower()
        delivered = any(
            k in tick_blob
            for k in (
                "dblcheck",
                "msg-check",
                "delivered",
                "wds-ic-delivered",
                "wds-ic-read",
                " read",
                "sent",
            )
        )

        out = {
            "ok": True,
            "from_contact": from_contact,
            "to_contact": to_contact,
            "needle": needle,
            "same_chat": same,
            "search_query": query,
            "dest_title": dest.get("title"),
            "before_count": before_count,
            "after_count": last.get("count"),
            "forwarded_indicator": last.get("forwardedPresent"),
            "latest": latest,
            "delivered_tick": delivered,
            "open_from": open_info if isinstance(open_info, dict) else {"title": None},
            "open_to": to_open if isinstance(to_open, dict) else {"title": None},
            "note": (
                "same-chat forward verified via new bubble count "
                "(Forwarded label often omitted when forwarding into the same chat)"
                if same and not last.get("forwardedPresent")
                else None
            ),
            "warning": None
            if delivered
            else "bubble visible but no delivery tick yet — may not have reached phone",
        }
        print(json.dumps(out, ensure_ascii=False))
        if not delivered:
            sys.exit(2)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-contact", required=True, help="Chat to open / find needle in")
    ap.add_argument("--needle", required=True, help="Message substring to forward")
    ap.add_argument("--to-contact", required=True, help="Destination chat (can equal from)")
    ap.add_argument("--no-search", action="store_true")
    args = ap.parse_args()
    try:
        asyncio.run(
            main(args.from_contact, args.needle, args.to_contact, not args.no_search)
        )
    except Exception as e:
        die(str(e))
