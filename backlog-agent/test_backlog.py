import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from backlog import (
    covering_pr,
    finish,
    is_major,
    plan,
    required_state,
    start,
    used_today,
)

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


def alert(
    package="urllib3",
    severity="high",
    covered_by_pr=None,
    created_at="2026-10-01T00:00:00Z",
):
    return {
        "ghsa": f"GHSA-{package}-{severity}",
        "severity": severity,
        "ecosystem": "pip",
        "package": package,
        "range": "< 2.5.0",
        "first_patched": "2.5.0",
        "created_at": created_at,
        "covered_by_pr": covered_by_pr,
    }


def pr(
    number,
    checks="red",
    created_at="2026-10-01T00:00:00Z",
    major=False,
    labels=(),
    mergeable="MERGEABLE",
):
    return {
        "number": number,
        "title": f"Bump x {number}",
        "created_at": created_at,
        "labels": list(labels),
        "mergeable": mergeable,
        "auto_merge_since": None,
        "checks": checks,
        "major": major,
    }


def repo(name="o/r", has_ci=True, main="green", prs=(), alerts=()):
    return {
        "repo": name,
        "has_ci": has_ci,
        "main": main,
        "prs": list(prs),
        "alerts": list(alerts),
    }


def cands(*repos, needs_review_open=0):
    return {
        "generated": NOW.isoformat(),
        "needs_review_open": needs_review_open,
        "repos": list(repos),
    }


def keys(result):
    return [t["key"] for t in result["dispatch"]]


