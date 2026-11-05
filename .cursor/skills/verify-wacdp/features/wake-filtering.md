# Wake filtering

Wake filtering decides which unmuted chat-list changes become an `unread` webhook. Allowlisted chats wake as `kind=client`. Non-contacts, including unsaved business names, wake as `kind=unknown`. Saved contacts that are not allowlisted, denylisted chats, muted chats, and groups that are not allowlisted stay quiet.

## Sub-features

- `wake-client-badge` posts `kind=client` and `reason=badge` for an allowlisted group with an unread count.
- `wake-unknown-business` posts `kind=unknown` for an unsaved business name (`is_address_book_contact` 0, `is_business` true).
- `wake-unknown-phone` posts `kind=unknown` for a phone-titled non-contact.
- `wake-unknown-archived` posts `kind=unknown` and `archived` true for archived non-contacts, including a pushname-only contact.
- `wake-quiet` baselines saved non-allowlisted contacts, the denylisted chat, muted allowlisted chats, off-allowlist groups, and a named row whose address-book flag is missing, and does not post them.
- `wake-preview` posts `kind=client` and `reason=preview` when an allowlisted 1:1 row changes while unread stays 0.
- `wake-dedup` does not post the same badge list on the next poll.

## How to get to it (user POV)

- A new unmuted message appears on the WhatsApp chat list for a client, an unknown number, or an unsaved business.
- A reply arrives in a chat that is already open, so WhatsApp shows no unread badge and the list preview changes.
- The listener is the poll in `daemon.py`, started on the box by `supervise.sh`.

## Driving it with verify-wacdp

Preconditions:

- `verify-wacdp doctor` reports the fixture CDP and `"live_port_9225": "untouched"`.
- The fixture config's `client_allowlist` contains `201092600692@c.us` and `120363000000009225@g.us`.
- `999000000000001@lid` is the only denylist id.
- No Gate webhook is configured. The drive posts to the fixture sink.

- **Badge batch.** Load the baseline snapshot and poll once. Run `python3 .cursor/skills/verify-wacdp/bin/verify-wacdp drive wake`. The first stored post has `"event": "unread"`. `WhatsApp slack bridging` is `kind=client`, `reason=badge`, sender `Sam`, preview `bridge ping`. `Mountain View Resale` and `+1 202 555 0147` are `kind=unknown`. `Archived Realty` and `Pushname Only` are `kind=unknown` with `archived` true. `Empty Yesterday` has preview `""` and `Swapped Clock` has preview `See you at 5`.
- **Quiet rows.** In that same post, these titles are absent: `Saved Friend`, `Denied Spam`, `Random Group`, `Named Quiet`, `Muted Client`, `Reaction Quiet`, `Inferred Saved`, `Day Word`, `Own Baseline`, `Tick Quiet`, `Odd Photo`, `Odd Emoji`, and `+20 10 92600692`. Each of those names is present in the listener baseline.
- **Same list again.** The drive polls the same snapshot. The webhook log does not grow.
- **Open-chat reply.** The drive loads the inbound snapshot and polls. The second post has one chat: `+20 10 92600692`, `kind=client`, `reason=preview`, preview `On my way`, unread `0`. The baseline preview for that title becomes `On my way`.
- **Proof.** `.verify-evidence/<run-id>/wake/result.json` contains both posts and `"baseline_preview": "On my way"`.

## Gotchas

- The first poll of a brand-new baseline alerts current badges. It does not replay unread `0` history. A missing badge on `+20 10 92600692` in the first post is the seeded row, not a failure.
- `drive wake` calls `daemon.tick`. It does not start `supervise.sh` or take `daemon.lock` in the repo.
- A day label rolling to `Yesterday` is not a wake. That case is the scrape map's `Day Word` row, whose preview is the word `Yesterday` and whose time is `4:00 PM`.
- Live wake checking needs the logged-in box and is not this drive. Do not point the fixture webhook at a real Gate URL.
