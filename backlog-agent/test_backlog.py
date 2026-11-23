import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import linear
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

    def test_package_names_group_case_and_separator_insensitively(self):
        c = cands(
            repo(
                alerts=[
                    alert("GitPython"),
                    alert("gitpython"),
                    alert("pydantic_settings"),
                ]
            )
        )
        result = plan(c, empty(), NOW)
        self.assertEqual(
            sorted(keys(result)),
            ["o/r:dep:pip:gitpython", "o/r:dep:pip:pydantic-settings"],
        )

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

    def test_no_ci_repo_prs_are_tasks_until_labelled(self):
        prs = [pr(1, checks=None), pr(2, checks=None, labels=["needs-review"])]
        alerts = [alert("urllib3", covered_by_pr=1), alert("idna", "low")]
        c = cands(repo(has_ci=False, main=None, prs=prs, alerts=alerts))
        result = plan(c, empty(), NOW)
        self.assertEqual(keys(result), ["o/r#1", "o/r:dep:pip:idna"])
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
        self.assertEqual(result["dispatch"][0]["attempt"], 2)

    def test_needs_review_cap_drops_no_ci_and_add_ci(self):
        c = cands(
            repo("o/noci", has_ci=False, main=None, alerts=[alert("a")]),
            repo("o/ci", alerts=[alert("b")]),
            repo("jakob1379/br18", has_ci=False, main=None),
            needs_review_open=5,
        )
        self.assertEqual(keys(plan(c, empty(), NOW)), ["o/ci:dep:pip:b"])
        c["needs_review_open"] = 3
        self.assertEqual(len(keys(plan(c, empty(), NOW))), 3)
        c["needs_review_open"] = 4
        self.assertEqual(len(keys(plan(c, empty(), NOW))), 2)
        L = empty()
        start(L, "o/noci:dep:pip:a", "t1", NOW)
        self.assertEqual(keys(plan(c, L, NOW)), ["o/ci:dep:pip:b"])

    def test_slots_respect_running_and_daily_cap(self):
        c = cands(repo(prs=[pr(n) for n in range(1, 6)]))
        L = empty()
        start(L, "x/y#1", "t1", NOW)
        start(L, "x/y#2", "t2", NOW)
        self.assertEqual(len(plan(c, L, NOW)["dispatch"]), 1)

        L = empty()
        L["starts"] = [NOW.isoformat()] * 100
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

    def test_done_pr_still_red_2h_after_finish_is_blocked(self):
        L = empty()
        for key in ["o/r#1", "o/r#2", "o/r#3"]:
            start(L, key, "t", NOW)
            finish(L, key, "done", "", datetime(2026, 10, 21, 9, tzinfo=UTC))
        finish(L, "o/r#3", "done", "", datetime(2026, 10, 21, 11, tzinfo=UTC))
        c = cands(repo(prs=[pr(1), pr(2, checks="green"), pr(3)]))
        result = plan(c, L, NOW)
        self.assertEqual(
            result["newly_blocked"],
            [{"key": "o/r#1", "note": "still red 2h after agent finished"}],
        )
        self.assertEqual(L["tasks"]["o/r#2"]["status"], "done")
        self.assertEqual(L["tasks"]["o/r#3"]["status"], "done")

    def test_alert_opened_after_a_finished_task_started_reopens_it(self):
        L = empty()
        for package, status in [("a", "done"), ("b", "blocked"), ("c", "blocked")]:
            key = f"o/r:dep:pip:{package}"
            start(L, key, "t", datetime(2026, 10, 20, 12, tzinfo=UTC))
            finish(L, key, status, "stale", datetime(2026, 10, 20, 13, tzinfo=UTC))
        later = "2026-10-21T08:00:00Z"
        c = cands(
            repo(
                alerts=[
                    alert("a"),
                    alert("a", created_at=later),
                    alert("b", created_at=later),
                    alert("c"),
                ]
            )
        )
        result = plan(c, L, NOW)
        self.assertEqual(keys(result), ["o/r:dep:pip:a", "o/r:dep:pip:b"])
        self.assertEqual([t["attempt"] for t in result["dispatch"]], [1, 1])
        self.assertEqual(L["tasks"]["o/r:dep:pip:c"]["status"], "blocked")

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
        self.assertLessEqual({"o/r#1", "o/r:dep:pip:urllib3"}, set(L["tasks"]))

    def test_health_counts_dependency_and_main_state(self):
        L = empty()
        start(L, "o/a#9", "t", NOW)
        finish(L, "o/a#9", "blocked", "", NOW)
        c = cands(
            repo("o/a", main="red", prs=[pr(9), pr(10)], alerts=[alert(covered_by_pr=9)]),
            repo("o/b", main="pending"),
        )
        h = plan(c, L, NOW)["health"]
        self.assertEqual(h["unhandled_dependency"], 1)
        self.assertEqual(h["red_mains"], 1)
        self.assertEqual(h["pending_mains"], 1)

    def test_health_stale_queue_and_review_use_time_in_status(self):
        def card(i, status, hours, moved=True):
            at = (NOW - timedelta(hours=hours)).isoformat()
            return {
                "id": i,
                "identifier": i,
                "title": i,
                "status": status,
                "labels": [],
                "link": None,
                "created_at": (NOW - timedelta(days=30)).isoformat(),
                "history": [{"from": "Backlog", "to": status, "actor": "owner", "at": at}]
                if moved
                else [],
            }

        c = cands(repo())
        c["board"] = {
            "statuses": [],
            "cards": [
                card("a", "Needs triage", 25),
                card("b", "Approved", 23),
                card("c", "Needs triage", 1, moved=False),
                card("d", "In Review", 24 * 7 + 1),
                card("e", "In Review", 24 * 6),
            ],
        }
        h = plan(c, empty(), NOW)["health"]
        self.assertEqual(h["stale_queue"], 2)
        self.assertEqual(h["stale_review"], 1)

    def test_health_counts_topup_cards_waiting_for_approval(self):
        L = empty()
        c = snap(backlog_repo(issue(1), issue(2)), cards=[card(1), asked(2)])
        self.assertEqual(
            picked(plan(c, L, NOW)), [(2, "triage", "owner"), (1, "triage", "topup")]
        )
        for key in ["o/r#1:triage", "o/r#2:triage"]:
            finish(L, key, "needs-approval", "plan posted", NOW)
        c = snap(
            backlog_repo(issue(1), issue(2)),
            cards=[card(1, "Needs approval"), {**asked(2), "status": "Needs approval"}],
        )
        self.assertEqual(plan(c, L, NOW)["health"]["topup_waiting"], 1)

    def test_health_without_board_is_zero(self):
        h = plan(cands(repo()), empty(), NOW)["health"]
        self.assertEqual(
            (h["stale_queue"], h["topup_waiting"], h["stale_review"]), (0, 0, 0)
        )


