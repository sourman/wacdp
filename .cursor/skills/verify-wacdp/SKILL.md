---
name: verify-wacdp
description: "Drive wacdp (WhatsApp Web CDP helpers and the unread listener) and prove wake filtering, chat-list scraping, and archive/send/reply/react/forward/delete. Use when changing daemon.py, scrape_chat_list.py, or the verb scripts, or when asked to prove a WhatsApp CDP change. CI uses a fixture CDP; live box-chrome on :9225 is opt-in and only the two safe targets."
---

# Verify wacdp

wacdp is a set of CLI helpers plus `daemon.py`. Operators run the helpers against an already logged-in WhatsApp Web tab. The listener polls the chat list and POSTs an `unread` webhook when a row should wake. There is no app server to start. Verification launches a disposable Chrome, serves recorded chat-list DOM and React fiber snapshots, and points `WA_CDP_HTTP` at that Chrome. The product scripts run unchanged.

Two instances of the fixture can run side by side. Set `VERIFY_RUN_ID` so each run gets its own scratch directory, CDP port, and evidence directory. Never point a fixture run at port `9225`. That port is the live box-chrome session, and two drivers must not share it.

This fixture is a stand-in. A run on the fixture does not prove the steps that only exist on the real box:

- box-chrome is already open and logged into WhatsApp Web on `http://127.0.0.1:9225`
- the page is `web.whatsapp.com`, not the recorded snapshot
- webhooks go to Gate, not the local sink
- the phone chat and the group already exist in that account

Those steps stay unverified until `verify-wacdp live` is run on that box. Live commands are refused for every contact except `+20 10 92600692` and the group `WhatsApp slack bridging`.

## Launch

From the repo root:

```bash
python3 .cursor/skills/verify-wacdp/bin/verify-wacdp launch
```

Launch needs the `websockets` package (`python3 -m pip install --user websockets` when the import fails). It starts a local HTTP server and headless Chrome with its own user-data dir. Ready means the JSON it prints has `"ok": true`, `"ready": "1"`, a non-empty chat list, and `"live_port_9225": "untouched"`. A process that merely answers `/json/version` is not ready.

The fixture CDP port defaults to `9335` and the HTTP port to `8766`. Override with `VERIFY_CDP_PORT` and `VERIFY_HTTP_PORT`. Launch refuses `9225`. A second launch of the same `VERIFY_RUN_ID` stops the previous fixture processes for that id and starts clean. It does not delete evidence.

Scratch state (Chrome profile, copied scripts, fixture `config.json`, webhook log) lives under `/tmp/verify-wacdp/<run-id>/`. The fixture config uses webhook key `fixture-not-a-secret` and the allowlist in `fixtures/allowlist.json`. It never reads or writes the repo `config.json`.

## Doctor

```bash
python3 .cursor/skills/verify-wacdp/bin/verify-wacdp doctor
```

Read-only. Exit `0` only when the fixture HTTP server and the Chrome process this run started are alive, the CDP target URL contains `whatsapp-fixture`, the page dataset says the fixture is ready, `#pane-side` is present, the page is not a QR login, and the copied product scripts still match the repo. Anything else: do not drive. Relaunch.

Live doctor, also read-only, does not click:

```bash
python3 .cursor/skills/verify-wacdp/bin/verify-wacdp doctor --live
```

It requires `http://127.0.0.1:9225` to serve a logged-in WhatsApp page. It fails if that port is down, showing a QR code, or serving this fixture.

## Drive

Run doctor first. Then one feature:

```bash
python3 .cursor/skills/verify-wacdp/bin/verify-wacdp drive scrape
python3 .cursor/skills/verify-wacdp/bin/verify-wacdp drive wake
python3 .cursor/skills/verify-wacdp/bin/verify-wacdp drive archive
python3 .cursor/skills/verify-wacdp/bin/verify-wacdp drive send
python3 .cursor/skills/verify-wacdp/bin/verify-wacdp drive reply
python3 .cursor/skills/verify-wacdp/bin/verify-wacdp drive react
python3 .cursor/skills/verify-wacdp/bin/verify-wacdp drive forward
python3 .cursor/skills/verify-wacdp/bin/verify-wacdp drive delete
```

`drive all` runs those in that order and stops on the first failure. Each command navigates the fixture to the snapshot that feature needs, runs the product script from a scratch copy, and writes evidence. Exit `0` and a final JSON line with `"ok": true` is the pass. A non-zero exit is a failure; read the `"error"` string. Do not treat a skipped feature as covered by a different one.

