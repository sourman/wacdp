#!/usr/bin/env python3
"""Scrape WhatsApp Web chat list via CDP. List-only — never opens chats."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request

import websockets

CDP_HTTP = os.environ.get("WA_CDP_HTTP", "http://127.0.0.1:9427")

SCRAPE_JS = r"""
(() => {
  const pane = document.querySelector('#pane-side')
    || document.querySelector('[aria-label="Chat list"]')
    || document.querySelector('[data-testid="chat-list"]');
  if (!pane) {
    const text = (document.body && document.body.innerText || '').slice(0, 200);
    const qr = /Scan.*?QR|Link with phone|Log into WhatsApp/i.test(text);
    return { error: qr ? 'qr_login' : 'no_chat_list', sample: text };
  }
  const items = pane.querySelectorAll('[role="row"], [role="listitem"]');
  const out = [];
  const seen = {};
  for (const el of items) {
    if (out.length >= 80) break;
    const titled = el.querySelector('span[title]');
    const who = titled ? (titled.getAttribute('title') || titled.textContent || '').trim() : '';
    if (!who || seen[who]) continue;
    seen[who] = 1;
    let unread = 0;
    const badge = el.querySelector('[aria-label*="unread"], [aria-label*="Unread"]');
    if (badge) {
      const m = /(\d+)/.exec(badge.getAttribute('aria-label') || '');
      unread = m ? parseInt(m[1], 10) : 1;
    }
    const muted = !!(
      el.querySelector('[aria-label="Muted chat"], [aria-label*="muted" i], [data-icon="muted"], [data-icon="pinned-muted"]')
      || /muted/i.test(el.getAttribute('aria-label') || '')
    );
    const lines = (el.innerText || '').split('\n').map(x => x.trim()).filter(Boolean);
    const body = lines.filter(x =>
      x !== who && !/^\d+$/.test(x) && !/^\d+ unread/i.test(x) && !/^muted$/i.test(x)
    );
    const preview = (body.length ? body[body.length - 1] : '').slice(0, 280);
    const time = lines.length > 1 ? lines[1] : '';
    out.push({ who, preview, unread, muted, time });
  }
  return {
    ok: true,
    title: document.title,
    chats: out,
  };
})()
"""


# Transport budget. A busy box can stall Chrome's DevTools endpoint for a few
# seconds; one stall used to fail the whole scrape (and any routine using it).
# Retry transport failures inside one overall budget that stays below the
# daemon's 45 s subprocess timeout. Page-level results (qr_login, no_chat_list,
# cdp errors) are returned at once and never retried.
SCRAPE_BUDGET_S = float(os.environ.get("WA_SCRAPE_BUDGET", "38"))
SCRAPE_ATTEMPTS = max(1, int(os.environ.get("WA_SCRAPE_ATTEMPTS", "3")))
ATTEMPT_TIMEOUT_S = 15.0
LIST_TIMEOUT_S = 8.0
OPEN_TIMEOUT_S = 10.0


def _list_tabs():
    return json.load(urllib.request.urlopen(f"{CDP_HTTP}/json/list", timeout=LIST_TIMEOUT_S))


async def eval_on_wa(expression: str):
    tabs = await asyncio.to_thread(_list_tabs)
    pages = [t for t in tabs if t.get("type") == "page"]
    pages.sort(key=lambda t: (0 if "whatsapp" in (t.get("url") or "").lower() else 1))
    if not pages:
        return {"error": "no_pages"}
    ws_url = pages[0]["webSocketDebuggerUrl"]
    async with websockets.connect(ws_url, max_size=None, open_timeout=OPEN_TIMEOUT_S) as w:
        await w.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {"expression": expression, "returnByValue": True},
                }
            )
        )
        while True:
            raw = json.loads(await w.recv())
            if raw.get("id") == 1:
                if "error" in raw:
                    return {"error": "cdp", "detail": raw["error"]}
                res = raw.get("result", {}).get("result", {})
                if res.get("type") == "object" and "value" in res:
                    return res["value"]
                if "value" in res:
                    return res["value"]
                return {"error": "bad_eval", "detail": res}


def _describe(e: BaseException) -> str:
    msg = str(e)
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__


async def eval_with_retry(expression: str):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + SCRAPE_BUDGET_S
    errors = []
    for attempt in range(1, SCRAPE_ATTEMPTS + 1):
        remaining = deadline - loop.time()
        if remaining < 2:
            break
        try:
            return await asyncio.wait_for(eval_on_wa(expression), timeout=min(ATTEMPT_TIMEOUT_S, remaining))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # transport: timeouts, refused, closed socket, HTTP hiccup
            errors.append(f"try {attempt}: {_describe(e)}")
            if attempt < SCRAPE_ATTEMPTS:
                pause = min(1.5 * attempt, max(0.0, deadline - loop.time() - 2))
                if pause > 0:
                    await asyncio.sleep(pause)
    raise RuntimeError("; ".join(errors) or "scrape budget exhausted")


def _scrub(o):
    """Replace lone UTF-16 surrogates (e.g. emoji cut mid-pair in a truncated preview)."""
    if isinstance(o, str):
        return o.encode("utf-8", "surrogatepass").decode("utf-8", "replace") if any(0xD800 <= ord(c) <= 0xDFFF for c in o) else o
    if isinstance(o, list):
        return [_scrub(x) for x in o]
    if isinstance(o, dict):
        return {_scrub(k): _scrub(v) for k, v in o.items()}
    return o


def main():
    try:
        data = asyncio.run(eval_with_retry(SCRAPE_JS))
    except Exception as e:
        print(json.dumps({"error": "exception", "detail": str(e) or type(e).__name__}))
        sys.exit(2)
    data = _scrub(data)
    print(json.dumps(data, ensure_ascii=False))
    if isinstance(data, dict) and data.get("error"):
        sys.exit(1)


if __name__ == "__main__":
    main()