OWNER = "o"


def issue(
    number, author=OWNER, created_at="2026-09-01T00:00:00Z", labels=(), comments=()
):
    return {
        "number": number,
        "title": f"Issue {number}",
        "author": author,
        "created_at": created_at,
        "labels": list(labels),
        "comments": list(comments),
    }


def comment(author, created_at, by_agent=False):
    return {"author": author, "created_at": created_at, "by_agent": by_agent}


def moved(frm, to, actor, at="2026-10-21T10:00:00Z"):
    return {"from": frm, "to": to, "actor": actor, "at": at}


def card(number, status="Backlog", history=(), labels=(), repo="o/r", type="issue"):
    return {
        "id": f"card-{number}",
        "identifier": f"JGA-{number}",
        "title": f"Issue {number}",
        "status": status,
        "labels": ["kind: backlog", f"repo: {repo.split('/')[1]}", *labels],
        "link": {"repo": repo, "number": number, "type": type},
        "created_at": "2026-09-01T00:00:00Z",
        "history": list(history),
    }


def asked(number, **kw):
    return card(
        number, "Needs triage", [moved("Backlog", "Needs triage", "owner")], **kw
    )


def working(c):
    return {
        **c,
        "status": "In Progress",
        "history": c["history"] + [moved(c["status"], "In Progress", "agent")],
    }


def backlog_repo(*issues, name="o/r", prs=(), other_prs=()):
    return {**repo(name, prs=prs), "issues": list(issues), "other_prs": list(other_prs)}


def snap(*repos, cards=()):
    return {
        **cands(*repos),
        "owner": OWNER,
        "board": {"statuses": [], "cards": list(cards)},
    }


def mv(n, to, add=(), remove=()):
    return {
        "op": "move",
        "card": f"card-{n}",
        "to": to,
        "add_labels": list(add),
        "remove_labels": list(remove),
    }


def picked(result):
    return [(t["issue"], t["kind"], t["origin"]) for t in result["dispatch"]]


