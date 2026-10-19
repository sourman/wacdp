#!/usr/bin/env python3
"""Open a WhatsApp Web chat via box-chrome CDP (exact title or phone search).

Usage:
  WA_CDP_HTTP=http://127.0.0.1:9225 python3 open_chat.py --contact '+201092600692'
  WA_CDP_HTTP=http://127.0.0.1:9225 python3 open_chat.py --contact 'Exact Title'
  WA_CDP_HTTP=http://127.0.0.1:9225 python3 open_chat.py --contact 'Exact Title' --close

Leaves the chat open (search is cleared by open_contact). Pass --close to
return to the chat list with no conversation open. Prints JSON with the
exact title and header.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from wa_cdp import call, header_titles, leave_neutral, open_contact, wa_ws_url
import websockets


def die(msg, code=1):
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
    sys.exit(code)


async def main(contact: str, allow_substring: bool, close_after: bool):
    try:
        ws_url = wa_ws_url()
    except Exception as e:
        die(str(e))
    async with websockets.connect(ws_url, max_size=None) as w:
        await call(w, 1, "Runtime.enable")
        mid = 10
        try:
            mid, title = await open_contact(w, mid, contact, allow_substring=allow_substring)
            mid, header = await header_titles(w, mid)
        except Exception as e:
            # Drop a half-open search or chat so the daemon can scrape.
            try:
                await leave_neutral(w, mid)
            except Exception:
                pass
            die(str(e))
        if close_after:
            try:
                await leave_neutral(w, mid)
            except Exception as e:
                die(f"opened but failed to close: {e}")
        print(json.dumps({"ok": True, "contact_arg": contact, "title": title, "header": header}, ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--contact", required=True, help="exact title or phone (+E.164 / local)")
    ap.add_argument("--allow-substring", action="store_true")
    ap.add_argument(
        "--close",
        action="store_true",
        help="close the chat before exiting (default: leave it open)",
    )
    args = ap.parse_args()
    asyncio.run(main(args.contact, args.allow_substring, args.close))
