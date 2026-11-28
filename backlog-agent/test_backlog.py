import http.client
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from datetime import UTC, datetime, timedelta
from email.message import Message
from pathlib import Path
from unittest import mock

import backlog
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
        "default_branch": "main",
        "prs_cut": False,
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

    def test_a_token_near_expiry_is_announced_once_a_day(self):
        soon = (NOW + timedelta(days=3)).isoformat()
        c = dict(cands(repo()), token_expires=soon)
        L = empty()
        self.assertEqual(plan(c, L, NOW)["rotate_token"], soon)
        self.assertIsNone(plan(c, L, NOW)["rotate_token"])
        self.assertEqual(plan(c, L, NOW + timedelta(days=1))["rotate_token"], soon)
        later = dict(c, token_expires=(NOW + timedelta(days=30)).isoformat())
        self.assertIsNone(plan(later, empty(), NOW)["rotate_token"])
        self.assertIsNone(plan(cands(repo()), empty(), NOW)["rotate_token"])

    def test_token_expiry_is_read_from_githubs_header(self):
        reply = subprocess.CompletedProcess(
            [],
            0,
            "HTTP/2.0 200 OK\r\n"
            "Github-Authentication-Token-Expiration: 2026-12-20 09:18:09 UTC\r\n"
            "\r\n{}",
            "",
        )
        with mock.patch("subprocess.run", return_value=reply):
            self.assertEqual(backlog.token_expiry(), "2026-12-20T09:18:09+00:00")
        with mock.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess(
                [], 0, "HTTP/2.0 200 OK\r\n\r\n{}", ""
            ),
        ):
            self.assertIsNone(backlog.token_expiry())

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
            repo(
                "o/a", main="red", prs=[pr(9), pr(10)], alerts=[alert(covered_by_pr=9)]
            ),
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
                "history": [
                    {"from": "Backlog", "to": status, "actor": "owner", "at": at}
                ]
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
        "attachments": [f"att-{number}"],
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

    def test_an_issue_a_scan_filed_waits_for_the_topup(self):
        found = {**issue(1, created_at="2026-10-21T11:00:00Z"), "by_agent": True}
        c = snap(
            backlog_repo(found, issue(2)), cards=[card(1), card(2, "Needs approval")]
        )
        result = plan(c, empty(), NOW)
        self.assertEqual((result["dispatch"], result["card_ops"]), ([], []))

        c = snap(backlog_repo(found), cards=[card(1)])
        self.assertEqual(picked(plan(c, empty(), NOW)), [(1, "triage", "topup")])

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


def active(name="o/r", head="h1", head_at="2026-10-20T00:00:00Z", issues=(), **kw):
    return {
        **backlog_repo(*issues, name=name),
        "head": head,
        "head_at": head_at,
        **kw,
    }


def found(number):
    return {**issue(number), "by_agent": True}


def last_scan(at, head="h0"):
    return {"at": at, "until": at, "head": head}


def taken(c, when):
    return {**c, "generated": when.isoformat()}


def parked(
    status,
    created_at="2026-10-21T12:30:00Z",
    title="o/r:scan — note",
    kind="kind: dependency",
):
    return {
        **owner_card("c1", title, status, [kind, "repo: r"]),
        "created_at": created_at,
    }


def scanned(key, status, at=NOW, head="h1", took=timedelta(0)):
    L = empty()
    start(L, key, "t", at)
    L["tasks"][key]["head"] = head
    finish(L, key, status, "note", at + took)
    return L


class ScanPlanTest(unittest.TestCase):
    def test_an_active_repo_never_scanned_gets_a_scan(self):
        L = empty()
        result = plan(cands(active()), L, NOW)
        self.assertEqual(
            result["dispatch"],
            [
                {
                    "repo": "o/r",
                    "key": "o/r:scan",
                    "kind": "scan",
                    "head": "h1",
                    "limit": 3,
                    "profile": True,
                    "attempt": 1,
                }
            ],
        )
        self.assertEqual(L["tasks"]["o/r:scan"]["head"], "h1")

    def test_a_repo_is_scanned_when_its_default_branch_moved_and_a_week_passed(self):
        old, recent = "2026-10-13T00:00:00+00:00", "2026-10-15T00:00:00+00:00"
        cases = {
            "no default branch": (repo(), None, []),
            "quiet for a month": (
                active(head_at="2026-09-21T11:00:00Z"),
                None,
                ["o/r:scan"],
            ),
            "fork": (active(fork=True), None, []),
            "head not moved": (active(), last_scan(old, head="h1"), []),
            "scanned this week": (active(), last_scan(recent), []),
            "due again": (active(), last_scan(old), ["o/r:scan"]),
        }
        for name, (r, last, expected) in cases.items():
            with self.subTest(name):
                L = empty()
                if last:
                    L["scans"] = {"o/r": last}
                self.assertEqual(keys(plan(cands(r), L, NOW)), expected)

    def test_scans_rank_last_and_run_one_at_a_time(self):
        c = cands(active("b/y"), active("a/x"), repo(prs=[pr(1)]))
        L = empty()
        self.assertEqual(keys(plan(c, L, NOW)), ["o/r#1", "a/x:scan"])
        self.assertEqual(keys(plan(c, L, NOW)), [])
        self.assertEqual(
            keys(plan(c, empty(), NOW, busy=frozenset({"a/x:scan"}))), ["o/r#1"]
        )

    def test_a_dormant_repo_gets_its_first_scan_before_any_repo_gets_a_second(self):
        dormant = active("a/x", head_at="2026-09-21T11:00:00Z")
        month_to_the_hour = active("a/x", head_at="2026-09-21T12:00:00Z")
        L = empty()
        L["scans"] = {"b/y": last_scan("2026-10-13T00:00:00+00:00")}
        self.assertEqual(
            keys(plan(cands(dormant, active("b/y")), L, NOW)), ["a/x:scan"]
        )
        for first, expected in ((dormant, "b/y:scan"), (month_to_the_hour, "a/x:scan")):
            c = cands(first, active("b/y"))
            self.assertEqual(keys(plan(c, empty(), NOW)), [expected])
        failed = scanned("a/x:scan", "failed")
        self.assertEqual(
            keys(plan(cands(dormant, active("b/y")), failed, NOW)), ["a/x:scan"]
        )
        failed = scanned("a/x:scan", "failed")
        failed["scans"] = {"a/x": last_scan("2026-10-13T00:00:00+00:00")}
        self.assertEqual(
            keys(plan(cands(dormant, active("b/y")), failed, NOW)), ["a/x:scan"]
        )

    def test_a_dormant_repo_waits_no_longer_than_others_for_its_next_scan(self):
        L = empty()
        L["scans"] = {
            "a/x": last_scan("2026-10-13T00:00:00+00:00"),
            "b/y": last_scan("2026-08-01T00:00:00+00:00"),
        }
        c = cands(active("a/x"), active("b/y", head_at="2026-09-20T12:00:00Z"))
        self.assertEqual(keys(plan(c, L, NOW)), ["b/y:scan"])

    def test_the_repo_scanned_longest_ago_goes_first(self):
        c = cands(active("a/x"), active("b/y"), active("c/z"))
        L = empty()
        L["scans"] = {
            "a/x": last_scan("2026-10-10T00:00:00+00:00"),
            "b/y": last_scan("2026-10-01T00:00:00+00:00"),
        }
        self.assertEqual(keys(plan(c, L, NOW)), ["c/z:scan"])
        L["scans"]["c/z"] = last_scan(NOW.isoformat())
        L["tasks"].clear()
        self.assertEqual(keys(plan(c, L, NOW)), ["b/y:scan"])

    def test_scans_stop_while_three_findings_wait_for_the_owner(self):
        waiting = snap(
            active(issues=[found(1), issue(2)]),
            active("a/x", issues=[found(3), found(4)]),
            cards=[
                card(1, "In Review"),
                card(3, "In Review", repo="a/x"),
                card(3, "Canceled", repo="a/x", type="pr"),
                card(4, "Canceled", repo="a/x"),
            ],
        )
        result = plan(waiting, empty(), NOW)
        self.assertEqual(
            [(t["key"], t["limit"]) for t in result["dispatch"]], [("a/x:scan", 1)]
        )

        full = cands(active(issues=[found(1), found(2), found(3)]), active("a/x"))
        self.assertEqual(keys(plan(full, empty(), NOW)), [])

        recarded = snap(
            active(issues=[found(1), found(2), found(3)]),
            active("a/x"),
            cards=[card(1, "Canceled"), {**card(1, "In Review"), "id": "card-9"}],
        )
        self.assertEqual(keys(plan(recarded, empty(), NOW)), [])

    def test_a_finished_scan_is_remembered_and_leaves_the_ledger(self):
        L = scanned("o/r:scan", "done")
        result = plan(cands(active()), L, NOW + timedelta(hours=49))
        at = NOW.isoformat()
        self.assertEqual(L["scans"], {"o/r": {"at": at, "until": at, "head": "h1"}})
        self.assertEqual(L["tasks"], {})
        self.assertEqual(
            (result["dispatch"], result["resolved"], result["newly_blocked"]),
            ([], [], []),
        )

    def test_a_commit_that_landed_while_the_scan_ran_is_scanned_next_time(self):
        L = scanned("o/r:scan", "done", took=timedelta(hours=3))
        later = NOW + timedelta(days=6)
        moved_on = taken(
            cands(active(head="h2", head_at="2026-10-21T12:30:00Z")), later
        )
        self.assertEqual(keys(plan(moved_on, L, later)), [])
        self.assertEqual(keys(plan(moved_on, L, NOW + timedelta(days=7))), ["o/r:scan"])

    def test_a_blocked_scan_stays_on_the_board_until_the_owner_closes_its_card(self):
        L = scanned("o/r:scan", "blocked")
        result = plan(snap(active(), active("a/x")), L, NOW)
        self.assertEqual(
            ops_of(result, "create"),
            [
                {
                    "op": "create",
                    "link": None,
                    "title": "o/r:scan — note",
                    "status": "Needs info",
                    "labels": ["kind: dependency", "repo: r"],
                }
            ],
        )
        self.assertEqual(keys(result), ["a/x:scan"])

        cases = {
            "open card": (parked("Needs info"), "h2", True, []),
            "card from an earlier time": (
                parked("Canceled", created_at="2026-10-01T00:00:00Z"),
                "h1",
                True,
                ["create"],
            ),
            "another task's closed card": (
                parked("Canceled", title="o/r:add-ci — note"),
                "h1",
                True,
                ["create"],
            ),
            "a closed card of another kind": (
                parked("Canceled", kind="kind: pr"),
                "h1",
                True,
                ["create"],
            ),
            "closed card": (parked("Canceled"), "h1", False, []),
        }
        for name, (c, head, kept, ops) in cases.items():
            with self.subTest(name):
                L = scanned("o/r:scan", "blocked")
                result = plan(snap(active(head=head), cards=[c]), L, NOW)
                self.assertEqual("o/r:scan" in L["tasks"], kept)
                self.assertEqual("o/r" in L["scans"], not kept)
                self.assertEqual([op["op"] for op in result["card_ops"]], ops)
                self.assertEqual(keys(result), [])

    def test_a_profile_pull_request_gets_its_own_card_beside_a_blocked_scan(self):
        L = scanned("o/r:scan", "blocked")
        L["tasks"]["o/r:scan"]["note"] = "held back; https://github.com/o/n/pull/9"
        profile = other_pr(9, labels=["needs-review"])
        c = snap(active(), backlog_repo(name="o/n", other_prs=[profile]))
        self.assertEqual(
            sorted(
                (op["status"], op["labels"][0])
                for op in ops_of(plan(c, L, NOW), "create")
            ),
            [("In Review", "kind: pr"), ("Needs info", "kind: dependency")],
        )

    def test_no_profile_pull_request_while_the_review_cap_is_full(self):
        full = plan(cands(active(), needs_review_open=5), empty(), NOW)
        self.assertEqual(
            [(t["key"], t["profile"]) for t in full["dispatch"]], [("o/r:scan", False)]
        )

    def test_a_run_that_could_not_read_the_board_leaves_a_blocked_scan_alone(self):
        for status in ("blocked", "failed"):
            with self.subTest(status):
                L = scanned("o/r:scan", "failed")
                plan(cands(active()), L, NOW)
                finish(L, "o/r:scan", status, "note", NOW)
                plan({**cands(active()), "owner": OWNER}, L, NOW)
                entry = L["tasks"]["o/r:scan"]
                self.assertEqual((entry["status"], entry["attempts"]), ("blocked", 2))
                self.assertEqual(L["scans"], {})

    def test_a_scan_that_failed_twice_is_tried_again_once_the_owner_closes_its_card(
        self,
    ):
        L = scanned("o/r:scan", "failed")
        c = snap(active())
        result = plan(c, L, NOW)
        self.assertEqual(
            [(t["key"], t["attempt"]) for t in result["dispatch"]], [("o/r:scan", 2)]
        )
        self.assertEqual(finish(L, "o/r:scan", "failed", "again", NOW), "blocked")
        self.assertEqual(keys(plan(c, L, NOW)), [])
        self.assertEqual(L["tasks"]["o/r:scan"]["status"], "blocked")

        closed = snap(active(head="h2"), cards=[parked("Canceled")])
        result = plan(closed, L, NOW)
        self.assertEqual(
            [(t["key"], t["attempt"]) for t in result["dispatch"]], [("o/r:scan", 1)]
        )
        self.assertEqual(L["scans"], {})

    def test_a_repo_that_left_scope_is_forgotten(self):
        L = scanned("gone/r:scan", "failed")
        L["scans"] = {"gone/r": last_scan(NOW.isoformat())}
        L["found"] = ["gone/r#1", "o/r#9"]
        plan(cands(repo()), L, NOW)
        self.assertEqual((L["tasks"], L["scans"], L["found"]), ({}, {}, []))

    def test_an_alert_for_a_package_named_scan_is_not_a_scan(self):
        L = scanned("o/r:dep:pip:scan", "done")
        plan(cands(repo(alerts=[alert(package="scan")])), L, NOW)
        self.assertEqual(L["tasks"]["o/r:dep:pip:scan"]["status"], "done")
        self.assertEqual(L["scans"], {})

    def test_a_scan_never_pushes_a_repo_past_the_issues_collect_reads(self):
        crowded = active(issues=[issue(n) for n in range(98)])
        self.assertEqual(keys(plan(cands(crowded), empty(), NOW)), [])
        roomy = active(issues=[issue(n) for n in range(97)])
        self.assertEqual(keys(plan(cands(roomy), empty(), NOW)), ["o/r:scan"])

    def test_no_scan_starts_from_a_snapshot_older_than_the_last_scan(self):
        L = scanned("o/r:scan", "done", at=NOW + timedelta(minutes=5))
        stale, later = cands(active(), active("a/x")), NOW + timedelta(minutes=10)
        for _ in range(2):
            self.assertEqual(keys(plan(stale, L, later)), [])
            self.assertEqual((list(L["tasks"]), L["scans"]), (["o/r:scan"], {}))
        self.assertEqual(keys(plan(taken(stale, later), L, later)), ["a/x:scan"])
        self.assertEqual(list(L["scans"]), ["o/r"])

        L = empty()
        L["scans"] = {"o/r": last_scan(later.isoformat(), "h1")}
        L["found"] = ["o/r#7"]
        self.assertEqual(keys(plan(stale, L, later)), [])
        self.assertEqual(L["found"], ["o/r#7"])

        L = scanned(
            "o/r:scan",
            "done",
            at=NOW - timedelta(minutes=10),
            took=timedelta(minutes=20),
        )
        plan(taken(cands(active()), later), L, later)
        L["found"] = ["o/r#7"]
        plan(cands(active()), L, later)
        self.assertEqual(L["found"], ["o/r#7"])

    def test_an_owner_issue_filed_while_a_scan_ran_counts_as_a_finding(self):
        ten = datetime(2026, 10, 21, 10, tzinfo=UTC)
        during = issue(1, created_at="2026-10-21T11:00:00Z")
        before = issue(1, created_at="2026-10-21T09:00:00Z")

        def running():
            L = empty()
            start(L, "o/r:scan", "t", ten)
            L["tasks"]["o/r:scan"]["head"] = "h1"
            return L

        def planned(first, ledger, name="o/r"):
            c = snap(
                active(),
                active(name, issues=[first, issue(2)]),
                cards=[card(1, repo=name), card(2, "Needs approval", repo=name)],
            )
            return picked(plan(c, ledger, NOW))

        L = running()
        self.assertEqual(planned(during, L), [])
        self.assertEqual(L["found"], ["o/r#1"])
        finish(L, "o/r:scan", "done", "x", NOW)
        self.assertEqual(planned(during, L), [])
        self.assertEqual(planned(during, L), [])

        self.assertEqual(planned(before, running()), [(1, "triage", "owner")])
        over = running()
        finish(over, "o/r:scan", "done", "x", ten + timedelta(minutes=30))
        self.assertEqual(planned(during, over), [(1, "triage", "owner")])
        self.assertEqual(planned(during, running(), "a/x"), [(1, "triage", "owner")])

    def test_an_issue_from_a_failed_attempt_is_still_a_finding_after_the_retry(self):
        L = scanned(
            "o/r:scan",
            "failed",
            at=datetime(2026, 10, 21, 9, tzinfo=UTC),
            took=timedelta(hours=1),
        )
        c = snap(
            active(issues=[issue(1, created_at="2026-10-21T09:30:00Z"), issue(2)]),
            cards=[card(1), card(2, "Needs approval")],
        )
        self.assertEqual(
            [(t["key"], t["limit"]) for t in plan(c, L, NOW)["dispatch"]],
            [("o/r:scan", 2)],
        )
        self.assertEqual(keys(plan(c, L, NOW + timedelta(minutes=30))), [])

    def test_only_the_owners_issues_from_a_scan_count_toward_the_limit(self):
        def limits(author):
            L = scanned("o/r:scan", "done", at=NOW - timedelta(hours=2))
            filed = issue(1, author=author, created_at="2026-10-21T10:00:00Z")
            c = snap(active(issues=[filed]), active("a/x"))
            result = plan(c, L, NOW)
            return L["found"], [(t["key"], t["limit"]) for t in result["dispatch"]]

        self.assertEqual(limits(OWNER), (["o/r#1"], [("a/x:scan", 2)]))
        self.assertEqual(limits("eve"), ([], [("a/x:scan", 3)]))

    def test_only_the_owners_issue_starting_with_the_marker_is_the_agents(self):
        def by_agent(body, author=OWNER):
            node = {
                "number": 1,
                "title": "t",
                "body": body,
                "createdAt": "2026-10-01T00:00:00Z",
                "author": {"login": author},
                "labels": {"nodes": []},
                "comments": {"nodes": []},
            }
            return backlog.issue_record(node, OWNER)["by_agent"]

        self.assertTrue(by_agent("<!-- backlog-agent -->\nx"))
        self.assertTrue(by_agent("\n <!-- backlog-agent -->\nx"))
        self.assertFalse(by_agent("see <!-- backlog-agent -->"))
        self.assertFalse(by_agent("<!-- backlog-agent -->\nx", author="mallory"))

    def test_sweep_keeps_the_clone_of_a_task_on_a_repo_with_a_dot_in_its_name(self):
        with tempfile.TemporaryDirectory() as d:
            work, key = Path(d), "o/o.github.io:scan"
            entry = {"thread_id": "", "task_id": "", "started": NOW.isoformat()}
            backlog.write_lock(work, key, entry)
            clone = work / "o-o.github.io-scan"
            clone.mkdir()
            busy, _ = backlog.sweep_locks(work, NOW)
            self.assertEqual((busy, clone.is_dir()), ({key}, True))


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


