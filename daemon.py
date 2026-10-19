#!/usr/bin/env python3
"""Poll WA chat list; POST new unmuted unreads to Gate webhook.

Also watches open_cases.json for outbound (from_me) replies on watched contacts.
A chat left open never grows an unread badge (WhatsApp marks the reply read);
those still alert when the list preview or clock time changes on an inbound row.

Single-instance: exclusive flock on daemon.lock (second copy exits 0).
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

DIR = Path(__file__).resolve().parent
STATE = DIR / "state.json"
CONFIG = DIR / "config.json"
OPEN_CASES = DIR / "open_cases.json"
GATE_SENT = DIR / "gate_sent.json"
SCRAPE = DIR / "scrape_chat_list.py"
LOG = DIR / "daemon.log"
LOCK = DIR / "daemon.lock"
PIDFILE = DIR / "daemon.pid"
INTERVAL = float(os.environ.get("WA_LISTEN_INTERVAL", "20"))


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    # stdout only when not already redirected to daemon.log by supervise
    if not os.environ.get("WA_LISTEN_LOG_FILE_ONLY"):
        print(line, flush=True)


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def acquire_lock():
    """Hold exclusive lock for process lifetime. Returns open file obj."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK, "a+", encoding="utf-8")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.seek(0)
        other = (fh.read() or "").strip() or "?"
        print(f"another wa-listen daemon holds the lock (pid≈{other}); exiting", flush=True)
        fh.close()
        sys.exit(0)
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    PIDFILE.write_text(str(os.getpid()) + "\n")
    return fh


def resolve_cdp(cfg: dict) -> str:
    env = (os.environ.get("WA_CDP_HTTP") or "").strip()
    if env:
        return env.rstrip("/")
    cdp = (cfg.get("cdp_http") or "").strip()
    if cdp:
        return cdp.rstrip("/")
    display = os.environ.get("DISPLAY") or ""
    if display.startswith(":"):
        try:
            num = int(display[1:].split(".", 1)[0])
            return f"http://127.0.0.1:{9222 + num}"
        except ValueError:
            pass
    return ""


def scrape(cfg: dict | None = None):
    env = os.environ.copy()
    cfg = cfg or load_json(CONFIG, {})
    cdp = resolve_cdp(cfg)
    if cdp:
        env["WA_CDP_HTTP"] = cdp
    r = subprocess.run(
        [sys.executable, str(SCRAPE)],
        capture_output=True,
        text=True,
        env=env,
        timeout=45,
    )
    raw = (r.stdout or "").strip() or (r.stderr or "").strip()
    try:
        return json.loads(raw)
    except Exception:
        return {"error": "bad_json", "detail": raw[:500], "code": r.returncode}


def fingerprint(chat: dict) -> str:
    return f"{chat.get('who','')}|{chat.get('unread',0)}|{chat.get('preview','')[:80]}"


def norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def post_webhook(cfg: dict, payload: dict) -> bool:
    url = cfg.get("webhook_url") or ""
    key = cfg.get("webhook_key") or os.environ.get("GATE_WA_WEBHOOK_KEY", "")
    if not url or not key:
        log("missing webhook_url or key in config/env - skip post")
        return False
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "X-Automation-Key": key,
            "User-Agent": "gate-wa-listen/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            log(f"webhook HTTP {resp.status}")
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        log(f"webhook HTTPError {e.code}")
        with (DIR / "failed_posts.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return False
    except Exception as e:
        log(f"webhook fail: {e}")
        with (DIR / "failed_posts.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return False


_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0000FE0F\U0000200D\U0001F1E6-\U0001F1FF\U0000E000-\U0000F8FF]"
)


def norm_title(s: str) -> str:
    """Emoji-stripped, whitespace-collapsed, casefolded title."""
    return " ".join(_EMOJI_RE.sub("", s or "").split()).casefold()


