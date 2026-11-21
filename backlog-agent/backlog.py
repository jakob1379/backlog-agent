#!/usr/bin/env python3
import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
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
RED_NOTE = "still red 2h after agent finished"
SKIP_LABELS = {"needs-review", "agent:blocked"}
NEEDS_REVIEW_CAP = 5
SKIP_STATUSES = {"running", "done", "blocked"}
ADD_CI_REPOS = {"jakob1379/pacify", "jakob1379/br18", "jakob1379/brobizz-usage"}
BACKLOG_TIERS = {"build": 8, "triage": 9}
TOPUP_LIMIT = 3
NEW_ISSUE_AGE = timedelta(hours=24)
REASONS = ("needs-repro", "unable-to-fix", "failed")
OUTCOMES = {
    "fix-pending": "In Review",
    "needs-approval": "Needs approval",
    "needs-repro": "Needs info",
    "unable-to-fix": "Needs info",
    "not-actionable": "Canceled",
}

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


def finish(ledger: dict, key: str, status: str, note: str, now: datetime) -> str:
    entry = ledger["tasks"][key]
    if status == "failed" and entry["attempts"] >= MAX_ATTEMPTS:
        status = "blocked"
    entry.update(status=status, note=note, finished=now.isoformat())
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
        if d.is_dir() and not d.with_suffix(".lock").exists():
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


