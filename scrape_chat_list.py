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


async def eval_on_wa(expression: str):
    tabs = json.load(urllib.request.urlopen(f"{CDP_HTTP}/json/list", timeout=5))
    pages = [t for t in tabs if t.get("type") == "page"]
    pages.sort(key=lambda t: (0 if "whatsapp" in (t.get("url") or "").lower() else 1))
    if not pages:
        return {"error": "no_pages"}
    ws_url = pages[0]["webSocketDebuggerUrl"]
    async with websockets.connect(ws_url, max_size=None) as w:
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
            raw = json.loads(await asyncio.wait_for(w.recv(), timeout=30))
            if raw.get("id") == 1:
                if "error" in raw:
                    return {"error": "cdp", "detail": raw["error"]}
                res = raw.get("result", {}).get("result", {})
                if res.get("type") == "object" and "value" in res:
                    return res["value"]
                if "value" in res:
                    return res["value"]
                return {"error": "bad_eval", "detail": res}


def main():
    try:
        data = asyncio.run(eval_on_wa(SCRAPE_JS))
    except Exception as e:
        print(json.dumps({"error": "exception", "detail": str(e)}))
        sys.exit(2)
    print(json.dumps(data, ensure_ascii=False))
    if isinstance(data, dict) and data.get("error"):
        sys.exit(1)


if __name__ == "__main__":
    main()