def twin(number, status="Backlog", attachments=(), created_at="2026-10-01T00:00:00Z"):
    return {
        **card(number, status),
        "id": f"card-{number}b",
        "identifier": f"JGA-{number}00",
        "created_at": created_at,
        "attachments": list(attachments),
    }


def ops_of(result, kind):
    return [op for op in result["card_ops"] if op["op"] == kind]


class CardGuardrailTest(unittest.TestCase):
    def test_a_second_open_card_for_an_issue_is_dropped_as_a_duplicate(self):
        c = snap(backlog_repo(issue(5)), cards=[card(5), twin(5, attachments=["x"])])
        result = plan(c, empty(), NOW)
        self.assertEqual(
            ops_of(result, "drop"),
            [
                {
                    "op": "drop",
                    "card": "card-5b",
                    "attachments": ["x"],
                    "to": "Duplicate",
                }
            ],
        )
        self.assertNotIn("card-5b", {op["card"] for op in ops_of(result, "move")})

    def test_the_card_the_owner_moved_on_is_kept(self):
        c = snap(backlog_repo(issue(5)), cards=[card(5), twin(5, "Needs info")])
        self.assertEqual(
            [op["card"] for op in ops_of(plan(c, empty(), NOW), "drop")], ["card-5"]
        )

    def test_closed_cards_are_never_dropped(self):
        c = snap(
            backlog_repo(issue(5)), cards=[card(5, "Done"), twin(5, attachments=["x"])]
        )
        self.assertEqual(ops_of(plan(c, empty(), NOW), "drop"), [])

    def test_an_open_issue_whose_cards_are_all_duplicates_gets_a_new_card(self):
        c = snap(backlog_repo(issue(5)), cards=[card(5, "Duplicate")])
        self.assertEqual(
            [op["link"]["number"] for op in ops_of(plan(c, empty(), NOW), "create")],
            [5],
        )

    def test_a_card_that_lost_its_github_link_is_linked_again(self):
        c = snap(backlog_repo(issue(5)), cards=[{**card(5), "attachments": []}])
        self.assertEqual(
            ops_of(plan(c, empty(), NOW), "link"),
            [
                {
                    "op": "link",
                    "card": "card-5",
                    "link": {"repo": "o/r", "number": 5, "type": "issue"},
                }
            ],
        )


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


HEAD = "a" * 40
REVIEW = "o/r#12:review"
LATER = NOW + timedelta(hours=2)


def agent_pr(
    number=12, issues=(1,), head=HEAD, draft=False, labels=(), linked_refs=(), **review
):
    return {
        **other_pr(number, draft, issues, linked_refs, labels),
        "head": head,
        "base": "main",
        "state": "OPEN",
        "merge_state": "CLEAN",
        "review": {
            "checks": "green",
            "pushed_at": "2026-10-21T10:00:00Z",
            "files": ["a.py"],
            "changed_files": 1,
            "lines": 7,
            "unresolved": 0,
            "feedback_at": None,
            "owner_feedback_at": None,
            **review,
        },
    }


def reviewable(*prs, cards=None):
    prs = prs or (agent_pr(),)
    numbers = sorted({n for p in prs for n in p["linked_issues"]})
    return snap(
        backlog_repo(*map(issue, numbers), other_prs=prs),
        cards=[card(n, "In Review") for n in numbers] if cards is None else cards,
    )


def reviewed(
    status="no-land", note=f"not_trivial {HEAD}", runs=1, took=timedelta(minutes=20)
):
    L = empty()
    for _ in range(runs):
        start(L, REVIEW, "t", NOW)
        finish(L, REVIEW, status, note, NOW + took)
    return L


def rounds(result):
    return [(t["key"], t["round"]) for t in result["dispatch"]]


def without(record, field):
    return {k: v for k, v in record.items() if k != field}


