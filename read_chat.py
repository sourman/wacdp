#!/usr/bin/env python3
"""Read a WhatsApp Web chat with FULL text (expands every "Read more" fold). READ ONLY.

Usage:
  WA_CDP_HTTP=http://127.0.0.1:9225 python3 read_chat.py --contact 'Exact Title' [--last 40] [--since 10/25/2026] [--no-open]

Opens the chat via open_contact (marks it read), scrolls up until --last messages
(or the --since date, M/D/YYYY) are loaded, clicks every "Read more" until none remain,
then prints JSON: [{id, direction, sender, pre, time, text, media}].
Never types, sends, reacts or deletes.
"""
from __future__ import annotations
import argparse, asyncio, json, re, sys
from datetime import datetime
import websockets
from wa_cdp import call, evaluate, open_contact, header_titles, wa_ws_url
try:
    from wa_cdp import leave_neutral
except ImportError:  # older deploys
    leave_neutral = None

ROWS_JS = r"""(() => {
  const m = document.querySelector('#main'); if (!m) return null;
  const rows = [...m.querySelectorAll('[data-id]')].filter(r => r.querySelector('[data-pre-plain-text], img, audio, [data-icon*="audio"], [data-icon*="ptt"], [data-icon*="document"], [data-icon*="PDF"]') && !r.parentElement.closest('[data-id]'));
  return rows.map(r => {
    const id = r.getAttribute('data-id') || '';
    const c = r.querySelector('[data-pre-plain-text]');
    const pre = c ? c.getAttribute('data-pre-plain-text') : '';
    const sel = c ? (c.querySelector('span.selectable-text') || c) : null;
    const text = sel ? sel.innerText : '';
    const out = !!(r.querySelector('.message-out') || r.closest('.message-out')) || id.startsWith('true_')
      || !!r.querySelector('[data-icon="tail-out"], [data-icon^="msg-check"], [data-icon^="msg-dblcheck"], [data-icon^="msg-time"]');
    const media = [];
    if (r.querySelector('img[src^="blob:"]')) media.push('image');
    if (r.querySelector('audio, [data-icon*="ptt"], [data-icon*="audio"]')) media.push('voice/audio');
    if (r.querySelector('[data-icon*="document"], [data-icon*="PDF"]')) media.push('document: ' + (r.innerText||'').replace(/\s+/g,' ').trim().slice(0,200));
    if (r.querySelector('video, [data-icon*="video"]')) media.push('video');
    return {id, direction: out ? 'out' : 'in', pre, text, media};
  });
})()"""

READMORE_JS = r"""(() => {
  const m = document.querySelector('#main'); if (!m) return 0;
  const b = [...m.querySelectorAll('[role="button"], button, span')].filter(e => /^read more$/i.test((e.innerText||'').trim()));
  b.forEach(e => { try { e.click(); } catch (_) {} });
  return b.length;
})()"""

SCROLL_JS = r"""((dir) => {
  const m = document.querySelector('#main');
  const s = [...m.querySelectorAll('div')].find(d => d.scrollHeight > d.clientHeight + 50 && /auto|scroll/.test(getComputedStyle(d).overflowY));
  if (!s) return -1; const before = s.scrollTop;
  if (dir === 'bottom') s.scrollTop = s.scrollHeight; else s.scrollTop = Math.max(0, before - s.clientHeight * 0.7);
  return before;
})"""

PRE_RE = re.compile(r"^\[(?P<t>[^,\]]+),\s*(?P<d>[^\]]+)\]\s*(?P<s>.*?):\s*$")

def parse_pre(pre):
    m = PRE_RE.match(pre or "")
    if not m: return None, None, None, None
    try: dt = datetime.strptime(f"{m['d']} {m['t']}", "%m/%d/%Y %I:%M %p")
    except ValueError: dt = None
    return m['t'], m['d'], m['s'], dt

async def main(a):
    async with websockets.connect(wa_ws_url(), max_size=None) as w:
        await call(w, 1, "Runtime.enable"); mid = 10
        if not a.no_open:
            mid, title = await open_contact(w, mid, a.contact)  # no Escape after: it closes #main
        mid, header = await header_titles(w, mid)
        if not header:
            print(json.dumps({"ok": False, "error": "#main not open"})); sys.exit(1)
        since = datetime.strptime(a.since, "%m/%d/%Y") if a.since else None
        for _ in range(3):
            await evaluate(w, mid, SCROLL_JS + "('bottom')"); mid += 1
            await asyncio.sleep(1.5)
        seen = {}
        def merge(rows):
            last_dt = None
            for i, r in enumerate(rows):
                p = parse_pre(r["pre"])
                if p[3]: last_dt = p[3]
                r["_k"] = (p[3] or last_dt or datetime.min, i)
                prev = seen.get(r["id"])
                if not prev or len(r["text"]) >= len(prev["text"]):
                    if prev and not r["pre"]: r["_k"] = prev["_k"]
                    seen[r["id"]] = r
        for _ in range(a.max_scrolls):
            merge(await evaluate(w, mid, ROWS_JS) or []); mid += 1
            for _ in range(30):
                n = await evaluate(w, mid, READMORE_JS); mid += 1
                if not n: break
                await asyncio.sleep(0.6)
                merge(await evaluate(w, mid, ROWS_JS) or []); mid += 1
            rows = await evaluate(w, mid, ROWS_JS) or []; mid += 1
            merge(rows)
            dts = [parse_pre(r["pre"]) for r in rows if r["pre"]]
            oldest = min((p[3] for p in dts if len(p) > 3 and p[3]), default=None)
            if len(seen) >= a.last and (not since or (oldest and oldest < since)):
                break
            prev = await evaluate(w, mid, SCROLL_JS + "('up')"); mid += 1
            await asyncio.sleep(1.5)
            if prev == 0:
                await asyncio.sleep(2)
        rows = sorted(seen.values(), key=lambda r: r["_k"])
        for r in rows: r.pop("_k", None)
        own = {parse_pre(r["pre"])[2] for r in rows if r["direction"] == "out" and r["pre"]} - {header[0]}
        for r in rows:
            snd = parse_pre(r["pre"])[2]
            if snd: r["direction"] = "out" if snd in own else "in"
        out = []
        for r in rows:
            p = parse_pre(r["pre"])
            t, d, s = (p[0], p[1], p[2]) if p and p[0] else (None, None, None)
            if since and p and len(p) > 3 and p[3] and p[3] < since: continue
            out.append({**r, "time": t, "date": d, "sender": s or ("me" if r["direction"] == "out" else header[0])})
        if not since: out = out[-a.last:]
        if a.close and leave_neutral:
            mid = await leave_neutral(w, mid)
        print(json.dumps({"ok": True, "header": header, "count": len(out), "messages": out}, ensure_ascii=False, indent=1))

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--contact", required=True)
    ap.add_argument("--last", type=int, default=40)
    ap.add_argument("--since", help="M/D/YYYY; include messages on/after this date")
    ap.add_argument("--max-scrolls", type=int, default=15)
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--close", action="store_true", help="close the chat after reading (leave_neutral)")
    asyncio.run(main(ap.parse_args()))
