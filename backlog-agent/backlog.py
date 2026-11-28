#!/usr/bin/env python3
import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import linear

MAX_RUNNING = 3
DAILY_CAP = 100
TZ = ZoneInfo("Europe/Copenhagen")
MAX_ATTEMPTS = 2
OVERDUE_AFTER = timedelta(hours=6)
STALE_DONE_AFTER = timedelta(hours=48)
STALE_NOTE = "still open 48h after agent finished"
STUCK_AFTER = timedelta(hours=2)
STALE_QUEUE_AFTER = timedelta(hours=24)
STALE_REVIEW_AFTER = timedelta(days=7)
QUEUE_STATUSES = {"Needs triage", "Approved"}
CLOSED_STATUSES = {"Done", "Canceled", "Duplicate"}
RED_NOTE = "still red 2h after agent finished"
SKIP_LABELS = {"needs-review", "agent:blocked"}
NEEDS_REVIEW_CAP = 5
SKIP_STATUSES = {"running", "done", "blocked"}
ADD_CI_REPOS = {"jakob1379/pacify", "jakob1379/br18", "jakob1379/brobizz-usage"}
BACKLOG_TIERS = {"build": 8, "triage": 9}
TOPUP_LIMIT = 3
NEW_ISSUE_AGE = timedelta(hours=24)
SCAN_INTERVAL = timedelta(days=7)
SCAN_ACTIVE_WITHIN = timedelta(days=30)
SCAN_OPEN_LIMIT = 3
ISSUE_PAGE = 100
SYNC_GRACE = timedelta(hours=1)
TOKEN_WARN = timedelta(days=7)
REASONS = ("needs-repro", "unable-to-fix", "failed")
OUTCOMES = {
    "fix-pending": "In Review",
    "needs-approval": "Needs approval",
    "needs-repro": "Needs info",
    "unable-to-fix": "Needs info",
    "not-actionable": "Canceled",
}
REVIEW_ROUNDS = 3
REVIEW_QUIET = timedelta(minutes=30)
AGENT_BRANCH = r"backlog-agent/.+-(build|triage)"
LAND_TOPIC = "backlog-agent-land"
NEVER_LAND_REPOS = {
    "jakob1379/backlog-agent",
    "jakob1379/nix-config",
    "jakob1379/converge-review",
}
LAND_DELAY = timedelta(hours=24)
LAND_MAX_FILES = 10
LAND_MAX_LINES = 300
LAND_DAILY_CAP = 3
LAND_RECORD = timedelta(days=60)
HOLD_LABEL = "agent:hold"
COMMIT = r"\b[0-9a-f]{40}\b"
PROTECTED_NAMES = """
.github .gitlab-ci.yml .pre-commit-config.yaml CODEOWNERS
.claude .agents .cursor* .mcp.json CLAUDE.md CLAUDE.local.md AGENTS.md GEMINI.md
SKILL.md
pyproject.toml setup.py setup.cfg requirements*.txt tox.ini noxfile.py pytest.ini
conftest.py .coveragerc codecov.y*ml package.json tsconfig*.json jest.config.*
vitest.config.* playwright.config.* Cargo.toml go.mod go.sum *.lock
package-lock.json pnpm-lock.yaml *.nix Makefile justfile Dockerfile*
*compose*.y*ml ruff.toml .ruff.toml mypy.ini biome.json* eslint.config.*
.eslintrc*
scripts *.sh .env*
"""
PROTECTED = re.compile(
    "(?:^|/)(?:"
    + "|".join(
        re.escape(name).replace(r"\*", "[^/]*") for name in PROTECTED_NAMES.split()
    )
    + ")(?:/|$)",
    re.IGNORECASE,
)

LEDGER = Path(os.environ.get("BACKLOG_LEDGER", Path(__file__).with_name("ledger.json")))
WORK = Path(
    os.environ.get("BACKLOG_WORK", Path(__file__).resolve().parent.parent / "work")
)


def load_ledger(path: Path) -> dict:
    if not path.exists():
        return {"tasks": {}, "starts": [], "cap_announced": None}
    return json.loads(path.read_text())