class ReviewPlanTest(unittest.TestCase):
    def test_an_agent_pull_request_whose_card_is_in_review_gets_a_review_run(self):
        L = empty()
        result = plan(reviewable(), L, NOW)
        self.assertEqual(
            result["dispatch"],
            [
                {
                    "key": REVIEW,
                    "kind": "review",
                    "repo": "o/r",
                    "pr": 12,
                    "head": HEAD,
                    "round": 1,
                    "attempt": 1,
                }
            ],
        )
        self.assertEqual(result["card_ops"], [])
        self.assertEqual(L["tasks"][REVIEW]["status"], "running")

    def test_a_pull_request_the_agent_did_not_open_gets_no_review(self):
        c = reviewable(other_pr(12, linked_issues=[1]))
        self.assertEqual(keys(plan(c, empty(), NOW)), [])

    def test_a_reviewed_head_is_not_reviewed_again(self):
        for status, note in [
            ("no-land", f"not_trivial {HEAD}"),
            ("land", f"clean {HEAD}"),
        ]:
            with self.subTest(status):
                L = reviewed(status, note)
                self.assertEqual(keys(plan(reviewable(), L, LATER)), [])

    def test_a_failed_review_is_run_again(self):
        L = reviewed("failed", f"review_error {HEAD}")
        self.assertEqual(rounds(plan(reviewable(), L, LATER)), [(REVIEW, 2)])

    def test_a_new_head_is_reviewed(self):
        c = reviewable(agent_pr(head="b" * 40))
        self.assertEqual(rounds(plan(c, reviewed(), LATER)), [(REVIEW, 2)])

    def test_feedback_given_after_a_review_started_starts_another(self):
        for at, expected in [
            ("2026-10-21T11:00:00Z", []),
            ("2026-10-21T12:10:00Z", [(REVIEW, 2)]),
            ("2026-10-21T13:00:00Z", [(REVIEW, 2)]),
        ]:
            with self.subTest(at):
                c = reviewable(agent_pr(feedback_at=at))
                self.assertEqual(rounds(plan(c, reviewed(), LATER)), expected)

    def test_red_checks_on_a_push_made_after_a_review_started_start_another(self):
        for checks, pushed_at, expected in [
            ("red", "2026-10-21T13:00:00Z", [(REVIEW, 2)]),
            ("red", "2026-10-21T12:10:00Z", [(REVIEW, 2)]),
            ("red", "2026-10-21T11:00:00Z", []),
            ("green", "2026-10-21T13:00:00Z", []),
        ]:
            with self.subTest(checks=checks, pushed_at=pushed_at):
                c = reviewable(agent_pr(checks=checks, pushed_at=pushed_at))
                self.assertEqual(rounds(plan(c, reviewed(), LATER)), expected)

    def test_without_a_board_no_review_starts(self):
        c = without(reviewable(), "board")
        self.assertEqual(keys(plan(c, empty(), NOW)), [])

    def test_no_review_starts_until_every_linked_issue_has_a_card_in_review(self):
        both = agent_pr(issues=[1, 2])
        far = agent_pr(linked_refs=["o/x#5"])
        cases = {
            "no card": (agent_pr(), []),
            "in progress": (agent_pr(), [card(1, "In Progress")]),
            "done": (agent_pr(), [card(1, "Done")]),
            "a pull request card": (agent_pr(), [card(1, "In Review", type="pr")]),
            "one of two": (both, [card(1, "In Review"), card(2, "Needs approval")]),
            "an issue of another repo": (far, [card(1, "In Review")]),
        }
        for name, (p, cards) in cases.items():
            with self.subTest(name):
                c = reviewable(p, cards=cards)
                self.assertEqual(keys(plan(c, empty(), NOW)), [])
        c = reviewable(
            far, cards=[card(1, "In Review"), card(5, "In Review", repo="o/x")]
        )
        self.assertEqual(keys(plan(c, empty(), NOW)), [REVIEW])

    def test_a_pull_request_the_owner_holds_gets_no_review(self):
        for p, expected in [
            (agent_pr(draft=True), []),
            (agent_pr(labels=["agent:hold"]), []),
            (agent_pr(labels=["agent:blocked"]), []),
            (agent_pr(labels=["needs-review"]), [REVIEW]),
        ]:
            with self.subTest(draft=p["draft"], labels=p["labels"]):
                self.assertEqual(keys(plan(reviewable(p), empty(), NOW)), expected)

    def test_a_review_waits_while_the_checks_are_pending(self):
        for checks, expected in [
            ("pending", []),
            ("green", [REVIEW]),
            ("red", [REVIEW]),
            (None, [REVIEW]),
        ]:
            with self.subTest(checks):
                c = reviewable(agent_pr(checks=checks))
                self.assertEqual(keys(plan(c, empty(), NOW)), expected)

    def test_a_review_waits_half_an_hour_after_the_last_push_or_feedback(self):
        for p, expected in [
            (agent_pr(pushed_at="2026-10-21T11:45:00Z"), []),
            (agent_pr(pushed_at="2026-10-21T11:31:00Z"), []),
            (agent_pr(feedback_at="2026-10-21T11:45:00Z"), []),
            (
                agent_pr(
                    pushed_at="2026-10-21T11:45:00Z", feedback_at="2026-10-21T11:00:00Z"
                ),
                [],
            ),
            (
                agent_pr(
                    pushed_at="2026-10-21T11:30:00Z", feedback_at="2026-10-21T11:00:00Z"
                ),
                [REVIEW],
            ),
        ]:
            with self.subTest(p["review"]):
                self.assertEqual(keys(plan(reviewable(p), empty(), NOW)), expected)

    def test_a_review_waits_for_the_triage_and_build_runs_of_its_issue(self):
        for kind in ("triage", "build"):
            for status in ("running", "failed"):
                with self.subTest(kind=kind, status=status):
                    L = empty()
                    start(L, f"o/r#1:{kind}", "t", NOW)
                    if status == "failed":
                        finish(L, f"o/r#1:{kind}", "failed", "boom", NOW)
                    self.assertEqual(keys(plan(reviewable(), L, NOW)), [])

    def test_one_review_runs_at_a_time(self):
        c = reviewable(agent_pr(12), agent_pr(13, issues=[2]))
        L = empty()
        result = plan(c, L, NOW)
        self.assertEqual((keys(result), result["queued"]), ([REVIEW], []))
        self.assertEqual(keys(plan(c, L, NOW)), [])
        claimed = frozenset({REVIEW})
        self.assertEqual(keys(plan(c, empty(), NOW, busy=claimed)), [])

    def test_the_pull_request_that_waited_longest_is_reviewed_first(self):
        c = reviewable(
            agent_pr(
                12, pushed_at="2026-10-21T09:00:00Z", feedback_at="2026-10-21T11:00:00Z"
            ),
            agent_pr(13, issues=[2], pushed_at="2026-10-21T10:00:00Z"),
        )
        self.assertEqual(keys(plan(c, empty(), NOW)), ["o/r#13:review"])

    def test_a_pull_request_gets_three_review_runs(self):
        c = reviewable(agent_pr(head="b" * 40))
        self.assertEqual(rounds(plan(c, reviewed(runs=2), LATER)), [(REVIEW, 3)])
        self.assertEqual(keys(plan(c, reviewed(runs=3), LATER)), [])
        failed = reviewed("failed", f"review_error {HEAD}", runs=3)
        self.assertEqual(keys(plan(reviewable(), failed, LATER)), [])
        red = reviewable(agent_pr(checks="red", pushed_at="2026-10-21T13:00:00Z"))
        self.assertEqual(keys(plan(red, reviewed(runs=3), LATER)), [])

    def test_the_owners_feedback_after_the_last_review_ended_buys_another_run(self):
        during, after = "2026-10-21T12:10:00Z", "2026-10-21T12:30:00Z"
        cases = {
            "the owner, after it ended": (after, after, [(REVIEW, 4)]),
            "the owner, while it ran": (during, during, []),
            "a bot, after it ended": (after, during, []),
        }
        for name, (feedback_at, owner_feedback_at, expected) in cases.items():
            with self.subTest(name):
                p = agent_pr(
                    feedback_at=feedback_at, owner_feedback_at=owner_feedback_at
                )
                L = reviewed(runs=3, took=timedelta(minutes=20))
                self.assertEqual(rounds(plan(reviewable(p), L, LATER)), expected)

    def test_a_review_runs_after_dependency_work_and_before_builds_and_triage(self):
        approved = card(6, "Approved", [moved("Needs approval", "Approved", "owner")])
        c = snap(
            backlog_repo(
                issue(1), issue(5), issue(6), prs=[pr(7)], other_prs=[agent_pr()]
            ),
            cards=[card(1, "In Review"), asked(5), approved],
        )
        result = plan(c, empty(), NOW)
        self.assertEqual(keys(result), ["o/r#7", REVIEW, "o/r#6:build"])
        self.assertEqual(result["queued"], ["o/r#5:triage"])

    def test_a_review_counts_against_the_daily_cap(self):
        L = empty()
        plan(reviewable(), L, NOW)
        self.assertEqual(used_today(L, NOW), 1)
        L = empty()
        L["starts"] = [NOW.isoformat()] * backlog.DAILY_CAP
        result = plan(reviewable(), L, NOW)
        self.assertEqual((keys(result), result["queued"]), ([], [REVIEW]))

    def test_a_review_entry_lives_as_long_as_its_pull_request_is_open(self):
        L = reviewed("land", f"clean {HEAD}")
        plan(reviewable(), L, LATER)
        self.assertEqual(L["tasks"][REVIEW]["status"], "land")
        closed = snap(backlog_repo(issue(1)), cards=[card(1, "In Review")])
        result = plan(closed, L, LATER)
        self.assertEqual((L["tasks"], result["resolved"]), ({}, []))

    def test_a_review_entry_is_dropped_once_the_pull_request_is_no_agent_pull_request(
        self,
    ):
        L = reviewed()
        plan(reviewable(other_pr(12, linked_issues=[1])), L, LATER)
        self.assertEqual(L["tasks"], {})

    def test_a_pull_request_with_a_value_missing_gets_no_review(self):
        whole = agent_pr()
        cases = {
            "head": without(whole, "head"),
            "draft": without(whole, "draft"),
            "labels": without(whole, "labels"),
            "pushed_at": {**whole, "review": without(whole["review"], "pushed_at")},
            "checks": {**whole, "review": without(whole["review"], "checks")},
            "linked issue": agent_pr(issues=[]),
        }
        for name, p in cases.items():
            with self.subTest(name):
                self.assertEqual(keys(plan(reviewable(p), empty(), NOW)), [])

    def test_a_pull_request_not_onto_the_default_branch_gets_no_review(self):
        cases = {
            "another base": reviewable({**agent_pr(), "base": "release"}),
            "no base": reviewable(without(agent_pr(), "base")),
            "no default branch": reviewable(),
        }
        cases["no default branch"]["repos"][0]["default_branch"] = None
        for name, c in cases.items():
            with self.subTest(name):
                self.assertEqual(keys(plan(c, empty(), NOW)), [])

    def test_a_pull_request_closed_since_it_was_listed_gets_no_review(self):
        for state in ("CLOSED", "MERGED"):
            with self.subTest(state):
                c = reviewable({**agent_pr(), "state": state})
                self.assertEqual(keys(plan(c, empty(), NOW)), [])

    def test_a_land_verdict_is_kept_only_with_the_clean_reason_and_the_head(self):
        url = "https://github.com/o/r/pull/12"
        for status, note, stored, head in [
            ("land", f"clean {HEAD} {url}; converged", "land", HEAD),
            ("land", f"not_trivial {HEAD} {url}", "no-land", HEAD),
            ("land", f"clean; head {HEAD}", "no-land", HEAD),
            ("land", f"clean but see below {HEAD}", "no-land", HEAD),
            ("land", f"not clean {HEAD}", "no-land", HEAD),
            ("land", f"clean {HEAD}0", "no-land", None),
            ("land", "clean", "no-land", None),
            ("no-land", f"off_plan {HEAD} {url}; too wide", "no-land", HEAD),
        ]:
            with self.subTest(status=status, note=note):
                L = empty()
                start(L, REVIEW, "t", NOW)
                self.assertEqual(finish(L, REVIEW, status, note, NOW), stored)
                entry = L["tasks"][REVIEW]
                self.assertEqual((entry["status"], entry["head"]), (stored, head))

    def test_a_later_no_land_takes_back_a_land_already_recorded(self):
        L = reviewed("land", f"clean {HEAD}")
        self.assertEqual(
            finish(L, REVIEW, "no-land", f"not_trivial {HEAD}", NOW), "no-land"
        )
        self.assertEqual(L["tasks"][REVIEW]["status"], "no-land")
        with self.assertRaises(SystemExit):
            finish(L, REVIEW, "land", f"clean {HEAD}", NOW)

    def test_a_finished_build_does_not_stop_a_review(self):
        L = empty()
        start(L, "o/r#1:build", "t", NOW)
        finish(L, "o/r#1:build", "fix-pending", "x", NOW)
        self.assertEqual(keys(plan(reviewable(), L, NOW)), [REVIEW])

    def test_a_running_build_of_a_second_linked_issue_stops_a_review(self):
        L = empty()
        start(L, "o/r#2:build", "t", NOW)
        c = reviewable(agent_pr(issues=[1, 2]))
        self.assertNotIn(REVIEW, keys(plan(c, L, NOW)))

    def test_running_work_that_is_no_review_does_not_stop_a_review(self):
        c = snap(
            backlog_repo(issue(1), prs=[pr(7)], other_prs=[agent_pr()]),
            cards=[card(1, "In Review")],
        )
        L = empty()
        start(L, "o/r#7", "t", NOW)
        self.assertEqual(keys(plan(c, L, NOW)), [REVIEW])

    def test_a_review_that_keeps_failing_is_never_blocked(self):
        L = empty()
        for status in ("failed", "blocked", "done", "fix-pending"):
            start(L, REVIEW, "t", NOW)
            self.assertEqual(finish(L, REVIEW, status, "boom", NOW), "failed")
        self.assertEqual(L["tasks"][REVIEW]["status"], "failed")

    def test_a_review_that_is_not_running_takes_no_verdict(self):
        L = reviewed()
        for ledger in (L, empty()):
            with self.assertRaises(SystemExit):
                finish(ledger, REVIEW, "land", f"clean {HEAD}", NOW)
        self.assertEqual(L["tasks"][REVIEW]["status"], "no-land")

    def test_a_review_entry_never_gets_a_card(self):
        note = f"clean {HEAD} https://github.com/o/r/pull/12"
        c = reviewable(agent_pr(labels=["needs-review"]))
        for status in ("land", "no-land", "failed"):
            with self.subTest(status):
                self.assertEqual(plan(c, reviewed(status, note), NOW)["card_ops"], [])

    def test_a_review_entry_an_older_ledger_left_blocked_is_not_dependency_work(self):
        far = agent_pr(issues=[], linked_refs=["o/x#5"], labels=["needs-review"])
        L = reviewed("land", f"clean {HEAD} https://github.com/o/r/pull/12")
        L["tasks"][REVIEW]["status"] = "blocked"
        self.assertEqual(
            plan(reviewable(far), L, NOW)["card_ops"],
            [
                {
                    "op": "create",
                    "link": {"repo": "o/r", "number": 12, "type": "pr"},
                    "title": "PR 12",
                    "status": "In Review",
                    "labels": ["kind: pr", "repo: r"],
                }
            ],
        )


DUE = NOW + backlog.LAND_DELAY


PROTECTED_PATHS = """
.github/workflows/ci.yml a/b/.github/x.yml .gitlab-ci.yml .pre-commit-config.yaml
CODEOWNERS docs/CODEOWNERS
.claude/settings.json .agents/x.md .cursor/rules/a.mdc .cursorrules .mcp.json
CLAUDE.md sub/CLAUDE.local.md AGENTS.md GEMINI.md skills/x/SKILL.md
pyproject.toml sub/pyproject.toml setup.py setup.cfg requirements.txt
requirements-dev.txt tox.ini noxfile.py pytest.ini tests/conftest.py .coveragerc
codecov.yml codecov.yaml package.json tsconfig.json tsconfig.build.json
jest.config.js vitest.config.ts playwright.config.ts Cargo.toml go.mod go.sum
uv.lock Cargo.lock flake.lock package-lock.json pnpm-lock.yaml flake.nix
nix/shell.nix Makefile justfile Dockerfile Dockerfile.dev compose.yml
docker-compose.prod.yaml ruff.toml .ruff.toml mypy.ini biome.json biome.jsonc
eslint.config.mjs .eslintrc .eslintrc.json
scripts/run.py tools/scripts/run.py install.sh bin/x.sh .env .env.local
config/.envrc
.gitmodules Pipfile bun.lockb npm-shrinkwrap.json .npmrc .husky/pre-commit
.circleci/config.yml Taskfile.yml Taskfile.yaml requirements/dev.txt bin/deploy
makefile dockerfile claude.md .GitHub/workflows/ci.yml
"""
FREE_PATHS = """
src/app/main.py docs/guide.md tests/test_x.py README.md src/environment.py
src/lockfile.py docs/makefile-tips.md src/scripts.py composer.py src/github/api.py
src/nix.py shell.py tsconfig-docs/schema.json
tests/test_setup.py src/mypackage.json notscripts/a.py docs/requirements.md
src/bin.py cabin/a.py
"""


def cleared(*keys, head=HEAD):
    L, keys = empty(), keys or (REVIEW,)
    for n, key in enumerate(keys):
        at = NOW - timedelta(minutes=len(keys) - 1 - n)
        start(L, key, "t", at - timedelta(minutes=20))
        finish(L, key, "land", f"clean {head}", at)
    return L


def spread(*numbers):
    names = {n: "o/r" if i == 0 else f"o/r{n}" for i, n in enumerate(numbers)}
    repos = [
        {**backlog_repo(issue(1), name=name, other_prs=[agent_pr(n)]), "land": True}
        for n, name in names.items()
    ]
    cards = [card(1, "In Review", repo=name) for name in names.values()]
    return snap(*repos, cards=cards)


def landable(*prs, cards=None, **fields):
    c = reviewable(*prs, cards=cards)
    c["repos"][0].update({"land": True, **fields})
    return c


def lands(result):
    return [t["pr"] for t in result["lands"]]


def why(result):
    return [(g["pr"], g["why"]) for g in result["greenlit"]]


def scene(passing):
    def ok(rule):
        return rule <= passing

    p = agent_pr(
        head=HEAD if ok(2) else "b" * 40,
        draft=not ok(5),
        checks="green" if ok(10) else "red",
        unresolved=0 if ok(12) else 1,
        lines=7 if ok(13) else 301,
        files=["a.py"] if ok(14) else [".github/x.yml"],
    )
    p["state"] = "OPEN" if ok(3) else "CLOSED"
    p["base"] = "main" if ok(4) else "release"
    p["merge_state"] = "CLEAN" if ok(9) else "BLOCKED"
    r = {
        **backlog_repo(
            issue(1, author=OWNER if ok(6) else "eve"),
            other_prs=[p] if ok(8) else [p, agent_pr(13)],
        ),
        "main": "green" if ok(11) else "red",
        "land": ok(16) or not ok(15),
    }
    c = snap(r, cards=[card(1, "In Review" if ok(7) else "In Progress")])
    now = DUE if ok(19) else DUE - timedelta(hours=1)
    L = cleared()
    L["tasks"][REVIEW]["offered"] = None if ok(17) else HEAD
    L["landed"] = [] if ok(15) else [{**landing(9, "s9"), "reverted": True}]
    if not ok(18):
        L["landed"] += [
            landing(n, f"s{n}", at=now.isoformat(), repo="o/elsewhere")
            for n in range(3)
        ]
    return c, L, now