class TriagePlanTest(unittest.TestCase):
    def test_owner_move_starts_a_triage_run_even_on_an_untrusted_issue(self):
        c = snap(backlog_repo(issue(5, author="eve")), cards=[asked(5)])
        result = plan(c, empty(), NOW)
        self.assertEqual(
            result["dispatch"],
            [
                {
                    "key": "o/r#5:triage",
                    "kind": "triage",
                    "repo": "o/r",
                    "issue": 5,
                    "card": "card-5",
                    "origin": "owner",
                    "tier": 9,
                    "attempt": 1,
                }
            ],
        )
        self.assertEqual(result["card_ops"], [mv(5, "In Progress")])

    def test_untrusted_authors_start_nothing(self):
        c = snap(
            backlog_repo(
                issue(1, author="eve", created_at="2026-10-21T11:00:00Z"),
                issue(2, author="eve"),
                issue(3, author="dependabot[bot]"),
                issue(4, author="eve"),
            ),
            cards=[
                card(1),
                card(2, "Needs triage", [moved("Backlog", "Needs triage", "agent")]),
                card(3, "Needs triage", [moved("Backlog", "Needs triage", "other")]),
                card(4),
            ],
        )
        result = plan(c, empty(), NOW)
        self.assertEqual((result["dispatch"], result["card_ops"]), ([], []))

    def test_new_owner_issue_is_triaged_at_once_older_ones_are_not(self):
        c = snap(
            backlog_repo(
                issue(1, created_at="2026-10-21T11:00:00Z"),
                issue(2, created_at="2026-10-21T11:00:00Z"),
                issue(3),
                issue(4),
            ),
            cards=[
                card(1),
                card(2, history=[moved("Needs triage", "Backlog", "owner")]),
                card(3),
                card(4, "Needs approval"),
            ],
        )
        result = plan(c, empty(), NOW)
        self.assertEqual(picked(result), [(1, "triage", "owner")])
        self.assertEqual(
            result["card_ops"], [mv(1, "Needs triage"), mv(1, "In Progress")]
        )

    def test_topup_picks_security_then_bug_then_oldest_and_stops_at_three(self):
        issues = [
            issue(1, created_at="2026-01-01T00:00:00Z"),
            issue(2, created_at="2026-08-01T00:00:00Z", labels=["bug"]),
            issue(3, created_at="2026-09-01T00:00:00Z", labels=["security"]),
            issue(4, created_at="2026-02-01T00:00:00Z"),
            issue(
                5,
                created_at="2025-01-01T00:00:00Z",
                comments=[comment(OWNER, "2025-02-01T00:00:00Z", by_agent=True)],
            ),
            issue(6, author="eve", labels=["security"]),
        ]
        c = snap(backlog_repo(*issues), cards=[card(n) for n in range(1, 7)])
        result = plan(c, empty(), NOW)
        self.assertEqual(
            picked(result),
            [(3, "triage", "topup"), (2, "triage", "topup"), (1, "triage", "topup")],
        )
        self.assertEqual(
            [op["card"] for op in result["card_ops"] if op["to"] == "Needs triage"],
            ["card-3", "card-2", "card-1"],
        )

        c = snap(
            backlog_repo(*issues, issue(7)),
            cards=[card(n) for n in range(1, 7)] + [asked(7)],
        )
        self.assertEqual(
            picked(plan(c, empty(), NOW)),
            [(3, "triage", "topup"), (2, "triage", "topup"), (7, "triage", "owner")],
        )

    def test_topup_waits_while_a_plan_or_a_run_is_pending(self):
        pending = {
            "needs approval": (card(2, "Needs approval"), empty()),
            "approved": (
                card(2, "Approved", [moved("Backlog", "Approved", "owner")]),
                empty(),
            ),
            "running": (working(asked(2)), empty()),
        }
        start(pending["running"][1], "o/r#2:triage", "t", NOW)
        for name, (other, L) in pending.items():
            with self.subTest(name):
                c = snap(backlog_repo(issue(1), issue(2)), cards=[card(1), other])
                result = plan(c, L, NOW)
                self.assertNotIn("card-1", [op["card"] for op in result["card_ops"]])
                self.assertNotIn("o/r#1:triage", keys(result))

    def test_approval_only_counts_from_the_owner(self):
        c = snap(
            backlog_repo(*(issue(n) for n in range(1, 5))),
            cards=[
                card(1, "Approved", [moved("Needs approval", "Approved", "agent")]),
                card(2, "Approved", [moved("Needs approval", "Approved", "other")]),
                card(3, "Approved", [moved("Needs approval", "Approved", "owner")]),
                card(4, "Approved", [moved("Backlog", "Approved", "owner")]),
            ],
        )
        result = plan(c, empty(), NOW)
        self.assertEqual(picked(result), [(3, "build", "owner")])
        self.assertEqual(result["dispatch"][0]["tier"], 8)
        self.assertEqual(
            result["card_ops"],
            [mv(1, "Needs approval"), mv(2, "Needs approval"), mv(3, "In Progress")],
        )

    def test_backlog_work_ranks_after_dependency_work_and_shares_the_caps(self):
        c = snap(
            backlog_repo(issue(3), issue(4), prs=[pr(1), pr(2)]),
            cards=[
                asked(3),
                card(4, "Approved", [moved("Needs approval", "Approved", "owner")]),
            ],
        )
        result = plan(c, empty(), NOW)
        self.assertEqual(keys(result), ["o/r#1", "o/r#2", "o/r#4:build"])
        self.assertEqual(result["queued"], ["o/r#3:triage"])
        self.assertEqual(result["card_ops"], [mv(4, "In Progress")])

        L = empty()
        L["starts"] = [NOW.isoformat()] * 100
        result = plan(c, L, NOW)
        self.assertEqual((result["dispatch"], result["card_ops"]), ([], []))

    def test_a_failed_run_is_retried_once_then_parked_in_needs_info(self):
        L = empty()
        c = snap(backlog_repo(issue(5)), cards=[asked(5)])
        self.assertEqual(keys(plan(c, L, NOW)), ["o/r#5:triage"])
        finish(L, "o/r#5:triage", "failed", "crash", NOW)
        c = snap(backlog_repo(issue(5)), cards=[working(asked(5))])
        result = plan(c, L, NOW)
        self.assertEqual(
            [(t["key"], t["attempt"]) for t in result["dispatch"]],
            [("o/r#5:triage", 2)],
        )
        self.assertEqual(result["card_ops"], [])
        self.assertEqual(finish(L, "o/r#5:triage", "failed", "crash", NOW), "blocked")
        result = plan(c, L, NOW)
        self.assertEqual(result["dispatch"], [])
        self.assertEqual(result["card_ops"], [mv(5, "Needs info", add=["failed"])])

    def test_a_topup_triage_cannot_go_straight_to_a_fix(self):
        L = empty()
        c = snap(backlog_repo(issue(1), issue(2)), cards=[card(1), asked(2)])
        self.assertEqual(
            picked(plan(c, L, NOW)), [(2, "triage", "owner"), (1, "triage", "topup")]
        )
        for key in ["o/r#1:triage", "o/r#2:triage"]:
            finish(L, key, "fix-pending", "https://github.com/o/r/pull/9", NOW)
        c = snap(
            backlog_repo(issue(1), issue(2)),
            cards=[working(card(1)), working(asked(2))],
        )
        self.assertEqual(
            plan(c, L, NOW)["card_ops"], [mv(1, "Needs approval"), mv(2, "In Review")]
        )

    def test_an_owner_reply_after_the_agents_last_comment_retriages(self):
        L = empty()
        plan(snap(backlog_repo(issue(1)), cards=[asked(1)]), L, NOW)
        finish(L, "o/r#1:triage", "needs-repro", "how do I reproduce?", NOW)
        result = plan(snap(backlog_repo(issue(1)), cards=[working(asked(1))]), L, NOW)
        self.assertEqual(result["card_ops"], [mv(1, "Needs info", add=["needs-repro"])])

        info = card(
            1,
            "Needs info",
            [moved("In Progress", "Needs info", "agent", "2026-10-21T10:00:00Z")],
            labels=["needs-repro"],
        )
        asked_first = [
            comment(OWNER, "2026-10-21T09:00:00Z"),
            comment(OWNER, "2026-10-21T10:00:00Z", by_agent=True),
        ]
        answered = asked_first + [comment(OWNER, "2026-10-21T11:00:00Z")]
        quiet = snap(backlog_repo(issue(1, comments=asked_first)), cards=[info])
        self.assertEqual(plan(quiet, L, NOW)["card_ops"], [])
        result = plan(
            snap(backlog_repo(issue(1, comments=answered)), cards=[info]), L, NOW
        )
        self.assertEqual(picked(result), [(1, "triage", "owner")])
        self.assertEqual(
            result["card_ops"],
            [mv(1, "Needs triage", remove=["needs-repro"]), mv(1, "In Progress")],
        )

    def test_only_cards_linked_to_an_issue_in_an_in_scope_repo_are_touched(self):
        unlinked = {**asked(3), "link": None}
        c = snap(
            backlog_repo(issue(1), issue(2), issue(4)),
            cards=[asked(1, repo="o/fork"), asked(2, type="pr"), unlinked, asked(4)],
        )
        result = plan(c, empty(), NOW)
        self.assertEqual(picked(result), [(4, "triage", "owner")])
        self.assertEqual(
            [op for op in result["card_ops"] if op["op"] != "create"],
            [mv(4, "In Progress")],
        )

    def test_a_review_card_closes_once_its_issue_is_closed(self):
        prs = [
            {
                "number": 9,
                "title": "Fix",
                "author": OWNER,
                "draft": False,
                "created_at": "2026-10-20T00:00:00Z",
                "linked_issues": [2],
            }
        ]
        c = snap(
            backlog_repo(issue(3), other_prs=prs),
            cards=[card(n, "In Review") for n in (1, 2, 3)],
        )
        self.assertEqual(
            plan(c, empty(), NOW)["card_ops"],
            [{"op": "close", "card": "card-1", "to": "Done"}],
        )

    def test_an_outcome_move_is_repeated_until_the_card_leaves_in_progress(self):
        L = empty()
        plan(snap(backlog_repo(issue(5)), cards=[asked(5)]), L, NOW)
        finish(L, "o/r#5:triage", "needs-approval", "plan posted", NOW)
        c = snap(backlog_repo(issue(5)), cards=[working(asked(5))])
        for _ in range(2):
            self.assertEqual(plan(c, L, NOW)["card_ops"], [mv(5, "Needs approval")])
        c = snap(backlog_repo(issue(5)), cards=[card(5, "Needs approval")])
        self.assertEqual(plan(c, L, NOW)["card_ops"], [])
        self.assertNotIn("o/r#5:triage", L["tasks"])

    def test_a_closed_duplicate_card_does_not_hide_the_issues_live_card(self):
        dup = {**card(5, "Duplicate"), "id": "dup-5"}
        for status, expected in [
            ("needs-approval", ([], [mv(5, "Needs approval")])),
            ("failed", ([("o/r#5:triage", 2)], [])),
        ]:
            with self.subTest(status):
                L = empty()
                plan(snap(backlog_repo(issue(5)), cards=[asked(5)]), L, NOW)
                finish(L, "o/r#5:triage", status, "x", NOW)
                c = snap(backlog_repo(issue(5)), cards=[dup, working(asked(5))])
                result = plan(c, L, NOW)
                self.assertEqual(
                    (
                        [(t["key"], t["attempt"]) for t in result["dispatch"]],
                        result["card_ops"],
                    ),
                    expected,
                )

    def test_backlog_cards_get_their_kind_and_repo_labels(self):
        bare = {**asked(5), "labels": []}
        half = {**card(6, "Needs approval"), "labels": ["kind: backlog"]}
        done = {**card(7, "Done"), "labels": []}
        c = snap(backlog_repo(issue(5), issue(6), issue(7)), cards=[bare, half, done])
        self.assertEqual(
            plan(c, empty(), NOW)["card_ops"],
            [
                mv(5, "Needs triage", add=["kind: backlog", "repo: r"]),
                mv(6, "Needs approval", add=["repo: r"]),
                mv(5, "In Progress"),
            ],
        )

    def test_a_multi_repo_build_stays_in_review_until_every_pr_merged(self):
        def snapshot(*y_prs):
            return snap(
                backlog_repo(name="o/x"),
                backlog_repo(name="o/y", other_prs=y_prs),
                cards=[card(5, "In Review", repo="o/x")],
            )

        self.assertEqual(
            plan(snapshot(other_pr(9, linked_refs=["o/x#5"])), empty(), NOW)[
                "card_ops"
            ],
            [],
        )
        self.assertEqual(
            plan(snapshot(), empty(), NOW)["card_ops"],
            [{"op": "close", "card": "card-5", "to": "Done"}],
        )