def is_denied(chat: dict, cfg: dict) -> bool:
    """Exact match only: chat_id in denylist_ids, or whole normalized title in
    denylist. No substring matching ("AF" must not hit "Afaf Mousa")."""
    ids = cfg.get("denylist_ids") or {}
    cid = (chat.get("chat_id") or "").strip()
    if cid and cid in ids:
        return True
    titles = {norm_title(x) for x in (cfg.get("denylist") or []) if norm_title(x)}
    return norm_title(chat.get("who") or "") in titles


def load_open_cases() -> dict:
    data = load_json(OPEN_CASES, {"cases": {}})
    if not isinstance(data, dict):
        return {"cases": {}}
    cases = data.get("cases")
    if not isinstance(cases, dict):
        data["cases"] = {}
    return data


def prune_expired_cases(cases_doc: dict, now: float) -> bool:
    """Drop expired cases in-place. Returns True if file should be rewritten."""
    cases = cases_doc.get("cases") or {}
    changed = False
    for who in list(cases.keys()):
        meta = cases.get(who) or {}
        try:
            opened = float(meta.get("opened_at") or 0)
            ttl = float(meta.get("ttl_hours") if meta.get("ttl_hours") is not None else 48)
        except (TypeError, ValueError):
            opened, ttl = 0.0, 48.0
        if opened and ttl >= 0 and now > opened + ttl * 3600:
            log(f"open_cases expire: {who}")
            cases.pop(who, None)
            changed = True
    return changed


def _gate_entry(who: str, now: float, gs: dict | None = None):
    """Live gate_sent row for who, or None. Pass gs to avoid reading the file."""
    if gs is None:
        gs = load_json(GATE_SENT, {})
    if not isinstance(gs, dict):
        return None
    entry = gs.get(who)
    if not isinstance(entry, dict):
        return None
    try:
        until = float(entry.get("until") or 0)
    except (TypeError, ValueError):
        return None
    if now >= until:
        return None
    return entry


def gate_sent_suppresses(who: str, preview: str, now: float, gs: dict | None = None) -> bool:
    entry = _gate_entry(who, now, gs)
    if not entry:
        return False
    stored = norm_ws(str(entry.get("preview") or ""))
    cur = norm_ws(preview)
    if not stored:
        return False
    return cur == stored or cur.startswith(stored)


def stamped_outbound(who: str, preview: str, now: float, gs: dict | None = None) -> bool:
    """True when preview is exactly a script send still inside its gate_sent window."""
    entry = _gate_entry(who, now, gs)
    if not entry:
        return False
    stored = norm_ws(str(entry.get("preview") or ""))
    return bool(stored) and norm_ws(preview) == stored


# Chat-list clock ("1:02 PM" / "13:02"). Day labels ("Yesterday", "Monday") are not.
_CLOCK_RE = re.compile(r"\d{1,2}:\d{2}")
# "You: …", "You reacted …", "You deleted …" — not "You there?".
_OWN_RE = re.compile(r"^\s*you\s*(?::|\breacted\b|\bdeleted\b)", re.IGNORECASE)
# "Reacted ❤️ to …", "You reacted …", "<name> reacted ❤️ to …".
# The name form requires "reacted <token> to" so a sentence like
# "I reacted badly" is still an inbound message.
_REACTION_RE = re.compile(
    r"^\s*(?:you\s+)?reacted\b|^\s*[^:\n]{1,60}?\sreacted\s+\S+\s+to\b",
    re.IGNORECASE,
)
_TICK_RE = re.compile(
    r"wds-ic-read|wds-ic-delivered|wds-ic-sent|msg-check|msg-dblcheck|"
    r"status-dblcheck|status-check",
    re.IGNORECASE,
)


def is_clock_time(value: str) -> bool:
    return bool(_CLOCK_RE.search(value or ""))


def is_own_activity(chat: dict) -> bool:
    """User's own row: from_me, a You: prefix, or an outbound status tick."""
    if not isinstance(chat, dict):
        return False
    if chat.get("from_me"):
        return True
    if _OWN_RE.match(norm_ws(str(chat.get("preview") or ""))):
        return True
    icon = str(chat.get("status_icon") or "")
    return bool(icon and _TICK_RE.search(icon))


