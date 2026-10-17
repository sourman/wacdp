#!/usr/bin/env python3
"""Poll WA chat list; POST new unmuted unreads to Gate webhook.

Also watches open_cases.json for outbound (from_me) replies on watched contacts.

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


def gate_sent_suppresses(who: str, preview: str, now: float) -> bool:
    gs = load_json(GATE_SENT, {})
    if not isinstance(gs, dict):
        return False
    entry = gs.get(who)
    if not isinstance(entry, dict):
        return False
    try:
        until = float(entry.get("until") or 0)
    except (TypeError, ValueError):
        return False
    if now >= until:
        return False
    stored = norm_ws(str(entry.get("preview") or ""))
    cur = norm_ws(preview)
    if not stored:
        return False
    return cur == stored or cur.startswith(stored)


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

    unmuted_unread = []
    for c in chats:
        if c.get("muted") or int(c.get("unread") or 0) <= 0:
            continue
        if is_denied(c, cfg):
            continue
        unmuted_unread.append(c)

    new_hits = []
    seen = state.setdefault("seen", {})
    for c in unmuted_unread:
        fp = fingerprint(c)
        if seen.get(c["who"]) != fp:
            new_hits.append(c)
            seen[c["who"]] = fp

    for who in list(seen.keys()):
        match = next((c for c in chats if c["who"] == who), None)
        if match and int(match.get("unread") or 0) == 0:
            seen.pop(who, None)

    if new_hits:
        log(f"new hits: {[c['who'] for c in new_hits]}")
        post_webhook(
            cfg,
            {
                "event": "unread",
                "chats": [
                    {
                        "who": c["who"],
                        "chat_id": c.get("chat_id", ""),
                        "sender": c.get("sender", ""),
                        "preview": c.get("preview", ""),
                        "unread": c.get("unread", 0),
                        "muted": False,
                    }
                    for c in new_hits
                ],
            },
        )
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
