#!/usr/bin/env python3
"""React to a WhatsApp Web message via Gate inoculum CDP.

Usage:
  WA_CDP_HTTP=http://127.0.0.1:9427 python3 react_to_message.py \\
    --contact '+201092600692' --needle 'You awesome bro' --emoji '❤️'

Flow (mirrors successful computerUse path):
  open contact (list / phone-digit / search) → find bubble with needle
  (incoming preferred) → hover → click aria React / reaction-entry-point
  → click matching img[alt=emoji] with real mouse coords → verify.
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
    # Real pointer path: move → press → release (click alone on img[alt] can fail to stick)
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
        die(f"refusing react; header does not match contact={contact!r} state={h}")
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
            const parent = n.closest('.focusable-list-item') || n.parentElement;
            const reactionBubble = parent && parent.querySelector('[data-testid="reaction-bubble"]');
            const reactionAria = reactionBubble && (reactionBubble.getAttribute('aria-label') || '');
            const reactionAlts = parent
              ? [...parent.querySelectorAll('img[alt]')].map(e => e.getAttribute('alt') || '')
              : [];
            hits.push({{
              incoming,
              text: t.slice(0, 160),
              x: r.x + r.width / 2,
              y: r.y + Math.min(r.height / 2, 28),
              left: r.x, top: r.y, right: r.right, bottom: r.bottom,
              w: r.width, h: r.height,
              reactionAria,
              reactionAlts,
            }});
          }}
          if (!hits.length) return {{found: false}};
          hits.sort((a, b) => (b.incoming ? 1 : 0) - (a.incoming ? 1 : 0));
          return {{found: true, ...hits[0], candidates: hits.length}};
        }})()""",
    )


async def locate_react_btn(w, mid, needle: str):
    return mid + 1, await evaluate(
        w,
        mid,
        f"""(() => {{
          const needle = {json.dumps(needle)};
          const n = [...document.querySelectorAll('#main [data-testid="msg-container"]')]
            .reverse().find(n => (n.innerText || '').includes(needle));
          if (!n) return {{found: false, reason: 'no bubble'}};
          const btn = n.querySelector('[data-testid="reaction-entry-point"]')
            || [...n.querySelectorAll('[aria-label]')].find(e => /^React$/i.test(e.getAttribute('aria-label') || ''));
          if (!btn) {{
            const aria = [...n.querySelectorAll('[aria-label]')].map(e => e.getAttribute('aria-label'));
            return {{found: false, reason: 'no react btn', aria}};
          }}
          const r = btn.getBoundingClientRect();
          if (r.width < 4 || r.height < 4) return {{found: false, reason: 'react btn not visible'}};
          return {{
            found: true,
            x: r.x + r.width / 2,
            y: r.y + r.height / 2,
            w: r.width, h: r.height
          }};
        }})()""",
    )


async def locate_emoji(w, mid, emoji: str, bubble_top: float, bubble_bottom: float):
    """Pick emoji from the reaction tray near the bubble (not sidebar previews)."""
    return mid + 1, await evaluate(
        w,
        mid,
        f"""(() => {{
          const emoji = {json.dumps(emoji)};
          const top = {json.dumps(bubble_top)};
          const bottom = {json.dumps(bubble_bottom)};
          const mainLeft = (document.querySelector('#main') || {{}}).getBoundingClientRect
            ? document.querySelector('#main').getBoundingClientRect().x : 400;
          const candidates = [];
          // Prefer button wrappers in the quick-react tray
          for (const btn of document.querySelectorAll('button, [role="button"]')) {{
            const img = btn.querySelector('img[alt]');
            const alt = (img && img.getAttribute('alt')) || '';
            if (alt !== emoji) continue;
            const r = btn.getBoundingClientRect();
            if (r.width < 12 || r.height < 12) continue;
            if (r.y < 0 || r.y > window.innerHeight) continue;
            if (r.x < mainLeft - 20) continue; // skip pane-side
            // tray sits just above/beside the bubble
            const nearY = r.bottom >= top - 80 && r.top <= bottom + 40;
            candidates.push({{
              kind: 'button',
              alt,
              x: r.x + r.width / 2,
              y: r.y + r.height / 2,
              w: r.width, h: r.height,
              nearY,
              score: (nearY ? 100 : 0) + Math.min(r.width, r.height)
            }});
          }}
          for (const img of document.querySelectorAll('#main img[alt]')) {{
            const alt = img.getAttribute('alt') || '';
            if (alt !== emoji) continue;
            const r = img.getBoundingClientRect();
            if (r.width < 12 || r.height < 12) continue;
            if (r.y < 0 || r.y > window.innerHeight) continue;
            // skip existing reaction chip under the bubble
            const isChip = !!img.closest('[data-testid="reaction-bubble"]');
            if (isChip) continue;
            const nearY = r.bottom >= top - 80 && r.top <= bottom + 40;
            candidates.push({{
              kind: 'img',
              alt,
              x: r.x + r.width / 2,
              y: r.y + r.height / 2,
              w: r.width, h: r.height,
              nearY,
              score: (nearY ? 100 : 0) + Math.min(r.width, r.height)
            }});
          }}
          candidates.sort((a, b) => b.score - a.score);
          if (!candidates.length) {{
            // diagnostic: any emoji imgs visible
            const alts = [...document.querySelectorAll('#main img[alt]')]
              .map(e => {{
                const r = e.getBoundingClientRect();
                return {{alt: e.getAttribute('alt'), w: r.width, h: r.height, y: r.y}};
              }})
              .filter(e => e.w >= 12 && e.h >= 12 && e.y > 0 && e.y < window.innerHeight)
              .slice(0, 20);
            return {{found: false, alts}};
          }}
          return {{found: true, ...candidates[0], n: candidates.length}};
        }})()""",
    )