def is_reaction_activity(chat: dict) -> bool:
    if not isinstance(chat, dict):
        return False
    if chat.get("is_reaction"):
        return True
    return bool(_REACTION_RE.match(norm_ws(str(chat.get("preview") or ""))))


def _unread_count(chat: dict) -> int:
    try:
        return int(chat.get("unread") or 0)
    except (TypeError, ValueError):
        return 0


def chat_snapshot(chat: dict) -> dict:
    """Comparable list-row state. Preview/time are whitespace-normalized."""
    return {
        "preview": norm_ws(str(chat.get("preview") or "")),
        "time": norm_ws(str(chat.get("time") or "")),
        "from_me": bool(chat.get("from_me")),
        "is_reaction": bool(chat.get("is_reaction")),
        "unread": _unread_count(chat),
    }


def inbound_read_activity(prev: dict | None, chat: dict) -> bool:
    """Inbound list change that may have no unread badge.

    The first time a chat is seen, prev is missing and this is False (seed
    the baseline, do not replay history). Own sends and reactions are False.
    A changed non-empty preview counts. The same preview with a new clock
    time counts (another copy of the same text). A roll from a clock to
    "Yesterday" or a weekday does not.
    """
    if not isinstance(prev, dict) or not isinstance(chat, dict):
        return False
    if is_own_activity(chat) or is_reaction_activity(chat):
        return False
    cur = chat_snapshot(chat)
    prev_preview = norm_ws(str(prev.get("preview") or ""))
    if cur["preview"] and cur["preview"] != prev_preview:
        return True
    prev_time = norm_ws(str(prev.get("time") or ""))
    if cur["preview"] and cur["time"] and cur["time"] != prev_time and is_clock_time(cur["time"]):
        return True
    return False


def _unread_payload(chat: dict, reason: str) -> dict:
    # Historical fields, plus reason (badge = unread count, preview = list diff).
    return {
        "who": chat.get("who") or "",
        "chat_id": chat.get("chat_id", ""),
        "sender": chat.get("sender", ""),
        "preview": chat.get("preview", ""),
        "unread": _unread_count(chat),
        "muted": False,
        "reason": reason,
    }


def diff_incoming(
    state: dict,
    chats: list,
    cfg: dict,
    now: float | None = None,
    gate_sent: dict | None = None,
) -> list:
    """Update state['seen'] / state['baseline'] and return unread payloads.

    ``badge``: unmuted, not denied, not a reaction, unread > 0, fingerprint changed.
    ``preview``: unread is 0, but inbound_read_activity against the saved row.
    A preview *text* change that exactly matches a live gate_sent stamp is the
    user's own send (status ticks can lag one poll) and is not alerted. A later
    message with that same text still alerts via a clock-time change.

    Does not post the webhook. ``reason`` is the only field added to each chat.
    """
    if now is None:
        now = time.time()
    if gate_sent is None:
        gate_sent = load_json(GATE_SENT, {})
    if not isinstance(gate_sent, dict):
        gate_sent = {}

    hits: list = []
    seen = state.setdefault("seen", {})
    if not isinstance(seen, dict):
        seen = {}
        state["seen"] = seen
    baseline = state.setdefault("baseline", {})
    if not isinstance(baseline, dict):
        baseline = {}
        state["baseline"] = baseline

    for c in chats or []:
        if not isinstance(c, dict):
            continue
        who = c.get("who") or ""
        if not who:
            continue
        prev = baseline.get(who) if isinstance(baseline.get(who), dict) else None
        snap = chat_snapshot(c)
        unread = snap["unread"]
        reason = None
        if not c.get("muted") and not is_denied(c, cfg):
            if unread > 0 and not is_reaction_activity(c):
                fp = fingerprint(c)
                if seen.get(who) != fp:
                    reason = "badge"
                    seen[who] = fp
            elif unread <= 0 and inbound_read_activity(prev, c):
                prev_preview = norm_ws(str((prev or {}).get("preview") or ""))
                text_changed = snap["preview"] != prev_preview
                if not (text_changed and stamped_outbound(who, snap["preview"], now, gate_sent)):
                    reason = "preview"
        if unread == 0:
            seen.pop(who, None)
        baseline[who] = snap
        if reason:
            hits.append(_unread_payload(c, reason))
    return hits