`drive wake` calls `daemon.tick` (the same poll `daemon.py` repeats) against the baseline snapshot, polls the same snapshot again, then loads the inbound snapshot and polls once more. It does not start `supervise.sh`. The webhook sink is the fixture HTTP server. Payloads are the listener's real POST bodies.

`drive send`, `reply`, `react`, `forward`, `archive`, and `delete` shell out to the matching product script with `WA_CDP_HTTP` set to the fixture and `WACDP_SYNTHETIC_CLICKS=0`. Selectors are the ones in those scripts (`#pane-side`, `span[title]`, `#main footer div[contenteditable="true"]`, `[role="menuitem"]`, `[data-testid="msg-container"]`). The safe fixture rows are titled `+20 10 92600692` and `WhatsApp slack bridging`. Delete removes a sacrificial row titled `Scratch Delete` and refuses the group when `--allow-group` is absent.

Live driving is opt-in and only after `doctor --live` succeeds. The wrapper adds `WA_CDP_HTTP=http://127.0.0.1:9225` and rejects every other contact:

```bash
python3 .cursor/skills/verify-wacdp/bin/verify-wacdp live scrape
python3 .cursor/skills/verify-wacdp/bin/verify-wacdp live send --text 'verify live ping'
python3 .cursor/skills/verify-wacdp/bin/verify-wacdp live reply --needle 'verify live ping' --text 'verify live reply'
python3 .cursor/skills/verify-wacdp/bin/verify-wacdp live react --needle 'verify live ping' --emoji '👍'
python3 .cursor/skills/verify-wacdp/bin/verify-wacdp live forward --needle 'verify live ping' --to-contact 'WhatsApp slack bridging'
python3 .cursor/skills/verify-wacdp/bin/verify-wacdp live archive
python3 .cursor/skills/verify-wacdp/bin/verify-wacdp live unarchive
python3 .cursor/skills/verify-wacdp/bin/verify-wacdp live delete
```

`live delete` always passes `--dry-run` and only accepts the phone. It does not accept `--allow-group`. `live archive` and `live unarchive` only accept the phone. Send, reply, react, and forward accept the phone or the group and no other destination. Live commands run the repo scripts, so a real send updates the gitignored `gate_sent.json` on that box.

## Evidence

Proof is written to `.verify-evidence/<run-id>/<feature>/result.json` (default run id `default`). The directory is gitignored. Each file records the feature id, the product command's JSON, and a second observation of the resulting state:

- `scrape` stores the scraper JSON. Field values must match `fixtures/oracle.json`.
- `wake` stores both webhook posts. The first post is the badge batch. The second post is the single open-chat preview wake. Quiet chats are absent from both posts and present in the listener baseline.
- `send`, `reply`, `react`, and `forward` store the verb JSON plus a later `scrape_chat_list.py` row for the chat that changed. The verb must also leave no chat open.
- `archive` stores the check, archive, re-check, and unarchive JSON, the refused group attempt, and the chat-list titles after unarchive.
- `delete` stores the refused group attempt, the outgoing-message delete, the `Scratch Delete` removal, and the chat-list titles afterward.

Capture the action and the result. A webhook status or a script `"ok": true` without the follow-up row, menu label, or list membership is not proof. The fixture mocks WhatsApp Web, which is the external system behind CDP. It does not mock the listener or the verb scripts.

## Cleanup

```bash
python3 .cursor/skills/verify-wacdp/bin/verify-wacdp cleanup
```

Cleanup signals the Chrome process group and the HTTP server process group recorded for this `VERIFY_RUN_ID`, then deletes `/tmp/verify-wacdp/<run-id>/`. It does not kill by process name and it does not touch port `9225`. Evidence under `.verify-evidence/<run-id>/` stays. Confirm the `result.json` files are still there after cleanup before calling the run done.

## Helpers

The only helper is `verify-wacdp`. Run it as `python3 .cursor/skills/verify-wacdp/bin/verify-wacdp <command>` from the repo root. Commands: `launch`, `doctor`, `doctor --live`, `drive <feature>`, `cleanup`, `live <command>`.

Recorded inputs:

- `fixtures/rows.json` — chat-list rows, including the empty preview, the day-word preview, and the row whose time label is after the preview
- `fixtures/fibers.json` — React props (`id`, `isAddressBookContact`, `isBusiness`, group) keyed by row id
- `fixtures/archived.json` — `WAWebChatCollection` archived models
- `fixtures/allowlist.json` — `client_allowlist` and `denylist_ids` used by the fixture config
- `fixtures/oracle.json` — scrape fields and wake payloads the drives assert
- `harness/fixture_page.html` and `harness/fixture_page.js` — the page Chrome loads

Feature recipes are in `features/`.