class PlanTest(unittest.TestCase):
    def test_alert_covered_by_pr_is_not_a_task(self):
        c = cands(
            repo(alerts=[alert("urllib3", covered_by_pr=7), alert("idna")], prs=[pr(7)])
        )
        result = plan(c, empty(), NOW)
        self.assertNotIn("o/r:dep:pip:urllib3", keys(result))
        self.assertIn("o/r:dep:pip:idna", keys(result))

    def test_red_main_holds_pr_tasks_not_alert_tasks(self):
        c = cands(repo(main="red", prs=[pr(3)], alerts=[alert()]))
        result = plan(c, empty(), NOW)
        self.assertEqual(sorted(keys(result)), ["o/r:dep:pip:urllib3", "o/r:fix-main"])
        self.assertEqual(result["held"], ["o/r#3"])

    def test_red_pr_is_a_task_when_main_green(self):
        result = plan(cands(repo(prs=[pr(3)])), empty(), NOW)
        self.assertEqual(keys(result), ["o/r#3"])
        self.assertEqual(result["dispatch"][0]["pr"], 3)

    def test_green_pr_younger_than_2h_is_dropped_older_is_stuck(self):
        young = pr(1, checks="green", created_at="2026-10-21T11:00:00+00:00")
        old = pr(2, checks="green", created_at="2026-10-21T09:00:00+00:00")
        armed = {
            **pr(3, checks="green", created_at="2026-10-01T00:00:00Z"),
            "auto_merge_since": "2026-10-21T11:30:00+00:00",
        }
        conflicting = pr(4, checks="pending", mergeable="CONFLICTING")
        pending = pr(5, checks="pending")
        result = plan(
            cands(repo(prs=[young, old, armed, conflicting, pending])), empty(), NOW
        )
        self.assertEqual(sorted(keys(result)), ["o/r#2", "o/r#4"])

    def test_labelled_prs_are_dropped(self):
        prs = [pr(1, labels=["needs-review"]), pr(2, labels=["agent:blocked"])]
        self.assertEqual(keys(plan(cands(repo(prs=prs)), empty(), NOW)), [])

    def test_no_ci_repo_prs_dropped_but_alerts_kept(self):
        c = cands(repo(has_ci=False, main=None, prs=[pr(1)], alerts=[alert()]))
        result = plan(c, empty(), NOW)
        self.assertEqual(keys(result), ["o/r:dep:pip:urllib3"])
        self.assertFalse(result["dispatch"][0]["has_ci"])

    def test_rank_order(self):
        c = cands(
            repo("o/high", main="red", alerts=[alert("a", "high")]),
            repo("o/low", alerts=[alert("b", "low")]),
            repo(
                "o/prs",
                prs=[
                    pr(2, created_at="2026-10-05T00:00:00Z"),
                    pr(1, created_at="2026-10-02T00:00:00Z"),
                    pr(3, major=True, created_at="2026-09-01T00:00:00Z"),
                ],
            ),
            repo("o/plain-red", main="red"),
            repo("jakob1379/pacify", has_ci=False, main=None),
        )
        result = plan(c, empty(), NOW)
        self.assertEqual(
            keys(result), ["o/high:dep:pip:a", "o/high:fix-main", "o/low:dep:pip:b"]
        )
        self.assertEqual(
            keys(result) + result["queued"],
            [
                "o/high:dep:pip:a",
                "o/high:fix-main",
                "o/low:dep:pip:b",
                "o/plain-red:fix-main",
                "o/prs#1",
                "o/prs#2",
                "o/prs#3",
                "jakob1379/pacify:add-ci",
            ],
        )

    def test_pr_covering_critical_alert_ranks_first(self):
        c = cands(
            repo(
                prs=[pr(9)],
                alerts=[alert("x", "critical", covered_by_pr=9), alert("y", "medium")],
            )
        )
        self.assertEqual(keys(plan(c, empty(), NOW))[0], "o/r#9")

    def test_failed_is_retried_blocked_running_done_are_not(self):
        L = empty()
        for n, status in [(1, "failed"), (2, "blocked"), (3, "running"), (4, "done")]:
            start(L, f"o/r#{n}", f"t{n}", NOW)
            if status != "running":
                finish(L, f"o/r#{n}", status, "", NOW)
        result = plan(cands(repo(prs=[pr(1), pr(2), pr(3), pr(4)])), L, NOW)
        self.assertEqual(keys(result), ["o/r#1"])

    def test_needs_review_cap_drops_no_ci_and_add_ci(self):
        c = cands(
            repo("o/noci", has_ci=False, main=None, alerts=[alert("a")]),
            repo("o/ci", alerts=[alert("b")]),
            repo("jakob1379/br18", has_ci=False, main=None),
            needs_review_open=5,
        )
        self.assertEqual(keys(plan(c, empty(), NOW)), ["o/ci:dep:pip:b"])
        c["needs_review_open"] = 4
        self.assertEqual(len(keys(plan(c, empty(), NOW))), 3)

    def test_slots_respect_running_and_daily_cap(self):
        c = cands(repo(prs=[pr(n) for n in range(1, 6)]))
        L = empty()
        start(L, "x/y#1", "t1", NOW)
        start(L, "x/y#2", "t2", NOW)
        self.assertEqual(len(plan(c, L, NOW)["dispatch"]), 1)

        L = empty()
        L["starts"] = [NOW.isoformat()] * 24
        first = plan(c, L, NOW)
        second = plan(c, L, NOW)
        self.assertEqual((first["dispatch"], first["announce_cap"]), ([], True))
        self.assertEqual((second["dispatch"], second["announce_cap"]), ([], False))

    def test_resolved_and_stale_done(self):
        L = empty()
        for key in ["o/r#1", "o/r#2", "o/r#3"]:
            start(L, key, "t", NOW)
        finish(L, "o/r#1", "done", "", NOW)
        finish(L, "o/r#2", "done", "", datetime(2026, 10, 19, 11, tzinfo=UTC))
        result = plan(cands(repo(prs=[pr(2)])), L, NOW)
        self.assertEqual(result["resolved"], ["o/r#1"])
        self.assertEqual(
            result["newly_blocked"],
            [{"key": "o/r#2", "note": "still open 48h after agent finished"}],
        )
        self.assertEqual(L["tasks"]["o/r#2"]["status"], "blocked")
        self.assertEqual(sorted(L["tasks"]), ["o/r#2", "o/r#3"])

    def test_done_item_still_open_but_not_a_task_is_not_resolved(self):
        L = empty()
        for key in ["o/r#1", "o/r:dep:pip:urllib3"]:
            start(L, key, "t", NOW)
            finish(L, key, "done", "", NOW)
        c = cands(
            repo(
                prs=[pr(1, labels=["needs-review"]), pr(2)],
                alerts=[alert(covered_by_pr=2)],
            )
        )
        self.assertEqual(plan(c, L, NOW)["resolved"], [])
        self.assertEqual(sorted(L["tasks"]), ["o/r#1", "o/r:dep:pip:urllib3"])


def run(name, conclusion, started="2026-10-21T10:00:00Z", status="COMPLETED"):
    return {
        "__typename": "CheckRun",
        "name": name,
        "conclusion": conclusion,
        "status": status,
        "startedAt": started,
    }


def ctx(state):
    return {"__typename": "StatusContext", "context": "ci", "state": state}


