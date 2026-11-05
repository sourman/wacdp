# Reply, react, and forward

Reply quotes an existing bubble, react adds an emoji to one, and forward copies one into another chat. Each helper opens the source chat, performs the gesture, confirms the result on the bubble, and leaves no conversation open.

## Sub-features

- `reply-quote` replies to `fixture inbound needle` with a bubble that quotes that needle and shows a delivery tick.
- `react-emoji` adds `👍` to `fixture inbound needle` and the chat-list preview becomes a reaction row.
- `forward-group` forwards `fixture outgoing needle` from the phone chat to `WhatsApp slack bridging` with a Forwarded label and a delivery tick.
- `actions-neutral` leaves the conversation header closed after each helper.

## How to get to it (user POV)

- Hover a message, choose Reply, type, and press Enter.
- Hover a message, choose React, and pick an emoji.
- Hover a message, choose Forward, pick the destination chat, and press Send.
- The operator runs `reply_to_message.py`, `react_to_message.py`, or `forward_message.py`.

## Driving it with verify-wacdp

Preconditions:

- `verify-wacdp doctor` reports the fixture CDP.
- The verbs snapshot's phone chat contains incoming text `fixture inbound needle` and outgoing text `fixture outgoing needle`.
- The group `WhatsApp slack bridging` is on the list.

- **Reply.** Run `python3 .cursor/skills/verify-wacdp/bin/verify-wacdp drive reply`. The reply object has `has_quote` true, `delivered_tick` true, and text `fixture verify reply`. The following scrape of `+20 10 92600692` has that preview and `from_me` true. The conversation header is gone.
- **React.** Run `python3 .cursor/skills/verify-wacdp/bin/verify-wacdp drive react`. The react object's `verify.ok` is true for emoji `👍`. The following scrape has `is_reaction` true and the preview contains `👍`. The conversation header is gone.
- **Forward.** Run `python3 .cursor/skills/verify-wacdp/bin/verify-wacdp drive forward`. The forward object has `forwarded_indicator` true and `delivered_tick` true. The destination preview on `WhatsApp slack bridging` is `fixture outgoing needle` and `from_me` is true. The conversation header is gone.
- **Proof.** `.verify-evidence/<run-id>/reply/result.json`, `react/result.json`, and `forward/result.json` each store the verb JSON and the scraped row.
- **Live.** After `doctor --live`, reply and react take `--needle` from a message that is already in the phone chat. Forward uses `--to-contact 'WhatsApp slack bridging'` or the phone. Any other destination is refused before the script starts.

## Gotchas

- Reply and react in the fixture both target `fixture inbound needle`. Forward targets the outgoing needle. Swapping those needles fails the bubble search.
- Each drive reloads the verbs snapshot first. A reply left over from a manual script run is not the fixture's starting state.
- The forward recipe is cross-chat. Same-chat forward is a different count check and is not this proof.
- Live react and reply mark the real chat. Keep the needle inside the safe phone chat or the safe group.