def candidate_tasks(candidates: dict, now: datetime) -> list[tuple]:
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
            c["status"] in QUEUE_STATUSES
            and in_status_for(c, now) > STALE_QUEUE_AFTER
            for c in cards
        ),
        "topup_waiting": sum(
            c["status"] == "Needs approval"
            and c["link"] is not None
            and ledger.get("origins", {}).get(f"{c['link']['repo']}#{c['link']['number']}")
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


def move(card_id: str, to: str, add: list = (), remove: list = ()) -> dict:
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


def owner_replied(issue: dict, history: list, owner: str) -> bool:
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
        f"{r['repo']}#{n}"
        for r in candidates["repos"]
        for p in r.get("other_prs", [])
        for n in p["linked_issues"]
    }
    origins = {k: v for k, v in ledger.get("origins", {}).items() if k in issues}
    ledger["origins"] = origins
    status, ops, ranked, cards = {}, [], [], []
    for c in candidates.get("board", {}).get("cards", []):
        link = c["link"] or {}
        if link.get("type") != "issue" or link["repo"] not in scope:
            continue
        ref = f"{link['repo']}#{link['number']}"
        if ref in issues:
            cards.append((ref, c))
            status[c["id"]] = c["status"]
        elif c["status"] == "In Review" and ref not in linked:
            ops.append({"op": "close", "card": c["id"], "to": "Done"})

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
            del ledger["tasks"][k]
            outcome = e["status"]
            if (
                outcome == "fix-pending"
                and kind == "triage"
                and origins.get(ref) != "owner"
            ):
                outcome = "needs-approval"
            op = outcome_op(c, outcome) if c["status"] == "In Progress" else None
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
            and now - datetime.fromisoformat(issue["created_at"]) < NEW_ISSUE_AGE
        ):
            to_triage(ref, c, "owner")

    counts = Counter(status.values())
    going = any(
        e["status"] == "running"
        for k, e in ledger["tasks"].items()
        if k.rsplit(":", 1)[-1] in BACKLOG_TIERS
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


def plan(
    candidates: dict,
    ledger: dict,
    now: datetime,
    thread_id: str = "",
    busy: frozenset = frozenset(),
) -> dict:
    present = present_keys(candidates)
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
    for key, e in list(ledger["tasks"].items()):
        if e["status"] == "running":
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
    backlog_tasks, card_ops, card_status = backlog_work(candidates, ledger, now)
    ready += [t for t in backlog_tasks if t["key"] not in busy]
    running = len(running_keys)
    used = used_today(ledger, now)
    slots = max(0, min(MAX_RUNNING - running, DAILY_CAP - used))
    today = now.astimezone(TZ).date().isoformat()
    announce_cap = (
        used >= DAILY_CAP and bool(ready) and ledger["cap_announced"] != today
    )
    if announce_cap:
        ledger["cap_announced"] = today
    dispatch = []
    for t in ready[:slots]:
        start(ledger, t["key"], "", now, thread_id)
        dispatch.append({**t, "attempt": ledger["tasks"][t["key"]]["attempts"]})
        if t["kind"] in BACKLOG_TIERS and card_status[t["card"]] != "In Progress":
            card_ops.append(move(t["card"], "In Progress"))
    return {
        "dispatch": dispatch,
        "card_ops": card_ops,
        "queued": [t["key"] for t in ready[slots:]],
        "held": held,
        "running": running,
        "used_today": used,
        "announce_cap": announce_cap,
        "resolved": resolved,
        "newly_blocked": newly_blocked,
        "health": health(candidates, ledger, tasks, now),
    }


AGENT_MARKER = "<!-- backlog-agent -->"
OPT_IN_TOPIC = "backlog-agent"

QUERY = """query($endCursor: String) { viewer { login
  repositories(first: 10, after: $endCursor, ownerAffiliations: OWNER, isArchived: false) {
    pageInfo { hasNextPage endCursor }
    nodes {
      nameWithOwner isFork owner { login }
      repositoryTopics(first: 20) { nodes { topic { name } } }
      defaultBranchRef { name target { oid } }
      pullRequests(states: OPEN, first: 50) { nodes {
        number title body createdAt isDraft mergeable headRefOid author { login }
        labels(first: 20) { nodes { name } }
        closingIssuesReferences(first: 10) { nodes { number repository { nameWithOwner } } }
        autoMergeRequest { enabledAt } } }
      issues(states: OPEN, first: 100) { nodes {
        number title createdAt author { login }
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
    for _ in range(3):
        r = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
        if not r.returncode or "HTTP 4" in r.stderr:
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


def issue_record(i: dict) -> dict:
    return {
        "number": i["number"],
        "title": i["title"],
        "author": login(i),
        "created_at": i["createdAt"],
        "labels": [lb["name"] for lb in i["labels"]["nodes"]],
        "comments": [
            {
                "author": login(c),
                "created_at": c["createdAt"],
                "by_agent": c["body"].startswith(AGENT_MARKER),
            }
            for c in i["comments"]["nodes"]
        ],
    }


def other_pr_record(name: str, p: dict) -> dict:
    return {
        "number": p["number"],
        "title": p["title"],
        "author": login(p),
        "draft": p["isDraft"],
        "created_at": p["createdAt"],
        "linked_issues": [
            i["number"]
            for i in p["closingIssuesReferences"]["nodes"]
            if i["repository"]["nameWithOwner"] == name
        ],
    }


def collect() -> dict:
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
        needs_review_open += sum(
            "needs-review" in {lb["name"] for lb in p["labels"]["nodes"]}
            for p in all_prs
        )
        prs = [p for p in all_prs if login(p) == "dependabot"]
        others = [p for p in all_prs if login(p) != "dependabot"]

        repos.append(
            {
                "repo": name,
                "fork": node["isFork"],
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
                "issues": [issue_record(i) for i in node["issues"]["nodes"]],
                "other_prs": [other_pr_record(name, p) for p in others],
            }
        )
    return {
        "generated": datetime.now(UTC).isoformat(),
        "needs_review_open": needs_review_open,
        "owner": owner,
        "repos": repos,
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
    p.add_argument("status", choices=["done", "blocked", "failed", *OUTCOMES])
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
    args = ap.parse_args()
    if args.cmd == "collect":
        print(json.dumps(collect(), indent=2))
        return
    if args.cmd == "board":
        print(json.dumps(linear.board(), indent=2))
        return
    if args.cmd == "apply":
        ops = json.loads(args.plan.read_text())["card_ops"]
        print(json.dumps(linear.apply(ops), indent=2))
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