def process_open_cases(cfg: dict, state: dict, chats: list) -> None:
    now = time.time()
    cases_doc = load_open_cases()
    if prune_expired_cases(cases_doc, now):
        save_json(OPEN_CASES, cases_doc)

    cases = cases_doc.get("cases") or {}
    if not cases:
        return

    by_who = {(c.get("who") or ""): c for c in chats}
    outbound_seen = state.setdefault("outbound_seen", {})
    outbound_hits = []

    for who, meta in list(cases.items()):
        chat = by_who.get(who)
        if not chat:
            continue
        if is_denied(chat, cfg):
            continue
        if not chat.get("from_me"):
            continue
        preview = chat.get("preview") or ""
        inbound = (meta or {}).get("inbound_preview") or ""
        if norm_ws(preview) == norm_ws(inbound):
            continue
        if outbound_seen.get(who) == preview[:80]:
            continue
        if gate_sent_suppresses(who, preview, now):
            log(f"outbound skip gate_sent: {who}")
            continue

        outbound_hits.append(
            {
                "who": who,
                "preview": preview,
                "from_me": True,
                "unread": int(chat.get("unread") or 0),
                "related": list((meta or {}).get("related") or []),
                "related_ids": list((meta or {}).get("related_ids") or []),
                "inbound_preview": inbound,
            }
        )
        outbound_seen[who] = preview[:80]
        try:
            meta["last_outbound_preview"] = preview[:80]
        except Exception:
            pass

    if outbound_hits:
        log(f"outbound hits: {[c['who'] for c in outbound_hits]}")
        if post_webhook(cfg, {"event": "outbound", "chats": outbound_hits}):
            # Persist optional last_outbound_preview on cases after successful post
            save_json(OPEN_CASES, cases_doc)


def tick(cfg: dict, state: dict) -> dict:
    data = scrape(cfg)
    if data.get("error"):
        log(f"scrape error: {data}")
        last_wedge = state.get("last_wedge_post", 0)
        if time.time() - last_wedge > 3600 and cfg.get("webhook_url"):
            if post_webhook(
                cfg,
                {
                    "event": "wedge",
                    "chats": [],
                    "note": f"cdp/scrape: {data.get('error')}",
                },
            ):
                state["last_wedge_post"] = time.time()
                save_json(STATE, state)
        return state

    chats = data.get("chats") or []
    # --- open-cases outbound watches (independent of unread) ---
    process_open_cases(cfg, state, chats)

    # badge: unread > 0. preview: inbound list change while unread stays 0
    # (chat was open, so WhatsApp marked the reply read). Event type stays
    # "unread"; each chat carries reason "badge" or "preview".
    new_hits = diff_incoming(state, chats, cfg)
    if new_hits:
        log(f"new hits: {[c['who'] for c in new_hits]}")
        post_webhook(cfg, {"event": "unread", "chats": new_hits})
    save_json(STATE, state)
    return state


def main() -> None:
    lock_fh = acquire_lock()
    cfg = load_json(CONFIG, {})
    if not cfg.get("webhook_url"):
        log("No config.json webhook_url yet - waiting for setup")
    state = load_json(STATE, {"seen": {}})
    cdp = resolve_cdp(cfg)
    if not cdp:
        log("No cdp_http in config and no WA_CDP_HTTP/DISPLAY - daemon cannot poll")
    log(
        f"daemon start pid={os.getpid()} interval={INTERVAL}s "
        f"cdp={cdp or 'UNSET'}"
    )
    try:
        while True:
            try:
                cfg = load_json(CONFIG, cfg)
                state = tick(cfg, state)
            except Exception:
                log("tick crashed:\n" + traceback.format_exc())
            time.sleep(INTERVAL)
    finally:
        try:
            lock_fh.close()
        except Exception:
            pass
        try:
            if PIDFILE.exists() and PIDFILE.read_text().strip() == str(os.getpid()):
                PIDFILE.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()