class RequiredStateTest(unittest.TestCase):
    def test_missing_required_check_is_pending(self):
        self.assertEqual(required_state([run("lint", "SUCCESS")], ["test"]), "pending")

    def test_latest_rerun_wins(self):
        nodes = [
            run("test", "SUCCESS", "2026-10-21T11:00:00Z"),
            run("test", "FAILURE", "2026-10-21T10:00:00Z"),
        ]
        self.assertEqual(required_state(nodes, ["test"]), "green")
        nodes = [
            run("test", "SUCCESS", "2026-10-21T10:00:00Z"),
            run("test", "FAILURE", "2026-10-21T11:00:00Z"),
        ]
        self.assertEqual(required_state(nodes, ["test"]), "red")

    def test_status_context_counts(self):
        self.assertEqual(required_state([ctx("SUCCESS")], ["ci"]), "green")
        self.assertEqual(required_state([ctx("PENDING")], ["ci"]), "pending")
        self.assertEqual(required_state([ctx("EXPECTED")], ["ci"]), "pending")
        self.assertEqual(required_state([ctx("ERROR")], ["ci"]), "red")

    def test_neutral_and_skipped_pass_in_progress_is_pending(self):
        nodes = [
            run("a", "NEUTRAL"),
            run("b", "SKIPPED"),
            run("c", None, status="IN_PROGRESS"),
        ]
        self.assertEqual(required_state(nodes, ["a", "b"]), "green")
        self.assertEqual(required_state(nodes, ["a", "c"]), "pending")
        self.assertIsNone(required_state(nodes, []))


class IsMajorTest(unittest.TestCase):
    def test_is_major(self):
        self.assertTrue(is_major("Bump requests from 1.9.2 to 2.0.0"))
        self.assertFalse(is_major("Bump ruff from 4.1 to 4.2"))
        self.assertTrue(is_major("Bump actions/checkout from v3 to v4"))
        self.assertFalse(is_major("Bump nixpkgs from abc123 to def456"))
        group = "Bumps the python group with 2 updates\nUpdates `a` from 1.0.0 to 1.1.0\nUpdates `b` from 1.4 to 2.0"
        self.assertTrue(is_major(group))
        self.assertFalse(is_major("Bumps the nix group"))
        self.assertFalse(is_major("Bump nixpkgs from 3f2a1c4 to 7b9e0d1"))
        self.assertTrue(is_major("Bump x from 1.0.0-rc.1 to 2.0.0."))


class CoveringPrTest(unittest.TestCase):
    def test_covering_pr_matches_whole_package_names(self):
        prs = [
            {"number": 8, "title": "Bump idna-ssl from 1.0 to 1.1", "body": ""},
            {
                "number": 5,
                "title": "Bump the python group",
                "body": "Updates `IDNA` from 3.6 to 3.7",
            },
            {"number": 3, "title": "Bump @babel/core from 7.1 to 7.2", "body": ""},
        ]
        self.assertEqual(covering_pr("idna", prs), 5)
        self.assertEqual(covering_pr("@babel/core", prs), 3)
        self.assertIsNone(covering_pr("ssl", prs))


SCRIPT = Path(__file__).with_name("backlog.py")


def cli(ledger_path, *args):
    env = {**os.environ, "BACKLOG_LEDGER": str(ledger_path)}
    return subprocess.run(
        [sys.executable, SCRIPT, *args],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


class CliTest(unittest.TestCase):
    def test_start_then_finish_persists_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "ledger.json")
            cli(path, "start", "o/r#1", "task-abc")
            out = cli(path, "finish", "o/r#1", "done", "merged")
            entry = json.loads(path.read_text())["tasks"]["o/r#1"]
            self.assertEqual(out.strip(), "done")
            self.assertEqual(
                (entry["status"], entry["task_id"], entry["note"]),
                ("done", "task-abc", "merged"),
            )

    def test_plan_prints_result_and_saves_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            path, cpath = Path(d, "ledger.json"), Path(d, "c.json")
            cpath.write_text(json.dumps(cands(repo(prs=[pr(1)]))))
            cli(path, "start", "gone/repo#9", "t")
            cli(path, "finish", "gone/repo#9", "done", "")
            out = json.loads(cli(path, "plan", str(cpath)))
            self.assertEqual([t["key"] for t in out["dispatch"]], ["o/r#1"])
            self.assertEqual(out["resolved"], ["gone/repo#9"])
            self.assertEqual(json.loads(path.read_text())["tasks"], {})


if __name__ == "__main__":
    unittest.main()