class LandGatePlanTest(unittest.TestCase):
    def holds(self, expected, c, L=None, now=DUE):
        result = plan(c, cleared() if L is None else L, now)
        self.assertEqual((lands(result), why(result)), ([], [(12, expected)]))

    def test_a_cleared_pull_request_lands_a_day_after_its_verdict(self):
        L = cleared()
        result = plan(landable(), L, DUE)
        self.assertEqual(
            result["lands"], [{"key": REVIEW, "repo": "o/r", "pr": 12, "head": HEAD}]
        )
        self.assertEqual((result["greenlit"], result["dispatch"]), ([], []))
        self.assertEqual(L["tasks"][REVIEW]["offered"], HEAD)

    def test_a_plan_without_a_land_verdict_lands_nothing(self):
        result = plan(cands(repo()), empty(), NOW)
        self.assertEqual((result["lands"], result["greenlit"]), ([], []))
        for status, note in [
            ("no-land", f"not_trivial {HEAD}"),
            ("failed", "boom"),
            ("land", f"not_trivial {HEAD}"),
        ]:
            with self.subTest(status=status, note=note):
                result = plan(landable(), reviewed(status, note), DUE)
                self.assertEqual((result["lands"], result["greenlit"]), ([], []))

    def test_the_first_rule_that_holds_a_pull_request_is_the_one_reported(self):
        reasons = [
            "verdict is stale",
            "not open",
            "base not the default branch",
            "held by the owner",
            "issue not the owner's",
            "card in progress",
            "split across pull requests",
            "merge state blocked",
            "checks red",
            "default branch red",
            "open feedback",
            "too large",
            "protected path .github/x.yml",
            "paused after a revert",
            "shadow",
            "merge not made",
            "daily limit",
            "lands 2026-10-22T12:00:00+00:00",
        ]
        for passing, expected in enumerate(reasons, 1):
            with self.subTest(expected):
                c, L, now = scene(passing)
                self.holds(expected, c, L, now)
        c, L, now = scene(19)
        self.assertEqual(lands(plan(c, L, now)), [12])

    def test_a_verdict_on_another_head_lands_nothing(self):
        for name, p in [
            ("a newer head", agent_pr(head="b" * 40)),
            ("no head", without(agent_pr(), "head")),
        ]:
            with self.subTest(name):
                self.holds("verdict is stale", landable(p))
        L = cleared()
        del L["tasks"][REVIEW]["head"]
        self.holds("verdict is stale", landable(without(agent_pr(), "head")), L)

    def test_a_pull_request_that_is_not_open_lands_nothing(self):
        for name, p in [
            ("closed", {**agent_pr(), "state": "CLOSED"}),
            ("merged", {**agent_pr(), "state": "MERGED"}),
            ("no state", without(agent_pr(), "state")),
        ]:
            with self.subTest(name):
                self.holds("not open", landable(p))

    def test_only_a_pull_request_onto_the_default_branch_lands(self):
        for name, c in [
            ("another base", landable({**agent_pr(), "base": "release"})),
            ("no base", landable(without(agent_pr(), "base"))),
            ("no default branch", landable(default_branch=None)),
        ]:
            with self.subTest(name):
                self.holds("base not the default branch", c)

    def test_a_pull_request_the_owner_holds_lands_nothing(self):
        for name, p in [
            ("a draft", agent_pr(draft=True)),
            ("agent:hold", agent_pr(labels=["agent:hold"])),
            ("agent:blocked", agent_pr(labels=["agent:blocked"])),
            ("no draft flag", without(agent_pr(), "draft")),
            ("no labels", without(agent_pr(), "labels")),
        ]:
            with self.subTest(name):
                self.holds("held by the owner", landable(p))
        c = landable(agent_pr(labels=["needs-review"]))
        self.assertEqual(lands(plan(c, cleared(), DUE)), [12])

    def test_every_linked_issue_must_be_open_and_written_by_the_owner(self):
        strangers, closed, scanned = landable(), landable(), landable()
        strangers["repos"][0]["issues"] = [issue(1, author="eve")]
        closed["repos"][0]["issues"] = []
        scanned["repos"][0]["issues"] = [found(1)]
        far = agent_pr(linked_refs=["o/x#5"])
        cases = {
            "someone else's issue": strangers,
            "a closed issue": closed,
            "an issue a scan filed": scanned,
            "an issue in a repo out of scope": landable(
                far, cards=[card(1, "In Review"), card(5, "In Review", repo="o/x")]
            ),
            "no linked issue": landable(agent_pr(issues=[])),
            "no owner in the snapshot": without(landable(), "owner"),
            "no owner and a closed issue": without(closed, "owner"),
        }
        for name, c in cases.items():
            with self.subTest(name):
                self.holds("issue not the owner's", c)
        L = cleared()
        L["found"] = ["o/r#1"]
        self.holds("issue not the owner's", landable(), L)

    def test_every_linked_issue_needs_a_card_in_review(self):
        both = agent_pr(issues=[1, 2])
        cases = {
            "no card": (landable(cards=[]), "card none"),
            "in progress": (
                landable(cards=[card(1, "In Progress")]),
                "card in progress",
            ),
            "needs info": (landable(cards=[card(1, "Needs info")]), "card needs info"),
            "done": (landable(cards=[card(1, "Done")]), "card done"),
            "a pull request card": (
                landable(cards=[card(1, "In Review", type="pr")]),
                "card none",
            ),
            "one of two": (
                landable(both, cards=[card(1, "In Review"), card(2, "Needs approval")]),
                "card needs approval",
            ),
            "no board": (without(landable(), "board"), "card none"),
            "moved beside a twin still there": (
                landable(cards=[card(1, "In Review"), twin(1, "Needs info", ["x"])]),
                "card needs info",
            ),
        }
        for name, (c, expected) in cases.items():
            with self.subTest(name):
                self.holds(expected, c)
        c = landable(both)
        self.assertEqual(lands(plan(c, cleared(), DUE)), [12])

    def test_an_issue_split_across_agent_pull_requests_lands_nothing(self):
        self.holds("split across pull requests", landable(agent_pr(12), agent_pr(13)))
        far = ["o/x#5"]
        c = landable(
            agent_pr(12, linked_refs=far),
            agent_pr(13, issues=[], linked_refs=far),
            cards=[card(1, "In Review"), card(5, "In Review", repo="o/x")],
        )
        c["repos"].append(backlog_repo(issue(5), name="o/x"))
        self.holds("split across pull requests", c)
        c = landable()
        c["repos"].append(
            backlog_repo(
                name="o/x", other_prs=[agent_pr(12, issues=[], linked_refs=["o/r#1"])]
            )
        )
        self.holds("split across pull requests", c)
        for name, cut in [
            ("list cut short", {**backlog_repo(name="o/x"), "prs_cut": True}),
            ("list not counted", without(backlog_repo(name="o/x"), "prs_cut")),
        ]:
            with self.subTest(name):
                c = landable()
                c["repos"].append(cut)
                self.holds("pull requests not all read", c)
        c = landable(agent_pr(12), other_pr(13, linked_issues=[1]))
        self.assertEqual(lands(plan(c, cleared(), DUE)), [12])

    def test_only_a_clean_merge_state_lands(self):
        for state in (
            "BLOCKED",
            "BEHIND",
            "DIRTY",
            "UNSTABLE",
            "UNKNOWN",
            "DRAFT",
            "HAS_HOOKS",
        ):
            with self.subTest(state):
                c = landable({**agent_pr(), "merge_state": state})
                self.holds(f"merge state {state.lower()}", c)
        c = landable(without(agent_pr(), "merge_state"))
        self.holds("merge state none", c)

    def test_only_green_required_checks_land(self):
        whole = agent_pr()
        cases = {
            "red": (agent_pr(checks="red"), "checks red"),
            "pending": (agent_pr(checks="pending"), "checks pending"),
            "none required": (agent_pr(checks=None), "checks none"),
            "not read": (
                {**whole, "review": without(whole["review"], "checks")},
                "checks none",
            ),
        }
        for name, (p, expected) in cases.items():
            with self.subTest(name):
                self.holds(expected, landable(p))

    def test_only_a_green_default_branch_lands(self):
        for main in ("red", "pending", None):
            with self.subTest(main):
                c = landable(main=main, has_ci=bool(main))
                self.holds(f"default branch {main or 'none'}", c)

    def test_open_feedback_lands_nothing(self):
        whole = agent_pr()
        cases = {
            "an unresolved thread": agent_pr(unresolved=1),
            "feedback after the review started": agent_pr(
                feedback_at="2026-10-21T13:00:00Z"
            ),
            "threads not read": {
                **whole,
                "review": without(whole["review"], "unresolved"),
            },
            "feedback not read": {
                **whole,
                "review": without(whole["review"], "feedback_at"),
            },
        }
        for name, p in cases.items():
            with self.subTest(name):
                self.holds("open feedback", landable(p))
        c = landable(agent_pr(feedback_at="2026-10-21T11:00:00Z"))
        self.assertEqual(lands(plan(c, cleared(), DUE)), [12])

    def test_feedback_given_while_the_review_ran_lands_nothing(self):
        L = reviewed("land", f"clean {HEAD}", took=timedelta(minutes=20))
        c = landable(agent_pr(feedback_at="2026-10-21T12:10:00Z"))
        self.holds("open feedback", c, L, DUE + timedelta(hours=1))

    def test_a_change_past_ten_files_or_three_hundred_lines_lands_nothing(self):
        names = [f"f{n}.py" for n in range(11)]
        whole = agent_pr()
        cases = {
            "eleven files": agent_pr(changed_files=11, files=names),
            "301 lines": agent_pr(lines=301),
            "files not counted": {
                **whole,
                "review": without(whole["review"], "changed_files"),
            },
            "lines not counted": {
                **whole,
                "review": without(whole["review"], "lines"),
            },
        }
        for name, p in cases.items():
            with self.subTest(name):
                self.holds("too large", landable(p))
        c = landable(agent_pr(changed_files=10, files=names[:10], lines=300))
        self.assertEqual(lands(plan(c, cleared(), DUE)), [12])

    def test_a_change_to_a_protected_path_lands_nothing(self):
        protected, free = PROTECTED_PATHS.split(), FREE_PATHS.split()
        for path in protected:
            with self.subTest(path):
                p = agent_pr(changed_files=2, files=[path, "a.py"])
                self.holds(f"protected path {path}", landable(p))
        for path in free:
            with self.subTest(path):
                c = landable(agent_pr(changed_files=2, files=["a.py", path]))
                self.assertEqual(lands(plan(c, cleared(), DUE)), [12])

    def test_the_old_name_of_a_renamed_file_is_checked_too(self):
        p = agent_pr(files=["docs/new.md", ".github/old.yml"])
        self.holds("protected path .github/old.yml", landable(p))

    def test_a_change_whose_file_names_were_not_all_read_lands_nothing(self):
        whole = agent_pr()
        for name, p in [
            ("no names", {**whole, "review": without(whole["review"], "files")}),
            ("fewer names than files", agent_pr(changed_files=2, files=["a.py"])),
        ]:
            with self.subTest(name):
                self.holds("protected path none", landable(p))

    def test_a_revert_pauses_the_repo_until_the_owner_takes_the_topic_off(self):
        L = cleared()
        L["landed"] = [landing(9, "abc")]
        self.holds("paused after a revert", landable(reverted=[9]), L)
        self.assertEqual(L["landed"], [{**landing(9, "abc"), "reverted": True}])
        self.holds("paused after a revert", landable(), L)
        self.holds("shadow", landable(land=False), L)
        self.assertEqual(L["landed"], [{**landing(9, "abc"), "reverted": False}])
        self.assertEqual(lands(plan(landable(reverted=[9]), L, DUE)), [12])

    def test_a_repo_missing_from_a_snapshot_stays_paused(self):
        paused = {**landing(9, "abc"), "reverted": True}
        for name, c in [
            ("repo not listed", snap(backlog_repo(name="o/other"))),
            ("land topic not read", snap(backlog_repo())),
        ]:
            with self.subTest(name):
                L = empty()
                L["landed"] = [dict(paused)]
                plan(c, L, DUE)
                self.assertEqual(L["landed"], [paused])

    def test_a_reverted_landing_still_counts_toward_the_day_it_was_made(self):
        today = DUE.isoformat()
        there = backlog_repo(issue(2), name="o/x", other_prs=[agent_pr(13, issues=[2])])
        c = snap(
            {**backlog_repo(), "land": False, "reverted": [9]},
            {**there, "land": True},
            cards=[card(2, "In Review", repo="o/x")],
        )
        L = cleared("o/x#13:review")
        L["landed"] = [landing(9, "abc", at=today)] + [
            landing(n, f"s{n}", at=today, repo="o/elsewhere") for n in (10, 11)
        ]
        result = plan(c, L, DUE)
        self.assertEqual((lands(result), why(result)), ([], [(13, "daily limit")]))

    def test_a_reverted_landing_pauses_only_its_own_repo(self):
        there = backlog_repo(issue(2), name="o/x", other_prs=[agent_pr(13, issues=[2])])
        c = snap(
            {**backlog_repo(issue(1), other_prs=[agent_pr()]), "land": True},
            {**there, "land": True, "reverted": [9]},
            cards=[card(1, "In Review"), card(2, "In Review", repo="o/x")],
        )
        L = cleared(REVIEW, "o/x#13:review")
        L["landed"] = [landing(9, "abc", repo="o/x"), landing(9, "def")]
        result = plan(c, L, DUE)
        self.assertEqual(
            (lands(result), why(result)), ([12], [(13, "paused after a revert")])
        )
        self.assertEqual([e.get("reverted") for e in L["landed"]], [True, None])

    def test_a_landing_is_forgotten_after_sixty_days_unless_it_was_reverted(self):
        old = (DUE - timedelta(days=61)).isoformat()
        recent = (DUE - timedelta(days=59)).isoformat()
        L = cleared()
        L["landed"] = [
            landing(7, "s7", at=old, repo="o/x"),
            {**landing(8, "s8", at=old), "reverted": True},
            landing(9, "s9", at=recent, repo="o/x"),
        ]
        plan(landable(), L, DUE)
        self.assertEqual([e["pr"] for e in L["landed"]], [8, 9])

    def test_a_repo_without_the_land_topic_only_gets_shadow_verdicts(self):
        own = "jakob1379/nix-config"
        agents_own = snap(
            {**backlog_repo(issue(1), name=own, other_prs=[agent_pr()]), "land": True},
            cards=[card(1, "In Review", repo=own)],
        )
        cases = {
            "no topic": (reviewable(), cleared()),
            "topic off": (landable(land=False), cleared()),
            "the agent's own repo": (agents_own, cleared(f"{own}#12:review")),
        }
        for name, (c, L) in cases.items():
            with self.subTest(name):
                self.holds("shadow", c, L)

    def test_a_merge_that_was_offered_and_not_made_waits_for_a_new_head(self):
        L, c = cleared(), landable()
        self.assertEqual(lands(plan(c, L, DUE)), [12])
        self.holds("merge not made", c, L)
        again = "b" * 40
        start(L, REVIEW, "t", DUE)
        finish(L, REVIEW, "land", f"clean {again}", DUE)
        result = plan(landable(agent_pr(head=again)), L, DUE + backlog.LAND_DELAY)
        self.assertEqual(
            result["lands"], [{"key": REVIEW, "repo": "o/r", "pr": 12, "head": again}]
        )

    def test_three_pull_requests_land_in_a_copenhagen_day_oldest_verdict_first(self):
        c = spread(12, 13, 14)
        last_night, today = "2026-10-21T22:30:00+00:00", "2026-10-22T08:00:00+00:00"
        cases = {
            "none today": ([], [13, 14, 12], []),
            "one today": ([today], [13, 14], [12]),
            "two since midnight in Copenhagen": ([last_night, today], [13], [14, 12]),
            "three today": ([today] * 3, [], [13, 14, 12]),
            "three yesterday": (["2026-10-21T21:59:00+00:00"] * 3, [13, 14, 12], []),
        }
        for name, (merged, landing_now, waiting) in cases.items():
            with self.subTest(name):
                L = cleared("o/r13#13:review", "o/r14#14:review", REVIEW)
                L["tasks"] = dict(sorted(L["tasks"].items()))
                L["landed"] = [
                    landing(n, f"s{n}", at=at, repo="o/elsewhere")
                    for n, at in enumerate(merged)
                ]
                result = plan(c, L, DUE)
                self.assertEqual(
                    (lands(result), why(result)),
                    (landing_now, [(n, "daily limit") for n in waiting]),
                )

    def test_today_is_the_copenhagen_day_of_the_plan(self):
        now = datetime(2026, 10, 22, 22, 30, tzinfo=UTC)
        for at, expected in [
            ("2026-10-22T10:00:00+00:00", ([12], [])),
            ("2026-10-22T22:10:00+00:00", ([], [(12, "daily limit")])),
        ]:
            with self.subTest(at):
                L = cleared()
                L["landed"] = [
                    landing(n, f"s{n}", at=at, repo="o/elsewhere") for n in range(3)
                ]
                result = plan(landable(), L, now)
                self.assertEqual((lands(result), why(result)), expected)

    def test_one_pull_request_lands_per_repo_in_a_plan(self):
        c = landable(agent_pr(12), agent_pr(13, issues=[2]))
        L = cleared("o/r#13:review", REVIEW)
        result = plan(c, L, DUE)
        self.assertEqual(
            (lands(result), why(result)), ([13], [(12, "default branch pending")])
        )
        self.assertIsNone(L["tasks"][REVIEW].get("offered"))

    def test_a_held_verdict_is_new_once(self):
        L = cleared()
        shadow = landable(land=False)
        seen = [plan(shadow, L, DUE)["greenlit"][0]["new"] for _ in range(2)]
        self.assertEqual(seen, [True, False])
        (held,) = plan(landable(agent_pr(lines=5000)), L, DUE)["greenlit"]
        self.assertEqual((held["why"], held["new"]), ("too large", True))

    def test_a_pull_request_waits_a_day_after_its_verdict(self):
        soon = DUE - timedelta(minutes=1)
        self.holds("lands 2026-10-22T12:00:00+00:00", landable(), now=soon)


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
            self.assertEqual(
                json.loads(out),
                {
                    "applied": [],
                    "pending": [],
                    "error": None,
                    "missing_labels": [],
                    "release": [],
                },
            )

    def test_a_review_verdict_is_planned_started_and_recorded(self):
        url = "https://github.com/o/r/pull/12"
        for status, note in [
            ("land", f"clean {HEAD} {url}; converged"),
            ("no-land", f"off_plan {HEAD} {url}; too wide"),
        ]:
            with self.subTest(status), tempfile.TemporaryDirectory() as d:
                path, cpath = Path(d, "ledger.json"), Path(d, "c.json")
                cpath.write_text(json.dumps(reviewable()))
                out = json.loads(cli(path, "plan", str(cpath), "th"))
                self.assertEqual(rounds(out), [(REVIEW, 1)])
                self.assertTrue(Path(d, "work", "o-r-12-review.lock").exists())
                cli(path, "start", REVIEW, "task-1")
                done = cli(path, "finish", REVIEW, "--task", "task-1", status, note)
                self.assertEqual(done.strip(), status)
                entry = json.loads(path.read_text())["tasks"][REVIEW]
                self.assertEqual(
                    (entry["status"], entry["head"], entry["note"]),
                    (status, HEAD, note),
                )
                self.assertEqual(list(Path(d, "work").iterdir()), [])

    def test_a_result_from_a_replaced_review_run_is_refused(self):
        note = f"clean {HEAD}"
        with tempfile.TemporaryDirectory() as d:
            path, cpath = Path(d, "ledger.json"), Path(d, "c.json")
            cpath.write_text(json.dumps(reviewable()))
            cli(path, "plan", str(cpath), "th")
            self.assertEqual(
                cli(path, "finish", REVIEW, "failed", "never started").strip(), "failed"
            )
            cli(path, "plan", str(cpath), "th")
            cli(path, "start", REVIEW, "task-A")
            cli(path, "finish", REVIEW, "--task", "task-A", "failed", "lost task")
            out = json.loads(cli(path, "plan", str(cpath), "th"))
            self.assertEqual(rounds(out), [(REVIEW, 3)])
            cli(path, "start", REVIEW, "task-B")
            lock = Path(d, "work", "o-r-12-review.lock")
            for stale in (["--task", "task-A"], []):
                with (
                    self.subTest(stale),
                    self.assertRaises(subprocess.CalledProcessError) as refused,
                ):
                    cli(path, "finish", REVIEW, *stale, "land", note)
                self.assertIn(
                    f"{REVIEW} is run by another task, so this result is dropped",
                    refused.exception.stderr,
                )
                entry = json.loads(path.read_text())["tasks"][REVIEW]
                self.assertEqual(
                    (entry["status"], entry["task_id"]), ("running", "task-B")
                )
                self.assertTrue(lock.exists())
            self.assertEqual(
                cli(path, "finish", REVIEW, "--task", "task-B", "land", note).strip(),
                "land",
            )
            self.assertFalse(lock.exists())

    def test_plan_prints_what_lands_and_saves_what_it_offered(self):
        with tempfile.TemporaryDirectory() as d:
            path, cpath = Path(d, "ledger.json"), Path(d, "c.json")
            path.write_text(json.dumps(cleared()))
            cpath.write_text(json.dumps(landable()))
            out = json.loads(cli(path, "plan", str(cpath), "th"))
            self.assertEqual(
                (out["lands"], out["greenlit"]),
                ([{"key": REVIEW, "repo": "o/r", "pr": 12, "head": HEAD}], []),
            )
            entry = json.loads(path.read_text())["tasks"][REVIEW]
            self.assertEqual(entry["offered"], HEAD)


