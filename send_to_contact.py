#!/usr/bin/env python3
"""Reliable WhatsApp Web send via Gate inoculum CDP.

Usage:
  WA_CDP_HTTP=http://127.0.0.1:9427 python3 send_to_contact.py --contact 'Name' --text '...'
  WA_CDP_HTTP=http://127.0.0.1:9427 python3 send_to_contact.py --contact '+201092600692' --text '...'

Guarantees:
  - Refuse if QR / no chat list
  - Open by exact title OR phone-digit match (non-contact chats title as raw numbers)
  - Search pane if not in recent list (--no-search to disable)
  - Header ok via title string OR matching phone digits (conversation-info-header text)
  - Prefer cell-frame-container click
  - After Enter: compose empty + bubble + no fail-container
  - Delivery: data-testid ticks OR svg title wds-ic-delivered / aria Sent|Delivered|Read
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
    return len(d) >= 8 and sum(c.isdigit() or c in "+- ()" for c in (s or "")) >= len((s or "").strip()) * 0.6


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


async def click(w, mid0, x, y, button="left"):
    for i, typ in enumerate(("mouseMoved", "mousePressed", "mouseReleased")):
        params = {"type": typ, "x": x, "y": y}
        if typ != "mouseMoved":
            params.update(button=button, clickCount=1)
        await call(w, mid0 + i, "Input.dispatchMouseEvent", params)


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
        die("contact not in list and no search input (non-contact open needs search)")
    await click(w, mid, loc["x"], loc["y"])
    mid += 3
    await asyncio.sleep(0.2)
    # clear then type digits-prefer query
    q = contact.strip()
    dig = digits_only(contact)
    query = dig if looks_like_phone(contact) and dig else q
    await call(w, mid, "Input.dispatchKeyEvent", {"type": "keyDown", "key": "a", "code": "KeyA", "windowsVirtualKeyCode": 65, "modifiers": 2})
    mid += 1
    await call(w, mid, "Input.dispatchKeyEvent", {"type": "keyUp", "key": "a", "code": "KeyA", "windowsVirtualKeyCode": 65, "modifiers": 2})
    mid += 1
    await call(w, mid, "Input.dispatchKeyEvent", {"type": "keyDown", "key": "Backspace", "code": "Backspace", "windowsVirtualKeyCode": 8})
    mid += 1
    await call(w, mid, "Input.dispatchKeyEvent", {"type": "keyUp", "key": "Backspace", "code": "Backspace", "windowsVirtualKeyCode": 8})
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
            parts, headerText: headerText.slice(0, 160), dig, phoneHit, titleHit,
            compose: !!document.querySelector('#main footer div[contenteditable="true"]')
          }};
        }})()""",
    )
    return mid + 1, st


