# wacdp

WhatsApp Web helpers over the Chrome DevTools Protocol (CDP).

Scripts talk to an already-logged-in Chromium (e.g. inoculum on `WA_CDP_HTTP`, default `http://127.0.0.1:9427`).

## Helpers

| Script | Job |
|---|---|
| `send_to_contact.py` | Send text (exact title or phone / non-contact) |
| `scrape_chat_list.py` | List chats / unread / muted (list pane only) |
| `open_chat.py` | Open a chat by title |
| `delete_outgoing.py` | Delete outgoing message (prefer for everyone) |
| `wa_cdp.py` | Shared CDP primitives |
| `daemon.py` + `ensure.sh` / `supervise.sh` | Unread poll → webhook |

Copy `config.json.example` → `config.json` locally. **Never commit webhook secrets.**

## License

MIT
