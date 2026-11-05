# Send a message

Send opens a chat by exact title or phone, types the exact draft, presses Enter, and leaves the chat list with no conversation open. The outgoing bubble shows a delivery tick. The listener stamp records the same preview the scraper will see.

## Sub-features

- `send-open` opens `+20 10 92600692` from the chat list.
- `send-exact-draft` refuses to press Enter when the compose box is not exactly the requested text.
- `send-tick` reports `delivered_tick` true when the bubble exposes a delivered or sent tick.
- `send-neutral` closes the conversation before the script exits.
- `send-stamp` writes the preview into the scratch `gate_sent.json` and does not touch the repo file.
- `send-list` shows that preview on the chat-list row as `from_me` true.

## How to get to it (user POV)

- Open the phone chat, type in the message box, and press Enter.
- The operator runs `python3 send_to_contact.py --contact '+20 10 92600692' --text '<text>'`.

## Driving it with verify-wacdp

Preconditions:

- `verify-wacdp doctor` reports the fixture CDP.
- The verbs snapshot lists `+20 10 92600692`.
- `WACDP_SYNTHETIC_CLICKS` is `0` for the fixture drive.

- **Send.** Run `python3 .cursor/skills/verify-wacdp/bin/verify-wacdp drive send`. Exit code `0`.
- **Bubble.** The stored send object has `"ok": true`, `"delivered_tick": true`, contact `+20 10 92600692`, and text `fixture verify send`.
- **Neutral list.** After the script returns, `#main header span[title]` is absent.
- **List row.** A following scrape of that title has preview `fixture verify send`, `from_me` true, and unread `0`.
- **Stamp.** The scratch `gate_sent.json` preview for that title is `fixture verify send`. The repo `gate_sent.json` is unchanged.
- **Proof.** `.verify-evidence/<run-id>/send/result.json` contains the send object and the scraped row.
- **Live.** After `doctor --live`, run `python3 .cursor/skills/verify-wacdp/bin/verify-wacdp live send --text 'verify live ping'`. Any other `--contact` is refused before the script starts. The group is allowed only when named exactly `WhatsApp slack bridging`.

## Gotchas

- Exit code `2` means the bubble was visible and the tick was missing. Treat it as a failure.
- The fixture send does not deliver to a handset. The tick is the fixture's delivered mark, which is what the script looks for.
- A live send writes the repo `gate_sent.json` on purpose. Do not commit that file.
- Leaving the chat open makes the listener miss the next unread badge. The neutral-list check is part of the proof.