async def verify_reaction(w, mid, needle: str, emoji: str):
    return mid + 1, await evaluate(
        w,
        mid,
        f"""(() => {{
          const needle = {json.dumps(needle)};
          const emoji = {json.dumps(emoji)};
          const nodes = [...document.querySelectorAll('#main [data-testid="msg-container"]')];
          let bubbleHit = null;
          for (const n of nodes) {{
            const t = (n.innerText || '');
            if (!t.includes(needle)) continue;
            const parent = n.closest('.focusable-list-item') || n.parentElement || n;
            const rb = parent.querySelector('[data-testid="reaction-bubble"]');
            const aria = (rb && rb.getAttribute('aria-label')) || '';
            const alts = [...parent.querySelectorAll('img[alt]')].map(e => e.getAttribute('alt') || '');
            bubbleHit = {{
              aria,
              alts,
              hasEmoji: aria.includes(emoji) || alts.includes(emoji),
              text: t.replace(/\\s+/g, ' ').trim().slice(0, 120),
            }};
            break;
          }}
          // toast / confirmation text
          const body = document.body.innerText || '';
          const toast = /You reacted\\s*.{{0,4}}\\s*to/i.test(body)
            || body.includes('You reacted ' + emoji)
            || [...document.querySelectorAll('[role="status"], [data-testid*="toast"], span, div')]
              .some(e => {{
                const tx = (e.textContent || '').trim();
                return /^You reacted/.test(tx) && tx.length < 120 && tx.includes(emoji);
              }});
          // any aria indicating our reaction near needle
          const ariaReact = [...document.querySelectorAll('#main [aria-label]')]
            .map(e => e.getAttribute('aria-label') || '')
            .filter(a => a.includes(emoji) && /react/i.test(a))
            .slice(0, 6);
          return {{
            bubble: bubbleHit,
            toast,
            ariaReact,
            ok: !!(bubbleHit && bubbleHit.hasEmoji) || toast || ariaReact.length > 0,
          }};
        }})()""",
    )


async def main(contact: str, needle: str, emoji: str, do_search: bool):
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

        # Hover bubble to reveal React affordance
        await mouse_move(w, mid, bubble["x"], bubble["y"])
        mid += 1
        await asyncio.sleep(0.55)

        mid, react = await locate_react_btn(w, mid, needle)
        if not react or not react.get("found"):
            # re-hover slightly and retry once
            await mouse_move(w, mid, bubble["x"] + 8, bubble["y"])
            mid += 1
            await asyncio.sleep(0.55)
            mid, react = await locate_react_btn(w, mid, needle)
        if not react or not react.get("found"):
            die(f"React affordance not found: {react}")

        await mouse(w, mid, react["x"], react["y"])
        mid += 3
        await asyncio.sleep(0.55)

        mid, emo = await locate_emoji(w, mid, emoji, bubble["top"], bubble["bottom"])
        if not emo or not emo.get("found"):
            # one more wait for tray
            await asyncio.sleep(0.45)
            mid, emo = await locate_emoji(w, mid, emoji, bubble["top"], bubble["bottom"])
        if not emo or not emo.get("found"):
            die(f"emoji {emoji!r} not in reaction tray: {emo}")

        # Real mouse click on emoji (center of button preferred)
        await mouse_move(w, mid, emo["x"], emo["y"])
        mid += 1
        await asyncio.sleep(0.12)
        await mouse(w, mid, emo["x"], emo["y"])
        mid += 3

        deadline = time.time() + 8
        last = None
        while time.time() < deadline:
            await asyncio.sleep(0.4)
            mid, last = await verify_reaction(w, mid, needle, emoji)
            if last and last.get("ok"):
                break

        if not last or not last.get("ok"):
            die(f"reaction not confirmed: {last}")

        out = {
            "ok": True,
            "contact": contact,
            "needle": needle,
            "emoji": emoji,
            "incoming": bubble.get("incoming"),
            "verify": last,
            "open": open_info if isinstance(open_info, dict) else {"title": open_info.get("title") if open_info else None},
        }
        print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--contact", required=True, help="Exact chat title OR phone (+20… / digits)")
    ap.add_argument("--needle", required=True, help="Message substring to react to")
    ap.add_argument("--emoji", default="👍", help="Reaction emoji (default 👍)")
    ap.add_argument("--no-search", action="store_true")
    args = ap.parse_args()
    try:
        asyncio.run(main(args.contact, args.needle, args.emoji, not args.no_search))
    except Exception as e:
        die(str(e))
