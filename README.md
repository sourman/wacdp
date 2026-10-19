# wacdp

WhatsApp Web helpers over the Chrome DevTools Protocol (CDP).

Scripts talk to an already-logged-in Chromium (URL from `WA_CDP_HTTP`, else `cdp_http` in `config.json`, else `DISPLAY` :N maps to port 9222+N).

## Helpers

| Script | Job |
|---|---|
| `send_to_contact.py` | Send text (exact title or phone / non-contact) |
| `send_media_to_contact.py` | Send photo(s) via Attach → Photos & videos (not stickers) |
| `scrape_chat_list.py` | List chats / unread / muted (list pane only) |
| `open_chat.py` | Open a chat by title or phone and leave it open (`--close` returns to the chat list) |
| `delete_outgoing.py` | Delete outgoing message (prefer for everyone) |
| `delete_chat.py` | Delete entire chat / nuke conversation |
| `archive_chat.py` | Archive / unarchive / check a chat via search (never opens it, verifies state) |
| `edit_outgoing.py` | Edit outgoing message (exact draft check) |
| `reply_to_message.py` | Reply quoting a bubble |
| `forward_message.py` | Forward a bubble to another chat |
| `react_to_message.py` | React with emoji |
| `wa_cdp.py` | Shared CDP primitives (`leave_neutral` / `closing_chat`) |
| `daemon.py` + `ensure.sh` / `supervise.sh` | Unmuted unread poll → webhook. Denylist is exact: `denylist_ids` (chat JID from scrape `chat_id`) or whole normalized titles; no substring match |

Copy `config.json.example` → `config.json` locally. **Never commit webhook secrets.**

## License

MIT

## Notes

- `scrape_chat_list.py` returns `chat_id` (JID), `sender` (group author), `from_me` per row.
- Phone lookups work for saved contacts: the chat's number is read from WhatsApp Web's React props.
- `WACDP_SYNTHETIC_CLICKS=1` (or a `.synthetic_clicks` file) adds JS click fallback for headless CDP. Off by default; don't enable on a normal Chrome.
- Tests: `python3 -m unittest discover tests`

## Neutral chat list

WhatsApp Web marks an incoming message read as soon as that chat is on screen, so it never gets an unread badge. Scripts that open a chat (`send_to_contact.py`, `send_media_to_contact.py`, `reply_to_message.py`, `react_to_message.py`, `forward_message.py`, `edit_outgoing.py`, `delete_outgoing.py`, `delete_chat.py`) call `wa_cdp.leave_neutral` when they finish, including on errors, so the UI is left with no conversation open. `archive_chat.py` already never opens the chat.

`open_chat.py` leaves the chat open. `--close` dismisses it (the old Escape-after-open behavior).

## Daemon alerts

`daemon.py` polls the chat list (default every 20s, `WA_LISTEN_INTERVAL`) and POSTs JSON. Event names are unchanged: `unread`, `outbound`, `wedge`, `daemon_down`. `config.json` is unchanged.

`unread` chat objects keep `who`, `chat_id`, `sender`, `preview`, `unread`, `muted`, and add:

| `reason` | When |
|---|---|
| `badge` | `unread` > 0, the row is not a reaction, and this who/unread/preview fingerprint is new |
| `preview` | `unread` is 0, but since the last poll the preview changed, or the clock time changed while the preview stayed the same. The row is not the user's (`from_me`, a `You:` / `You reacted` / `You deleted` preview, or an outbound status icon) and not a reaction (`is_reaction`, or an English `Reacted …` / `<name> reacted … to …` preview). |

`unread` may be `0` when `reason` is `preview`. That is the open-chat case: a reply was auto-marked read, so there is no badge, but the list row still moved.

Not alerted:

- The user's own sends, including a preview that exactly matches a live `gate_sent.json` stamp (script sends record one for about 120s). A later message whose text equals that stamp still alerts, because the clock time changed.
- Reactions.
- The same row on the next poll (state keeps a per-chat baseline and the badge fingerprint).
- Muted chats and denylisted chats.
- A chat the daemon has never baselined. The first poll records it and does not replay history. A day-label roll (`1:00 PM` → `Yesterday`) is not a new message.

Restart the daemon (or `supervise.sh`) after upgrading. The new poll loop and the baseline in `state.json` load only on process start. The first poll after a restart with an empty baseline only seeds state; badge unreads still alert immediately.
