#!/usr/bin/env python3
"""Edit an outgoing WhatsApp Web message via Gate inoculum CDP.

Usage:
  WA_CDP_HTTP=http://127.0.0.1:9427 python3 edit_outgoing.py \\
    --contact 'Title' --needle 'unique old substring' --text 'new exact text'

Hard-clears the edit field, requires EXACT draft match before save, then
verifies the bubble shows the new text (and preferably an Edited label).
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
    return td.endswith(qd[-9:]) or qd.endswith(td[-9:]) or td in qd or qd in td


def norm(s: str) -> str:
    return " ".join((s or "").replace("\n", " ").split())


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


async def hard_clear_editable(w, mid, selector_js: str):
    """Clear a contenteditable until trim-empty. Returns (mid, cleared_ok, leftover_len)."""
    prior = await evaluate(
        w,
        mid,
        f"""(() => {{
          const c = {selector_js};
          if (!c) return {{ok:false, prior:''}};
          return {{ok:true, prior:(c.innerText||'').trim()}};
        }})()""",
    )
    mid += 1
    if not prior or not prior.get("ok"):
        return mid, False, 0, "no editable field"
    leftover_len = len((prior.get("prior") or "").strip())
    st = None
    for _attempt in range(6):
        st = await evaluate(
            w,
            mid,
            f"""(() => {{
              const c = {selector_js};
              if (!c) return {{ok:false, empty:false, text:''}};
              c.focus();
              try {{
                const sel = window.getSelection();
                const range = document.createRange();
                range.selectNodeContents(c);
                sel.removeAllRanges();
                sel.addRange(range);
              }} catch (e) {{}}
              document.execCommand('selectAll');
              document.execCommand('delete');
              const t = (c.innerText || '').trim();
              if (t) {{
                c.textContent = '';
                c.dispatchEvent(new InputEvent('input', {{bubbles:true}}));
              }}
              const after = (c.innerText || '').trim();
              return {{ok:true, empty:!after, text: after.slice(0, 80)}};
            }})()""",
        )
        mid += 1
        if st and st.get("ok") and st.get("empty"):
            return mid, True, leftover_len, None
        await call(
            w,
            mid,
            "Input.dispatchKeyEvent",
            {
                "type": "keyDown",
                "key": "a",
                "code": "KeyA",
                "windowsVirtualKeyCode": 65,
                "modifiers": 2,
            },
        )
        mid += 1
        await call(
            w,
            mid,
            "Input.dispatchKeyEvent",
            {
                "type": "keyUp",
                "key": "a",
                "code": "KeyA",
                "windowsVirtualKeyCode": 65,
                "modifiers": 2,
            },
        )
        mid += 1
        await call(
            w,
            mid,
            "Input.dispatchKeyEvent",
            {
                "type": "keyDown",
                "key": "Backspace",
                "code": "Backspace",
                "windowsVirtualKeyCode": 8,
            },
        )
        mid += 1
        await call(
            w,
            mid,
            "Input.dispatchKeyEvent",
            {
                "type": "keyUp",
                "key": "Backspace",
                "code": "Backspace",
                "windowsVirtualKeyCode": 8,
            },
        )
        mid += 1
        await asyncio.sleep(0.08)
    return mid, False, leftover_len, f"could not empty edit field: {st}"


async def main(contact: str, needle: str, text: str, do_search: bool):
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

        # Header verify
        hdr2 = await evaluate(
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
        mid += 1
        if not hdr2 or not hdr2.get("ok"):
            die(f"refusing edit; header does not match contact={contact!r} state={hdr2}")

        # find outgoing bubble with needle
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
                return {{found:true, x, y, raw: t.slice(0,160)}};
              }}
              return {{found:false}};
            }})()""",
        )
        mid += 1
        if not bubble or not bubble.get("found"):
            die(f"outgoing bubble not found: {bubble}")

        # hover → options caret
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

        edit_item = await evaluate(
            w,
            mid,
            """(() => {
              const els = [...document.querySelectorAll('[role="menuitem"]')];
              const el = els.find(e => /^Edit$/i.test((e.innerText||'').trim()))
                || els.find(e => /\\bEdit\\b/i.test((e.innerText||'').trim()));
              if (!el) return {found:false, menu: els.map(e=>(e.innerText||'').trim())};
              const r = el.getBoundingClientRect();
              return {found:true, x:r.x+r.width/2, y:r.y+r.height/2, label:(el.innerText||'').trim()};
            })()""",
        )
        mid += 1
        if not edit_item or not edit_item.get("found"):
            die(f"Edit menu missing: {edit_item}")
        await mouse(w, mid, edit_item["x"], edit_item["y"])
        mid += 3
        await asyncio.sleep(0.55)

        # Edit field: prefer in-message / dialog contenteditable; fall back to footer compose
        edit_sel = (
            '('
            'document.querySelector(\'[role="dialog"] div[contenteditable="true"]\')'
            ' || document.querySelector(\'#main [data-testid="msg-container"] div[contenteditable="true"]\')'
            ' || document.querySelector(\'#main footer div[contenteditable="true"][data-tab="10"]\')'
            ' || document.querySelector(\'#main footer div[contenteditable="true"]\')'
            ')'
        )

        # Wait until an editable is present and preferably contains the needle
        edit_ready = None
        for _ in range(15):
            edit_ready = await evaluate(
                w,
                mid,
                f"""(() => {{
                  const c = {edit_sel};
                  if (!c) return {{ok:false}};
                  const t = (c.innerText||'').trim();
                  return {{ok:true, text: t.slice(0,200), hasNeedle: t.includes({json.dumps(needle)})}};
                }})()""",
            )
            mid += 1
            if edit_ready and edit_ready.get("ok"):
                break
            await asyncio.sleep(0.25)
        if not edit_ready or not edit_ready.get("ok"):
            die(f"edit field never appeared: {edit_ready}")

        if leftover := (edit_ready.get("text") or "").strip():
            print(
                json.dumps(
                    {"info": "cleared_leftover_edit", "chars": len(leftover)},
                    ensure_ascii=False,
                ),
                flush=True,
            )

        mid, cleared, leftover_len, err = await hard_clear_editable(w, mid, edit_sel)
        if not cleared:
            die(err or "could not empty edit field")

        await call(w, mid, "Input.insertText", {"text": text})
        mid += 1
        await asyncio.sleep(0.25)

        draft = await evaluate(
            w,
            mid,
            f"""(() => {{
              const c = {edit_sel};
              return (c && (c.innerText||'')).trim();
            }})()""",
        )
        mid += 1
        if norm(draft) != norm(text):
            die(f"edit draft not exact match before save: draft={draft!r} want={text!r}")

        # Save: prefer Save / check button, else Enter
        save_btn = await evaluate(
            w,
            mid,
            """(() => {
              const cands = [
                ...document.querySelectorAll('[aria-label]'),
                ...document.querySelectorAll('button, [role="button"]'),
              ];
              const el = cands.find(e => {
                const a = (e.getAttribute('aria-label')||'').trim();
                const t = (e.innerText||'').trim();
                return /^(Save|Send)$/i.test(a) || /^(Save|Send)$/i.test(t)
                  || /save edited message|confirm edit/i.test(a);
              });
              if (!el) return {found:false};
              const r = el.getBoundingClientRect();
              if (r.width < 4 || r.height < 4) return {found:false};
              return {found:true, x:r.x+r.width/2, y:r.y+r.height/2,
                label: (el.getAttribute('aria-label')||el.innerText||'').trim().slice(0,40)};
            })()""",
        )
        mid += 1
        if save_btn and save_btn.get("found"):
            await mouse(w, mid, save_btn["x"], save_btn["y"])
            mid += 3
        else:
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
        want = norm(text)
        while time.time() < deadline:
            await asyncio.sleep(0.4)
            last = await evaluate(
                w,
                mid,
                f"""(() => {{
                  const wantNeedle = {json.dumps(text.strip())};
                  const nodes = [...document.querySelectorAll('#main [data-testid="msg-container"]')];
                  let match = null;
                  for (const n of nodes.slice(-15).reverse()) {{
                    const raw = (n.innerText||'');
                    const t = raw.replace(/\\s+/g,' ').trim();
                    if (!t.includes(wantNeedle.slice(0, Math.min(40, wantNeedle.length)))) continue;
                    const _aria = [...n.querySelectorAll('[aria-label]')].map(e => e.getAttribute('aria-label') || '');
                    const _icons = [...n.querySelectorAll('[data-icon], svg title')].map(e => e.getAttribute('data-icon') || e.textContent || '');
                    const _out = !!(n.querySelector('[aria-label="You:"], [aria-label="You"]')
                      || _aria.some(a => /Sent|Delivered|Read/i.test(a || ''))
                      || n.querySelector('[data-testid="msg-dblcheck"], [data-testid="msg-check"], [data-icon="msg-dblcheck"], [data-icon="msg-check"]')
                      || _icons.some(x => /delivered|msg-check|msg-dblcheck|wds-ic-read|wds-ic-delivered|wds-ic-sent/i.test(x)));
                    if (!_out) continue;
                    const edited = /\\bEdited\\b/i.test(raw)
                      || !!n.querySelector('[data-testid="edited-label"], [aria-label*="Edited"]')
                      || _aria.some(a => /Edited/i.test(a||''));
                    const quote = n.querySelector('[data-testid="quoted-message"]');
                    const sel = [...n.querySelectorAll('[data-testid="selectable-text"]')]
                      .filter(e => !quote || !quote.contains(e))
                      .map(e => (e.innerText||'').trim())
                      .join(' ');
                    const primary = (sel || '').replace(/\\s+/g,' ').trim();
                    match = {{
                      text: t.slice(0, 240),
                      primary: primary.slice(0, 240),
                      edited,
                    }};
                    break;
                  }}
                  const editing = !!document.querySelector('#main [data-testid="msg-container"] div[contenteditable="true"]')
                    || !!document.querySelector('[role="dialog"] div[contenteditable="true"]');
                  return {{match, editing}};
                }})()""",
            )
            mid += 1
            if last and last.get("match") and not last.get("editing"):
                primary = norm(last["match"].get("primary") or "")
                if primary == want:
                    break
                if not primary:
                    bn = norm(last["match"].get("text") or "")
                    if want in bn:
                        break

        if not last or not last.get("match"):
            die(f"edit not confirmed: {last}")
        primary = norm(last["match"].get("primary") or "")
        if primary and primary != want:
            die(
                f"edited bubble text not exact: primary={last['match'].get('primary')!r} want={text!r} last={last}"
            )
        if not primary:
            bn = norm(last["match"].get("text") or "")
            if want not in bn:
                die(f"edited bubble text not exact: bubble={last['match'].get('text')!r} want={text!r}")

        out = {
            "ok": True,
            "contact": contact,
            "needle": needle,
            "text": text,
            "edited_label": bool(last["match"].get("edited")),
            "verify": last["match"],
            "leftover_cleared_chars": leftover_len,
            "warning": None
            if last["match"].get("edited")
            else "bubble text updated but Edited label not detected",
        }
        print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--contact", required=True)
    ap.add_argument("--needle", required=True, help="unique substring of the outgoing bubble to edit")
    ap.add_argument("--text", required=True, help="new exact message text")
    ap.add_argument("--no-search", action="store_true")
    args = ap.parse_args()
    try:
        asyncio.run(main(args.contact, args.needle, args.text, not args.no_search))
    except Exception as e:
        die(str(e))