def linear_reply(body: dict):
    r = mock.MagicMock()
    r.__enter__.return_value.read.return_value = json.dumps({"data": body}).encode()
    return r


def rate_limited(url, *_):
    return urllib.error.HTTPError(
        url, 429, "ratelimited", Message(), io.BytesIO(b"ratelimit exceeded")
    )


class FakeLinear:
    def __init__(self, link_limited=False, link_fails=False, cards=(), owners=1):
        self.calls, self.link_limited, self.n = [], link_limited, 0
        self.link_fails, self.cards, self.owners = link_fails, cards, owners

    def __call__(self, req, timeout=None):
        q = json.loads(req.data)["query"]
        if "viewer" in q:
            return linear_reply(
                {
                    "viewer": {"id": "agent"},
                    "users": {
                        "nodes": [
                            {"id": f"u{n}", "admin": True, "app": False, "active": True}
                            for n in range(self.owners)
                        ]
                    },
                    "teams": {
                        "nodes": [
                            {
                                "id": "team",
                                "states": {
                                    "nodes": [
                                        {"id": s, "name": s} for s in linear.STATUSES
                                    ]
                                },
                            }
                        ]
                    },
                    "issueLabels": {"nodes": []},
                }
            )
        if "issues(" in q:
            nodes = [
                {
                    "id": c["id"],
                    "identifier": c["identifier"],
                    "title": c["title"],
                    "description": None,
                    "createdAt": c["created_at"],
                    "state": {"name": c["status"]},
                    "labels": {"nodes": []},
                    "attachments": {
                        "nodes": [
                            {
                                "id": c["attachments"][0],
                                "url": linear.github_url(c["link"]),
                            }
                        ]
                    },
                    "history": {"nodes": []},
                }
                for c in self.cards
            ]
            page = {"hasNextPage": False, "endCursor": None}
            return linear_reply({"issues": {"pageInfo": page, "nodes": nodes}})
        name = next(
            n
            for n in (
                "issueLabelCreate",
                "issueCreate",
                "issueUpdate",
                "attachmentDelete",
                "attachment",
            )
            if n in q
        )
        self.calls.append(name)
        if name == "attachment" and self.link_limited:
            raise rate_limited(req.full_url)
        self.n += 1
        return linear_reply(
            {
                "issueLabelCreate": {"issueLabel": {"id": f"l{self.n}"}},
                "issueCreate": {
                    "issue": {"id": f"i{self.n}", "identifier": f"JGA-{self.n}"}
                },
                "issueUpdate": {"success": True},
                "attachmentLinkURL": {"success": not self.link_fails},
                "attachmentDelete": {"success": True},
            }
        )


CREATE = {
    "op": "create",
    "link": {"repo": "o/r", "number": 9, "type": "issue"},
    "title": "t",
    "status": "Backlog",
    "labels": [],
}


def card_move(card, to):
    return {"op": "move", "card": card, "to": to, "add_labels": [], "remove_labels": []}


class LinearApplyTest(unittest.TestCase):
    def run_apply(self, fake, ops, **kw):
        with (
            mock.patch.object(linear, "_token", "t"),
            mock.patch("urllib.request.urlopen", side_effect=fake),
            mock.patch("time.sleep") as sleep,
        ):
            return linear.apply(ops, **kw), sleep

    def test_graphql_waits_out_a_rate_limit(self):
        with (
            mock.patch.object(linear, "_token", "t"),
            mock.patch(
                "urllib.request.urlopen",
                side_effect=[rate_limited(linear.API), linear_reply({"x": 1})],
            ),
            mock.patch("time.sleep") as sleep,
        ):
            self.assertEqual(linear.graphql("q"), {"x": 1})
        sleep.assert_called_once()

    def test_a_link_linear_refuses_stays_pending(self):
        link = {"op": "link", "card": "c", "link": CREATE["link"]}
        out, _ = self.run_apply(FakeLinear(link_fails=True), [link])
        self.assertEqual(out["applied"], [])
        self.assertEqual(out["pending"], [link])
        self.assertIn("attachmentLinkURL", out["error"])

    def test_a_lost_reply_leaves_the_op_pending_without_retrying_it(self):
        for failure in (
            TimeoutError("timed out"),
            urllib.error.HTTPError(
                linear.API, 502, "bad gateway", Message(), io.BytesIO(b"bad gateway")
            ),
        ):
            fake = FakeLinear()
            replies = iter([None, failure])

            def flaky(req, timeout=None, replies=replies, fake=fake):
                reply = next(replies, None)
                if reply is None:
                    return fake(req, timeout)
                raise reply

            out, _ = self.run_apply(flaky, [CREATE])
            self.assertEqual(fake.calls, [])
            self.assertEqual(len(out["pending"]), 1)
            self.assertIsNotNone(out["error"])

    def test_a_dropped_card_loses_its_links_before_it_is_closed(self):
        fake = FakeLinear()
        drop = {"op": "drop", "card": "c1", "attachments": ["a1"], "to": "Duplicate"}
        link = {"op": "link", "card": "c2", "link": CREATE["link"]}
        out, _ = self.run_apply(fake, [link, CREATE, drop])
        self.assertEqual(
            fake.calls,
            [
                "attachmentDelete",
                "issueUpdate",
                "issueCreate",
                "attachment",
                "attachment",
            ],
        )
        self.assertEqual(len(out["applied"]), 3)

    def test_moves_land_before_creates(self):
        fake = FakeLinear()
        out, _ = self.run_apply(fake, [CREATE, card_move("c1", "In Progress")])
        self.assertEqual(fake.calls, ["issueUpdate", "issueCreate", "attachment"])
        self.assertEqual([op.get("card") for op in out["applied"]], ["c1", "JGA-2"])
        self.assertEqual((out["pending"], out["error"]), ([], None))

    def test_a_lasting_rate_limit_leaves_the_rest_pending(self):
        fake = FakeLinear(link_limited=True)
        out, sleep = self.run_apply(fake, [CREATE, dict(CREATE, title="u")])
        self.assertEqual(out["applied"], [])
        self.assertEqual([op["title"] for op in out["pending"]], ["t", "u"])
        self.assertIn("429", out["error"])
        self.assertEqual(sleep.call_count, 2)

    def test_a_label_the_board_lacks_is_left_off_and_named_for_the_owner(self):
        fake = FakeLinear()
        out, _ = self.run_apply(
            fake,
            [
                dict(CREATE, labels=["repo: new"]),
                dict(card_move("c1", "Backlog"), add_labels=["repo: new"]),
            ],
        )
        self.assertNotIn("issueLabelCreate", fake.calls)
        self.assertEqual(len(out["applied"]), 2)
        self.assertEqual((out["pending"], out["error"]), ([], None))
        self.assertEqual(out["missing_labels"], ["repo: new"])

    def test_spent_budget_defers_the_rest_without_error(self):
        fake = FakeLinear()
        out, _ = self.run_apply(fake, [card_move("c1", "Backlog"), CREATE], budget=-1)
        self.assertEqual(out["applied"], [])
        self.assertEqual(len(out["pending"]), 2)
        self.assertIsNone(out["error"])

    def test_cli_releases_only_claims_whose_card_moves_are_pending(self):
        fake = FakeLinear()
        decided = {
            "dispatch": [
                {"key": "o/r#1:triage", "card": "c1"},
                {"key": "o/r#2", "kind": "dependency"},
            ],
            "card_ops": [card_move("c1", "In Progress"), CREATE],
        }
        with tempfile.TemporaryDirectory() as d:
            plan_path = Path(d, "plan.json")
            plan_path.write_text(json.dumps(decided))
            with (
                mock.patch.object(linear, "_token", "t"),
                mock.patch.object(linear, "APPLY_BUDGET", -1),
                mock.patch("urllib.request.urlopen", side_effect=fake),
                mock.patch.object(sys, "argv", ["backlog.py", "apply", str(plan_path)]),
                mock.patch("sys.stdout", new_callable=io.StringIO) as out,
            ):
                backlog.main()
        self.assertEqual(json.loads(out.getvalue())["release"], ["o/r#1:triage"])

    def test_cli_keeps_a_claim_whose_card_is_already_in_progress(self):
        decided = {
            "dispatch": [{"key": "o/r#1:triage", "card": "c1"}],
            "card_ops": [
                dict(card_move("c1", "In Progress"), add_labels=["kind: backlog"])
            ],
        }
        with tempfile.TemporaryDirectory() as d:
            plan_path = Path(d, "plan.json")
            plan_path.write_text(json.dumps(decided))
            with (
                mock.patch.object(linear, "_token", "t"),
                mock.patch.object(linear, "APPLY_BUDGET", -1),
                mock.patch("urllib.request.urlopen", side_effect=FakeLinear()),
                mock.patch.object(sys, "argv", ["backlog.py", "apply", str(plan_path)]),
                mock.patch("sys.stdout", new_callable=io.StringIO) as out,
            ):
                backlog.main()
        self.assertEqual(json.loads(out.getvalue())["release"], [])


