#!/usr/bin/env python3
import argparse
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

MAX_RUNNING = 3
DAILY_CAP = 24
TZ = ZoneInfo("Europe/Copenhagen")
MAX_ATTEMPTS = 2

LEDGER = Path(os.environ.get("BACKLOG_LEDGER", Path(__file__).with_name("ledger.json")))


def load_ledger(path: Path) -> dict:
    if not path.exists():
        return {"tasks": {}, "starts": [], "cap_announced": None}
    return json.loads(path.read_text())


def save_ledger(path: Path, ledger: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def start(ledger: dict, key: str, task_id: str, now: datetime) -> None:
    entry = ledger["tasks"].get(key, {"attempts": 0})
    entry.update(
        status="running",
        task_id=task_id,
        attempts=entry["attempts"] + 1,
        started=now.isoformat(),
        finished=None,
        note="",
    )
    ledger["tasks"][key] = entry
    cutoff = now - timedelta(days=2)
    ledger["starts"] = [
        t for t in ledger["starts"] if datetime.fromisoformat(t) >= cutoff
    ]
    ledger["starts"].append(now.isoformat())


def finish(ledger: dict, key: str, status: str, note: str, now: datetime) -> str:
    entry = ledger["tasks"][key]
    if status == "failed" and entry["attempts"] >= MAX_ATTEMPTS:
        status = "blocked"
    entry.update(status=status, note=note, finished=now.isoformat())
    return status


def used_today(ledger: dict, now: datetime) -> int:
    today = now.astimezone(TZ).date()
    return sum(
        datetime.fromisoformat(t).astimezone(TZ).date() == today
        for t in ledger["starts"]
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("start")
    p.add_argument("key")
    p.add_argument("task_id")
    p = sub.add_parser("finish")
    p.add_argument("key")
    p.add_argument("status", choices=["done", "blocked", "failed"])
    p.add_argument("note")
    args = ap.parse_args()
    now = datetime.now(UTC)
    ledger = load_ledger(LEDGER)
    if args.cmd == "start":
        start(ledger, args.key, args.task_id, now)
    else:
        print(finish(ledger, args.key, args.status, args.note, now))
    save_ledger(LEDGER, ledger)


if __name__ == "__main__":
    main()
