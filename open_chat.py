#!/usr/bin/env python3
"""Open a WhatsApp Web chat via Gate box-chrome CDP (exact title or phone search).

Usage:
  WA_CDP_HTTP=http://127.0.0.1:9427 python3 open_chat.py --contact '+201092600692'
  WA_CDP_HTTP=http://127.0.0.1:9427 python3 open_chat.py --contact 'Exact Title'

Leaves #pane-side healthy (Escape after search). Prints JSON with exact title + header.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from wa_cdp import call, escape_ui, open_contact, wa_ws_url
import websockets


def die(msg, code=1):
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
    sys.exit(code)


async def main(contact: str, allow_substring: bool):
    try:
        ws_url = wa_ws_url()
    except Exception as e:
        die(str(e))
    async with websockets.connect(ws_url, max_size=None) as w:
        await call(w, 1, "Runtime.enable")
        mid = 10
        try:
            mid, title = await open_contact(w, mid, contact, allow_substring=allow_substring)
            mid = await escape_ui(w, mid, 1)
            from wa_cdp import header_titles
            mid, header = await header_titles(w, mid)
        except Exception as e:
            # best-effort Escape so daemon can scrape
            try:
                await escape_ui(w, mid, 3)
            except Exception:
                pass
            die(str(e))
        print(json.dumps({"ok": True, "contact_arg": contact, "title": title, "header": header}, ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--contact", required=True, help="exact title or phone (+E.164 / local)")
    ap.add_argument("--allow-substring", action="store_true")
    args = ap.parse_args()
    asyncio.run(main(args.contact, args.allow_substring))