def other_pr(number, draft=False, linked_issues=(), linked_refs=(), labels=()):
    return {
        "number": number,
        "title": f"PR {number}",
        "author": OWNER,
        "draft": draft,
        "created_at": "2026-10-01T00:00:00Z",
        "labels": list(labels),
        "linked_issues": list(linked_issues),
        "linked_refs": list(linked_refs),
    }


def owner_card(id, title, status, labels, link=None):
    return {
        **card(0, status),
        "labels": list(labels),
        "id": id,
        "title": title,
        "link": link,
    }


def close(id):
    return {"op": "close", "card": id, "to": "Done"}


class OwnerCardsPlanTest(unittest.TestCase):
    def test_every_open_issue_gets_a_card_once_the_sync_had_its_chance(self):
        c = snap(
            backlog_repo(
                issue(1, author="eve"),
                issue(2),
                issue(3, created_at="2026-10-21T11:30:00Z"),
            ),
            cards=[card(2, "Done")],
        )
        self.assertEqual(
            [op for op in plan(c, empty(), NOW)["card_ops"] if op["op"] == "create"],
            [
                {
                    "op": "create",
                    "link": {"repo": "o/r", "number": 1, "type": "issue"},
                    "title": "Issue 1",
                    "status": "Backlog",
                    "labels": ["kind: backlog", "repo: r"],
                }
            ],
        )

    def test_pull_request_cards_open_and_close(self):
        def pr_card(n, status="In Review", repo="o/r"):
            link = {"repo": repo, "number": n, "type": "pr"}
            return owner_card(f"pr-{n}", f"PR {n}", status, ["kind: pr"], link)

        c = snap(
            backlog_repo(
                issue(9),
                other_prs=[
                    other_pr(1),
                    other_pr(2, draft=True),
                    other_pr(3, linked_issues=[9]),
                    other_pr(4),
                ],
            ),
            cards=[
                card(9, "Needs approval"),
                pr_card(4),
                pr_card(5),
                pr_card(6, "Done"),
                pr_card(7, repo="o/fork"),
            ],
        )
        self.assertEqual(
            plan(c, empty(), NOW)["card_ops"],
            [
                {
                    "op": "create",
                    "link": {"repo": "o/r", "number": 1, "type": "pr"},
                    "title": "PR 1",
                    "status": "In Review",
                    "labels": ["kind: pr", "repo: r"],
                },
                {
                    "op": "create",
                    "link": {"repo": "o/r", "number": 2, "type": "pr"},
                    "title": "PR 2",
                    "status": "In Review",
                    "labels": ["kind: pr", "repo: r", "draft"],
                },
                close("pr-5"),
            ],
        )

    def test_dependency_work_that_needs_the_owner_gets_a_card_until_resolved(self):
        L = empty()
        for key, status, note in [
            ("o/r:dep:pip:urllib3", "blocked", "no patched version\nmore"),
            ("o/r#1", "done", "review lane"),
            ("o/r#2", "done", "auto-merge lane"),
            ("o/r#3", "blocked", "conflicts"),
            ("o/r:fix-main", "blocked", "flaky"),
        ]:
            start(L, key, "t", NOW)
            finish(L, key, status, note, NOW)
        dependency = ["kind: dependency", "repo: r"]
        c = snap(
            backlog_repo(
                prs=[
                    pr(1, labels=["needs-review"]),
                    pr(2, checks="green"),
                    pr(3, labels=["agent:blocked"]),
                ],
                other_prs=[],
            ),
            cards=[
                owner_card("d-3", "o/r#3 — conflicts", "In Review", dependency),
                owner_card("d-4", "o/r#4 — gone", "Needs info", dependency),
                owner_card("d-5", "o/r:fix-main — flaky", "Needs info", dependency),
                owner_card("d-6", "o/r#6 — done", "Done", dependency),
            ],
        )
        c["repos"][0]["alerts"] = [alert()]
        c["repos"][0]["main"] = "red"
        self.assertEqual(
            plan(c, L, NOW)["card_ops"],
            [
                close("d-4"),
                {
                    "op": "create",
                    "link": None,
                    "title": "o/r:dep:pip:urllib3 — no patched version",
                    "status": "Needs info",
                    "labels": dependency,
                },
                {
                    "op": "create",
                    "link": {"repo": "o/r", "number": 1, "type": "pr"},
                    "title": "o/r#1 — review lane",
                    "status": "In Review",
                    "labels": dependency,
                },
                mv(3, "Needs info") | {"card": "d-3"},
            ],
        )

    def test_a_dependency_pr_in_the_review_lane_gets_a_dependency_card(self):
        L = empty()
        start(L, "o/r:dep:pip:urllib3", "t", NOW)
        finish(L, "o/r:dep:pip:urllib3", "done", "https://github.com/o/r/pull/7", NOW)
        c = snap(
            backlog_repo(other_prs=[other_pr(7, labels=["needs-review"])]),
        )
        c["repos"][0]["alerts"] = [alert()]
        self.assertEqual(
            plan(c, L, NOW)["card_ops"],
            [
                {
                    "op": "create",
                    "link": None,
                    "title": "o/r:dep:pip:urllib3 — https://github.com/o/r/pull/7",
                    "status": "In Review",
                    "labels": ["kind: dependency", "repo: r"],
                }
            ],
        )

    def test_an_alert_on_a_package_named_like_a_backlog_kind_is_dependency_work(self):
        L = empty()
        start(L, "o/r:dep:pip:build", "t", NOW)
        c = snap(backlog_repo(issue(1)), cards=[card(1)])
        c["repos"][0]["alerts"] = [alert("build")]
        self.assertEqual(picked(plan(c, L, NOW)), [(1, "triage", "topup")])
        L = empty()
        start(L, "o/r:dep:pip:build", "t", NOW)
        finish(L, "o/r:dep:pip:build", "blocked", "no patched version", NOW)
        c = snap(backlog_repo())
        c["repos"][0]["alerts"] = [alert("build")]
        self.assertEqual(
            plan(c, L, NOW)["card_ops"],
            [
                {
                    "op": "create",
                    "link": None,
                    "title": "o/r:dep:pip:build — no patched version",
                    "status": "Needs info",
                    "labels": ["kind: dependency", "repo: r"],
                }
            ],
        )

    def test_without_a_board_no_cards_are_made(self):
        L = empty()
        start(L, "o/r#1", "t", NOW)
        finish(L, "o/r#1", "blocked", "stuck", NOW)
        c = cands({**repo(prs=[pr(1)]), "other_prs": [other_pr(2)], "issues": []})
        self.assertEqual(plan(c, L, NOW)["card_ops"], [])


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


