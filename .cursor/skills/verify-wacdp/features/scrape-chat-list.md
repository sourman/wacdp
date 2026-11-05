# Scrape the chat list

Chat-list scrape reads the open WhatsApp Web list without opening a chat. Each row exposes the title, JID, preview, time, unread count, mute, sender, outgoing flag, address-book flag, business flag, group flag, and archived flag the listener relies on.

## Sub-features

- `scrape-fields` maps title, JID, preview, time, unread, mute, and group sender from the recorded rows.
- `scrape-address-book` keeps a saved contact at `is_address_book_contact` 1, an explicit non-contact at 0, a named row with no contact props at null, and infers 1 when a contact has a name but no flag.
- `scrape-business` marks the unsaved business row `is_business` true.
- `scrape-empty-preview` leaves the preview empty when the only extra line is `Yesterday`.
- `scrape-swapped-time` keeps `See you at 5` as the preview when `Yesterday` is the last line.
- `scrape-odd-preview` keeps `Photo`, `📷`, and a real message whose text is `Yesterday` next to clock time `4:00 PM`.
- `scrape-archived` merges unread archived models that are not in the visible list.

## How to get to it (user POV)

- The operator runs `python3 scrape_chat_list.py` against the logged-in tab.
- The listener runs that same script on every poll. The command does not open a conversation.

## Driving it with verify-wacdp

Preconditions:

- `verify-wacdp doctor` reports a ready fixture page with at least 8 titled rows.
- The page was loaded from `fixtures/rows.json`, `fixtures/fibers.json`, and `fixtures/archived.json`.

- **Scrape.** Run `python3 .cursor/skills/verify-wacdp/bin/verify-wacdp drive scrape`. Exit code `0`. The result has 20 chats.
- **Group sender.** `WhatsApp slack bridging` has `chat_id` `120363000000009225@g.us`, sender `Sam`, preview `bridge ping`, time `1:05 PM`, unread `2`, `is_group` true, and `is_address_book_contact` null.
- **Address book.** `Saved Friend` and `Inferred Saved` are `is_address_book_contact` 1. `Mountain View Resale` and `+1 202 555 0147` are `0`. `Named Quiet` is null and not a business.
- **Empty and swapped time.** `Empty Yesterday` has preview `""` and time `Yesterday`. `Swapped Clock` has preview `See you at 5` and time `Yesterday`.
- **Odd previews.** `Odd Photo` preview is `Photo` and time is `Monday`. `Odd Emoji` preview is `📷`. `Day Word` preview is `Yesterday` and time is `4:00 PM`.
- **Outgoing markers.** `Own Baseline` is `from_me` true because the preview starts with `You:`. `Tick Quiet` is `from_me` true with `status_icon` `wds-ic-delivered` and preview `delivered hello`.
- **Archived.** `Archived Realty` is archived, business, address-book `0`, unread `4`. `Pushname Only` is archived, address-book `0`, not a business, preview `Open house`.
- **Proof.** `.verify-evidence/<run-id>/scrape/result.json` is the scraper JSON and matches `fixtures/oracle.json`.

## Gotchas

- A row whose preview is the word `Yesterday` is valid when a separate clock time is present. Do not assert that the word never appears as a preview.
- Archived rows are absent from `#pane-side`. A scrape that only returns visible rows failed the archived merge.
- The scraper must not open a chat. Doctor after this drive still shows no conversation header.
- Live scrape is `python3 .cursor/skills/verify-wacdp/bin/verify-wacdp live scrape` after `doctor --live`. It prints the real account's chat list. Do not commit that output.