class GhRateLimitTest(unittest.TestCase):
    def test_gh_waits_out_a_rate_limit(self):
        limited = subprocess.CompletedProcess(
            [], 1, "", "HTTP 403: API rate limit exceeded"
        )
        ok = subprocess.CompletedProcess([], 0, "[]", "")
        with (
            mock.patch("subprocess.run", side_effect=[limited, ok]),
            mock.patch("time.sleep") as sleep,
        ):
            self.assertEqual(backlog.gh("api", "x"), [])
        sleep.assert_called_once()

    def test_gh_gives_up_on_other_client_errors(self):
        missing = subprocess.CompletedProcess([], 1, "", "HTTP 404: Not Found")
        with (
            mock.patch("subprocess.run", return_value=missing) as run,
            self.assertRaises(RuntimeError),
        ):
            backlog.gh("api", "x")
        self.assertEqual(run.call_count, 1)


ME = {"__typename": "User", "login": OWNER}
BOT = {"__typename": "Bot", "login": "reviewbot"}
EVE = {"__typename": "User", "login": "eve"}
AGENT_BRANCH = "backlog-agent/o-r-1-build"
PR_READ = "-F number=12"
REPOS_READ = "viewer {"
HISTORY_READ = "history("
REQUIRES_CI = [
    {
        "type": "required_status_checks",
        "parameters": {"required_status_checks": [{"context": "ci"}]},
    }
]


def fake_gh(replies):
    replies = dict(replies)
    replies.setdefault("rules/branches", [])
    replies.setdefault("rate_limit", "")
    calls = []

    def answer(cmd, **_):
        line = " ".join(cmd)
        calls.append(line)
        for needle, reply in replies.items():
            if needle in line:
                if isinstance(reply, list) and needle == PR_READ:
                    reply = reply.pop(0) if len(reply) > 1 else reply[0]
                if isinstance(reply, BaseException):
                    raise reply
                if isinstance(reply, subprocess.CompletedProcess):
                    return reply
                out = reply if isinstance(reply, str) else json.dumps(reply)
                return subprocess.CompletedProcess(cmd, 0, out, "")
        raise AssertionError(f"unexpected gh call: {line[:80]}")

    return mock.patch("subprocess.run", side_effect=answer), calls


def said(author, at="2026-10-21T10:00:00Z", body="please rename this", edited=None):
    return {"author": author, "createdAt": at, "lastEditedAt": edited, "body": body}


def thread(*comments, resolved=False, more=False):
    page = {"pageInfo": {"hasNextPage": more}, "nodes": list(comments)}
    return {"isResolved": resolved, "comments": page}


def pull(comments=(), reviews=(), threads=(), more=(), **kw):
    node = {
        "state": "OPEN",
        "isDraft": False,
        "mergeStateStatus": "CLEAN",
        "headRefOid": "h1",
        "baseRefName": "main",
        "additions": 5,
        "deletions": 2,
        "changedFiles": 1,
        "mergeCommit": None,
        "labels": {"nodes": [{"name": "needs-review"}]},
        "commits": {"nodes": [{"commit": {"committedDate": "2026-10-21T09:00:00Z"}}]},
        "comments": {
            "pageInfo": {"hasPreviousPage": "comments" in more},
            "nodes": list(comments),
        },
        "reviews": {
            "pageInfo": {"hasPreviousPage": "reviews" in more},
            "nodes": list(reviews),
        },
        "reviewThreads": {"totalCount": len(threads), "nodes": list(threads)},
        **kw,
    }
    return {"data": {"repository": {"pullRequest": node}}}


def detail(*pulls, files=({"filename": "a.py"},), **replies):
    patched, calls = fake_gh(
        {PR_READ: list(pulls), "pulls/12/files": list(files), **replies}
    )
    with patched, mock.patch("time.sleep") as sleep:
        return backlog.pr_detail("o/r", 12, OWNER), calls, sleep


def review_of(*pulls, **replies):
    return detail(*pulls, **replies)[0]["review"]


class PullRequestReadTest(unittest.TestCase):
    def test_a_read_gives_the_state_the_size_and_the_required_checks_of_the_head(self):
        checks = {
            "rules/branches/main": REQUIRES_CI,
            "commits/h1/check-runs": [
                {
                    "check_runs": [
                        {
                            "name": "ci",
                            "status": "completed",
                            "conclusion": "success",
                            "started_at": "2026-10-21T09:05:00Z",
                        }
                    ]
                }
            ],
            "commits/h1/status": {"statuses": []},
        }
        self.assertEqual(
            detail(pull(), **checks)[0],
            {
                "state": "OPEN",
                "head": "h1",
                "base": "main",
                "draft": False,
                "merge_state": "CLEAN",
                "labels": ["needs-review"],
                "review": {
                    "checks": "green",
                    "pushed_at": "2026-10-21T09:00:00Z",
                    "files": ["a.py"],
                    "changed_files": 1,
                    "lines": 7,
                    "unresolved": 0,
                    "feedback_at": None,
                    "owner_feedback_at": None,
                },
            },
        )

    def test_a_base_branch_that_requires_no_checks_gives_no_check_state(self):
        got, calls, _ = detail(pull())
        self.assertIsNone(got["review"]["checks"])
        self.assertFalse([c for c in calls if "check-runs" in c])

    def test_a_renamed_file_is_listed_under_both_names(self):
        files = [
            {"filename": "docs/new.md", "previous_filename": ".github/old.yml"},
            {"filename": "a.py"},
        ]
        self.assertEqual(
            review_of(pull(), files=files)["files"],
            ["docs/new.md", ".github/old.yml", "a.py"],
        )

    def test_only_the_owner_and_a_bot_opening_a_thread_give_feedback(self):
        at = "2026-10-21T10:00:00Z"
        marked = "\n <!-- backlog-agent -->\nfixed"
        cases = {
            "the owner's comment": (pull(comments=[said(ME)]), at),
            "the owner's review body": (pull(reviews=[said(ME)]), at),
            "the owner's reply in a thread": (
                pull(threads=[thread(said(EVE, "2026-10-21T08:00:00Z"), said(ME))]),
                at,
            ),
            "a bot opening a thread": (pull(threads=[thread(said(BOT))]), at),
            "a bot's comment": (pull(comments=[said(BOT)]), None),
            "a bot's review body": (pull(reviews=[said(BOT)]), None),
            "a bot's reply in a thread": (
                pull(threads=[thread(said(EVE), said(BOT))]),
                None,
            ),
            "a stranger's comment": (pull(comments=[said(EVE)]), None),
            "a stranger's review body": (pull(reviews=[said(EVE)]), None),
            "a stranger's thread": (pull(threads=[thread(said(EVE))]), None),
            "a deleted account's comment": (pull(comments=[said(None)]), None),
            "a comment by a bot named like the owner": (
                pull(comments=[said({"__typename": "Bot", "login": OWNER})]),
                None,
            ),
            "the agent's comment": (pull(comments=[said(ME, body=marked)]), None),
            "the agent's thread reply": (
                pull(threads=[thread(said(EVE), said(ME, body=marked))]),
                None,
            ),
            "a bot's thread that starts with the marker": (
                pull(threads=[thread(said(BOT, body=marked))]),
                None,
            ),
            "the empty review a thread reply leaves": (
                pull(reviews=[said(ME, body="")]),
                None,
            ),
            "a review the owner has not submitted": (
                pull(reviews=[said(ME, at=None)]),
                None,
            ),
        }
        for name, (node, expected) in cases.items():
            with self.subTest(name):
                self.assertEqual(review_of(node)["feedback_at"], expected)

    def test_the_owners_newest_feedback_is_timed_apart_from_a_bots(self):
        got = review_of(
            pull(
                comments=[
                    said(ME, "2026-10-21T09:00:00Z"),
                    said(ME, "2026-10-21T08:00:00Z"),
                ],
                threads=[thread(said(BOT, "2026-10-21T11:00:00Z"))],
            )
        )
        self.assertEqual(
            (got["feedback_at"], got["owner_feedback_at"]),
            ("2026-10-21T11:00:00Z", "2026-10-21T09:00:00Z"),
        )
        self.assertIsNone(
            review_of(pull(threads=[thread(said(BOT))]))["owner_feedback_at"]
        )

    def test_the_owners_edit_counts_as_new_feedback_and_a_bots_does_not(self):
        edited = "2026-10-21T15:00:00Z"
        got = review_of(
            pull(
                comments=[said(ME, "2026-10-21T09:00:00Z", edited=edited)],
                threads=[thread(said(BOT, "2026-10-21T11:00:00Z", edited=edited))],
            )
        )
        self.assertEqual(
            (got["feedback_at"], got["owner_feedback_at"]), (edited, edited)
        )
        got = review_of(
            pull(threads=[thread(said(BOT, "2026-10-21T11:00:00Z", edited=edited))])
        )
        self.assertEqual(got["feedback_at"], "2026-10-21T11:00:00Z")

    def test_the_required_checks_are_those_of_the_base_branch(self):
        green = {
            "commits/h1/check-runs": [
                {
                    "check_runs": [
                        {
                            "name": "ci",
                            "status": "completed",
                            "conclusion": "success",
                            "started_at": "2026-10-21T09:05:00Z",
                        }
                    ]
                }
            ],
            "commits/h1/status": {"statuses": []},
        }
        node = pull(baseRefName="release")
        on_main = {"rules/branches/main": REQUIRES_CI, **green}
        on_release = {"rules/branches/release": REQUIRES_CI, **green}
        self.assertIsNone(review_of(node, **on_main)["checks"])
        self.assertEqual(review_of(node, **on_release)["checks"], "green")

    def test_an_unresolved_thread_counts_when_feedback_opened_it(self):
        marked = "<!-- backlog-agent -->\nwhy"
        got = review_of(
            pull(
                threads=[
                    thread(said(ME)),
                    thread(said(BOT), said(ME, body=marked)),
                    thread(said(BOT), resolved=True),
                    thread(said(EVE), said(ME)),
                    thread(said(ME, body=marked)),
                ]
            )
        )
        self.assertEqual(got["unresolved"], 2)

    def test_a_thread_past_the_page_read_counts_as_unresolved(self):
        threads = {"totalCount": 101, "nodes": [thread(said(BOT), resolved=True)]}
        self.assertEqual(review_of(pull(reviewThreads=threads))["unresolved"], 100)

    def test_a_merge_state_github_is_still_computing_is_read_once_more(self):
        unknown = pull(mergeStateStatus="UNKNOWN")
        got, calls, sleep = detail(unknown, pull())
        self.assertEqual(got["merge_state"], "CLEAN")
        sleep.assert_called_once()

        got, calls, sleep = detail(unknown, unknown, pull())
        self.assertEqual(got["merge_state"], "UNKNOWN")
        self.assertEqual(len([c for c in calls if PR_READ in c]), 2)

        merged = pull(state="MERGED", mergeStateStatus="UNKNOWN")
        self.assertEqual(len([c for c in detail(merged)[1] if PR_READ in c]), 1)


def pr_node(number=12, author=OWNER, branch=AGENT_BRANCH, fork=False, closes=(1,)):
    return {
        "number": number,
        "title": f"PR {number}",
        "body": "",
        "createdAt": "2026-10-01T00:00:00Z",
        "isDraft": False,
        "mergeable": "MERGEABLE",
        "headRefOid": "h0",
        "headRefName": branch,
        "isCrossRepository": fork,
        "author": {"login": author},
        "labels": {"nodes": []},
        "closingIssuesReferences": {
            "nodes": [
                {"number": n, "repository": {"nameWithOwner": "o/r"}} for n in closes
            ]
        },
        "autoMergeRequest": None,
    }


def repo_node(name="o/r", topics=(), prs=(), issues=(), more_prs=0):
    return {
        "nameWithOwner": name,
        "isFork": False,
        "owner": {"login": name.split("/")[0]},
        "repositoryTopics": {"nodes": [{"topic": {"name": t}} for t in topics]},
        "defaultBranchRef": {
            "name": "main",
            "target": {"oid": "m1", "committedDate": "2026-10-20T00:00:00Z"},
        },
        "pullRequests": {"totalCount": more_prs + len(prs), "nodes": list(prs)},
        "issues": {"nodes": list(issues)},
        "vulnerabilityAlerts": {"nodes": []},
    }


def viewer(*repos, login=OWNER):
    return [
        {"data": {"viewer": {"login": login, "repositories": {"nodes": list(repos)}}}}
    ]


def commit(message, branch=None):
    prs = [{"headRefName": branch}] if branch else []
    return {"message": message, "associatedPullRequests": {"nodes": prs}}


def history(*commits):
    target = {"history": {"nodes": list(commits)}}
    return [{"data": {"repository": {"defaultBranchRef": {"target": target}}}}]


def landing(pr, sha, at="2026-10-20T12:00:00+00:00", repo="o/r"):
    return {"repo": repo, "pr": pr, "head": f"h{pr}", "sha": sha, "at": at}