async def main(contact: str, text: str, allow_substring: bool, do_search: bool):
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

        mid, loc = await find_row(w, mid, contact, allow_substring)
        if not loc or not loc.get("found"):
            if not do_search:
                die(f"contact not found: {loc}")
            mid, loc = await open_via_search(w, mid, contact)

        # Prefer exact title; phone-digit match is OK for non-contacts
        if (
            loc.get("title") != contact
            and not allow_substring
            and not loc.get("phoneHit")
            and not phone_match(loc.get("title") or "", contact)
        ):
            die(f"title mismatch {loc.get('title')!r} != {contact!r}")

        await click(w, mid, loc["x"], loc["y"])
        mid += 3
        await asyncio.sleep(0.35)
        # JS reinforce click on cell-frame
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
            die(f"refusing send; header does not match contact={contact!r} state={h}")

        compose_ok = await evaluate(
            w,
            mid,
            """(() => {
              const c = document.querySelector('#main footer div[contenteditable="true"][data-tab="10"]')
                || document.querySelector('#main footer div[contenteditable="true"]');
              if (!c) return false;
              c.focus();
              document.execCommand('selectAll');
              document.execCommand('delete');
              return true;
            })()""",
        )
        mid += 1
        if not compose_ok:
            die("no compose box")

        await call(w, mid, "Input.insertText", {"text": text})
        mid += 1
        await asyncio.sleep(0.25)

        draft = await evaluate(
            w,
            mid,
            """(() => {
              const c = document.querySelector('#main footer div[contenteditable="true"]');
              return (c && (c.innerText||'')).trim();
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
            last = await evaluate(
                w,
                mid,
                f"""(() => {{
                  const contact = {json.dumps(contact)};
                  const phoneDigits = {json.dumps(digits_only(contact))};
                  const needle = {json.dumps(text.strip())};
                  const headerText = ((document.querySelector('#main header')||{{}}).innerText||'');
                  const parts = [...document.querySelectorAll('#main header span[title], #main header [dir="auto"]')]
                    .map(e => (e.getAttribute('title')||e.textContent||'').trim()).filter(Boolean);
                  const blob = parts.join(' ') + ' ' + headerText;
                  const dig = blob.replace(/\\D/g, '');
                  const phoneHit = phoneDigits.length >= 8 && dig.length >= 8 && (
                    dig.includes(phoneDigits) || phoneDigits.includes(dig)
                    || dig.endsWith(phoneDigits.slice(-9)) || phoneDigits.endsWith(dig.slice(-9))
                  );
                  const headerOk = parts.some(h => h === contact || h.includes(contact))
                    || blob.includes(contact) || phoneHit;
                  const compose = ((document.querySelector('#main footer div[contenteditable="true"]')||{{}}).innerText||'').trim();
                  const nodes = [...document.querySelectorAll('#main [data-testid="msg-container"]')];
                  let match = null;
                  for (const b of nodes.slice(-10).reverse()) {{
                    const t = (b.innerText||'').replace(/\\s+/g,' ').trim();
                    if (!t.includes(needle.slice(0, Math.min(40, needle.length)))) continue;
                    const tick = b.querySelector('[data-testid="msg-dblcheck"], [data-testid="msg-check"], [data-icon="msg-dblcheck"], [data-icon="msg-check"]');
                    const svgs = [...b.querySelectorAll('svg title, [data-icon]')].map(e => e.getAttribute('data-icon')||e.textContent||'');
                    const aria = [...b.querySelectorAll('[aria-label]')].map(e => e.getAttribute('aria-label')||'');
                    const failed = !!b.querySelector('[data-testid="fail-container"]');
                    match = {{
                      text: t.slice(0, 200),
                      failed,
                      tickTestId: tick && (tick.getAttribute('data-testid')||tick.getAttribute('data-icon')),
                      aria: (tick && tick.getAttribute('aria-label')) || aria.find(a => /Sent|Delivered|Read/i.test(a)) || null,
                      svgs, ariaAll: aria.slice(0, 8),
                    }};
                    break;
                  }}
                  return {{
                    headerOk,
                    headers: parts,
                    composeEmpty: !compose || compose === '\\n',
                    compose,
                    match,
                  }};
                }})()""",
            )
            mid += 1
            if (
                last
                and last.get("headerOk")
                and last.get("composeEmpty")
                and last.get("match")
                and not last["match"].get("failed")
            ):
                break
            await asyncio.sleep(0.45)

        if not last or not last.get("match") or not last.get("composeEmpty"):
            die(f"send not confirmed: {last}")
        if last["match"].get("failed"):
            die(f"send failed (fail-container): {last}")
        if not last.get("headerOk"):
            die(f"header drifted after send: {last}")

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
            "resolved_title": loc.get("title"),
            "text": text,
            "delivered_tick": delivered,
            "tick": last["match"].get("tickTestId"),
            "aria": last["match"].get("aria"),
            "svgs": last["match"].get("svgs"),
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
    ap.add_argument("--text", required=True)
    ap.add_argument("--allow-substring", action="store_true")
    ap.add_argument(
        "--no-search",
        action="store_true",
        help="Do not fall back to Search pane (default: search if missing from list)",
    )
    args = ap.parse_args()
    try:
        asyncio.run(
            main(args.contact, args.text, args.allow_substring, not args.no_search)
        )
    except Exception as e:
        die(str(e))
