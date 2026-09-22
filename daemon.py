#!/usr/bin/env python3
"""Poll WA chat list; POST new unmuted unreads to Gate webhook.

Single-instance: exclusive flock on daemon.lock (second copy exits 0).
"""
from __future__ import annotations

import fcntl
import json
import os
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


def scrape():
    env = os.environ.copy()
    env.setdefault("WA_CDP_HTTP", "http://127.0.0.1:9427")
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


def post_webhook(cfg: dict, payload: dict) -> bool:
    url = cfg.get("webhook_url") or ""
    key = cfg.get("webhook_key") or os.environ.get("GATE_WA_WEBHOOK_KEY", "")
    if not url or not key:
        log("missing webhook_url or key in config/env — skip post")
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


def tick(cfg: dict, state: dict) -> dict:
    data = scrape()
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
    deny = {x.lower() for x in (cfg.get("denylist") or [])}
    unmuted_unread = []
    for c in chats:
        if c.get("muted") or int(c.get("unread") or 0) <= 0:
            continue
        who = (c.get("who") or "").strip()
        who_l = who.lower()
        if any(d in who_l or who_l in d for d in deny if d):
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
        log("No config.json webhook_url yet — waiting for setup")
    state = load_json(STATE, {"seen": {}})
    log(
        f"daemon start pid={os.getpid()} interval={INTERVAL}s "
        f"cdp={os.environ.get('WA_CDP_HTTP', 'http://127.0.0.1:9427')}"
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
