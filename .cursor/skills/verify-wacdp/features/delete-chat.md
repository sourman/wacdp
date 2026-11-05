# Delete

Delete removes one outgoing message for everyone, or deletes an entire chat after a confirm step. A group is refused unless the operator passes `--allow-group`. The live recipe never confirms a delete.

## Sub-features

- `delete-group-refused` refuses to delete `WhatsApp slack bridging` without `--allow-group`.
- `delete-outgoing` deletes `fixture outgoing needle` with mode `everyone` and the bubble no longer contains that needle.
- `delete-chat` deletes the sacrificial row `Scratch Delete` and the title leaves the chat list.
- `delete-neutral` leaves no conversation open and keeps the safe group on the list.
- `delete-live-dry-run` on the live box opens the confirm step for the phone and cancels it.

## How to get to it (user POV)

- Open a chat, open an outgoing message's menu, choose Delete, then Delete for everyone.
- Open a chat, open the header Menu, choose Delete chat, then confirm Delete.
- The operator runs `python3 delete_outgoing.py` or `python3 delete_chat.py`.

## Driving it with verify-wacdp

Preconditions:

- `verify-wacdp doctor` reports the fixture CDP.
- The verbs snapshot contains `Scratch Delete`, the phone chat's outgoing needle `fixture outgoing needle`, and the group.

- **Refuse the group.** Run `python3 .cursor/skills/verify-wacdp/bin/verify-wacdp drive delete`. The group payload has `"ok": false` and the error says the chat is a group. The command continues only because that refusal is expected.
- **Delete one message.** The outgoing result has `"mode": "everyone"`, `stillNeedle` false, and `recalled` true.
- **Delete the sacrificial chat.** The delete-chat result has `"verify.gone": true` for `Scratch Delete`.
- **List afterward.** `Scratch Delete` is not on `#pane-side`, no conversation header is open, and `WhatsApp slack bridging` is still listed.
- **Proof.** `.verify-evidence/<run-id>/delete/result.json` stores the refusal, the outgoing delete, the chat delete, and `list_after`.
- **Live dry-run.** After `doctor --live`, run `python3 .cursor/skills/verify-wacdp/bin/verify-wacdp live delete`. The wrapper forces `--dry-run`, accepts only `+20 10 92600692`, and refuses `--allow-group`. The real chat stays on the list.

## Gotchas

- Do not add `--allow-group` to make the group leg pass. The refusal is the proof.
- The fixture deletes `Scratch Delete` only. Deleting `+20 10 92600692` in the fixture is a different chat from the live account, and the live command will not do it.
- `live delete` still opens the confirm dialog and then cancels. If doctor says the page is not logged in, stop. Do not retry against another contact.
- Message delete and chat delete are separate scripts. A passing chat delete does not prove the outgoing-message path.
