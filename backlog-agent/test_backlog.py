import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from backlog import finish, start, used_today

NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)


def empty():
    return {"tasks": {}, "starts": [], "cap_announced": None}


class LedgerTest(unittest.TestCase):
    def test_start_counts_attempts_and_starts(self):
        L = empty()
        start(L, "o/r#1", "t1", NOW)
        finish(L, "o/r#1", "failed", "x", NOW)
        start(L, "o/r#1", "t2", NOW)
        self.assertEqual(L["tasks"]["o/r#1"]["attempts"], 2)
        self.assertEqual(L["tasks"]["o/r#1"]["status"], "running")
        self.assertEqual(L["tasks"]["o/r#1"]["task_id"], "t2")
        self.assertEqual(len(L["starts"]), 2)

    def test_first_failure_stays_failed(self):
        L = empty()
        start(L, "o/r#1", "t1", NOW)
        self.assertEqual(finish(L, "o/r#1", "failed", "boom", NOW), "failed")

    def test_second_failure_becomes_blocked(self):
        L = empty()
        start(L, "o/r#1", "t1", NOW)
        finish(L, "o/r#1", "failed", "boom", NOW)
        start(L, "o/r#1", "t2", NOW)
        self.assertEqual(finish(L, "o/r#1", "failed", "boom again", NOW), "blocked")
        self.assertEqual(L["tasks"]["o/r#1"]["status"], "blocked")

    def test_daily_cap_uses_copenhagen_day(self):
        L = empty()
        L["starts"] = ["2026-10-21T23:30:00+00:00"]
        self.assertEqual(used_today(L, datetime(2026, 10, 22, 8, tzinfo=UTC)), 1)
        self.assertEqual(used_today(L, datetime(2026, 10, 21, 20, tzinfo=UTC)), 0)

    def test_starts_older_than_two_days_are_trimmed_on_start(self):
        L = empty()
        L["starts"] = ["2026-10-18T12:00:00+00:00", "2026-10-20T12:00:00+00:00"]
        start(L, "o/r#1", "t1", NOW)
        self.assertEqual(L["starts"], ["2026-10-20T12:00:00+00:00", NOW.isoformat()])


SCRIPT = Path(__file__).with_name("backlog.py")


def cli(ledger_path, *args):
    env = {**os.environ, "BACKLOG_LEDGER": str(ledger_path)}
    return subprocess.run([sys.executable, SCRIPT, *args], env=env, capture_output=True, text=True, check=True).stdout


class CliTest(unittest.TestCase):
    def test_start_then_finish_persists_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "ledger.json")
            cli(path, "start", "o/r#1", "task-abc")
            out = cli(path, "finish", "o/r#1", "done", "merged")
            entry = json.loads(path.read_text())["tasks"]["o/r#1"]
            self.assertEqual(out.strip(), "done")
            self.assertEqual((entry["status"], entry["task_id"], entry["note"]), ("done", "task-abc", "merged"))


if __name__ == "__main__":
    unittest.main()