class ReviewFixTest(unittest.TestCase):
    def test_fix_main_survives_pending_main(self):
        L = empty()
        start(L, "o/r:fix-main", "t", NOW)
        finish(L, "o/r:fix-main", "blocked", "needs owner", NOW)
        plan(cands(repo(main="pending")), L, NOW)
        result = plan(cands(repo(main="red")), L, NOW)
        self.assertEqual(keys(result), [])
        self.assertEqual(L["tasks"]["o/r:fix-main"]["status"], "blocked")

    def test_release_notes_and_boilerplate_do_not_count_as_coverage(self):
        body = (
            "Bumps [requests](https://github.com/psf/requests) from 2.31.0 to 2.32.0.\n"
            "<details><summary>Release notes</summary>Pin urllib3 below 3.</details>\n"
            "- `@dependabot merge` will merge this PR after your CI passes on it"
        )
        prs = [
            {"number": 4, "title": "Bump requests from 2.31.0 to 2.32.0", "body": body}
        ]
        self.assertEqual(covering_pr("requests", prs), 4)
        self.assertIsNone(covering_pr("urllib3", prs))
        self.assertIsNone(covering_pr("merge", prs))

    def test_pr_covering_critical_alert_is_not_held(self):
        c = cands(
            repo(
                main="red",
                prs=[pr(7), pr(8)],
                alerts=[alert("x", "critical", covered_by_pr=7)],
            )
        )
        result = plan(c, empty(), NOW)
        self.assertEqual(keys(result), ["o/r#7", "o/r:fix-main"])
        self.assertEqual(result["held"], ["o/r#8"])


