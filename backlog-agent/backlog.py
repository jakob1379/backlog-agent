#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

MAX_RUNNING = 3
DAILY_CAP = 24
TZ = ZoneInfo("Europe/Copenhagen")
MAX_ATTEMPTS = 2
OVERDUE_AFTER = timedelta(hours=6)
STALE_DONE_AFTER = timedelta(hours=48)
STALE_NOTE = "still open 48h after agent finished"
STUCK_AFTER = timedelta(hours=2)
SKIP_LABELS = {"needs-review", "agent:blocked"}
NEEDS_REVIEW_CAP = 5
SKIP_STATUSES = {"running", "done", "blocked"}
ADD_CI_REPOS = {"jakob1379/pacify", "jakob1379/br18", "jakob1379/brobizz-usage"}

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


def running_tasks(ledger: dict, now: datetime) -> list[dict]:
    return [
        {
            "key": key,
            "task_id": e["task_id"],
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
    return keys


def plan(candidates: dict, ledger: dict, now: datetime) -> dict:
    present = present_keys(candidates)
    resolved, newly_blocked = [], []
    for key, e in list(ledger["tasks"].items()):
        if e["status"] == "running":
            continue
        if key not in present:
            del ledger["tasks"][key]
            if e["status"] == "done":
                resolved.append(key)
        elif (
            e["status"] == "done"
            and now - datetime.fromisoformat(e["finished"]) >= STALE_DONE_AFTER
        ):
            e.update(status="blocked", note=STALE_NOTE)
            newly_blocked.append({"key": key, "note": STALE_NOTE})
    tasks = candidate_tasks(candidates, now)
    keys = {t["key"] for t in tasks}
    held = [
        t["key"]
        for t in tasks
        if t["kind"] == "pr" and t["tier"] != 1 and f"{t['repo']}:fix-main" in keys
    ]
    review_full = candidates["needs_review_open"] >= NEEDS_REVIEW_CAP
    ready = [
        t
        for t in tasks
        if t["key"] not in held
        and ledger["tasks"].get(t["key"], {}).get("status") not in SKIP_STATUSES
        and not (review_full and (not t["has_ci"] or t["kind"] == "add-ci"))
    ]
    running = sum(e["status"] == "running" for e in ledger["tasks"].values())
    used = used_today(ledger, now)
    slots = max(0, min(MAX_RUNNING - running, DAILY_CAP - used))
    today = now.astimezone(TZ).date().isoformat()
    announce_cap = (
        used >= DAILY_CAP and bool(ready) and ledger["cap_announced"] != today
    )
    if announce_cap:
        ledger["cap_announced"] = today
    return {
        "dispatch": [
            {**t, "attempt": ledger["tasks"].get(t["key"], {}).get("attempts", 0) + 1}
            for t in ready[:slots]
        ],
        "queued": [t["key"] for t in ready[slots:]],
        "held": held,
        "running": running,
        "used_today": used,
        "announce_cap": announce_cap,
        "resolved": resolved,
        "newly_blocked": newly_blocked,
    }


QUERY = """query($endCursor: String) { viewer {
  repositories(first: 20, after: $endCursor, ownerAffiliations: OWNER, isArchived: false, isFork: false) {
    pageInfo { hasNextPage endCursor }
    nodes {
      nameWithOwner
      defaultBranchRef { name target { oid } }
      pullRequests(states: OPEN, first: 50) { nodes {
        number title body createdAt mergeable headRefOid author { login }
        labels(first: 20) { nodes { name } }
        autoMergeRequest { enabledAt } } }
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


def collect() -> dict:
    # shortcut: first 50 PRs / 100 alerts per repo, paginate those if a repo ever exceeds them
    pages = gh("api", "graphql", "--paginate", "--slurp", "-f", f"query={QUERY}")
    repos, needs_review_open = [], 0
    for node in (
        n for page in pages for n in page["data"]["viewer"]["repositories"]["nodes"]
    ):
        name, branch = node["nameWithOwner"], node["defaultBranchRef"]
        required = required_checks(name, branch["name"]) if branch else []
        all_prs = node["pullRequests"]["nodes"]
        needs_review_open += sum(
            "needs-review" in {lb["name"] for lb in p["labels"]["nodes"]}
            for p in all_prs
        )
        prs = [p for p in all_prs if (p["author"] or {}).get("login") == "dependabot"]

        repos.append(
            {
                "repo": name,
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
            }
        )
    return {
        "generated": datetime.now(UTC).isoformat(),
        "needs_review_open": needs_review_open,
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
    p.add_argument("status", choices=["done", "blocked", "failed"])
    p.add_argument("note")
    sub.add_parser("collect")
    sub.add_parser("running")
    p = sub.add_parser("plan")
    p.add_argument("candidates", type=Path)
    args = ap.parse_args()
    if args.cmd == "collect":
        print(json.dumps(collect(), indent=2))
        return
    now = datetime.now(UTC)
    ledger = load_ledger(LEDGER)
    if args.cmd == "running":
        print(json.dumps(running_tasks(ledger, now), indent=2))
        return
    if args.cmd == "plan":
        result = plan(json.loads(args.candidates.read_text()), ledger, now)
        print(json.dumps(result, indent=2))
    elif args.cmd == "start":
        start(ledger, args.key, args.task_id, now)
    else:
        note = sys.stdin.read().strip() if args.note == "-" else args.note
        print(finish(ledger, args.key, args.status, note, now))
    save_ledger(LEDGER, ledger)


if __name__ == "__main__":
    main()
