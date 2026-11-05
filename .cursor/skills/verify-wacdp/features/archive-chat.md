# Archive a chat

Archive flips a chat between the main list and the archived list from the search menu, without opening the conversation. The menu shows `Archive chat` on the main list and `Unarchive chat` when the chat is archived. A group is refused unless the operator passes `--allow-group`.

## Sub-features

- `archive-check` reports `state` `main` for the phone row before any change.
- `archive-flip` archives the phone row and the following check reports `archived`.
- `archive-hide` removes that title from the main list and leaves no conversation open.
- `archive-restore` unarchives the phone row and the title returns to the main list.
- `archive-group-refused` refuses `WhatsApp slack bridging` without `--allow-group`.

## How to get to it (user POV)

- Search the chat list, right-click the row, and choose `Archive chat` or `Unarchive chat`.
- The operator runs `python3 archive_chat.py --contact '<title or phone>'`, with `--check` or `--unarchive`.

## Driving it with verify-wacdp

Preconditions:

- `verify-wacdp doctor` reports the fixture CDP.
- The verbs snapshot includes `+20 10 92600692` on the main list and the group row.
- No conversation is open.

- **Fixture archive.** Run `python3 .cursor/skills/verify-wacdp/bin/verify-wacdp drive archive`. Exit code `0`.
- **Check.** The stored check object has `"state": "main"` and the phone title.
- **Archive.** The archive object has `"action": "archive"` and `"state": "archived"`. The phone title is absent from `#pane-side` and `#main header span[title]` is absent.
- **Unarchive.** The unarchive object has `"state": "main"`. The phone title is back on the pane and the conversation header is still absent.
- **Group.** The group attempt returns `"ok": false` and the error names a group. The group title remains on the list.
- **Proof.** `.verify-evidence/<run-id>/archive/result.json` holds the four phone results, the refused group payload, and `list_after_unarchive`.
- **Live phone only.** After `doctor --live`, run `python3 .cursor/skills/verify-wacdp/bin/verify-wacdp live archive` and then `python3 .cursor/skills/verify-wacdp/bin/verify-wacdp live unarchive`. Both refuse a contact other than `+20 10 92600692`.

## Gotchas

- Archiving opens a context menu. It must not leave `#main` showing a conversation, or WhatsApp will mark new messages read.
- Finish a live archive with `live unarchive` before the run is done. The fixture drive already unarchives.
- `--allow-group` is not part of this recipe. Passing it on the live group archives a real chat the recipe does not restore.
- Search has to match the exact title or the exact digits. A shortened phone number is a failed search, not a successful archive.
