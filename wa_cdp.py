#!/usr/bin/env python3
"""Shared WhatsApp Web CDP helpers for Gate inoculum."""
from __future__ import annotations

import asyncio
import json
import os
import urllib.request

import websockets

CDP = os.environ.get("WA_CDP_HTTP", "http://127.0.0.1:9427")


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


async def mouse(w, mid0, x, y, button="left", click_count=1):
    await call(w, mid0, "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
    await call(
        w,
        mid0 + 1,
        "Input.dispatchMouseEvent",
        {
            "type": "mousePressed",
            "x": x,
            "y": y,
            "button": button,
            "clickCount": click_count,
        },
    )
    await call(
        w,
        mid0 + 2,
        "Input.dispatchMouseEvent",
        {
            "type": "mouseReleased",
            "x": x,
            "y": y,
            "button": button,
            "clickCount": click_count,
        },
    )


async def key(w, mid, name, code, vk):
    await call(
        w,
        mid,
        "Input.dispatchKeyEvent",
        {
            "type": "keyDown",
            "key": name,
            "code": code,
            "windowsVirtualKeyCode": vk,
            "nativeVirtualKeyCode": vk,
        },
    )
    await call(
        w,
        mid + 1,
        "Input.dispatchKeyEvent",
        {
            "type": "keyUp",
            "key": name,
            "code": code,
            "windowsVirtualKeyCode": vk,
            "nativeVirtualKeyCode": vk,
        },
    )


def wa_ws_url():
    tabs = json.load(urllib.request.urlopen(f"{CDP}/json/list", timeout=5))
    pages = [
        t
        for t in tabs
        if t.get("type") == "page" and "whatsapp" in (t.get("url") or "").lower()
    ]
    if not pages:
        pages = [t for t in tabs if t.get("type") == "page"]
        pages.sort(
            key=lambda t: (0 if "whatsapp" in (t.get("url") or "").lower() else 1)
        )
    if not pages:
        raise RuntimeError("no whatsapp page on CDP")
    return pages[0]["webSocketDebuggerUrl"]



# JS snippets inlined by verb helpers (WhatsApp DOM drifts; You: aria often missing).
JS_IS_OUTGOING = r"""
  (n) => {
    if (!n) return false;
    if (n.querySelector('[aria-label="You:"], [aria-label="You"]')) return true;
    const aria = [...n.querySelectorAll('[aria-label]')].map(e => e.getAttribute('aria-label') || '');
    if (aria.some(a => /Sent|Delivered|Read/i.test(a || ''))) return true;
    if (n.querySelector('[data-testid="msg-dblcheck"], [data-testid="msg-check"], [data-icon="msg-dblcheck"], [data-icon="msg-check"]')) return true;
    const icons = [...n.querySelectorAll('[data-icon], svg title')].map(e => e.getAttribute('data-icon') || e.textContent || '');
    if (icons.some(t => /delivered|msg-check|msg-dblcheck|wds-ic-read|wds-ic-delivered|wds-ic-sent/i.test(t))) return true;
    return false;
  }
"""

JS_PRIMARY_HAS_NEEDLE = r"""
  (n, needle) => {
    if (!n || !needle) return false;
    const quote = n.querySelector('[data-testid="quoted-message"]');
    const texts = [...n.querySelectorAll('[data-testid="selectable-text"]')]
      .filter(e => !quote || !quote.contains(e))
      .map(e => e.innerText || '')
      .join(' ');
    if (texts) return texts.includes(needle);
    if (quote) {
      const qt = quote.innerText || '';
      const without = (n.innerText || '').replace(qt, '');
      return without.includes(needle);
    }
    return (n.innerText || '').includes(needle);
  }
"""


def digits_only(s: str) -> str:
    return "".join(c for c in (s or "") if c.isdigit())


def phone_match(title: str, query: str) -> bool:
    td, qd = digits_only(title), digits_only(query)
    if not td or not qd or len(td) < 8 or len(qd) < 8:
        return False
    return (
        td.endswith(qd[-9:])
        or qd.endswith(td[-9:])
        or td in qd
        or qd in td
    )


async def escape_ui(w, mid, times=3):
    for _ in range(times):
        await key(w, mid, "Escape", "Escape", 27)
        mid += 2
        await asyncio.sleep(0.15)
    return mid


async def ensure_ready(w, mid):
    ready = await evaluate(
        w,
        mid,
        """(() => ({
      pane: !!document.querySelector('#pane-side'),
      qr: !!(document.querySelector('div[data-testid="qrcode"]')
        || document.querySelector('canvas[aria-label*="Scan"]'))
        || /Scan.*?QR|Link with phone|Log into WhatsApp/i.test((document.body&&document.body.innerText)||'')
    }))()""",
    )
    mid += 1
    if not ready or not ready.get("pane") or ready.get("qr"):
        raise RuntimeError(f"whatsapp not ready: {ready}")
    return mid


async def header_titles(w, mid):
    titles = await evaluate(
        w,
        mid,
        """(() => [...document.querySelectorAll('#main header span[title], #main header [dir="auto"]')]
          .map(e => (e.getAttribute('title')||e.textContent||'').trim()).filter(Boolean))()""",
    )
    return mid + 1, titles or []


async def clear_search(w, mid):
    """Clear search filter so #pane-side shows the full chat list again."""
    for _ in range(4):
        state = await evaluate(
            w,
            mid,
            """(() => {
              const el = document.querySelector('input[aria-label="Search or start a new chat"]')
                || [...document.querySelectorAll('input')].find(i => /search/i.test(i.getAttribute('aria-label')||''));
              const val = el ? (el.value || '') : '';
              const n = document.querySelectorAll('#pane-side span[title]').length;
              if (el && val) {
                el.focus();
                el.value = '';
                el.dispatchEvent(new Event('input', {bubbles: true}));
              }
              const back = [...document.querySelectorAll('[aria-label], [data-icon], [data-testid]')]
                .map(e => {
                  const blob = ((e.getAttribute('aria-label')||'') + ' ' + (e.getAttribute('data-icon')||'') + ' ' + (e.getAttribute('data-testid')||'')).toLowerCase();
                  if (!/cancel search|back-refreshed|x-viewer|(^| )back($| )/.test(blob)) return null;
                  const r = e.getBoundingClientRect();
                  if (r.width < 8 || r.height < 8 || r.y > 120) return null;
                  return {x: r.x + r.width / 2, y: r.y + r.height / 2, blob};
                }).filter(Boolean)[0];
              return {val, n, back, needEsc: !!val || n < 5};
            })()""",
        )
        mid += 1
        if state and state.get("back"):
            await mouse(w, mid, state["back"]["x"], state["back"]["y"])
            mid += 3
            await asyncio.sleep(0.3)
        elif state and state.get("needEsc"):
            mid = await escape_ui(w, mid, 1)
            await asyncio.sleep(0.25)
        n = await evaluate(
            w, mid, "document.querySelectorAll('#pane-side span[title]').length"
        )
        mid += 1
        if (n or 0) >= 8:
            break
        if state and not state.get("needEsc") and not state.get("val"):
            break
    return mid


async def find_list_title(w, mid, contact: str, allow_substring: bool, phone_query: str | None = None):
    data = await evaluate(
        w,
        mid,
        f"""(() => {{
          const want = {json.dumps(contact)};
          const allowSub = {json.dumps(allow_substring)};
          const phoneDigits = {json.dumps(digits_only(phone_query or contact))};
          const spans = [...document.querySelectorAll('#pane-side span[title]')];
          const all = spans.map(s => {{
            const title = s.getAttribute('title') || '';
            const dig = title.replace(/\\D/g, '');
            const row = s.closest('[data-testid="cell-frame-container"]')
              || s.closest('[role="row"], [role="listitem"]') || s;
            const r = row.getBoundingClientRect();
            const phoneHit = phoneDigits && dig && dig.length >= 8 && (
              dig.endsWith(phoneDigits.slice(-9)) || phoneDigits.endsWith(dig.slice(-9))
              || dig.includes(phoneDigits) || phoneDigits.includes(dig)
            );
            return {{
              title, dig, phoneHit,
              exact: title === want,
              sub: allowSub && title.includes(want),
              x: r.x + r.width / 2, y: r.y + r.height / 2,
              y0: Math.round(r.y),
              visible: r.height > 8 && r.y >= 40 && r.y < innerHeight
            }};
          }});
          let hit = all.find(a => a.exact && a.visible)
            || all.find(a => a.phoneHit && a.visible)
            || all.find(a => a.sub && a.visible)
            || all.find(a => a.exact)
            || all.find(a => a.phoneHit)
            || all.find(a => a.sub);
          return {{ found: !!hit, hit, titles: all.map(a => a.title).filter(Boolean).slice(0, 40) }};
        }})()""",
    )
    return mid + 1, data


async def click_title(w, mid, title: str, x: float, y: float):
    loc = await evaluate(
        w,
        mid,
        f"""(() => {{
          const want = {json.dumps(title)};
          const el = [...document.querySelectorAll('span[title], div[title]')]
            .find(s => s.getAttribute('title') === want);
          if (!el) return null;
          const row = el.closest('[data-testid="cell-frame-container"]')
            || el.closest('[role="listitem"], [role="row"]') || el;
          const r = row.getBoundingClientRect();
          return {{x: r.x + r.width / 2, y: r.y + r.height / 2}};
        }})()""",
    )
    mid += 1
    if loc:
        x, y = loc["x"], loc["y"]
    await mouse(w, mid, x, y)
    mid += 3
    await asyncio.sleep(0.25)
    await evaluate(
        w,
        mid,
        f"""(() => {{
          const want = {json.dumps(title)};
          const el = [...document.querySelectorAll('span[title], div[title]')]
            .find(s => s.getAttribute('title') === want);
          if (!el) return false;
          const row = el.closest('[data-testid="cell-frame-container"]')
            || el.closest('[role="listitem"], [role="row"]') || el;
          row.dispatchEvent(new MouseEvent('mousedown', {{bubbles: true}}));
          row.dispatchEvent(new MouseEvent('mouseup', {{bubbles: true}}));
          row.click();
          return true;
        }})()""",
    )
    mid += 1
    await asyncio.sleep(1.1)
    return mid


async def find_search_input(w, mid):
    data = await evaluate(
        w,
        mid,
        """(() => {
          const el = document.querySelector('input[aria-label="Search or start a new chat"]')
            || [...document.querySelectorAll('input')].find(i =>
                 /Search or start a new chat/i.test(i.getAttribute('aria-label') || '')
                 || /search/i.test((i.getAttribute('aria-label')||'') + (i.getAttribute('placeholder')||''))
               );
          if (!el) {
            return {
              found: false,
              inputs: [...document.querySelectorAll('input')].slice(0, 12).map(i => ({
                aria: i.getAttribute('aria-label'), ph: i.getAttribute('placeholder'),
                testid: i.getAttribute('data-testid'), y: Math.round(i.getBoundingClientRect().y)
              }))
            };
          }
          const r = el.getBoundingClientRect();
          return {found: true, aria: el.getAttribute('aria-label'), x: r.x + r.width / 2, y: r.y + r.height / 2};
        })()""",
    )
    return mid + 1, data


async def open_via_search(w, mid, query: str):
    """Search for query, click matching row, wait for header, clear search."""
    mid, search = await find_search_input(w, mid)
    if not search.get("found"):
        icon = await evaluate(
            w,
            mid,
            """(() => {
              const el = [...document.querySelectorAll('[aria-label], [data-testid], [data-icon]')]
                .find(e => {
                  const blob = (e.getAttribute('aria-label')||'') + (e.getAttribute('data-testid')||'') + (e.getAttribute('data-icon')||'');
                  return /Search or start|chat-list-search|^search$/i.test(blob);
                });
              if (!el) return null;
              const r = el.getBoundingClientRect();
              return {x: r.x + r.width / 2, y: r.y + r.height / 2};
            })()""",
        )
        mid += 1
        if not icon:
            raise RuntimeError(f"no search input: {search}")
        await mouse(w, mid, icon["x"], icon["y"])
        mid += 3
        await asyncio.sleep(0.5)
        mid, search = await find_search_input(w, mid)
        if not search.get("found"):
            raise RuntimeError(f"no search input after icon: {search}")

    await mouse(w, mid, search["x"], search["y"])
    mid += 3
    await asyncio.sleep(0.2)
    await evaluate(
        w,
        mid,
        """(() => {
          const el = document.activeElement;
          if (!el) return false;
          if (el.tagName === 'INPUT') {
            el.value = '';
            el.dispatchEvent(new Event('input', {bubbles: true}));
          } else {
            document.execCommand('selectAll');
            document.execCommand('delete');
          }
          return true;
        })()""",
    )
    mid += 1
    await call(w, mid, "Input.insertText", {"text": query})
    mid += 1
    await asyncio.sleep(2.0)

    results = await evaluate(
        w,
        mid,
        f"""(() => {{
          const digits = {json.dumps(digits_only(query))};
          const want = {json.dumps(query)};
          const out = [];
          for (const el of document.querySelectorAll('span[title], div[title]')) {{
            const title = el.getAttribute('title') || '';
            if (!title || title.length > 90) continue;
            if (/^(All|Unread|Favorites|Groups|Communities)$/i.test(title)) continue;
            const dig = title.replace(/\\D/g, '');
            const row = el.closest('[data-testid="cell-frame-container"]')
              || el.closest('[role="listitem"], [role="row"]') || el;
            const r = row.getBoundingClientRect();
            if (r.height < 10 || r.y < 50 || r.y > innerHeight) continue;
            const phoneHit = dig && digits && dig.length >= 8 && (
              dig.endsWith(digits.slice(-9)) || digits.endsWith(dig.slice(-9))
              || dig.includes(digits) || digits.includes(dig)
            );
            out.push({{
              title, dig, phoneHit, exact: title === want,
              x: r.x + r.width / 2, y: r.y + r.height / 2
            }});
          }}
          const seen = new Set();
          const uniq = [];
          for (const r of out) {{
            if (seen.has(r.title)) continue;
            seen.add(r.title);
            uniq.push(r);
          }}
          return uniq.slice(0, 30);
        }})()""",
    )
    mid += 1
    hit = next((r for r in (results or []) if r.get("exact") or r.get("phoneHit")), None)
    if not hit:
        mid = await clear_search(w, mid)
        raise RuntimeError(f"search no match for {query!r}: {results}")

    mid = await click_title(w, mid, hit["title"], hit["x"], hit["y"])

    opened = False
    last_header = []
    for _ in range(15):
        mid, header = await header_titles(w, mid)
        last_header = header
        if hit["title"] in header or any(
            phone_match(h, query) or phone_match(h, hit["title"]) for h in header
        ):
            opened = True
            break
        has_compose = await evaluate(
            w,
            mid,
            "!!document.querySelector('#main footer div[contenteditable=\"true\"]')",
        )
        mid += 1
        if has_compose and header:
            opened = True
            break
        await asyncio.sleep(0.35)
    if not opened:
        mid = await clear_search(w, mid)
        raise RuntimeError(
            f"chat did not open after search click; header={last_header!r} title={hit['title']!r}"
        )

    mid = await clear_search(w, mid)
    await asyncio.sleep(0.3)
    mid, header = await header_titles(w, mid)
    if hit["title"] not in header and not any(phone_match(h, hit["title"]) for h in header):
        raise RuntimeError(f"header lost after clear_search: {header!r}")
    return mid, hit["title"]


async def open_contact(
    w, mid, contact: str, allow_substring: bool = False, via_search_if_missing: bool = True
):
    """Open chat by exact title, or search if phone / missing. Returns (mid, exact_title)."""
    mid = await ensure_ready(w, mid)
    mid = await clear_search(w, mid)

    looks_phone = len(digits_only(contact)) >= 8
    mid, loc = await find_list_title(
        w, mid, contact, allow_substring, phone_query=contact if looks_phone else None
    )
    title = None
    if loc and loc.get("found") and loc.get("hit"):
        title = loc["hit"]["title"]
        mid = await click_title(w, mid, title, loc["hit"]["x"], loc["hit"]["y"])
        mid = await clear_search(w, mid)
    elif via_search_if_missing:
        mid, title = await open_via_search(w, mid, contact)
    else:
        raise RuntimeError(f"contact not found: {loc}")

    mid, header = await header_titles(w, mid)
    header_blob = " ".join(header)
    ok = (
        title in header
        or contact in header_blob
        or any(h == title or h == contact for h in header)
    )
    if looks_phone and not ok:
        ok = any(phone_match(h, contact) or phone_match(h, title) for h in header)
    if not ok:
        raise RuntimeError(
            f"header mismatch after open: header={header!r} title={title!r} contact={contact!r}"
        )
    return mid, title


def find_clickable(items, *labels):
    norms = [
        (it, " ".join((it.get("t") or it.get("aria") or "").split())) for it in items
    ]
    for want in labels:
        for it, t in norms:
            if t == want:
                return it
    for want in labels:
        for it, t in norms:
            if t.startswith(want):
                return it
    for want in labels:
        for it, t in norms:
            if want in t:
                return it
    return None
