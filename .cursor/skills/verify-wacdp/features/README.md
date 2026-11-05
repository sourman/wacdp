# wacdp verification map

This directory is the maintained source for verifying the operator-facing behavior of the WhatsApp Web CDP helpers and the unread listener. Read the index before driving, then use the matching feature file as the recipe.

## Baseline preconditions

- Launch the fixture with `python3 .cursor/skills/verify-wacdp/bin/verify-wacdp launch` from the repo root.
- Set `VERIFY_RUN_ID` when two runs must not share a Chrome profile or evidence directory.
- Run `python3 .cursor/skills/verify-wacdp/bin/verify-wacdp doctor` and require `"ok": true`, a `whatsapp-fixture` page, and `"live_port_9225": "untouched"`.
- Never drive an instance that this verification run did not start.
- Do not point the fixture at port `9225`.

## Driving conventions

- Start every recipe from a fresh `launch` or from the snapshot the drive command loads itself.
- Treat every command as literal. Keep the phone number, the group title, and the fixture needles unchanged.
- Fixture drives go through `verify-wacdp drive`. Live drives go through `verify-wacdp live` and only after `doctor --live`.
- The safe live targets are the phone `+20 10 92600692` and the group `WhatsApp slack bridging`.
- Restore the phone chat after a live archive. Do not remove proof artifacts during cleanup.

## Proof and skip reporting

- Capture the command and the resulting state, not only the script's `"ok": true`.
- Fixture proof is `.verify-evidence/<run-id>/<feature>/result.json`.
- A webhook proof includes the POST body. A verb proof includes a second view: the chat list scrape, the menu label, or the row's absence.
- Record the feature id with the artifact.
- Report an unreachable path with the command and the unmet precondition. Live paths are unreachable when box-chrome is not logged in on `:9225`.
- Do not report a skipped entry point as verified through a different path.

## Feature entry contract

Each feature file starts with an H1 title and one paragraph describing the operator-visible behavior. It then uses exactly four H2 sections in this order.

1. `Sub-features` lists short IDs with one line for each behavior.
2. `How to get to it (user POV)` lists every operator entry point.
3. `Driving it with verify-wacdp` starts with `Preconditions:` and uses labeled bullets that pair each action with an exact command and observable result.
4. `Gotchas` lists traps that can waste or invalidate a verification run.

## Features

- [Wake filtering](./wake-filtering.md) covers allowlisted clients, unknown non-contacts, quiet saved contacts, denylist, muted chats, and groups.
- [Scrape the chat list](./scrape-chat-list.md) covers field mapping, address-book detection, empty and odd previews, and a time label parsed as the preview.
- [Archive a chat](./archive-chat.md) covers archive, unarchive, and the group refusal.
- [Send a message](./send-message.md) covers text send, delivery tick, neutral list, and the gate stamp.
- [Reply, react, and forward](./message-actions.md) covers quote reply, emoji reaction, and forward into the safe group.
- [Delete](./delete-chat.md) covers refusing a group, deleting one outgoing message, and deleting a sacrificial chat.
