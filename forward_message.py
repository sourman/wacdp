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
import re
import re
import re
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


def normalize_handle(s: str) -> str:
    """Lowercased username without leading @ or trailing ' (You)'."""
    t = (s or "").strip()
    t = re.sub(r"\s*\(You\)\s*$", "", t, flags=re.I).strip()
    if t.startswith("@"):
        t = t[1:]
    return t.lower()


def is_handle(s: str) -> bool:
    t = (s or "").strip()
    t = re.sub(r"\s*\(You\)\s*$", "", t, flags=re.I).strip()
    return t.startswith("@") and len(t) > 1


def handle_match(a: str, b: str) -> bool:
    ha, hb = normalize_handle(a), normalize_handle(b)
    if not ha or not hb:
        return False
    return ha == hb or ha.startswith(hb) or hb.startswith(ha)


def is_noise_row(title: str) -> bool:
    """True Message-yourself / My status noise — not a username dest."""
    t = (title or "").strip().lower()
    if not t:
        return False
    if t in ("message yourself", "my status", "status"):
        return True
    if "message yourself" in t and not t.startswith("@"):
        return True
    return False


def same_contact(a: str, b: str) -> bool:
    if (a or "").strip() == (b or "").strip():
        return True
    if handle_match(a, b):
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


async def find_row(w, mid, contact: str, allow_substring: bool, root_sel: str = "",
                 phone_query_typed: bool = False):
    """Find a chat/contact row.

    Preference when dest is a phone:
      exact phone title → phone-digit match in row → @handle after typing the
      phone → fail.

    Phone searches often resolve to @username / @username (You). Those are valid
    hits — do NOT treat them as Message-yourself noise. Only skip true
    "Message yourself" / "My status" rows unless they are the explicit dest.
    """
    root_js = root_sel or "document"
    want_handle = normalize_handle(contact) if is_handle(contact) else ""
    query_is_phone = looks_like_phone(contact) or (
        phone_query_typed and len(digits_only(contact)) >= 8
    )
    return mid + 1, await evaluate(
        w,
        mid,
        f"""(() => {{
          const want = {json.dumps(contact)};
          const allowSub = {json.dumps(allow_substring)};
          const phoneDigits = {json.dumps(digits_only(contact))};
          const wantHandle = {json.dumps(want_handle)};
          const queryIsPhone = {json.dumps(query_is_phone)};
          const root = {root_js} || document;
          const spans = [...root.querySelectorAll('span[title], div[title]')];
          const normHandle = (t) => {{
            let s = (t || '').trim();
            s = s.replace(/\\s*\\(You\\)\\s*$/i, '').trim();
            if (s.startsWith('@')) s = s.slice(1);
            return s.toLowerCase();
          }};
          const isHandleTitle = (t) => {{
            let s = (t || '').trim();
            s = s.replace(/\\s*\\(You\\)\\s*$/i, '').trim();
            return s.startsWith('@') && s.length > 1;
          }};
          const isNoise = (t) => {{
            const s = (t || '').trim().toLowerCase();
            if (!s) return false;
            if (s === 'message yourself' || s === 'my status' || s === 'status') return true;
            if (s.includes('message yourself') && !s.startsWith('@')) return true;
            return false;
          }};
          const candidates = [];
          for (const s of spans) {{
            const title = s.getAttribute('title') || '';
            const dig = title.replace(/\\D/g, '');
            const exact = title === want;
            const sub = allowSub && want && title.includes(want);
            const phoneHit = phoneDigits.length >= 8 && dig.length >= 8 && (
              dig.endsWith(phoneDigits.slice(-9)) || phoneDigits.endsWith(dig.slice(-9))
              || dig.includes(phoneDigits) || phoneDigits.includes(dig)
            );
            const hTitle = normHandle(title);
            const handleHit = !!(
              (wantHandle && hTitle && (hTitle === wantHandle
                || hTitle.startsWith(wantHandle) || wantHandle.startsWith(hTitle)))
              || (queryIsPhone && isHandleTitle(title) && !isNoise(title))
            );
            const noise = isNoise(title);
            // Explicit dest may be Message yourself; otherwise skip noise.
            if (noise && !exact && !phoneHit && !(wantHandle && hTitle === wantHandle)
                && !/^message yourself$/i.test((want || '').trim())
                && !/^my status$/i.test((want || '').trim())) {{
              continue;
            }}
            if (!exact && !sub && !phoneHit && !handleHit) continue;
            const row = s.closest('[data-testid="cell-frame-container"]')
              || s.closest('[data-testid="checkbox-selectable-wrapper"]')
              || s.closest('[role="row"], [role="listitem"], label') || s;
            const r = row.getBoundingClientRect();
            if (r.width < 4 || r.height < 4) continue;
            // Prefer: exact phone/title → phone digits → @handle
            let score = 0;
            if (exact) score += 100;
            if (phoneHit) score += 80;
            if (sub) score += 10;
            if (handleHit) {{
              // Handle after phone search is a valid dest (e.g. +1… → @sourmansa).
              // Prefer plain @user slightly over @user (You); do NOT reject either.
              score += queryIsPhone ? 55 : 90;
              if (/\\(You\\)/i.test(title)) score -= 5;
            }}
            if (noise) score -= 40;
            candidates.push({{
              title, exact, sub, phoneHit, handleHit, noise,
              x: r.x + r.width / 2, y: r.y + r.height / 2,
              score
            }});
          }}
          candidates.sort((a,b) => b.score - a.score);
          const titles = spans.map(s => s.getAttribute('title')).filter(Boolean).slice(0, 40);
          if (!candidates.length) {{
            return {{found:false, titles}};
          }}
          let hit = candidates[0];
          if (queryIsPhone) {{
            const phoneish = candidates.filter(c => c.exact || c.phoneHit);
            const handles = candidates.filter(c => c.handleHit && !c.phoneHit && !c.exact);
            if (phoneish.length) hit = phoneish[0];
            else if (handles.length) hit = handles[0];
          }}
          const el = spans.find(s => s.getAttribute('title') === hit.title);
          if (el) el.scrollIntoView({{block:'center'}});
          return {{
            found:true, ...hit,
            titles,
            candidate_count: candidates.length,
            handle_only: !!(queryIsPhone && hit.handleHit && !hit.phoneHit && !hit.exact)
          }};
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


async def header_ok(w, mid, contact: str, alias: str | None = None):
    want_handle = ""
    if is_handle(contact):
        want_handle = normalize_handle(contact)
    elif alias and is_handle(alias):
        want_handle = normalize_handle(alias)
    return mid + 1, await evaluate(
        w,
        mid,
        f"""(() => {{
          const contact = {json.dumps(contact)};
          const alias = {json.dumps(alias or "")};
          const phoneDigits = {json.dumps(digits_only(contact))};
          const wantHandle = {json.dumps(want_handle)};
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
          const titleHit = parts.some(h => h === contact || h.includes(contact)) || blob.includes(contact)
            || (alias && (parts.some(h => h === alias || h.includes(alias)) || blob.includes(alias)));
          const norm = (t) => {{
            let s = (t || '').trim().replace(/\\s*\\(You\\)\\s*$/i, '').trim();
            if (s.startsWith('@')) s = s.slice(1);
            return s.toLowerCase();
          }};
          const handleHit = !!(wantHandle && (
            parts.some(h => {{
              const n = norm(h);
              return n && (n === wantHandle || n.startsWith(wantHandle) || wantHandle.startsWith(n));
            }})
            || norm(blob).includes(wantHandle)
          ));
          return {{
            ok: !!(document.querySelector('#main footer div[contenteditable="true"]') && (titleHit || phoneHit || handleHit)),
            parts, headerText: headerText.slice(0, 160), dig, phoneHit, titleHit, handleHit
          }};
        }})()""",
    )


async def ensure_chat_open(w, mid, contact: str, do_search: bool, alias: str | None = None):
    for _ in range(3):
        stuck = await evaluate(
            w,
            mid,
            """(() => ({
              dialog: !!document.querySelector('[role="dialog"], [data-testid="confirm-popup"]'),
              cancelFwd: !!document.querySelector('[aria-label="Cancel forward"]'),
              compose: !!document.querySelector('#main footer div[contenteditable="true"]')
            }))()""",
        )
        mid += 1
        if stuck and not stuck.get("dialog") and not stuck.get("cancelFwd"):
            break
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
    hdr_text = (hdr.get("text") or "") if hdr else ""
    already = hdr and hdr.get("compose") and (
        contact in hdr_text
        or (alias and alias in hdr_text)
        or phone_match(hdr_text, contact)
        or phone_match(hdr.get("dig") or "", contact)
        or handle_match(hdr_text, contact)
        or (alias and handle_match(hdr_text, alias))
        or (is_handle(contact) and handle_match(hdr_text, contact))
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
        and not loc.get("handleHit")
        and not phone_match(loc.get("title") or "", contact)
        and not handle_match(loc.get("title") or "", contact)
        and not (alias and handle_match(loc.get("title") or "", alias))
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
    mid, h = await header_ok(w, mid, contact, alias=alias or (loc.get("title") if loc.get("handleHit") else None))
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
            const ariaLabs = [...n.querySelectorAll('[aria-label]')].map(e => e.getAttribute('aria-label') || '');
            const icons = [...n.querySelectorAll('[data-icon], svg title')].map(e => e.getAttribute('data-icon') || e.textContent || '');
            const outgoing = !!(
              n.querySelector('[aria-label="You:"], [aria-label="You"]')
              || ariaLabs.some(a => /Sent|Delivered|Read/i.test(a || ''))
              || n.querySelector('[data-testid="msg-dblcheck"], [data-testid="msg-check"], [data-icon="msg-dblcheck"], [data-icon="msg-check"]')
              || icons.some(x => /delivered|msg-check|msg-dblcheck|wds-ic-read|wds-ic-delivered|wds-ic-sent/i.test(x))
            );
            n.scrollIntoView({{block: 'center'}});
            const r = n.getBoundingClientRect();
            const quote = n.querySelector('[data-testid="quoted-message"]');
            const primaryText = [...n.querySelectorAll('[data-testid="selectable-text"]')]
              .filter(e => !quote || !quote.contains(e))
              .map(e => e.innerText || '').join(' ');
            const primary = primaryText ? primaryText.includes(needle)
              : (quote ? (n.innerText || '').replace(quote.innerText || '', '').includes(needle) : true);
            hits.push({{
              outgoing, primary,
              text: t.slice(0, 160),
              x: r.x + r.width / 2,
              y: r.y + Math.min(r.height / 2, 28),
              left: r.x, top: r.y, right: r.right, bottom: r.bottom,
              w: r.width, h: r.height,
            }});
          }}
          if (!hits.length) return {{found: false}};
          // Prefer primary-text match, then outgoing, then most recent
          hits.sort((a, b) => ((b.primary?2:0)+(b.outgoing?1:0)) - ((a.primary?2:0)+(a.outgoing?1:0)));
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
            const ariaLabs = [...n.querySelectorAll('[aria-label]')].map(e => e.getAttribute('aria-label') || '');
            const icons = [...n.querySelectorAll('[data-icon], svg title')].map(e => e.getAttribute('data-icon') || e.textContent || '');
            const outgoing = !!(
              n.querySelector('[aria-label="You:"], [aria-label="You"]')
              || ariaLabs.some(a => /Sent|Delivered|Read/i.test(a || ''))
              || n.querySelector('[data-testid="msg-dblcheck"], [data-testid="msg-check"], [data-icon="msg-dblcheck"], [data-icon="msg-check"]')
              || icons.some(x => /delivered|msg-check|msg-dblcheck|wds-ic-read|wds-ic-delivered|wds-ic-sent/i.test(x))
            );
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
          const primaryHas = (el) => {{
            const quote = el.querySelector('[data-testid="quoted-message"]');
            const texts = [...el.querySelectorAll('[data-testid="selectable-text"]')]
              .filter(e => !quote || !quote.contains(e))
              .map(e => e.innerText || '').join(' ');
            if (texts) return texts.includes(needle);
            if (quote) return (el.innerText || '').replace(quote.innerText || '', '').includes(needle);
            return (el.innerText || '').includes(needle);
          }};
          const nodes = [...document.querySelectorAll('#main [data-testid="msg-container"]')].reverse();
          const n = nodes.find(el => primaryHas(el)) || nodes.find(el => (el.innerText || '').includes(needle));
          if (!n) return {{found: false, reason: 'no bubble'}};
          const root = n.closest('.focusable-list-item') || n.parentElement || n;
          const btn = root.querySelector('[data-testid="icon-down-context"], [data-testid="down-context"]')
            || [...root.querySelectorAll('[aria-label]')].find(e =>
              /Context menu|open message options|message options/i.test(e.getAttribute('aria-label') || ''));
          if (!btn) {{
            const aria = [...root.querySelectorAll('[aria-label]')].map(e => e.getAttribute('aria-label'));
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
            || document.querySelector('[data-testid="chat-modal"]')
            || document.querySelector('[role="dialog"]');
          const dialog = popup;
          const root = popup || document;
          const search = root.querySelector('input[aria-label="Search name, number or @username"]')
            || root.querySelector('input[placeholder="Search name, number or @username"]')
            || [...root.querySelectorAll('input[type="text"], input:not([type])')].find(i => {
              const blob = ((i.getAttribute('aria-label')||'') + ' ' + (i.getAttribute('placeholder')||'')).toLowerCase();
              return /search/.test(blob) && /name|number|user/.test(blob);
            })
            || [...root.querySelectorAll('input[type="text"]')].find(i => {
              const r = i.getBoundingClientRect();
              return r.width > 80 && r.height > 8 && r.y < 200;
            });
          const text = ((popup || {}).innerText || '').slice(0, 120);
          return {
            ok: !!(search && popup),
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
          const popup = document.querySelector('[data-testid="confirm-popup"]')
            || document.querySelector('[data-testid="chat-modal"]')
            || document.querySelector('[role="dialog"]');
          const root = popup || document;
          const inp = root.querySelector('input[aria-label="Search name, number or @username"]')
            || root.querySelector('input[placeholder="Search name, number or @username"]')
            || [...root.querySelectorAll('input[type="text"], input:not([type])')].find(i => {
              const blob = ((i.getAttribute('aria-label')||'') + ' ' + (i.getAttribute('placeholder')||'')).toLowerCase();
              return /search/.test(blob) && /name|number|user/.test(blob);
            })
            || [...root.querySelectorAll('input[type="text"]')].find(i => {
              const r = i.getBoundingClientRect();
              return r.width > 80 && r.height > 8 && r.y < 200;
            });
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
        "document.querySelector('[data-testid=\"confirm-popup\"]') "
        "|| document.querySelector('[data-testid=\"chat-modal\"]') "
        "|| document.querySelector('[role=\"dialog\"]')"
    )
    typed_phone = looks_like_phone(contact) or len(digits_only(contact)) >= 8
    mid, hit = await find_row(
        w, mid, contact, True, root_sel=root, phone_query_typed=typed_phone
    )
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
            const ariaLabs = [...b.querySelectorAll('[aria-label]')].map(e => e.getAttribute('aria-label') || '');
            const icons = [...b.querySelectorAll('[data-icon], svg title')].map(e => e.getAttribute('data-icon') || e.textContent || '');
            const outgoing = !!(
              b.querySelector('[aria-label="You:"], [aria-label="You"]')
              || ariaLabs.some(a => /Sent|Delivered|Read/i.test(a || ''))
              || b.querySelector('[data-testid="msg-dblcheck"], [data-testid="msg-check"], [data-icon="msg-dblcheck"], [data-icon="msg-check"]')
              || icons.some(x => /delivered|msg-check|msg-dblcheck|wds-ic-read|wds-ic-delivered|wds-ic-sent/i.test(x))
            );
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
        await asyncio.sleep(0.85)

        mid, aff = await locate_options_btn(w, mid, needle)
        if not aff or not aff.get("found"):
            await mouse_move(w, mid, bubble["x"] + 8, bubble["y"])
            mid += 1
            await asyncio.sleep(0.85)
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
        await asyncio.sleep(1.8)

        dest_alias = None
        if dest and (dest.get("handleHit") or dest.get("handle_only") or is_handle(dest.get("title") or "")):
            dest_alias = dest.get("title")
        if not same:
            # Phone dests often open as @handle — pass resolved picker title as alias.
            mid, to_open = await ensure_chat_open(
                w, mid, to_contact, do_search, alias=dest_alias
            )
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
            "dest_handle_hit": bool(dest.get("handleHit") or dest.get("handle_only")),
            "dest_alias": dest_alias,
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
    ap.add_argument("--to-contact", required=True, help="Destination chat: title, +phone, or @handle (e.g. @sourmansa)")
    ap.add_argument("--no-search", action="store_true")
    args = ap.parse_args()
    try:
        asyncio.run(
            main(args.from_contact, args.needle, args.to_contact, not args.no_search)
        )
    except Exception as e:
        die(str(e))