SCRIPT = Path(__file__).with_name("backlog.py")


def cli(ledger_path, *args):
    env = {
        **os.environ,
        "BACKLOG_LEDGER": str(ledger_path),
        "BACKLOG_WORK": str(Path(ledger_path).with_name("work")),
    }
    return subprocess.run(
        [sys.executable, SCRIPT, *args],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


class CliTest(unittest.TestCase):
    def test_plan_claims_then_start_and_finish_persist(self):
        with tempfile.TemporaryDirectory() as d:
            path, cpath = Path(d, "ledger.json"), Path(d, "c.json")
            cpath.write_text(json.dumps(cands(repo(prs=[pr(1)]))))
            out = json.loads(cli(path, "plan", str(cpath), "thread-1"))
            self.assertEqual(
                [(t["key"], t["attempt"]) for t in out["dispatch"]], [("o/r#1", 1)]
            )
            claimed = json.loads(cli(path, "running"))
            self.assertEqual(
                [(t["key"], t["task_id"], t["thread_id"]) for t in claimed],
                [("o/r#1", "", "thread-1")],
            )
            lock = Path(d, "work", "o-r-1.lock")
            cli(path, "start", "o/r#1", "task-abc")
            self.assertEqual(
                {
                    k: v
                    for k, v in json.loads(lock.read_text()).items()
                    if k != "started"
                },
                {"key": "o/r#1", "thread_id": "thread-1", "task_id": "task-abc"},
            )
            Path(d, "work", "o-r-1").mkdir()
            out = cli(path, "finish", "o/r#1", "done", "merged")
            entry = json.loads(path.read_text())["tasks"]["o/r#1"]
            self.assertEqual(out.strip(), "done")
            self.assertEqual(
                (entry["status"], entry["task_id"], entry["thread_id"], entry["note"]),
                ("done", "task-abc", "thread-1", "merged"),
            )
            self.assertEqual(list(Path(d, "work").iterdir()), [])

    def test_each_triage_outcome_becomes_its_card_move(self):
        expected = {
            "fix-pending": mv(5, "In Review"),
            "needs-approval": mv(5, "Needs approval"),
            "needs-repro": mv(5, "Needs info", add=["needs-repro"]),
            "unable-to-fix": mv(5, "Needs info", add=["unable-to-fix"]),
            "not-actionable": {"op": "close", "card": "card-5", "to": "Canceled"},
        }
        for outcome, op in expected.items():
            with self.subTest(outcome), tempfile.TemporaryDirectory() as d:
                path, cpath = Path(d, "ledger.json"), Path(d, "c.json")
                cpath.write_text(
                    json.dumps(snap(backlog_repo(issue(5)), cards=[asked(5)]))
                )
                out = json.loads(cli(path, "plan", str(cpath), "th"))
                self.assertEqual(keys(out), ["o/r#5:triage"])
                self.assertEqual(out["card_ops"], [mv(5, "In Progress")])
                cli(path, "start", "o/r#5:triage", "task-1")
                self.assertEqual(
                    cli(path, "finish", "o/r#5:triage", outcome, "note").strip(),
                    outcome,
                )
                entry = json.loads(path.read_text())["tasks"]["o/r#5:triage"]
                self.assertEqual(entry["status"], outcome)
                cpath.write_text(
                    json.dumps(snap(backlog_repo(issue(5)), cards=[working(asked(5))]))
                )
                out = json.loads(cli(path, "plan", str(cpath), "th"))
                self.assertEqual((out["dispatch"], out["card_ops"]), ([], [op]))

    def test_a_dependency_status_on_a_backlog_task_counts_as_a_failure(self):
        with tempfile.TemporaryDirectory() as d:
            path, cpath = Path(d, "ledger.json"), Path(d, "c.json")
            cpath.write_text(json.dumps(snap(backlog_repo(issue(5)), cards=[asked(5)])))
            cli(path, "plan", str(cpath), "th")
            cli(path, "start", "o/r#5:triage", "task-1")
            self.assertEqual(
                cli(path, "finish", "o/r#5:triage", "done", "x").strip(), "failed"
            )
            cpath.write_text(
                json.dumps(snap(backlog_repo(issue(5)), cards=[working(asked(5))]))
            )
            out = json.loads(cli(path, "plan", str(cpath), "th"))
            self.assertEqual(
                [(t["key"], t["attempt"]) for t in out["dispatch"]],
                [("o/r#5:triage", 2)],
            )

    def test_live_lock_blocks_dispatch_and_stale_lock_is_cleaned_up(self):
        with tempfile.TemporaryDirectory() as d:
            path, cpath, work = (
                Path(d, "ledger.json"),
                Path(d, "c.json"),
                Path(d, "work"),
            )
            cpath.write_text(json.dumps(cands(repo(prs=[pr(1), pr(2)]))))
            work.mkdir()
            now = datetime.now(UTC).isoformat()
            for key, slug, started in [
                ("o/r#1", "o-r-1", now),
                ("o/r#2", "o-r-2", "2026-01-01T00:00:00+00:00"),
            ]:
                lock = {"key": key, "thread_id": "x", "task_id": "", "started": started}
                Path(work, f"{slug}.lock").write_text(json.dumps(lock))
                Path(work, slug).mkdir()
            Path(work, "orphan").mkdir()
            Path(work, ".claude").mkdir()
            out = json.loads(cli(path, "plan", str(cpath), "th"))
            self.assertEqual([t["key"] for t in out["dispatch"]], ["o/r#2"])
            self.assertEqual(out["stale_locks"], ["o/r#2"])
            self.assertFalse(Path(work, "o-r-2").exists())
            self.assertFalse(Path(work, "orphan").exists())
            self.assertTrue(Path(work, "o-r-1").exists())
            self.assertTrue(Path(work, ".claude").exists())
            self.assertEqual(
                json.loads(Path(work, "o-r-2.lock").read_text())["thread_id"], "th"
            )

    def test_plan_prints_result_and_saves_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            path, cpath = Path(d, "ledger.json"), Path(d, "c.json")
            cpath.write_text(json.dumps(cands(repo(prs=[pr(1)]))))
            L = empty()
            start(L, "gone/repo#9", "t", NOW)
            finish(L, "gone/repo#9", "done", "", NOW)
            path.write_text(json.dumps(L))
            out = json.loads(cli(path, "plan", str(cpath), "th"))
            self.assertEqual([t["key"] for t in out["dispatch"]], ["o/r#1"])
            self.assertEqual(out["resolved"], ["gone/repo#9"])
            self.assertEqual(list(json.loads(path.read_text())["tasks"]), ["o/r#1"])

    def test_start_rejects_a_key_that_is_not_claimed(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "ledger.json")
            env = {
                **os.environ,
                "BACKLOG_LEDGER": str(path),
                "BACKLOG_WORK": str(Path(path).with_name("work")),
            }
            r = subprocess.run(
                [sys.executable, SCRIPT, "start", "o/r#1", "t"],
                env=env,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(r.returncode, 0)

    def test_running_lists_tasks_with_overdue_flag(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "ledger.json")
            L = empty()
            start(L, "o/r#1", "old", datetime(2026, 1, 1, tzinfo=UTC))
            start(L, "o/r#2", "new", datetime.now(UTC), "th-2")
            path.write_text(json.dumps(L))
            out = json.loads(cli(path, "running"))
            self.assertEqual(
                [(t["key"], t["task_id"], t["thread_id"], t["overdue"]) for t in out],
                [("o/r#1", "old", "", True), ("o/r#2", "new", "th-2", False)],
            )

    def test_overlapping_plans_dispatch_each_task_once(self):
        with tempfile.TemporaryDirectory() as d:
            path, cpath = Path(d, "ledger.json"), Path(d, "c.json")
            # Many candidates make each plan slow enough for the runs to overlap.
            cpath.write_text(json.dumps(cands(repo(prs=[pr(n) for n in range(5000)]))))
            env = {
                **os.environ,
                "BACKLOG_LEDGER": str(path),
                "BACKLOG_WORK": str(Path(path).with_name("work")),
            }
            procs = [
                subprocess.Popen(
                    [sys.executable, SCRIPT, "plan", str(cpath), f"th{n}"],
                    env=env,
                    stdout=subprocess.PIPE,
                )
                for n in range(10)
            ]
            dispatched = [
                t["key"]
                for p in procs
                for t in json.loads(p.communicate()[0])["dispatch"]
            ]
            self.assertEqual(len(dispatched), 3)
            self.assertEqual(len(set(dispatched)), 3)

    def test_finish_reads_note_from_stdin(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "ledger.json")
            L = empty()
            start(L, "o/r#1", "t", NOW)
            path.write_text(json.dumps(L))
            env = {
                **os.environ,
                "BACKLOG_LEDGER": str(path),
                "BACKLOG_WORK": str(Path(path).with_name("work")),
            }
            note = "it's $(rm -rf /) `x`"
            subprocess.run(
                [sys.executable, SCRIPT, "finish", "o/r#1", "done", "-"],
                input=note,
                env=env,
                text=True,
                check=True,
            )
            self.assertEqual(
                json.loads(path.read_text())["tasks"]["o/r#1"]["note"], note
            )

    def test_apply_with_no_card_ops_needs_no_tracker(self):
        with tempfile.TemporaryDirectory() as d:
            ppath = Path(d, "plan.json")
            ppath.write_text(json.dumps({"dispatch": [], "card_ops": []}))
            out = subprocess.run(
                [sys.executable, SCRIPT, "apply", str(ppath)],
                env={**os.environ, "HOME": d},
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            self.assertEqual(json.loads(out), [])


if __name__ == "__main__":
    unittest.main()


class LinearRateLimitTest(unittest.TestCase):
    def test_graphql_waits_out_a_rate_limit(self):
        limited = urllib.error.HTTPError(linear.API, 429, "ratelimited", {}, None)
        limited.read = lambda: b"ratelimit exceeded"
        ok = mock.MagicMock()
        ok.__enter__.return_value.read.return_value = b'{"data": {"x": 1}}'
        with (
            mock.patch.object(linear, "_token", "t"),
            mock.patch("urllib.request.urlopen", side_effect=[limited, ok]),
            mock.patch("time.sleep") as sleep,
        ):
            self.assertEqual(linear.graphql("q"), {"x": 1})
        sleep.assert_called_once()