def save_ledger(path: Path, ledger: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def start(
    ledger: dict, key: str, task_id: str, now: datetime, thread_id: str = ""
) -> None:
    entry = ledger["tasks"].get(key, {"attempts": 0})
    entry.update(
        status="running",
        task_id=task_id,
        thread_id=thread_id,
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


def backlog_kind(key: str) -> str | None:
    m = re.fullmatch(r".+#\d+:(triage|build)", key)
    return m and m[1]


def scan_repo(key: str) -> str | None:
    m = re.fullmatch(r"([^:#]+):scan", key)
    return m and m[1]


def review_pr(key: str) -> tuple[str, int] | None:
    m = re.fullmatch(r"(.+)#(\d+):review", key)
    return m and (m[1], int(m[2]))


def finish_review(ledger: dict, key: str, status: str, note: str, now: datetime) -> str:
    entry = ledger["tasks"].get(key)
    if not entry or entry["status"] != "running":
        if entry and entry["status"] == "land" and status != "land":
            entry["status"] = "no-land"
            return "no-land"
        raise SystemExit(f"{key} is not running, so it takes no verdict")
    if status == "land" and not re.match("clean " + COMMIT, note):
        status = "no-land"
    elif status not in ("land", "no-land"):
        status = "failed"
    head = re.search(COMMIT, note)
    entry.update(
        status=status,
        note=note,
        finished=now.isoformat(),
        crashed=status == "failed",
        head=head and head[0],
    )
    return status


def finish(ledger: dict, key: str, status: str, note: str, now: datetime) -> str:
    if review_pr(key):
        return finish_review(ledger, key, status, note, now)
    entry = ledger["tasks"][key]
    if status not in (
        {*OUTCOMES, "blocked"} if backlog_kind(key) else {"done", "blocked"}
    ):
        status = "failed"
    crashed = status == "failed"
    if crashed and entry["attempts"] >= MAX_ATTEMPTS:
        status = "blocked"
    entry.update(status=status, note=note, finished=now.isoformat(), crashed=crashed)
    return status


def assign(ledger: dict, key: str, task_id: str) -> None:
    entry = ledger["tasks"].get(key)
    if not entry or entry["status"] != "running" or entry["task_id"]:
        raise SystemExit(f"{key} is not claimed and waiting for a task id")
    entry["task_id"] = task_id


def lock_path(work: Path, key: str) -> Path:
    return work / (re.sub(r"[/#:]", "-", key) + ".lock")


def write_lock(work: Path, key: str, entry: dict) -> None:
    work.mkdir(exist_ok=True)
    fields = ("thread_id", "task_id", "started")
    lock = {"key": key, **{f: entry[f] for f in fields}}
    lock_path(work, key).write_text(json.dumps(lock, indent=2) + "\n")


def release(work: Path, key: str) -> None:
    lock = lock_path(work, key)
    lock.unlink(missing_ok=True)
    shutil.rmtree(lock.with_suffix(""), ignore_errors=True)


def sweep_locks(work: Path, now: datetime) -> tuple[set[str], list[str]]:
    busy, stale = set(), []
    for f in sorted(work.glob("*.lock")):
        lock = json.loads(f.read_text())
        if now - datetime.fromisoformat(lock["started"]) >= OVERDUE_AFTER:
            release(work, lock["key"])
            stale.append(lock["key"])
        else:
            busy.add(lock["key"])
    for d in work.glob("[!.]*"):
        if d.is_dir() and not d.with_name(d.name + ".lock").exists():
            shutil.rmtree(d, ignore_errors=True)
    return busy, stale


def running_tasks(ledger: dict, now: datetime) -> list[dict]:
    return [
        {
            "key": key,
            "task_id": e["task_id"],
            "thread_id": e.get("thread_id", ""),
            "attempts": e["attempts"],
            "overdue": now - datetime.fromisoformat(e["started"]) >= OVERDUE_AFTER,
        }
        for key, e in ledger["tasks"].items()
        if e["status"] == "running"
    ]


def used_today(ledger: dict, now: datetime) -> int:
    today = now.astimezone(TZ).date()
    return sum(
        datetime.fromisoformat(t).astimezone(TZ).date() == today
        for t in ledger["starts"]
    )


PASSING = {"SUCCESS", "NEUTRAL", "SKIPPED"}
WAITING = {"PENDING", "EXPECTED"}


def required_state(contexts: list[dict], required: list[str]) -> str | None:
    if not required:
        return None
    latest = {}
    for node in contexts:
        name = node.get("name") or node.get("context")
        if name in required and node.get("startedAt", "") >= latest.get(name, {}).get(
            "startedAt", ""
        ):
            latest[name] = node
    states = []
    for name in required:
        node = latest.get(name)
        if (
            node is None
            or node.get("status", "COMPLETED") != "COMPLETED"
            or node.get("state") in WAITING
        ):
            states.append("pending")
        else:
            states.append(
                "green"
                if (node.get("conclusion") or node.get("state")) in PASSING
                else "red"
            )
    return "red" if "red" in states else "pending" if "pending" in states else "green"


VERSION_BUMP = re.compile(r"from v?(\d+)(?:\.\d+)*\b\S* to v?(\d+)(?:\.\d+)*\b")


def is_major(text: str) -> bool:
    return any(a != b for a, b in VERSION_BUMP.findall(text))


def dependency_lines(p: dict) -> str:
    lines = [
        ln for ln in p["body"].splitlines() if ln.startswith(("Bumps ", "Updates "))
    ]
    return "\n".join([p["title"], *lines])


def covering_pr(package: str, prs: list[dict]) -> int | None:
    pattern = re.compile(rf"(?<![\w.-]){re.escape(package)}(?![\w.-])", re.IGNORECASE)
    matches = [p["number"] for p in prs if pattern.search(dependency_lines(p))]
    return min(matches, default=None)


def pr_needs_work(p: dict, now: datetime) -> bool:
    if SKIP_LABELS & set(p["labels"]):
        return False
    if p["checks"] is None or p["mergeable"] == "CONFLICTING" or p["checks"] == "red":
        return True
    since = datetime.fromisoformat(p["auto_merge_since"] or p["created_at"])
    return p["checks"] == "green" and now - since >= STUCK_AFTER


SEVERITY = {"critical": 0, "high": 1, "medium": 2, "low": 3}
NO_SEVERITY = 4


def alert_key(repo: str, a: dict) -> str:
    return f"{repo}:dep:{a['ecosystem']}:{re.sub(r'[-_.]+', '-', a['package']).lower()}"


def candidate_tasks(candidates: dict, now: datetime) -> list[dict]:
    ranked = []
    for r in candidates["repos"]:
        base = {"repo": r["repo"], "has_ci": r["has_ci"]}
        covered, groups = {}, {}
        for a in r["alerts"]:
            sev = SEVERITY[a["severity"]]
            if a["covered_by_pr"] is None:
                groups.setdefault(alert_key(r["repo"], a), []).append(a)
            else:
                covered[a["covered_by_pr"]] = min(
                    sev, covered.get(a["covered_by_pr"], NO_SEVERITY)
                )
        repo_sev = min(
            (SEVERITY[a["severity"]] for a in r["alerts"]), default=NO_SEVERITY
        )
        for key, alerts in groups.items():
            sev = min(SEVERITY[a["severity"]] for a in alerts)
            task = {
                **base,
                "key": key,
                "kind": "alert",
                "alerts": alerts,
            }
            ranked.append(
                (1 if sev <= 1 else 3, sev, min(a["created_at"] for a in alerts), task)
            )
        if r["has_ci"] and r["main"] == "red":
            task = {**base, "key": f"{r['repo']}:fix-main", "kind": "fix-main"}
            ranked.append((2 if repo_sev <= 1 else 4, repo_sev, "", task))
        for p in r["prs"]:
            if pr_needs_work(p, now):
                sev = covered.get(p["number"], NO_SEVERITY)
                tier = 1 if sev <= 1 else 6 if p["major"] else 5
                task = {
                    **base,
                    "key": f"{r['repo']}#{p['number']}",
                    "kind": "pr",
                    "pr": p["number"],
                }
                ranked.append((tier, sev, p["created_at"], task))
        if r["repo"] in ADD_CI_REPOS and not r["has_ci"]:
            ranked.append(
                (
                    7,
                    NO_SEVERITY,
                    "",
                    {**base, "key": f"{r['repo']}:add-ci", "kind": "add-ci"},
                )
            )
    ranked.sort(key=lambda x: x[:3])
    return [{**task, "tier": tier} for tier, _, _, task in ranked]


def present_keys(candidates: dict) -> set[str]:
    keys = set()
    for r in candidates["repos"]:
        name = r["repo"]
        keys |= {f"{name}#{p['number']}" for p in r["prs"]}
        keys |= {alert_key(name, a) for a in r["alerts"]}
        if r["has_ci"] and r["main"] != "green":
            keys.add(f"{name}:fix-main")
        if not r["has_ci"]:
            keys.add(f"{name}:add-ci")
        for i in r.get("issues", []):
            keys |= {f"{name}#{i['number']}:{kind}" for kind in BACKLOG_TIERS}
        keys |= {
            f"{name}#{p['number']}:review"
            for p in r.get("other_prs", [])
            if "review" in p
        }
    return keys


def in_status_for(card: dict, now: datetime) -> timedelta:
    since = next(
        (h["at"] for h in reversed(card["history"]) if h["to"] == card["status"]),
        card["created_at"],
    )
    return now - datetime.fromisoformat(since)


def health(candidates: dict, ledger: dict, tasks: list[dict], now: datetime) -> dict:
    cards = candidates.get("board", {}).get("cards", [])
    return {
        "unhandled_dependency": sum(
            t["kind"] in ("pr", "alert")
            and ledger["tasks"].get(t["key"], {}).get("status") not in SKIP_STATUSES
            for t in tasks
        ),
        "stale_queue": sum(
            c["status"] in QUEUE_STATUSES and in_status_for(c, now) > STALE_QUEUE_AFTER
            for c in cards
        ),
        "topup_waiting": sum(
            c["status"] == "Needs approval"
            and c["link"] is not None
            and ledger.get("origins", {}).get(
                f"{c['link']['repo']}#{c['link']['number']}"
            )
            == "topup"
            for c in cards
        ),
        "stale_review": sum(
            c["status"] == "In Review" and in_status_for(c, now) > STALE_REVIEW_AFTER
            for c in cards
        ),
        "red_mains": sum(r["main"] == "red" for r in candidates["repos"]),
        "pending_mains": sum(r["main"] == "pending" for r in candidates["repos"]),
    }


def move(
    card_id: str, to: str, add: list | tuple = (), remove: list | tuple = ()
) -> dict:
    return {
        "op": "move",
        "card": card_id,
        "to": to,
        "add_labels": list(add),
        "remove_labels": list(remove),
    }


def outcome_op(card: dict, outcome: str) -> dict | None:
    to = "Needs info" if outcome == "blocked" else OUTCOMES.get(outcome)
    if to == "Canceled":
        return {"op": "close", "card": card["id"], "to": to}
    if to:
        reason = "failed" if outcome == "blocked" else outcome
        add = [reason] if reason in REASONS else []
        stale = [lb for lb in card["labels"] if lb in REASONS and lb != reason]
        return move(card["id"], to, add, stale)
    return None


def owner_replied(issue: dict, history: list, owner: str | None) -> bool:
    marks = [
        datetime.fromisoformat(m["created_at"])
        for m in issue["comments"]
        if m["by_agent"]
    ] + [datetime.fromisoformat(h["at"]) for h in history[-1:]]
    since = max(marks, default=None)
    return since is not None and any(
        m["author"] == owner
        and not m["by_agent"]
        and datetime.fromisoformat(m["created_at"]) > since
        for m in issue["comments"]
    )


def issue_links(name: str, p: dict) -> list[str]:
    return [f"{name}#{n}" for n in p.get("linked_issues", [])] + p.get(
        "linked_refs", []
    )


def label_rank(issue: dict, card: dict) -> int:
    labels = {lb.lower() for lb in issue["labels"] + card["labels"]}
    return 0 if "security" in labels else 1 if "bug" in labels else 2


def backlog_work(
    candidates: dict, ledger: dict, now: datetime
) -> tuple[list[dict], list[dict], dict]:
    owner = candidates.get("owner")
    scope = {r["repo"] for r in candidates["repos"]}
    issues = {
        f"{r['repo']}#{i['number']}": i
        for r in candidates["repos"]
        for i in r.get("issues", [])
    }
    linked = {
        ref
        for r in candidates["repos"]
        for p in r.get("other_prs", [])
        for ref in issue_links(r["repo"], p)
    }
    origins = {k: v for k, v in ledger.get("origins", {}).items() if k in issues}
    ledger["origins"] = origins
    ops, ranked, chosen = [], [], {}
    for c in candidates.get("board", {}).get("cards", []):
        link = c["link"] or {}
        if (
            link.get("type") != "issue"
            or link["repo"] not in scope
            or c["status"] == "Duplicate"
        ):
            continue
        ref = f"{link['repo']}#{link['number']}"
        if ref in issues:
            if ref not in chosen or (
                chosen[ref]["status"] in CLOSED_STATUSES
                and c["status"] not in CLOSED_STATUSES
            ):
                chosen[ref] = c
        elif c["status"] == "In Review" and ref not in linked:
            ops.append({"op": "close", "card": c["id"], "to": "Done"})
    cards = list(chosen.items())
    status = {c["id"]: c["status"] for c in chosen.values()}

    def queue(kind, ref, card, origin):
        issue = issues[ref]
        if kind == "triage":
            origins[ref] = origin
        task = {
            "key": f"{ref}:{kind}",
            "kind": kind,
            "repo": card["link"]["repo"],
            "issue": issue["number"],
            "card": card["id"],
            "origin": origin,
            "tier": BACKLOG_TIERS[kind],
        }
        ranked.append(
            (task["tier"], label_rank(issue, card), issue["created_at"], task)
        )

    def to_triage(ref, card, origin):
        stale = [lb for lb in card["labels"] if lb in REASONS]
        ops.append(move(card["id"], "Needs triage", remove=stale))
        status[card["id"]] = "Needs triage"
        queue("triage", ref, card, origin)

    for ref, c in cards:
        issue, history = issues[ref], c["history"]
        missing = [
            lb
            for lb in ("kind: backlog", repo_label(c["link"]["repo"]))
            if lb not in c["labels"]
        ]
        if missing and c["status"] not in CLOSED_STATUSES:
            ops.append(move(c["id"], c["status"], add=missing))
        trusted = issue["author"] == owner or any(
            h["actor"] == "owner" and h["to"] == "Needs triage" for h in history
        )
        entries = {
            k: ledger["tasks"][k]
            for k in (f"{ref}:{kind}" for kind in BACKLOG_TIERS)
            if k in ledger["tasks"]
        }
        if any(e["status"] == "running" for e in entries.values()):
            continue
        for k, e in entries.items():
            kind = k.rsplit(":", 1)[1]
            if c["status"] == "In Progress" and e["status"] == "failed":
                queue(
                    kind,
                    ref,
                    c,
                    origins.get(ref, "topup") if kind == "triage" else "owner",
                )
                continue
            if c["status"] != "In Progress":
                del ledger["tasks"][k]
                continue
            outcome = e["status"]
            if (
                outcome == "fix-pending"
                and kind == "triage"
                and origins.get(ref) != "owner"
            ):
                outcome = "needs-approval"
            op = outcome_op(c, outcome)
            if op:
                ops.append(op)
                status[c["id"]] = op["to"]
        if c["status"] == "Approved" and history:
            if history[-1]["actor"] != "owner":
                ops.append(move(c["id"], "Needs approval"))
                status[c["id"]] = "Needs approval"
            elif history[-1]["from"] == "Needs approval":
                queue("build", ref, c, "owner")
        elif c["status"] == "Needs triage" and trusted:
            last = next(
                (h for h in reversed(history) if h["to"] == "Needs triage"), None
            )
            origin = (
                "owner"
                if last and last["actor"] == "owner"
                else origins.get(ref, "topup")
            )
            queue("triage", ref, c, origin)
        elif (
            c["status"] == "Needs info"
            and trusted
            and owner_replied(issue, history, owner)
        ):
            to_triage(ref, c, origins.get(ref, "topup"))
        elif (
            c["status"] == "Backlog"
            and not history
            and issue["author"] == owner
            and not scan_filed(ledger, ref, issue)
            and now - datetime.fromisoformat(issue["created_at"]) < NEW_ISSUE_AGE
        ):
            to_triage(ref, c, "owner")

    counts = Counter(status.values())
    going = any(
        e["status"] == "running" and backlog_kind(k) for k, e in ledger["tasks"].items()
    )
    if not (
        going or counts["Needs approval"] or counts["Approved"] or counts["In Progress"]
    ):
        pool = sorted(
            (
                (label_rank(issues[ref], c), issues[ref]["created_at"], ref, c)
                for ref, c in cards
                if status[c["id"]] == "Backlog"
                and issues[ref]["author"] == owner
                and not any(m["by_agent"] for m in issues[ref]["comments"])
            ),
            key=lambda x: x[:3],
        )
        for _, _, ref, c in pool[: max(0, TOPUP_LIMIT - counts["Needs triage"])]:
            to_triage(ref, c, "topup")
    ranked.sort(key=lambda x: x[:3])
    return [task for *_, task in ranked], ops, status


def scan_filed(ledger: dict, ref: str, issue: dict) -> bool:
    return bool(issue.get("by_agent")) or ref in ledger.get("found", [])


def scan_tasks(
    candidates: dict, ledger: dict, now: datetime, busy: frozenset
) -> list[dict]:
    entries = {scan_repo(k): e for k, e in ledger["tasks"].items() if scan_repo(k)}
    issue_cards = [
        ((c["link"]["repo"], c["link"]["number"]), c["status"])
        for c in candidates.get("board", {}).get("cards", [])
        if c["link"] and c["link"]["type"] == "issue"
    ]
    rejected = {ref for ref, status in issue_cards if status == "Canceled"} - {
        ref for ref, status in issue_cards if status not in CLOSED_STATUSES
    }
    limit = SCAN_OPEN_LIMIT - sum(
        scan_filed(ledger, f"{r['repo']}#{i['number']}", i)
        and (r["repo"], i["number"]) not in rejected
        for r in candidates["repos"]
        for i in r.get("issues", [])
    )
    if (
        limit <= 0
        or any(scan_repo(k) for k in busy)
        or any(e["status"] == "running" for e in entries.values())
    ):
        return []
    scans = ledger.get("scans", {})
    due = []
    for r in candidates["repos"]:
        repo, head = r["repo"], r.get("head")
        if (
            r.get("fork")
            or not head
            or entries.get(repo, {}).get("status") == "blocked"
            or len(r.get("issues", [])) + limit > ISSUE_PAGE
        ):
            continue
        last = scans.get(repo)
        if last and (
            last["head"] == head
            or now - datetime.fromisoformat(last["at"]) < SCAN_INTERVAL
        ):
            continue
        dormant = (
            not last and now - datetime.fromisoformat(r["head_at"]) > SCAN_ACTIVE_WITHIN
        )
        due.append(
            (
                repo not in entries,
                bool(last),
                dormant,
                last["at"] if last else "",
                repo,
                head,
            )
        )
    return [
        {
            "repo": repo,
            "key": f"{repo}:scan",
            "kind": "scan",
            "head": head,
            "limit": limit,
        }
        for *_, repo, head in sorted(due)[:1]
    ]


def owner_hold(p: dict) -> bool:
    labels = set(p.get("labels", [HOLD_LABEL]))
    return bool(p.get("draft", True) or {HOLD_LABEL, "agent:blocked"} & labels)


def later(a: str | None, b: str | None) -> bool:
    return bool(a and b) and datetime.fromisoformat(a) > datetime.fromisoformat(b)


def review_due(p: dict, entry: dict | None) -> bool:
    if not entry:
        return True
    review, started = p["review"], entry["started"]
    return (
        entry["status"] == "failed"
        or entry.get("head") != p["head"]
        or later(review.get("feedback_at"), started)
        or (review.get("checks") == "red" and later(review.get("pushed_at"), started))
    ) and (
        entry["attempts"] < REVIEW_ROUNDS
        or later(review.get("owner_feedback_at"), entry["finished"])
    )


def review_tasks(
    candidates: dict, ledger: dict, now: datetime, busy: frozenset
) -> list[dict]:
    tasks = ledger["tasks"]
    if (
        "board" not in candidates
        or any(review_pr(k) for k in busy)
        or any(e["status"] == "running" and review_pr(k) for k, e in tasks.items())
    ):
        return []
    in_review = {
        f"{c['link']['repo']}#{c['link']['number']}"
        for c in candidates["board"]["cards"]
        if c["link"] and c["link"]["type"] == "issue" and c["status"] == "In Review"
    }
    due = []
    for r in candidates["repos"]:
        for p in r.get("other_prs", []):
            review, refs = p.get("review", {}), issue_links(r["repo"], p)
            pushed = review.get("pushed_at")
            if (
                not (p.get("head") and pushed and refs)
                or not in_review.issuperset(refs)
                or owner_hold(p)
                or review.get("checks", "pending") == "pending"
                or any(
                    tasks.get(f"{ref}:{kind}", {}).get("status")
                    in ("running", "failed")
                    for ref in refs
                    for kind in BACKLOG_TIERS
                )
            ):
                continue
            last = max(
                datetime.fromisoformat(t)
                for t in (pushed, review.get("feedback_at"))
                if t
            )
            key = f"{r['repo']}#{p['number']}:review"
            entry = tasks.get(key)
            if now - last >= REVIEW_QUIET and review_due(p, entry):
                due.append((last, key, r["repo"], p, entry))
    return [
        {
            "key": key,
            "kind": "review",
            "repo": repo,
            "pr": p["number"],
            "head": p["head"],
            "round": (entry["attempts"] if entry else 0) + 1,
        }
        for _, key, repo, p, entry in sorted(due, key=lambda d: d[:2])[:1]
    ]


def lower(state: str | None) -> str:
    return (state or "none").lower()


def land_block(
    candidates: dict,
    repo: dict,
    pr: dict,
    entry: dict | None,
    ledger: dict,
    now: datetime,
    taken: int,
) -> str | None:
    name, head, review = repo["repo"], pr.get("head"), pr.get("review", {})
    if not entry or entry.get("status") != "land":
        return "no verdict"
    if not head or entry.get("head") != head:
        return "verdict is stale"
    if pr.get("state") != "OPEN":
        return "not open"
    if owner_hold(pr):
        return "held by the owner"
    refs, owner = issue_links(name, pr), candidates.get("owner")
    authors = {
        f"{r['repo']}#{i['number']}": i["author"]
        for r in candidates["repos"]
        for i in r.get("issues", [])
    }
    if not (refs and owner) or any(authors.get(ref) != owner for ref in refs):
        return "issue not the owner's"
    cards = {}
    for c in candidates.get("board", {}).get("cards", []):
        if c["link"] and c["link"]["type"] == "issue":
            ref = f"{c['link']['repo']}#{c['link']['number']}"
            cards.setdefault(ref, []).append(c["status"])
    for ref in refs:
        statuses = cards.get(ref, [None])
        if "In Review" not in statuses:
            return f"card {lower(statuses[0])}"
    if any(
        "review" in other
        and (r["repo"], other["number"]) != (name, pr.get("number"))
        and set(refs) & set(issue_links(r["repo"], other))
        for r in candidates["repos"]
        for other in r.get("other_prs", [])
    ):
        return "split across pull requests"
    if pr.get("merge_state") not in ("CLEAN", "HAS_HOOKS"):
        return f"merge state {lower(pr.get('merge_state'))}"
    if review.get("checks") != "green":
        return f"checks {lower(review.get('checks'))}"
    if repo.get("main") != "green":
        return f"default branch {lower(repo.get('main'))}"
    started = entry.get("started")
    if (
        review.get("unresolved") != 0
        or not started
        or "feedback_at" not in review
        or later(review["feedback_at"], started)
    ):
        return "open feedback"
    changed, lines = review.get("changed_files"), review.get("lines")
    if (
        changed is None
        or lines is None
        or changed > LAND_MAX_FILES
        or lines > LAND_MAX_LINES
    ):
        return "too large"
    files = review.get("files")
    if files is None or len(files) < changed:
        return "protected path none"
    for path in files:
        if PROTECTED.search(path):
            return f"protected path {path}"
    landed = ledger.get("landed", [])
    if any(e["repo"] == name and e.get("reverted") for e in landed):
        return "paused after a revert"
    if repo.get("land") is not True or name in NEVER_LAND_REPOS:
        return "shadow"
    if entry.get("offered") == head:
        return "merge not made"
    today = now.astimezone(TZ).date()
    if (
        taken
        + sum(
            datetime.fromisoformat(e["at"]).astimezone(TZ).date() == today
            for e in landed
        )
        >= LAND_DAILY_CAP
    ):
        return "daily limit"
    finished = entry.get("finished")
    if not finished:
        return "lands none"
    due = datetime.fromisoformat(finished) + LAND_DELAY
    return f"lands {due.isoformat()}" if now < due else None


def snapshot_pr(candidates: dict, name: str, number: int) -> tuple[dict, dict]:
    repos = {r["repo"]: r for r in candidates["repos"]}
    repo: dict = repos.get(name, {"repo": name})
    prs = {p["number"]: p for p in repo.get("other_prs", [])}
    return repo, prs.get(number, {})


def land_work(
    candidates: dict, ledger: dict, now: datetime
) -> tuple[list[dict], list[dict]]:
    repos = {r["repo"]: r for r in candidates["repos"]}
    kept = []
    for e in ledger.get("landed", []):
        repo = repos.get(e["repo"], {})
        if e["pr"] in repo.get("reverted", []):
            e["reverted"] = True
        if (
            repo.get("land")
            if e.get("reverted")
            else now - datetime.fromisoformat(e["at"]) <= LAND_RECORD
        ):
            kept.append(e)
    ledger["landed"] = kept
    verdicts = sorted(
        (e.get("finished") or "", key, *ref)
        for key, e in ledger["tasks"].items()
        if (ref := review_pr(key)) and e["status"] == "land"
    )
    lands, greenlit = [], []
    for _, key, name, number in verdicts:
        entry = ledger["tasks"][key]
        repo, pr = snapshot_pr(candidates, name, number)
        why = land_block(candidates, repo, pr, entry, ledger, now, len(lands))
        if not why and any(t["repo"] == name for t in lands):
            why = "default branch pending"
        task = {"key": key, "repo": name, "pr": number}
        if why:
            greenlit.append({**task, "why": why, "new": entry.get("held") != why})
            entry["held"] = why
        else:
            entry["offered"] = pr["head"]
            lands.append({**task, "head": pr["head"]})
    return lands, greenlit


def land(candidates: dict, ledger: dict, name: str, number: int, now: datetime) -> dict:
    task = {"repo": name, "pr": number}
    entry = ledger["tasks"].get(f"{name}#{number}:review")
    if entry:
        entry["offered"] = None
    owner = candidates.get("owner", "")
    repo, known = snapshot_pr(candidates, name, number)
    try:
        pr = {**known, **pr_detail(name, number, owner)}
    except RuntimeError as e:
        return {**task, "why": str(e).splitlines()[-1]}
    why = land_block(candidates, repo, pr, entry, ledger, now, 0)
    if why:
        return {**task, "why": why}
    head = entry["offered"] = entry["head"]
    merge = subprocess.run(
        [
            "gh",
            "pr",
            "merge",
            str(number),
            "--repo",
            name,
            "--squash",
            "--match-head-commit",
            head,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        after = pr_node(name, number)
    except RuntimeError:
        after = None
    merged = bool(after and after["state"] == "MERGED" and after["headRefOid"] == head)
    sha = (after["mergeCommit"] or {}).get("oid") if after and merged else None
    # An unread merge that gh reported made still counts for the daily limit and the pause.
    if merged or (after is None and not merge.returncode):
        ledger.setdefault("landed", []).append(
            {**task, "head": head, "sha": sha, "at": now.isoformat()}
        )
    if not merged:
        said = merge.stderr.strip().splitlines() or ["merge not made"]
        return {**task, "why": said[-1]}
    return {**task, "sha": sha}


def tidy_cards(cards: list[dict], scope: set[str]) -> tuple[list[dict], set[str]]:
    groups = {}
    for c in cards:
        link = c["link"]
        if link and link["repo"] in scope and c["status"] not in CLOSED_STATUSES:
            groups.setdefault((link["type"], link["repo"], link["number"]), []).append(
                c
            )
    ops, dropped = [], set()
    for group in groups.values():
        keep, *extra = sorted(
            group,
            key=lambda c: (
                c["status"] == "Backlog",
                not c["attachments"],
                c["created_at"],
            ),
        )
        if not keep["attachments"]:
            ops.append({"op": "link", "card": keep["id"], "link": keep["link"]})
        for c in extra:
            dropped.add(c["id"])
            ops.append(
                {
                    "op": "drop",
                    "card": c["id"],
                    "attachments": c["attachments"],
                    "to": "Duplicate",
                }
            )
    return ops, dropped


def create(link: dict | None, title: str, status: str, labels: list) -> dict:
    return {
        "op": "create",
        "link": link,
        "title": title,
        "status": status,
        "labels": labels,
    }


PR_URL = r"https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)"


def repo_label(name: str) -> str:
    return f"repo: {name.split('/', 1)[1]}"


def owner_cards(
    candidates: dict, ledger: dict, present: set[str], now: datetime
) -> list[dict]:
    if "board" not in candidates:
        return []
    repos = {r["repo"]: r for r in candidates["repos"]}
    cards = candidates["board"]["cards"]
    ops = []
    live = [c for c in cards if c["link"] and c["status"] != "Duplicate"]
    carded = {
        (c["link"]["repo"], c["link"]["number"])
        for c in live
        if c["link"]["type"] == "pr"
    }
    issue_carded = {
        (c["link"]["repo"], c["link"]["number"])
        for c in live
        if c["link"]["type"] == "issue"
    }
    for name, r in repos.items():
        for i in r.get("issues", []):
            if (name, i["number"]) in issue_carded or now - datetime.fromisoformat(
                i["created_at"]
            ) < SYNC_GRACE:
                continue
            link = {"repo": name, "number": i["number"], "type": "issue"}
            ops.append(
                create(link, i["title"], "Backlog", ["kind: backlog", repo_label(name)])
            )
    open_prs = {
        (name, p["number"])
        for name, r in repos.items()
        for p in r["prs"] + r.get("other_prs", [])
    }
    agent_prs = {
        (m[1], int(m[2])): key
        for key, e in ledger["tasks"].items()
        if not backlog_kind(key)
        and not scan_repo(key)
        and not review_pr(key)
        and e["status"] in ("done", "blocked")
        for m in re.finditer(PR_URL, e["note"])
    }
    for name, r in repos.items():
        for p in r.get("other_prs", []):
            if (
                any(ref.split("#")[0] in repos for ref in issue_links(name, p))
                or (name, p["number"]) in carded
                or (name, p["number"]) in agent_prs
            ):
                continue
            labels = ["kind: pr", repo_label(name)]
            if p["draft"]:
                labels.append("draft")
            link = {"repo": name, "number": p["number"], "type": "pr"}
            ops.append(create(link, p["title"], "In Review", labels))
    dependency = {}
    for c in cards:
        if c["status"] in CLOSED_STATUSES:
            continue
        link = c["link"]
        if (
            "kind: pr" in c["labels"]
            and link
            and link["repo"] in repos
            and (link["repo"], link["number"]) not in open_prs
        ):
            ops.append({"op": "close", "card": c["id"], "to": "Done"})
        key = c["title"].split(" ", 1)[0]
        if "kind: dependency" in c["labels"] and re.split("[#:]", key)[0] in repos:
            if key in present:
                dependency[key] = c
            else:
                ops.append({"op": "close", "card": c["id"], "to": "Done"})
    review = {
        f"{name}#{p['number']}"
        for name, r in repos.items()
        for p in r["prs"]
        if "needs-review" in p["labels"]
    } | {
        agent_prs[(name, p["number"])]
        for name, r in repos.items()
        for p in r.get("other_prs", [])
        if "needs-review" in p.get("labels", []) and (name, p["number"]) in agent_prs
    }
    for key, e in ledger["tasks"].items():
        if backlog_kind(key) or review_pr(key):
            continue
        if e["status"] in ("done", "blocked") and key in review:
            to = "In Review"
        elif e["status"] == "blocked":
            to = "Needs info"
        else:
            continue
        c = dependency.get(key)
        if c:
            if c["status"] != to:
                ops.append(move(c["id"], to))
            continue
        name, _, number = key.partition("#")
        link = {"repo": name, "number": int(number), "type": "pr"} if number else None
        title = " — ".join([key, *e["note"].splitlines()[:1]])
        labels = ["kind: dependency", repo_label(re.split("[#:]", key)[0])]
        ops.append(create(link, title, to, labels))
    return ops


def plan(
    candidates: dict,
    ledger: dict,
    now: datetime,
    thread_id: str = "",
    busy: frozenset = frozenset(),
) -> dict:
    present = present_keys(candidates)
    tidy_ops = []
    if "board" in candidates:
        board = candidates["board"]
        tidy_ops, dropped = tidy_cards(
            board["cards"], {r["repo"] for r in candidates["repos"]}
        )
        candidates = {
            **candidates,
            "board": {
                **board,
                "cards": [c for c in board["cards"] if c["id"] not in dropped],
            },
        }
    newest_alert = {}
    for r in candidates["repos"]:
        for a in r["alerts"]:
            k = alert_key(r["repo"], a)
            created = datetime.fromisoformat(a["created_at"])
            newest_alert[k] = max(newest_alert.get(k, created), created)
    red_prs = {
        f"{r['repo']}#{p['number']}"
        for r in candidates["repos"]
        for p in r["prs"]
        if p["checks"] == "red" and not SKIP_LABELS & set(p["labels"])
    }
    resolved, newly_blocked = [], []
    heads = {r["repo"]: r.get("head") for r in candidates["repos"]}
    scans = {k: v for k, v in ledger.get("scans", {}).items() if k in heads}
    generated = datetime.fromisoformat(candidates["generated"])
    cards = candidates.get("board", {}).get("cards", [])
    spans = []
    hold_scans = any(
        datetime.fromisoformat(v["until"]) > generated for v in scans.values()
    )
    for key, e in list(ledger["tasks"].items()):
        scanned = scan_repo(key)
        if scanned:
            spans.append((scanned, e["started"], e["finished"]))
        if e["status"] == "running":
            continue
        if scanned:
            finished = datetime.fromisoformat(e["finished"])
            if scanned not in heads:
                del ledger["tasks"][key]
            elif finished > generated:
                hold_scans = True
            elif e["status"] == "done" or (
                e["status"] == "blocked"
                and any(
                    c["status"] in CLOSED_STATUSES
                    and "kind: dependency" in c["labels"]
                    and c["title"].split(" ", 1)[0] == key
                    and datetime.fromisoformat(c["created_at"]) > finished
                    for c in cards
                )
            ):
                del ledger["tasks"][key]
                if not e["crashed"]:
                    scans[scanned] = {
                        "at": e["started"],
                        "until": e["finished"],
                        "head": e["head"],
                    }
            continue
        if key not in present:
            del ledger["tasks"][key]
            if e["status"] == "done":
                resolved.append(key)
        elif (
            # A child only saw the alerts collected before it started.
            e["status"] in ("done", "blocked")
            and key in newest_alert
            and newest_alert[key] > datetime.fromisoformat(e["started"])
        ):
            del ledger["tasks"][key]
        elif (
            e["status"] == "done"
            and now - datetime.fromisoformat(e["finished"]) >= STALE_DONE_AFTER
        ):
            e.update(status="blocked", note=STALE_NOTE)
            newly_blocked.append({"key": key, "note": STALE_NOTE})
        elif (
            e["status"] == "done"
            and key in red_prs
            and now - datetime.fromisoformat(e["finished"]) >= STUCK_AFTER
        ):
            e.update(status="blocked", note=RED_NOTE)
            newly_blocked.append({"key": key, "note": RED_NOTE})
    ledger["scans"] = scans
    present |= {k for k in ledger["tasks"] if scan_repo(k)}
    owner, found, still_open = candidates.get("owner"), set(), set()
    for r in candidates["repos"]:
        for i in r.get("issues", []):
            ref = f"{r['repo']}#{i['number']}"
            still_open.add(ref)
            created = datetime.fromisoformat(i["created_at"])
            if i["author"] == owner and any(
                r["repo"] == repo
                and datetime.fromisoformat(since)
                <= created
                <= (datetime.fromisoformat(until) if until else now)
                for repo, since, until in spans
            ):
                found.add(ref)
    found |= set(ledger.get("found", []))
    ledger["found"] = sorted(found if hold_scans else found & still_open)
    tasks = candidate_tasks(candidates, now)
    keys = {t["key"] for t in tasks}
    held = [
        t["key"]
        for t in tasks
        if t["kind"] == "pr" and t["tier"] != 1 and f"{t['repo']}:fix-main" in keys
    ]

    def review_only(t):
        return not t["has_ci"] or t["kind"] == "add-ci"

    running_keys = {k for k, e in ledger["tasks"].items() if e["status"] == "running"}
    review_room = (
        NEEDS_REVIEW_CAP
        - candidates["needs_review_open"]
        - sum(t["key"] in running_keys and review_only(t) for t in tasks)
    )
    ready = []
    for t in tasks:
        if (
            t["key"] in held
            or t["key"] in busy
            or ledger["tasks"].get(t["key"], {}).get("status") in SKIP_STATUSES
        ):
            continue
        if review_only(t):
            if review_room <= 0:
                continue
            review_room -= 1
        ready.append(t)
    ready += review_tasks(candidates, ledger, now, busy)
    lands, greenlit = land_work(candidates, ledger, now)
    backlog_tasks, card_ops, card_status = backlog_work(candidates, ledger, now)
    card_ops += owner_cards(candidates, ledger, present, now) + tidy_ops
    ready += [t for t in backlog_tasks if t["key"] not in busy]
    if not hold_scans:
        ready += [
            {**t, "profile": review_room > 0}
            for t in scan_tasks(candidates, ledger, now, busy)
        ]
    running = len(running_keys)
    used = used_today(ledger, now)
    slots = max(0, min(MAX_RUNNING - running, DAILY_CAP - used))
    today = now.astimezone(TZ).date().isoformat()
    announce_cap = (
        used >= DAILY_CAP and bool(ready) and ledger["cap_announced"] != today
    )
    if announce_cap:
        ledger["cap_announced"] = today
    expires = candidates.get("token_expires")
    rotate_token = (
        expires
        if expires
        and datetime.fromisoformat(expires) - now < TOKEN_WARN
        and ledger.get("rotation_announced") != today
        else None
    )
    if rotate_token:
        ledger["rotation_announced"] = today
    dispatch = []
    for t in ready[:slots]:
        start(ledger, t["key"], "", now, thread_id)
        dispatch.append({**t, "attempt": ledger["tasks"][t["key"]]["attempts"]})
        if t["kind"] in BACKLOG_TIERS and card_status[t["card"]] != "In Progress":
            card_ops.append(move(t["card"], "In Progress"))
        if t["kind"] == "scan":
            ledger["tasks"][t["key"]]["head"] = t["head"]
    return {
        "dispatch": dispatch,
        "card_ops": card_ops,
        "queued": [t["key"] for t in ready[slots:]],
        "held": held,
        "running": running,
        "used_today": used,
        "announce_cap": announce_cap,
        "rotate_token": rotate_token,
        "resolved": resolved,
        "newly_blocked": newly_blocked,
        "lands": lands,
        "greenlit": greenlit,
        "health": health(candidates, ledger, tasks, now),
    }


AGENT_MARKER = "<!-- backlog-agent -->"
ISSUE_REF = r"(?:https://github\.com/)?([\w.-]+/[\w.-]+)(?:#|/issues/)(\d+)\b"
OPT_IN_TOPIC = "backlog-agent"

QUERY = """query($endCursor: String) { viewer { login
  repositories(first: 10, after: $endCursor, ownerAffiliations: OWNER, isArchived: false) {
    pageInfo { hasNextPage endCursor }
    nodes {
      nameWithOwner isFork owner { login }
      repositoryTopics(first: 20) { nodes { topic { name } } }
      defaultBranchRef { name target { oid ... on Commit { committedDate } } }
      pullRequests(states: OPEN, first: 50) { nodes {
        number title body createdAt isDraft mergeable headRefOid headRefName
        isCrossRepository author { login }
        labels(first: 20) { nodes { name } }
        closingIssuesReferences(first: 10) { nodes { number repository { nameWithOwner } } }
        autoMergeRequest { enabledAt } } }
      issues(states: OPEN, first: 100) { nodes {
        number title body createdAt author { login }
        labels(first: 20) { nodes { name } }
        comments(last: 50) { nodes { author { login } createdAt body } } } }
      vulnerabilityAlerts(states: OPEN, first: 100) { nodes {
        createdAt
        securityAdvisory { ghsaId }
        securityVulnerability {
          severity vulnerableVersionRange
          package { name ecosystem }
          firstPatchedVersion { identifier } } } } } } } }"""


def gh(*args: str):
    for attempt in range(3):
        r = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
        if not r.returncode or attempt == 2:
            break
        if "rate limit" in r.stderr:
            time.sleep(60)
        elif "HTTP 4" in r.stderr:
            break
    if r.returncode:
        raise RuntimeError(f"gh {' '.join(args[:2])}: {r.stderr.strip()}")
    return json.loads(r.stdout)


def required_checks(repo: str, branch: str) -> list[str]:
    try:
        rules = gh("api", f"repos/{repo}/rules/branches/{branch}")
    except RuntimeError as e:
        if "HTTP 403" in str(e):
            return []
        raise
    return [
        c["context"]
        for r in rules
        if r["type"] == "required_status_checks"
        for c in r["parameters"]["required_status_checks"]
    ]


def commit_contexts(repo: str, sha: str) -> list[dict]:
    runs = gh(
        "api",
        "--paginate",
        "--slurp",
        f"repos/{repo}/commits/{sha}/check-runs?per_page=100",
    )
    status = gh("api", f"repos/{repo}/commits/{sha}/status")
    return [
        {
            "name": c["name"],
            "status": c["status"].upper(),
            "conclusion": (c["conclusion"] or "").upper() or None,
            "startedAt": c["started_at"] or "",
        }
        for page in runs
        for c in page["check_runs"]
    ] + [
        {"context": st["context"], "state": st["state"].upper()}
        for st in status["statuses"]
    ]


def sha_state(repo: str, sha: str, required: list[str]) -> str | None:
    return required_state(commit_contexts(repo, sha), required) if required else None


def login(node: dict) -> str:
    return (node["author"] or {}).get("login", "ghost")


def issue_record(i: dict, owner: str) -> dict:
    return {
        "number": i["number"],
        "title": i["title"],
        "author": login(i),
        "by_agent": login(i) == owner and i["body"].lstrip().startswith(AGENT_MARKER),
        "created_at": i["createdAt"],
        "labels": [lb["name"] for lb in i["labels"]["nodes"]],
        "comments": [
            {
                "author": login(c),
                "created_at": c["createdAt"],
                "by_agent": c["body"].lstrip().startswith(AGENT_MARKER),
            }
            for c in i["comments"]["nodes"]
        ],
    }


def other_pr_record(name: str, p: dict) -> dict:
    closes = [
        f"{i['repository']['nameWithOwner']}#{i['number']}"
        for i in p["closingIssuesReferences"]["nodes"]
    ]
    mentions = [f"{m[1]}#{m[2]}" for m in re.finditer(ISSUE_REF, p["body"])]
    return {
        "number": p["number"],
        "title": p["title"],
        "author": login(p),
        "draft": p["isDraft"],
        "created_at": p["createdAt"],
        "labels": [lb["name"] for lb in p["labels"]["nodes"]],
        "head": p["headRefOid"],
        "branch": p["headRefName"],
        "linked_issues": [
            int(ref.rpartition("#")[2])
            for ref in closes
            if ref.rpartition("#")[0] == name
        ],
        "linked_refs": sorted(
            {
                ref
                for ref in closes + mentions
                if ref.split("/")[0] == name.split("/")[0]
                and ref.rpartition("#")[0] != name
            }
        ),
    }


def is_agent_pr(name: str, p: dict, record: dict, owner: str) -> bool:
    return bool(
        login(p) == owner
        and not p["isCrossRepository"]
        and re.fullmatch(AGENT_BRANCH, p["headRefName"])
        and issue_links(name, record)
    )


# shortcut: last 100 comments and reviews, first 100 of a thread, page them if a PR exceeds that
PR_QUERY = """query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) { pullRequest(number: $number) {
    state isDraft mergeStateStatus headRefOid headRefName baseRefName
    additions deletions changedFiles mergeCommit { oid }
    labels(first: 50) { nodes { name } }
    commits(last: 1) { nodes { commit { committedDate } } }
    comments(last: 100) { nodes { body createdAt author { __typename login } } }
    reviews(last: 100) { nodes {
      body createdAt: submittedAt author { __typename login } } }
    reviewThreads(first: 100) { totalCount nodes { isResolved
      comments(first: 100) { nodes {
        body createdAt author { __typename login } } } } } } } }"""


def is_feedback(c: dict, owner: str, opens_thread: bool = False) -> bool:
    author, text = c["author"] or {}, c["body"].lstrip()
    kind = author.get("__typename")
    return (
        bool(text)
        and not text.startswith(AGENT_MARKER)
        and (
            (kind == "User" and author.get("login") == owner)
            or (kind == "Bot" and opens_thread)
        )
    )


def newest(nodes: list[dict]) -> str | None:
    times = [c["createdAt"] for c in nodes if c["createdAt"]]
    return max(times, key=datetime.fromisoformat, default=None)


def pr_node(repo: str, number: int) -> dict:
    owner, name = repo.split("/")
    reply = gh(
        "api",
        "graphql",
        "-f",
        f"query={PR_QUERY}",
        "-f",
        f"owner={owner}",
        "-f",
        f"name={name}",
        "-F",
        f"number={number}",
    )
    return reply["data"]["repository"]["pullRequest"]


def pr_detail(repo: str, number: int, owner: str) -> dict:
    p = pr_node(repo, number)
    if p["state"] == "OPEN" and p["mergeStateStatus"] == "UNKNOWN":
        time.sleep(5)
        p = pr_node(repo, number)
    threads = [t for t in p["reviewThreads"]["nodes"] if t["comments"]["nodes"]]
    spoken = (
        p["comments"]["nodes"]
        + p["reviews"]["nodes"]
        + [c for t in threads for c in t["comments"]["nodes"]]
    )
    by_owner = [c for c in spoken if is_feedback(c, owner)]
    opened = [t for t in threads if is_feedback(t["comments"]["nodes"][0], owner, True)]
    commits = p["commits"]["nodes"]
    files = gh("api", f"repos/{repo}/pulls/{number}/files?per_page=100")
    return {
        "state": p["state"],
        "head": p["headRefOid"],
        "branch": p["headRefName"],
        "draft": p["isDraft"],
        "merge_state": p["mergeStateStatus"],
        "merge_commit": (p["mergeCommit"] or {}).get("oid"),
        "labels": [lb["name"] for lb in p["labels"]["nodes"]],
        "review": {
            "checks": sha_state(
                repo, p["headRefOid"], required_checks(repo, p["baseRefName"])
            ),
            "pushed_at": commits[-1]["commit"]["committedDate"] if commits else None,
            "files": [
                f[k]
                for f in files
                for k in ("filename", "previous_filename")
                if f.get(k)
            ],
            "changed_files": p["changedFiles"],
            "lines": p["additions"] + p["deletions"],
            "unresolved": p["reviewThreads"]["totalCount"]
            - len(p["reviewThreads"]["nodes"])
            + sum(not t["isResolved"] for t in opened),
            "feedback_at": newest(
                by_owner + [t["comments"]["nodes"][0] for t in opened]
            ),
            "owner_feedback_at": newest(by_owner),
        },
    }


# shortcut: newest 100 commits since the oldest landing, page them if a repo exceeds that
REVERT_QUERY = """query($owner: String!, $name: String!, $since: GitTimestamp!) {
  repository(owner: $owner, name: $name) { defaultBranchRef { target { ... on Commit {
    history(first: 100, since: $since) { nodes {
      message associatedPullRequests(first: 1) { nodes { headRefName } } } } } } } } }"""


def reverted_prs(repo: str, landed: list[dict]) -> list[int]:
    if not landed:
        return []
    owner, name = repo.split("/")
    since = min(landed, key=lambda e: datetime.fromisoformat(e["at"]))["at"]
    reply = gh(
        "api",
        "graphql",
        "-f",
        f"query={REVERT_QUERY}",
        "-f",
        f"owner={owner}",
        "-f",
        f"name={name}",
        "-f",
        f"since={since}",
    )
    branch = reply["data"]["repository"]["defaultBranchRef"]
    commits = branch["target"]["history"]["nodes"] if branch else []
    return sorted(
        {
            e["pr"]
            for e in landed
            for c in commits
            if f"This reverts commit {e['sha']}" in c["message"]
            or any(
                p["headRefName"].startswith(f"revert-{e['pr']}-")
                for p in c["associatedPullRequests"]["nodes"]
            )
        }
    )


def token_expiry() -> str | None:
    r = subprocess.run(
        ["gh", "api", "--include", "rate_limit"],
        capture_output=True,
        text=True,
        check=False,
    )
    m = re.search(
        r"^github-authentication-token-expiration: (.+?)\s*$",
        r.stdout,
        re.IGNORECASE | re.MULTILINE,
    )
    if not m:
        return None
    return (
        datetime.strptime(m[1], "%Y-%m-%d %H:%M:%S %Z").replace(tzinfo=UTC).isoformat()
    )


def collect(landed: list[dict] | tuple = ()) -> dict:
    generated = datetime.now(UTC).isoformat()
    # shortcut: first 50 PRs / 100 alerts per repo, paginate those if a repo ever exceeds them
    pages = gh("api", "graphql", "--paginate", "--slurp", "-f", f"query={QUERY}")
    owner = pages[0]["data"]["viewer"]["login"]
    repos, needs_review_open = [], 0
    for node in (
        n for page in pages for n in page["data"]["viewer"]["repositories"]["nodes"]
    ):
        topics = {t["topic"]["name"] for t in node["repositoryTopics"]["nodes"]}
        if node["owner"]["login"] != owner or (
            node["isFork"] and OPT_IN_TOPIC not in topics
        ):
            continue
        name, branch = node["nameWithOwner"], node["defaultBranchRef"]
        required = required_checks(name, branch["name"]) if branch else []
        all_prs = node["pullRequests"]["nodes"]
        prs = [p for p in all_prs if login(p) == "dependabot"]
        rest = [p for p in all_prs if login(p) != "dependabot"]
        others = [other_pr_record(name, p) for p in rest]
        for p, o in zip(rest, others):
            if is_agent_pr(name, p, o, owner):
                try:
                    o.update(pr_detail(name, p["number"], owner))
                except RuntimeError as e:
                    o["review"] = {}
                    print(
                        f"{name}#{p['number']} not read, so no review run and no"
                        f" landing for it this run: {e}",
                        file=sys.stderr,
                    )
        needs_review_open += sum(
            "needs-review" in {lb["name"] for lb in p["labels"]["nodes"]} for p in prs
        ) + sum(
            "needs-review" in o["labels"] and not issue_links(name, o) for o in others
        )

        repos.append(
            {
                "repo": name,
                "fork": node["isFork"],
                "head": branch["target"]["oid"] if branch else None,
                "head_at": branch["target"]["committedDate"] if branch else None,
                "has_ci": bool(required),
                "main": sha_state(name, branch["target"]["oid"], required)
                if branch
                else None,
                "prs": [
                    {
                        "number": p["number"],
                        "title": p["title"],
                        "created_at": p["createdAt"],
                        "labels": [lb["name"] for lb in p["labels"]["nodes"]],
                        "mergeable": p["mergeable"],
                        "auto_merge_since": (p["autoMergeRequest"] or {}).get(
                            "enabledAt"
                        ),
                        "checks": sha_state(name, p["headRefOid"], required),
                        "major": is_major(f"{p['title']}\n{p['body']}"),
                    }
                    for p in prs
                ],
                "alerts": [
                    alert_record(a, prs) for a in node["vulnerabilityAlerts"]["nodes"]
                ],
                "issues": [issue_record(i, owner) for i in node["issues"]["nodes"]],
                "other_prs": others,
                "land": LAND_TOPIC in topics and name not in NEVER_LAND_REPOS,
                "reverted": reverted_prs(
                    name, [e for e in landed if e["repo"] == name]
                ),
            }
        )
    return {
        "generated": generated,
        "needs_review_open": needs_review_open,
        "owner": owner,
        "repos": repos,
        "token_expires": token_expiry(),
    }


def alert_record(a: dict, prs: list[dict]) -> dict:
    v = a["securityVulnerability"]
    severity = v["severity"].lower()
    return {
        "ghsa": a["securityAdvisory"]["ghsaId"],
        "severity": "medium" if severity == "moderate" else severity,
        "ecosystem": v["package"]["ecosystem"].lower(),
        "package": v["package"]["name"],
        "range": v["vulnerableVersionRange"],
        "first_patched": (v["firstPatchedVersion"] or {}).get("identifier"),
        "created_at": a["createdAt"],
        "covered_by_pr": covering_pr(v["package"]["name"], prs),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("start")
    p.add_argument("key")
    p.add_argument("task_id")
    p = sub.add_parser("finish")
    p.add_argument("key")
    p.add_argument(
        "status", choices=["done", "blocked", "failed", "land", "no-land", *OUTCOMES]
    )
    p.add_argument("note")
    sub.add_parser("collect")
    sub.add_parser("board")
    p = sub.add_parser("apply")
    p.add_argument("plan", type=Path)
    sub.add_parser("setup")
    sub.add_parser("running")
    p = sub.add_parser("plan")
    p.add_argument("candidates", type=Path)
    p.add_argument("thread_id")
    p = sub.add_parser("land")
    p.add_argument("candidates", type=Path)
    p.add_argument("plan", type=Path)
    args = ap.parse_args()
    if args.cmd == "collect":
        snapshot = collect(load_ledger(LEDGER).get("landed", []))
        if linear.CLIENT.exists():
            try:
                snapshot["board"] = linear.board()
            except (RuntimeError, SystemExit) as e:
                print(
                    f"board not read, so no card moves this run: {e}", file=sys.stderr
                )
        print(json.dumps(snapshot, indent=2))
        return
    if args.cmd == "board":
        print(json.dumps(linear.board(), indent=2))
        return
    if args.cmd == "apply":
        decided = json.loads(args.plan.read_text())
        result = linear.apply(decided["card_ops"])
        held = {
            op["card"]
            for op in result["pending"]
            if op.get("to") == "In Progress" and not op["add_labels"]
        }
        result["release"] = [
            t["key"] for t in decided["dispatch"] if t.get("card") in held
        ]
        print(json.dumps(result, indent=2))
        return
    if args.cmd == "setup":
        linear.setup()
        return
    # Concurrent runs share the ledger; flock is released even if the process dies.
    with open(LEDGER.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        locked(args)


def locked(args: argparse.Namespace) -> None:
    now = datetime.now(UTC)
    ledger = load_ledger(LEDGER)
    if args.cmd == "running":
        print(json.dumps(running_tasks(ledger, now), indent=2))
        return
    if args.cmd == "land":
        candidates = json.loads(args.candidates.read_text())
        result = {"landed": [], "refused": []}
        try:
            for t in json.loads(args.plan.read_text())["lands"]:
                outcome = land(candidates, ledger, t["repo"], t["pr"], now)
                result["refused" if "why" in outcome else "landed"].append(outcome)
                save_ledger(LEDGER, ledger)
        finally:
            print(json.dumps(result, indent=2))
        return
    if args.cmd == "plan":
        busy, stale = sweep_locks(WORK, now)
        result = plan(
            json.loads(args.candidates.read_text()),
            ledger,
            now,
            args.thread_id,
            frozenset(busy),
        )
        for t in result["dispatch"]:
            write_lock(WORK, t["key"], ledger["tasks"][t["key"]])
        print(json.dumps({**result, "stale_locks": stale}, indent=2))
    elif args.cmd == "start":
        assign(ledger, args.key, args.task_id)
        write_lock(WORK, args.key, ledger["tasks"][args.key])
    else:
        note = sys.stdin.read().strip() if args.note == "-" else args.note
        print(finish(ledger, args.key, args.status, note, now))
        release(WORK, args.key)
    save_ledger(LEDGER, ledger)


if __name__ == "__main__":
    main()