def collected(replies, *args):
    patched, calls = fake_gh(replies)
    with patched:
        return backlog.collect(*args), calls


class CollectTest(unittest.TestCase):
    def test_the_owners_issue_linked_pull_request_on_an_agent_branch_is_read_in_full(
        self,
    ):
        replies = {
            REPOS_READ: viewer(repo_node(prs=[pr_node()])),
            PR_READ: [pull(labels={"nodes": [{"name": "agent:hold"}]})],
            "pulls/12/files": [{"filename": "a.py"}],
        }
        snapshot, _ = collected(replies)
        self.assertEqual(
            snapshot["repos"][0]["other_prs"],
            [
                {
                    "number": 12,
                    "title": "PR 12",
                    "author": OWNER,
                    "draft": False,
                    "created_at": "2026-10-01T00:00:00Z",
                    "labels": ["agent:hold"],
                    "linked_issues": [1],
                    "linked_refs": [],
                    "head": "h1",
                    "base": "main",
                    "state": "OPEN",
                    "merge_state": "CLEAN",
                    "review": {
                        "checks": None,
                        "pushed_at": "2026-10-21T09:00:00Z",
                        "files": ["a.py"],
                        "changed_files": 1,
                        "lines": 7,
                        "unresolved": 0,
                        "feedback_at": None,
                        "owner_feedback_at": None,
                    },
                }
            ],
        )

    def test_a_list_of_open_pull_requests_cut_short_is_marked(self):
        for more, cut in ((0, False), (1, True)):
            with self.subTest(more):
                snapshot, _ = collected({REPOS_READ: viewer(repo_node(more_prs=more))})
                self.assertIs(snapshot["repos"][0]["prs_cut"], cut)

    def test_an_agent_pull_request_that_cannot_be_read_keeps_its_place_and_cannot_land(
        self,
    ):
        for unread in (gh_error("HTTP 502: Bad Gateway"), "not json"):
            replies = {REPOS_READ: viewer(repo_node(prs=[pr_node()])), PR_READ: unread}
            with mock.patch("sys.stderr", io.StringIO()) as err:
                snapshot, _ = collected(replies)
            (pr,) = snapshot["repos"][0]["other_prs"]
            self.assertEqual(pr["review"], {})
            self.assertIn("o/r#12 not read", err.getvalue())
        L = cleared()
        L["tasks"][REVIEW]["head"] = pr["head"]
        result = plan({**landable(), "repos": snapshot["repos"]}, L, DUE)
        self.assertNotIn(REVIEW, keys(result))
        self.assertEqual((lands(result), why(result)), ([], [(12, "not open")]))
        self.assertEqual(L["tasks"][REVIEW]["status"], "land")

    def test_a_pull_request_read_onto_another_branch_than_the_default_cannot_land(
        self,
    ):
        for base, expected in [
            ("release", "base not the default branch"),
            ("main", "issue not the owner's"),
        ]:
            with self.subTest(base):
                replies = {
                    REPOS_READ: viewer(repo_node(prs=[pr_node()])),
                    PR_READ: [pull(headRefOid=HEAD, baseRefName=base)],
                    "pulls/12/files": [],
                }
                snapshot, _ = collected(replies)
                result = plan(
                    {**landable(), "repos": snapshot["repos"]}, cleared(), DUE
                )
                self.assertEqual(why(result), [(12, expected)])

    def test_any_other_pull_request_gets_its_head_and_no_review(self):
        others = {
            "someone else's": pr_node(author="eve"),
            "from a fork": pr_node(fork=True),
            "on the owner's own branch": pr_node(branch="fix/typo"),
            "on a branch that only starts like the agent's": pr_node(
                branch=AGENT_BRANCH + "-2"
            ),
            "on a dependency-work branch for a package named build": pr_node(
                branch="backlog-agent/o-r-dep-pip-build"
            ),
            "without an issue": pr_node(closes=()),
        }
        for name, node in others.items():
            with self.subTest(name):
                snapshot, _ = collected({REPOS_READ: viewer(repo_node(prs=[node]))})
                (got,) = snapshot["repos"][0]["other_prs"]
                self.assertEqual((got["head"], "review" in got), ("h0", False))

    def test_a_pull_request_that_only_mentions_an_issue_of_another_repo_is_the_agents(
        self,
    ):
        node = {**pr_node(closes=()), "body": "Part of o/other#3"}
        replies = {
            REPOS_READ: viewer(repo_node(prs=[node])),
            PR_READ: [pull()],
            "pulls/12/files": [],
        }
        (got,) = collected(replies)[0]["repos"][0]["other_prs"]
        self.assertIn("review", got)

    def test_a_repo_lands_only_with_the_topic_and_never_the_agents_own_repos(self):
        topics = ["backlog-agent-land"]
        replies = {
            REPOS_READ: viewer(
                repo_node("jakob1379/x", topics),
                repo_node("jakob1379/y", ["backlog-agent"]),
                repo_node("jakob1379/backlog-agent", topics),
                repo_node("jakob1379/nix-config", topics),
                repo_node("jakob1379/converge-review", topics),
                login="jakob1379",
            )
        }
        snapshot, _ = collected(replies)
        self.assertEqual(
            {r["repo"].split("/")[1]: r["land"] for r in snapshot["repos"]},
            {
                "x": True,
                "y": False,
                "backlog-agent": False,
                "nix-config": False,
                "converge-review": False,
            },
        )

    def test_a_landing_is_reverted_by_a_revert_commit_or_a_revert_pull_request(self):
        landed = [
            landing(12, "abc123"),
            landing(14, "def456", at="2026-10-19T12:00:00+00:00"),
            landing(15, "fed789"),
            landing(16, "aaa111", repo="o/elsewhere"),
        ]
        replies = {
            REPOS_READ: viewer(repo_node()),
            "since=2026-10-19T12:00:00+00:00": history(
                commit('Revert "fix"\n\nThis reverts commit abc123.'),
                commit('Revert "feat" (#20)', "revert-14-backlog-agent/o-r-2-build"),
                commit("Mentions fed789 and aaa111", "revert-150-x"),
                commit("feat: more"),
            ),
        }
        snapshot, calls = collected(replies, landed)
        self.assertEqual(snapshot["repos"][0]["reverted"], [12, 14])
        self.assertEqual(len([c for c in calls if HISTORY_READ in c]), 1)

    def test_a_revert_past_the_first_hundred_commits_still_pauses(self):
        replies = {
            REPOS_READ: viewer(repo_node()),
            HISTORY_READ: history(*[commit("feat: n")] * 100)
            + history(commit("This reverts commit abc123.")),
        }
        snapshot, calls = collected(replies, [landing(12, "abc123")])
        self.assertEqual(snapshot["repos"][0]["reverted"], [12])
        (read,) = [c for c in calls if HISTORY_READ in c]
        self.assertIn("--paginate --slurp", read)

    def test_a_landing_not_read_back_is_still_reverted_by_a_revert_commit(self):
        cases = {
            "the merge commit is read": ({"oid": "m9"}, "m9", [12]),
            "no merge commit to read": (None, "None", []),
        }
        for name, (merge_commit, reverts, expected) in cases.items():
            with self.subTest(name):
                replies = {
                    REPOS_READ: viewer(repo_node()),
                    PR_READ: [pull(state="MERGED", mergeCommit=merge_commit)],
                    HISTORY_READ: history(
                        commit(f'Revert "fix (#12)"\n\nThis reverts commit {reverts}.')
                    ),
                }
                snapshot, _ = collected(replies, [landing(12, None)])
                self.assertEqual(snapshot["repos"][0]["reverted"], expected)

    def test_a_landing_already_reverted_is_not_looked_for_again(self):
        old = landing(12, "abc123", at="2026-09-01T12:00:00+00:00")
        replies = {
            REPOS_READ: viewer(repo_node()),
            HISTORY_READ: history(commit("This reverts commit abc123.")),
        }
        for name, paused in [("still paused", True), ("pause lifted", False)]:
            with self.subTest(name):
                landed = [{**old, "reverted": paused}, landing(14, "def456")]
                snapshot, calls = collected(replies, landed)
                self.assertEqual(snapshot["repos"][0]["reverted"], [])
                (read,) = [c for c in calls if HISTORY_READ in c]
                self.assertIn("since=2026-10-20T12:00:00+00:00", read)

    def test_a_repo_without_a_landing_costs_no_history_read(self):
        replies = {REPOS_READ: viewer(repo_node("o/r"), repo_node("o/quiet"))}
        for args in ((), ([landing(16, "aaa111", repo="o/elsewhere")],)):
            snapshot, calls = collected(replies, *args)
            self.assertEqual([r["reverted"] for r in snapshot["repos"]], [[], []])
            self.assertFalse([c for c in calls if HISTORY_READ in c])

    def test_the_collect_command_reads_the_landings_from_the_ledger(self):
        replies = {
            REPOS_READ: viewer(repo_node()),
            HISTORY_READ: history(commit("This reverts commit abc123.")),
        }
        patched, _ = fake_gh(replies)
        with tempfile.TemporaryDirectory() as d:
            ledger = Path(d, "ledger.json")
            ledger.write_text(
                json.dumps({**empty(), "landed": [landing(12, "abc123")]})
            )
            with (
                patched,
                mock.patch.object(backlog, "LEDGER", ledger),
                mock.patch.object(linear, "CLIENT", Path(d, "no-client")),
                mock.patch.object(sys, "argv", ["backlog.py", "collect"]),
                mock.patch("sys.stdout", new_callable=io.StringIO) as out,
            ):
                backlog.main()
        self.assertEqual(json.loads(out.getvalue())["repos"][0]["reverted"], [12])

    def test_a_strangers_marker_comment_does_not_hide_the_owners_reply(self):
        marked = "\n <!-- backlog-agent -->\nhi"
        node = {
            "number": 1,
            "title": "t",
            "body": "",
            "createdAt": "2026-10-01T00:00:00Z",
            "author": {"login": OWNER},
            "labels": {"nodes": []},
            "comments": {
                "nodes": [
                    said(ME, "2026-10-21T10:00:00Z", marked),
                    said(ME, "2026-10-21T11:00:00Z", "like this"),
                    said(EVE, "2026-10-21T11:30:00Z", marked),
                ]
            },
        }
        snapshot, _ = collected({REPOS_READ: viewer(repo_node(issues=[node]))})
        info = card(
            1,
            "Needs info",
            [moved("In Progress", "Needs info", "agent", "2026-10-21T10:00:00Z")],
            labels=["needs-repro"],
        )
        snapshot["board"] = {"statuses": [], "cards": [info]}
        self.assertEqual(
            plan(snapshot, empty(), NOW)["card_ops"][:1],
            [mv(1, "Needs triage", remove=["needs-repro"])],
        )

    def test_an_issue_comment_starting_with_the_marker_after_whitespace_is_the_agents(
        self,
    ):
        def by_agent(body):
            node = {
                "number": 1,
                "title": "t",
                "body": "",
                "createdAt": "2026-10-01T00:00:00Z",
                "author": {"login": OWNER},
                "labels": {"nodes": []},
                "comments": {
                    "nodes": [
                        {
                            "author": {"login": OWNER},
                            "createdAt": "2026-10-02T00:00:00Z",
                            "body": body,
                        }
                    ]
                },
            }
            return backlog.issue_record(node, OWNER)["comments"][0]["by_agent"]

        self.assertTrue(by_agent("<!-- backlog-agent -->\nx"))
        self.assertTrue(by_agent("\n <!-- backlog-agent -->\nx"))
        self.assertFalse(by_agent("see <!-- backlog-agent -->"))


MAIN_CHECKS = "commits/main/check-runs"
REPO_READ = "repos/o/r"


def check_runs(status="completed", conclusion="success"):
    run = {"name": "ci", "status": status, "conclusion": conclusion}
    return [{"check_runs": [{**run, "started_at": "2026-10-21T09:05:00Z"}]}]


GREEN_CHECKS = {
    "rules/branches": REQUIRES_CI,
    MAIN_CHECKS: check_runs(),
    "check-runs": check_runs(),
    "/status": {"statuses": []},
    "pulls/12/files": [{"filename": "a.py"}],
    REPO_READ: {"topics": [backlog.LAND_TOPIC], "default_branch": "main"},
}
STILL_OPEN = pull(headRefOid=HEAD)
NOW_MERGED = pull(headRefOid=HEAD, state="MERGED", mergeCommit={"oid": "m9"})
MERGE = "pr merge"


def gh_error(text):
    return subprocess.CompletedProcess([], 1, "", text)


def clock(at):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return at or datetime.now(tz)

    return Clock


class LandCommandTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = Path(d.name)

    def land(self, L, replies, c=None, decided=None, tracker=None, now=None):
        c = c or landable()
        tracker = tracker or FakeLinear(cards=c.get("board", {}).get("cards", []))
        decided = decided or plan(c, L, DUE)
        paths = [self.dir / n for n in ("ledger.json", "c.json", "plan.json")]
        for path, content in zip(paths, (L, c, decided)):
            path.write_text(json.dumps(content))
        patched, self.calls = fake_gh({**GREEN_CHECKS, **replies})
        self.out = io.StringIO()
        with (
            patched,
            mock.patch("time.sleep"),
            mock.patch.object(linear, "_token", "t"),
            mock.patch("urllib.request.urlopen", side_effect=tracker),
            mock.patch.object(backlog, "LEDGER", paths[0]),
            mock.patch.object(backlog, "datetime", clock(now)),
            mock.patch.object(
                sys, "argv", ["backlog.py", "land", *map(str, paths[1:])]
            ),
            mock.patch("sys.stdout", self.out),
        ):
            backlog.main()
        return json.loads(self.out.getvalue())

    def ledger(self):
        return json.loads((self.dir / "ledger.json").read_text())

    def merges(self):
        return [c for c in self.calls if MERGE in c]

    def test_a_merge_that_succeeds_is_recorded_with_its_merge_commit(self):
        out = self.land(cleared(), {PR_READ: [STILL_OPEN, NOW_MERGED], MERGE: ""})
        self.assertEqual(
            out, {"landed": [{"repo": "o/r", "pr": 12, "sha": "m9"}], "refused": []}
        )
        self.assertEqual(
            self.merges(),
            [f"gh pr merge 12 --repo o/r --squash --match-head-commit {HEAD}"],
        )
        (entry,) = self.ledger()["landed"]
        self.assertEqual(
            without(entry, "at"), {"repo": "o/r", "pr": 12, "head": HEAD, "sha": "m9"}
        )
        self.assertLess(
            datetime.now(UTC) - datetime.fromisoformat(entry["at"]),
            timedelta(minutes=1),
        )

    def test_what_changed_after_the_plan_is_refused_and_nothing_is_merged(self):
        cases = {
            "the head moved": (pull(headRefOid="b" * 40), "verdict is stale"),
            "the owner closed it": (pull(headRefOid=HEAD, state="CLOSED"), "not open"),
            "the base changed": (
                pull(headRefOid=HEAD, baseRefName="release"),
                "base not the default branch",
            ),
            "the owner held it": (
                pull(headRefOid=HEAD, labels={"nodes": [{"name": "agent:hold"}]}),
                "held by the owner",
            ),
            "a check went red": (
                pull(headRefOid=HEAD, mergeStateStatus="UNSTABLE"),
                "merge state unstable",
            ),
            "the owner opened a thread": (
                pull(headRefOid=HEAD, threads=[thread(said(ME))]),
                "open feedback",
            ),
            "more comments than the read holds": (
                pull(headRefOid=HEAD, more=["comments"]),
                "open feedback",
            ),
            "more reviews than the read holds": (
                pull(headRefOid=HEAD, more=["reviews"]),
                "open feedback",
            ),
            "a thread longer than the read": (
                pull(
                    headRefOid=HEAD,
                    threads=[thread(said(BOT), resolved=True, more=True)],
                ),
                "open feedback",
            ),
        }
        for name, (fresh, expected) in cases.items():
            with self.subTest(name):
                out = self.land(cleared(), {PR_READ: [fresh]})
                self.assertEqual(
                    out,
                    {
                        "landed": [],
                        "refused": [{"repo": "o/r", "pr": 12, "why": expected}],
                    },
                )
                self.assertEqual(self.merges(), [])
                self.assertEqual(self.ledger().get("landed", []), [])
                self.assertEqual(lands(plan(landable(), self.ledger(), DUE)), [12])

    def test_what_changed_outside_the_pull_request_after_the_plan_is_refused(self):
        down = urllib.error.HTTPError(
            linear.API, 502, "bad gateway", Message(), io.BytesIO(b"bad gateway")
        )
        cases = {
            "the land topic was taken off": (
                {REPO_READ: {"topics": [], "default_branch": "main"}},
                None,
                "shadow",
            ),
            "the default branch was renamed": (
                {
                    REPO_READ: {
                        "topics": [backlog.LAND_TOPIC],
                        "default_branch": "trunk",
                    }
                },
                None,
                "base not the default branch",
            ),
            "the default branch is no longer green": (
                {MAIN_CHECKS: check_runs("in_progress", None)},
                None,
                "default branch pending",
            ),
            "the card was moved": (
                {},
                FakeLinear(cards=[card(1, "Needs info")]),
                "card needs info",
            ),
            "the card was moved and its twin was not": (
                {},
                FakeLinear(
                    cards=[card(1, "In Review"), twin(1, "Needs info", ["att-1b"])]
                ),
                "card needs info",
            ),
            "the repo cannot be read": (
                {REPO_READ: gh_error("HTTP 502: Bad Gateway")},
                None,
                "gh api repos/o/r: HTTP 502: Bad Gateway",
            ),
            "the board cannot be read": ({}, down, "linear HTTP 502: bad gateway"),
            "the board reply is cut short": (
                {},
                http.client.IncompleteRead(b""),
                "IncompleteRead(0 bytes read)",
            ),
            "the board has no single owner": (
                {},
                FakeLinear(owners=2),
                "expected one human Linear admin as the owner, found 2",
            ),
        }
        for name, (replies, tracker, expected) in cases.items():
            with self.subTest(name):
                replies = {PR_READ: [STILL_OPEN, NOW_MERGED], MERGE: "", **replies}
                out = self.land(cleared(), replies, tracker=tracker)
                self.assertEqual(
                    out,
                    {
                        "landed": [],
                        "refused": [{"repo": "o/r", "pr": 12, "why": expected}],
                    },
                )
                self.assertEqual(self.merges(), [])
                self.assertEqual(self.ledger().get("landed", []), [])
                self.assertEqual(lands(plan(landable(), self.ledger(), DUE)), [12])

    def test_a_landing_reverted_after_the_plan_pauses_the_repo(self):
        L = cleared()
        L["landed"] = [landing(9, "s9")]
        replies = {
            PR_READ: [STILL_OPEN, NOW_MERGED],
            MERGE: "",
            HISTORY_READ: history(commit("This reverts commit s9.")),
        }
        out = self.land(L, replies)
        self.assertEqual(
            out["refused"], [{"repo": "o/r", "pr": 12, "why": "paused after a revert"}]
        )
        self.assertEqual(self.merges(), [])
        self.assertEqual([e["pr"] for e in self.ledger()["landed"]], [9])
        result = plan(landable(), self.ledger(), DUE)
        self.assertEqual(
            (lands(result), why(result)), ([], [(12, "paused after a revert")])
        )

    def test_a_pause_the_owner_lifted_is_not_put_back_by_the_same_revert(self):
        L = cleared()
        L["landed"] = [{**landing(9, "s9"), "reverted": False}]
        replies = {
            PR_READ: [STILL_OPEN, NOW_MERGED],
            MERGE: "",
            HISTORY_READ: history(commit("This reverts commit s9.")),
        }
        out = self.land(L, replies)
        self.assertEqual(out["landed"], [{"repo": "o/r", "pr": 12, "sha": "m9"}])

    def test_a_pull_request_whose_verdict_is_gone_is_refused(self):
        L = cleared()
        decided = plan(landable(), L, DUE)
        start(L, REVIEW, "t", DUE)
        for name, ledger, expected in [
            ("a review is running", L, "no verdict"),
            ("no entry", empty(), "not offered"),
        ]:
            with self.subTest(name):
                out = self.land(ledger, {PR_READ: [STILL_OPEN]}, decided=decided)
                self.assertEqual(
                    out["refused"], [{"repo": "o/r", "pr": 12, "why": expected}]
                )
                self.assertEqual(self.merges(), [])

    def test_a_verdict_with_a_value_missing_is_refused(self):
        for field, expected in [
            ("head", "not offered"),
            ("started", "open feedback"),
            ("finished", "lands none"),
        ]:
            with self.subTest(field):
                L = cleared()
                decided = plan(landable(), L, DUE)
                del L["tasks"][REVIEW][field]
                out = self.land(L, {PR_READ: [STILL_OPEN]}, decided=decided)
                self.assertEqual(
                    out["refused"], [{"repo": "o/r", "pr": 12, "why": expected}]
                )
                self.assertEqual(self.merges(), [])

    def test_a_pull_request_the_snapshot_does_not_list_is_refused(self):
        L = cleared()
        decided = plan(landable(), L, DUE)
        out = self.land(L, {PR_READ: [STILL_OPEN]}, cands(repo()), decided)
        self.assertEqual(
            out["refused"],
            [{"repo": "o/r", "pr": 12, "why": "issue not the owner's"}],
        )
        self.assertEqual(self.merges(), [])

    def test_a_merge_that_was_not_made_is_not_offered_again(self):
        refusal = "GraphQL: Pull request is not mergeable (mergePullRequest)"
        moved_on = pull(headRefOid="b" * 40, state="MERGED", mergeCommit={"oid": "m9"})
        cases = {
            "the merge command failed": (
                gh_error(f"X Pull request o/r#12 is not mergeable\n{refusal}\n"),
                STILL_OPEN,
                refusal,
            ),
            "the merge command said nothing and merged nothing": (
                "",
                STILL_OPEN,
                "merge not made",
            ),
            "another head was merged": ("", moved_on, "merge not made"),
        }
        for name, (merge, after, expected) in cases.items():
            with self.subTest(name):
                replies = {PR_READ: [STILL_OPEN, after], MERGE: merge}
                out = self.land(cleared(), replies)
                self.assertEqual(
                    out,
                    {
                        "landed": [],
                        "refused": [{"repo": "o/r", "pr": 12, "why": expected}],
                    },
                )
                self.assertEqual(len(self.merges()), 1)
                self.assertEqual(self.ledger().get("landed", []), [])
                result = plan(landable(), self.ledger(), DUE)
                self.assertEqual(
                    (lands(result), why(result)), ([], [(12, "merge not made")])
                )

    def test_a_merge_that_was_tried_is_not_made_by_running_the_same_plan_again(self):
        L, c = cleared(), landable()
        decided = plan(c, L, DUE)
        failed = {PR_READ: [STILL_OPEN], MERGE: gh_error("not mergeable")}
        self.land(L, failed, c, decided)
        again = {PR_READ: [STILL_OPEN, NOW_MERGED], MERGE: ""}
        out = self.land(self.ledger(), again, c, decided)
        self.assertEqual(
            out,
            {
                "landed": [],
                "refused": [{"repo": "o/r", "pr": 12, "why": "not offered"}],
            },
        )
        self.assertEqual(self.merges(), [])
        entry = self.ledger()["tasks"][REVIEW]
        self.assertEqual((entry["offered"], entry["tried"]), (None, HEAD))
        result = plan(c, self.ledger(), DUE)
        self.assertEqual((lands(result), why(result)), ([], [(12, "merge not made")]))

    def test_a_plan_that_offered_another_head_merges_nothing(self):
        L, c = cleared(), landable()
        decided = plan(c, L, DUE)
        decided["lands"][0]["head"] = "0" * 40
        out = self.land(L, {PR_READ: [STILL_OPEN, NOW_MERGED], MERGE: ""}, c, decided)
        self.assertEqual(
            out,
            {
                "landed": [],
                "refused": [{"repo": "o/r", "pr": 12, "why": "not offered"}],
            },
        )
        self.assertEqual(self.calls, [])
        self.assertEqual(self.ledger()["tasks"][REVIEW]["offered"], HEAD)

    def test_an_offer_with_no_head_or_for_another_verdict_merges_nothing(self):
        for name, offered, verdict, offer in [
            ("no head", None, HEAD, {"head": None}),
            ("no head key", None, HEAD, {}),
            ("a newer verdict", HEAD, "b" * 40, {"head": HEAD}),
        ]:
            with self.subTest(name):
                L, c = cleared(), landable()
                decided = plan(c, L, DUE)
                L["tasks"][REVIEW].update(offered=offered, head=verdict)
                decided["lands"][0] = {"repo": "o/r", "pr": 12, **offer}
                out = self.land(L, {}, c, decided)
                self.assertEqual(
                    out["refused"], [{"repo": "o/r", "pr": 12, "why": "not offered"}]
                )
                self.assertEqual(self.calls, [])
                self.assertEqual(self.ledger().get("landed", []), [])

    def test_a_merge_cut_off_midway_is_already_on_record(self):
        with self.assertRaises(KeyboardInterrupt):
            self.land(cleared(), {PR_READ: [STILL_OPEN], MERGE: KeyboardInterrupt()})
        (cut,) = self.ledger()["landed"]
        self.assertEqual((cut["pr"], cut["sha"]), (12, None))
        self.assertEqual(self.ledger()["tasks"][REVIEW]["tried"], HEAD)
        result = plan(landable(), self.ledger(), DUE)
        self.assertEqual((lands(result), why(result)), ([], [(12, "merge not made")]))

    def test_a_pull_request_that_cannot_be_read_is_refused_and_decided_again(self):
        cases = {
            "gh fails": (
                gh_error("HTTP 502: Bad Gateway"),
                "gh api graphql: HTTP 502: Bad Gateway",
            ),
            "gh prints no JSON": (
                "not json",
                "Expecting value: line 1 column 1 (char 0)",
            ),
        }
        for name, (unread, expected) in cases.items():
            with self.subTest(name):
                c = spread(13, 12)
                replies = {
                    "-F number=13": unread,
                    PR_READ: [STILL_OPEN, NOW_MERGED],
                    MERGE: "",
                }
                out = self.land(cleared("o/r#13:review", "o/r12#12:review"), replies, c)
                self.assertEqual(
                    out,
                    {
                        "landed": [{"repo": "o/r12", "pr": 12, "sha": "m9"}],
                        "refused": [{"repo": "o/r", "pr": 13, "why": expected}],
                    },
                )
                self.assertEqual([e["pr"] for e in self.ledger()["landed"]], [12])
                left = spread(13, 12)
                left["repos"] = left["repos"][:1]
                self.assertEqual(lands(plan(left, self.ledger(), DUE)), [13])

    def test_a_crash_on_the_second_keeps_the_first_landing(self):
        L, c = cleared(), landable()
        decided = plan(c, L, DUE)
        decided["lands"].append({"repo": "o/r"})
        with self.assertRaises(KeyError):
            self.land(L, {PR_READ: [STILL_OPEN, NOW_MERGED], MERGE: ""}, c, decided)
        self.assertEqual([e["pr"] for e in self.ledger().get("landed", [])], [12])
        self.assertEqual(
            [t["pr"] for t in json.loads(self.out.getvalue())["landed"]], [12]
        )

    def test_a_daily_limit_reached_after_the_plan_is_refused(self):
        L = cleared()
        decided = plan(landable(), L, DUE)
        L["landed"] = [
            landing(n, f"s{n}", at=DUE.isoformat(), repo="o/elsewhere")
            for n in range(3)
        ]
        out = self.land(L, {PR_READ: [STILL_OPEN]}, decided=decided, now=DUE)
        self.assertEqual(
            out["refused"], [{"repo": "o/r", "pr": 12, "why": "daily limit"}]
        )
        self.assertEqual(self.merges(), [])

    def test_a_merge_that_cannot_be_read_back_is_not_offered_again(self):
        for name, after, merge, said in [
            ("gh fails", gh_error("HTTP 502: Bad Gateway"), "", "merge not made"),
            ("gh prints no JSON", "not json", "", "merge not made"),
            ("the merge reply was lost too", "not json", gh_error("EOF"), "EOF"),
        ]:
            with self.subTest(name):
                out = self.land(cleared(), {PR_READ: [STILL_OPEN, after], MERGE: merge})
                self.assertEqual(
                    out,
                    {
                        "landed": [],
                        "refused": [{"repo": "o/r", "pr": 12, "why": said}],
                    },
                )
                self.assertEqual(len(self.merges()), 1)
                (unconfirmed,) = self.ledger()["landed"]
                self.assertEqual((unconfirmed["pr"], unconfirmed["sha"]), (12, None))
                result = plan(landable(), self.ledger(), DUE)
                self.assertEqual(
                    (lands(result), why(result)), ([], [(12, "merge not made")])
                )


if __name__ == "__main__":
    unittest.main()
