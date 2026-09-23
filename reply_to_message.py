#!/usr/bin/env python3
"""Reply-to (quote) a WhatsApp Web message via Gate box-chrome CDP.

Usage:
  WA_CDP_HTTP=http://127.0.0.1:9427 python3 reply_to_message.py \\
    --contact '+201092600692' --needle 'You awesome bro' --text 'cdp reply 123'

Flow (mirrors react_to_message / delete_outgoing):
  open contact (list / phone-digit / search) → find bubble with needle
  → hover → message options (icon-down-context) → Reply
  → confirm quoted-message / reply bar → insertText → Enter
  → verify outgoing bubble + delivery tick (prefer quote context).
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


async def find_row(w, mid, contact: str, allow_substring: bool):
    return mid + 1, await evaluate(
        w,
        mid,
        f"""(() => {{
          const want = {json.dumps(contact)};
          const allowSub = {json.dumps(allow_substring)};
          const phoneDigits = {json.dumps(digits_only(contact))};
          const spans = [...document.querySelectorAll('#pane-side span[title], span[title]')];
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
              || s.closest('[role="row"], [role="listitem"]') || s;
            const r = row.getBoundingClientRect();
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
        die(f"refusing reply; header does not match contact={contact!r} state={h}")
    return mid, loc


async def find_bubble(w, mid, needle: str):
    """Prefer incoming bubble containing needle; fall back to any match."""
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
            const incoming = !n.querySelector('[aria-label="You:"]');
            n.scrollIntoView({{block: 'center'}});
            const r = n.getBoundingClientRect();
            hits.push({{
              incoming,
              text: t.slice(0, 160),
              x: r.x + r.width / 2,
              y: r.y + Math.min(r.height / 2, 28),
              left: r.x, top: r.y, right: r.right, bottom: r.bottom,
              w: r.width, h: r.height,
            }});
          }}
          if (!hits.length) return {{found: false}};
          hits.sort((a, b) => (b.incoming ? 1 : 0) - (a.incoming ? 1 : 0));
          return {{found: true, ...hits[0], candidates: hits.length}};
        }})()""",
    )


async def locate_reply_affordance(w, mid, needle: str):
    """Prefer dedicated Reply control; else message-options chevron."""
    return mid + 1, await evaluate(
        w,
        mid,
        f"""(() => {{
          const needle = {json.dumps(needle)};
          const n = [...document.querySelectorAll('#main [data-testid="msg-container"]')]
            .reverse().find(n => (n.innerText || '').includes(needle));
          if (!n) return {{found: false, reason: 'no bubble'}};
          // Dedicated Reply (rare on hover; keep for future DOM)
          const direct = n.querySelector('[data-testid="reply"], [data-testid="reply-btn"]')
            || [...n.querySelectorAll('[aria-label]')].find(e => /^Reply$/i.test(e.getAttribute('aria-label') || ''));
          if (direct) {{
            const r = direct.getBoundingClientRect();
            if (r.width >= 4 && r.height >= 4) {{
              return {{
                found: true, kind: 'direct-reply',
                x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width, h: r.height
              }};
            }}
          }}
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
            found: true, kind: 'context',
            x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width, h: r.height
          }};
        }})()""",
    )


async def locate_reply_menuitem(w, mid):
    return mid + 1, await evaluate(
        w,
        mid,
        """(() => {
          const items = [...document.querySelectorAll('[role="menuitem"]')]
            .map(e => (e.innerText || '').trim());
          const el = [...document.querySelectorAll('[role="menuitem"]')]
            .find(e => /^Reply$/i.test((e.innerText || '').trim())
              || /^Reply$/i.test(e.getAttribute('aria-label') || ''));
          if (!el) return {found: false, items};
          const r = el.getBoundingClientRect();
          if (r.width < 4 || r.height < 4) return {found: false, reason: 'Reply not visible', items};
          return {
            found: true,
            x: r.x + r.width / 2, y: r.y + r.height / 2,
            w: r.width, h: r.height, items
          };
        })()""",
    )


async def reply_bar_active(w, mid, needle: str):
    return mid + 1, await evaluate(
        w,
        mid,
        f"""(() => {{
          const needle = {json.dumps(needle)};
          const quoted = document.querySelector('#main footer [data-testid="quoted-message"]')
            || document.querySelector('#main [data-testid="quoted-message"]');
          const quotedText = quoted ? (quoted.innerText || '').replace(/\\s+/g, ' ').trim() : '';
          const ariaQuoted = !!document.querySelector('#main footer [aria-label="Quoted message"]');
          const ft = ((document.querySelector('#main footer') || {{}}).innerText || '');
          const compose = !!document.querySelector('#main footer div[contenteditable="true"]');
          const needleInBar = !!(quoted && quotedText.includes(needle)) || ft.includes(needle);
          return {{
            ok: !!(compose && (quoted || ariaQuoted) && needleInBar),
            quoted: !!quoted,
            ariaQuoted,
            quotedText: quotedText.slice(0, 160),
            needleInBar,
            compose,
          }};
        }})()""",
    )


async def verify_reply(w, mid, text: str, needle: str):
    return mid + 1, await evaluate(
        w,
        mid,
        f"""(() => {{
          const needle = {json.dumps(needle)};
          const replyText = {json.dumps(text.strip())};
          const compose = ((document.querySelector('#main footer div[contenteditable="true"]')||{{}}).innerText||'').trim();
          const nodes = [...document.querySelectorAll('#main [data-testid="msg-container"]')];
          let match = null;
          for (const b of nodes.slice(-12).reverse()) {{
            const t = (b.innerText || '').replace(/\\s+/g, ' ').trim();
            if (!t.includes(replyText.slice(0, Math.min(40, replyText.length)))) continue;
            if (!b.querySelector('[aria-label="You:"]')) continue;
            const failed = !!b.querySelector('[data-testid="fail-container"]');
            const tick = b.querySelector('[data-testid="msg-dblcheck"], [data-testid="msg-check"], [data-icon="msg-dblcheck"], [data-icon="msg-check"]');
            const svgs = [...b.querySelectorAll('svg title, [data-icon]')].map(e => e.getAttribute('data-icon') || e.textContent || '');
            const aria = [...b.querySelectorAll('[aria-label]')].map(e => e.getAttribute('aria-label') || '');
            const quoted = b.querySelector('[data-testid="quoted-message"]');
            const quotedText = quoted ? (quoted.innerText || '').replace(/\\s+/g, ' ').trim() : '';
            const hasQuote = !!(quoted && (quotedText.includes(needle) || /\\S/.test(quotedText)));
            match = {{
              text: t.slice(0, 220),
              failed,
              tickTestId: tick && (tick.getAttribute('data-testid') || tick.getAttribute('data-icon')),
              aria: (tick && tick.getAttribute('aria-label')) || aria.find(a => /Sent|Delivered|Read/i.test(a)) || null,
              svgs, ariaAll: aria.slice(0, 10),
              hasQuote, quotedText: quotedText.slice(0, 120),
            }};
            break;
          }}
          const stillQuotedComposer = !!document.querySelector('#main footer [data-testid="quoted-message"]');
          return {{
            composeEmpty: !compose || compose === '\\n',
            compose,
            match,
            stillQuotedComposer,
          }};
        }})()""",
    )


async def main(contact: str, needle: str, text: str, do_search: bool):
    tabs = json.load(urllib.request.urlopen(f"{CDP}/json/list", timeout=5))
    pages = [
        t
        for t in tabs
        if t.get("type") == "page" and "whatsapp" in (t.get("url") or "").lower()
    ]
    if not pages:
        die("no whatsapp page on CDP")
    ws_url = pages[0]["webSocketDebuggerUrl"]

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

        mid, open_info = await ensure_chat_open(w, mid, contact, do_search)

        mid, bubble = await find_bubble(w, mid, needle)
        if not bubble or not bubble.get("found"):
            die(f"bubble not found for needle={needle!r}")

        # Hover to reveal options / Reply
        await mouse_move(w, mid, bubble["x"], bubble["y"])
        mid += 1
        await asyncio.sleep(0.55)

        mid, aff = await locate_reply_affordance(w, mid, needle)
        if not aff or not aff.get("found"):
            await mouse_move(w, mid, bubble["x"] + 8, bubble["y"])
            mid += 1
            await asyncio.sleep(0.55)
            mid, aff = await locate_reply_affordance(w, mid, needle)
        if not aff or not aff.get("found"):
            die(f"Reply affordance not found: {aff}")

        await mouse(w, mid, aff["x"], aff["y"])
        mid += 3
        await asyncio.sleep(0.5)

        if aff.get("kind") != "direct-reply":
            mid, menuitem = await locate_reply_menuitem(w, mid)
            if not menuitem or not menuitem.get("found"):
                # one more open attempt
                await mouse_move(w, mid, bubble["x"], bubble["y"])
                mid += 1
                await asyncio.sleep(0.45)
                mid, aff2 = await locate_reply_affordance(w, mid, needle)
                if aff2 and aff2.get("found"):
                    await mouse(w, mid, aff2["x"], aff2["y"])
                    mid += 3
                    await asyncio.sleep(0.5)
                mid, menuitem = await locate_reply_menuitem(w, mid)
            if not menuitem or not menuitem.get("found"):
                die(f"Reply menu item missing: {menuitem}")
            await mouse(w, mid, menuitem["x"], menuitem["y"])
            mid += 3
            await asyncio.sleep(0.55)

        mid, bar = await reply_bar_active(w, mid, needle)
        if not bar or not bar.get("ok"):
            await asyncio.sleep(0.4)
            mid, bar = await reply_bar_active(w, mid, needle)
        if not bar or not bar.get("ok"):
            die(f"reply composer / quoted preview not active: {bar}")

        # Focus compose and insert reply text
        compose_ok = await evaluate(
            w,
            mid,
            """(() => {
              const c = document.querySelector('#main footer div[contenteditable="true"][data-tab="10"]')
                || document.querySelector('#main footer div[contenteditable="true"]');
              if (!c) return false;
              c.focus();
              return true;
            })()""",
        )
        mid += 1
        if not compose_ok:
            die("no compose box after Reply")

        await call(w, mid, "Input.insertText", {"text": text})
        mid += 1
        await asyncio.sleep(0.25)

        draft = await evaluate(
            w,
            mid,
            """(() => {
              const c = document.querySelector('#main footer div[contenteditable="true"]');
              return (c && (c.innerText || '')).trim();
            })()""",
        )
        mid += 1
        norm = lambda s: " ".join((s or "").replace("\n", " ").split())
        if norm(text) not in norm(draft) and text.strip() not in (draft or ""):
            die(f"compose draft mismatch before send: {draft!r}")

        for typ in ("keyDown", "keyUp"):
            await call(
                w,
                mid,
                "Input.dispatchKeyEvent",
                {
                    "type": typ,
                    "key": "Enter",
                    "code": "Enter",
                    "windowsVirtualKeyCode": 13,
                    "nativeVirtualKeyCode": 13,
                },
            )
            mid += 1

        deadline = time.time() + 12
        last = None
        while time.time() < deadline:
            await asyncio.sleep(0.45)
            mid, last = await verify_reply(w, mid, text, needle)
            if (
                last
                and last.get("composeEmpty")
                and last.get("match")
                and not last["match"].get("failed")
            ):
                break

        if not last or not last.get("match") or not last.get("composeEmpty"):
            die(f"reply send not confirmed: {last}")
        if last["match"].get("failed"):
            die(f"reply failed (fail-container): {last}")

        tick_blob = " ".join(
            [
                last["match"].get("tickTestId") or "",
                last["match"].get("aria") or "",
                " ".join(last["match"].get("svgs") or []),
                " ".join(last["match"].get("ariaAll") or []),
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
            "contact": contact,
            "needle": needle,
            "text": text,
            "incoming": bubble.get("incoming"),
            "reply_bar": bar,
            "has_quote": last["match"].get("hasQuote"),
            "quoted_text": last["match"].get("quotedText"),
            "delivered_tick": delivered,
            "tick": last["match"].get("tickTestId"),
            "aria": last["match"].get("aria"),
            "svgs": last["match"].get("svgs"),
            "open": open_info if isinstance(open_info, dict) else {"title": getattr(open_info, "get", lambda *_: None)("title")},
            "warning": None
            if delivered
            else "bubble visible but no delivery tick yet — may not have reached phone",
        }
        print(json.dumps(out, ensure_ascii=False))
        if not delivered:
            sys.exit(2)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--contact", required=True, help="Exact chat title OR phone (+20… / digits)")
    ap.add_argument("--needle", required=True, help="Message substring to reply to")
    ap.add_argument("--text", required=True, help="Reply body")
    ap.add_argument("--no-search", action="store_true")
    args = ap.parse_args()
    try:
        asyncio.run(main(args.contact, args.needle, args.text, not args.no_search))
    except Exception as e:
        die(str(e))
