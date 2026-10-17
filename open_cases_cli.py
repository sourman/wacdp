#!/usr/bin/env python3
"""Manage /home/box/wa-listen/open_cases.json checklist for outbound watches.

Usage:
  open_cases_cli.py add --contact TITLE --related Name [--related-id UUID]
                        [--preview TEXT] [--ttl-hours 48] [--note TEXT]
  open_cases_cli.py close --contact TITLE
  open_cases_cli.py list
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

DIR = Path(__file__).resolve().parent
OPEN_CASES = DIR / "open_cases.json"


def load_cases() -> dict:
    if not OPEN_CASES.exists():
        return {"cases": {}}
    try:
        data = json.loads(OPEN_CASES.read_text(encoding="utf-8"))
    except Exception:
        return {"cases": {}}
    if not isinstance(data, dict):
        return {"cases": {}}
    if not isinstance(data.get("cases"), dict):
        data["cases"] = {}
    return data


def save_cases(data: dict) -> None:
    OPEN_CASES.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def emit(ok: bool, **extra) -> None:
    out = {"ok": ok, **extra}
    print(json.dumps(out, ensure_ascii=False))
    if not ok:
        sys.exit(1)


def cmd_add(args: argparse.Namespace) -> None:
    contact = (args.contact or "").strip()
    related = (args.related or "").strip()
    if not contact:
        emit(False, error="contact required")
    if not related:
        emit(False, error="related required")
    data = load_cases()
    cases = data["cases"]
    entry = cases.get(contact) or {}
    related_list = list(entry.get("related") or [])
    if related not in related_list:
        related_list.append(related)
    related_ids = list(entry.get("related_ids") or [])
    if args.related_id:
        rid = args.related_id.strip()
        if rid and rid not in related_ids:
            related_ids.append(rid)
    preview = args.preview if args.preview is not None else entry.get("inbound_preview", "")
    ttl = args.ttl_hours if args.ttl_hours is not None else entry.get("ttl_hours", 48)
    note = args.note if args.note is not None else entry.get("note")
    opened_at = entry.get("opened_at") or time.time()
    new_entry = {
        "related": related_list,
        "related_ids": related_ids,
        "inbound_preview": preview or "",
        "opened_at": float(opened_at),
        "ttl_hours": float(ttl),
    }
    if note:
        new_entry["note"] = note
    elif "note" in entry and args.note is None:
        # keep existing note if not overridden
        if entry.get("note"):
            new_entry["note"] = entry["note"]
    if entry.get("last_outbound_preview"):
        new_entry["last_outbound_preview"] = entry["last_outbound_preview"]
    cases[contact] = new_entry
    save_cases(data)
    emit(True, action="add", contact=contact, case=new_entry)


def cmd_close(args: argparse.Namespace) -> None:
    contact = (args.contact or "").strip()
    if not contact:
        emit(False, error="contact required")
    data = load_cases()
    cases = data["cases"]
    if contact not in cases:
        emit(False, error="not_found", contact=contact)
    removed = cases.pop(contact)
    save_cases(data)
    emit(True, action="close", contact=contact, removed=removed)


def cmd_list(_args: argparse.Namespace) -> None:
    data = load_cases()
    emit(True, action="list", cases=data.get("cases") or {})


def main() -> None:
    ap = argparse.ArgumentParser(description="Manage WA open_cases.json")
    sub = ap.add_subparsers(dest="cmd", required=True)

    add_p = sub.add_parser("add", help="Open or update a case")
    add_p.add_argument("--contact", required=True, help="Exact chat-list who title")
    add_p.add_argument("--related", required=True, help="Related bot/project name")
    add_p.add_argument("--related-id", default=None, help="Optional UUID")
    add_p.add_argument("--preview", default=None, help="Inbound preview text at open")
    add_p.add_argument("--ttl-hours", type=float, default=48.0)
    add_p.add_argument("--note", default=None)
    add_p.set_defaults(func=cmd_add)

    close_p = sub.add_parser("close", help="Close a case")
    close_p.add_argument("--contact", required=True)
    close_p.set_defaults(func=cmd_close)

    list_p = sub.add_parser("list", help="List open cases")
    list_p.set_defaults(func=cmd_list)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
