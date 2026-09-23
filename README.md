# wacdp

WhatsApp Web helpers over the Chrome DevTools Protocol (CDP).

Scripts talk to an already-logged-in Chromium (e.g. box-chrome on `WA_CDP_HTTP`, default `http://127.0.0.1:9427`).

## Helpers

| Script | Job |
|---|---|
| `send_to_contact.py` | Send text (exact title or phone / non-contact) |
| `send_media_to_contact.py` | Send photo(s) via Attach → Photos & videos (not stickers) |
| `scrape_chat_list.py` | List chats / unread / muted (list pane only) |
| `open_chat.py` | Open a chat by title |
| `delete_outgoing.py` | Delete outgoing message (prefer for everyone) |
| `delete_chat.py` | Delete entire chat / nuke conversation |
| `edit_outgoing.py` | Edit outgoing message (exact draft check) |
| `reply_to_message.py` | Reply quoting a bubble |
| `forward_message.py` | Forward a bubble to another chat |
| `react_to_message.py` | React with emoji |
| `wa_cdp.py` | Shared CDP primitives |
| `daemon.py` + `ensure.sh` / `supervise.sh` | Unmuted unread poll → webhook (denylist-only) |

Copy `config.json.example` → `config.json` locally. **Never commit webhook secrets.**

## License

MIT
