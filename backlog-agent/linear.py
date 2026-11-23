import base64
import json
import re
import time
import urllib.error
import urllib.request
from operator import itemgetter
from pathlib import Path

API = "https://api.linear.app/graphql"
TOKEN_URL = "https://api.linear.app/oauth/token"
CLIENT = Path.home() / ".config/backlog-agent/linear-client"
SCOPES = "read,write"
_token = None
TEAM = "JGA"
APPLY_BUDGET = 240.0
STATUSES = {
    "Backlog": "backlog",
    "Needs triage": "unstarted",
    "Needs approval": "unstarted",
    "Approved": "unstarted",
    "In Progress": "started",
    "In Review": "started",
    "Needs info": "started",
    "Done": "completed",
    "Canceled": "canceled",
    "Duplicate": "canceled",
}
SETUP_LABELS = [
    "kind: dependency",
    "kind: backlog",
    "kind: pr",
    "needs-repro",
    "unable-to-fix",
    "failed",
    "draft",
]
GITHUB = r"https://github\.com/([\w.-]+/[\w.-]+)/(issues|pull)/(\d+)"

CONTEXT = """query($team: String!) {
  viewer { id }
  users(first: 250) { nodes { id admin app active } }
  teams(filter: {key: {eq: $team}}) { nodes { id states(first: 100) { nodes { id name } } } }
  issueLabels(first: 250) { nodes { id name isGroup parent { name } team { key } } }
}"""

ISSUES = """query($team: String!, $after: String) {
  issues(first: 20, after: $after, filter: {team: {key: {eq: $team}}}) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id identifier title description createdAt
      state { name }
      labels(first: 20) { nodes { name parent { name } } }
      attachments(first: 10) { nodes { id url } }
      history(first: 100) { nodes {
        createdAt actor { id } fromState { name } toState { name } } } } } }"""

CREATE_ISSUE = """mutation($input: IssueCreateInput!) {
  issueCreate(input: $input) { issue { id identifier } } }"""
UPDATE_ISSUE = """mutation($id: String!, $input: IssueUpdateInput!) {
  issueUpdate(id: $id, input: $input) { success } }"""
LINK_URL = """mutation($issueId: String!, $url: String!) {
  attachmentLinkURL(issueId: $issueId, url: $url) { success } }"""
DELETE_ATTACHMENT = """mutation($id: String!) {
  attachmentDelete(id: $id) { success } }"""
CREATE_LABEL = """mutation($input: IssueLabelCreateInput!) {
  issueLabelCreate(input: $input) { issueLabel { id } } }"""
CREATE_STATE = """mutation($input: WorkflowStateCreateInput!) {
  workflowStateCreate(input: $input) { workflowState { id } } }"""


def token() -> str:
    global _token
    if _token is None:
        if not CLIENT.exists():
            raise SystemExit(f"no Linear app credentials at {CLIENT}")
        client_id, secret = CLIENT.read_text().split()
        basic = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
        req = urllib.request.Request(
            TOKEN_URL,
            data=f"grant_type=client_credentials&scope={SCOPES}".encode(),
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                _token = json.load(r)["access_token"]
        except urllib.error.HTTPError as e:
            raise RuntimeError(
                f"linear token HTTP {e.code}: {e.read().decode(errors='replace')[:300]}"
            ) from None
        except OSError as e:
            raise RuntimeError(f"linear token: {getattr(e, 'reason', e)}") from None
    return _token


def graphql(query: str, **variables) -> dict:
    req = urllib.request.Request(
        API,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={
            "Authorization": f"Bearer {token()}",
            "Content-Type": "application/json",
        },
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = json.load(r)
            break
        except urllib.error.HTTPError as e:
            error = f"HTTP {e.code}: {e.read().decode(errors='replace')[:500]}"
            if e.code != 429 or attempt == 2:
                raise RuntimeError(f"linear {error}") from None
            time.sleep(60)
        except OSError as e:
            raise RuntimeError(f"linear {getattr(e, 'reason', e)}") from None
    if body.get("errors"):
        raise RuntimeError(f"linear: {[e['message'] for e in body['errors']]}")
    failed = [
        k
        for k, v in body["data"].items()
        if isinstance(v, dict) and v.get("success") is False
    ]
    if failed:
        raise RuntimeError(f"linear: {failed} returned success: false")
    return body["data"]


def label_name(lb: dict) -> str:
    return f"{lb['parent']['name']}: {lb['name']}" if lb["parent"] else lb["name"]


def context(check: bool = True) -> dict:
    data = graphql(CONTEXT, team=TEAM)
    if not data["teams"]["nodes"]:
        raise SystemExit(f"no Linear team {TEAM}")
    team = data["teams"]["nodes"][0]
    states = {s["name"]: s["id"] for s in team["states"]["nodes"]}
    missing = [s for s in STATUSES if s not in states]
    if check and missing:
        raise SystemExit(f"Linear team {TEAM} is missing statuses: {missing}")
    nodes = [
        lb
        for lb in data["issueLabels"]["nodes"]
        if lb["team"] is None or lb["team"]["key"] == TEAM
    ]
    labels = {label_name(lb): lb["id"] for lb in nodes if not lb["isGroup"]}
    for lb in nodes:
        if not lb["isGroup"]:
            labels.setdefault(lb["name"], lb["id"])
    return {
        "team": team["id"],
        "agent": data["viewer"]["id"],
        "owners": [
            u["id"]
            for u in data["users"]["nodes"]
            if u["admin"] and u["active"] and not u["app"]
        ],
        "states": states,
        "labels": labels,
        "groups": {lb["name"]: lb["id"] for lb in nodes if lb["isGroup"]},
    }


def issues() -> list[dict]:
    nodes, after = [], None
    while True:
        page = graphql(ISSUES, team=TEAM, after=after)["issues"]
        nodes += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            return nodes
        after = page["pageInfo"]["endCursor"]


def github_link(m: re.Match | None) -> dict | None:
    if not m:
        return None
    return {
        "repo": m[1],
        "number": int(m[3]),
        "type": "issue" if m[2] == "issues" else "pr",
    }


def attached(node: dict) -> dict | None:
    links = [
        link
        for a in node["attachments"]["nodes"]
        if (link := github_link(re.match(GITHUB, a["url"])))
    ]
    return min(links, key=lambda x: x["type"] != "issue") if links else None


def imported(node: dict) -> dict | None:
    return github_link(
        re.search(rf"Imported from[^\n]*?{GITHUB}", node["description"] or "")
    )


def card(node: dict, agent: str, owner: str) -> dict:
    def actor(h):
        who = (h["actor"] or {}).get("id")
        return "agent" if who == agent else "owner" if who == owner else "other"

    link = attached(node) or imported(node)
    return {
        "id": node["id"],
        "identifier": node["identifier"],
        "title": node["title"],
        "status": node["state"]["name"],
        "labels": [label_name(lb) for lb in node["labels"]["nodes"]],
        "link": link,
        "attachments": [
            a["id"]
            for a in node["attachments"]["nodes"]
            if link and github_link(re.match(GITHUB, a["url"])) == link
        ],
        "created_at": node["createdAt"],
        "history": sorted(
            (
                {
                    "from": (h["fromState"] or {}).get("name"),
                    "to": h["toState"]["name"],
                    "actor": actor(h),
                    "at": h["createdAt"],
                }
                for h in node["history"]["nodes"]
                if h["toState"]
            ),
            key=itemgetter("at"),
        ),
    }


def board() -> dict:
    ctx = context()
    if len(ctx["owners"]) != 1:
        raise SystemExit(
            f"expected one human Linear admin as the owner, found {len(ctx['owners'])}"
        )
    return {
        "statuses": list(ctx["states"]),
        "cards": [card(n, ctx["agent"], ctx["owners"][0]) for n in issues()],
    }


def label_id(ctx: dict, name: str) -> str:
    if name in ctx["labels"]:
        return ctx["labels"][name]
    group, _, child = name.rpartition(": ")
    new = {"name": child, "teamId": ctx["team"]}
    if group:
        if group not in ctx["groups"]:
            ctx["groups"][group] = graphql(
                CREATE_LABEL,
                input={"name": group, "teamId": ctx["team"], "isGroup": True},
            )["issueLabelCreate"]["issueLabel"]["id"]
        new["parentId"] = ctx["groups"][group]
    ctx["labels"][name] = graphql(CREATE_LABEL, input=new)["issueLabelCreate"][
        "issueLabel"
    ]["id"]
    return ctx["labels"][name]


def known_labels(ctx: dict, names: list[str]) -> list[str]:
    ctx["missing"].update(n for n in names if n not in ctx["labels"])
    return [ctx["labels"][n] for n in names if n in ctx["labels"]]


def github_url(link: dict) -> str:
    kind = "issues" if link["type"] == "issue" else "pull"
    return f"https://github.com/{link['repo']}/{kind}/{link['number']}"


def apply_op(ctx: dict, op: dict) -> dict:
    if op["op"] == "create":
        issue = graphql(
            CREATE_ISSUE,
            input={
                "teamId": ctx["team"],
                "title": op["title"],
                "description": f"Imported from {github_url(op['link'])}"
                if op["link"]
                else "",
                "stateId": ctx["states"][op["status"]],
                "labelIds": known_labels(ctx, op["labels"]),
            },
        )["issueCreate"]["issue"]
        if op["link"]:
            graphql(LINK_URL, issueId=issue["id"], url=github_url(op["link"]))
        return {**op, "card": issue["identifier"]}
    if op["op"] == "link":
        graphql(LINK_URL, issueId=op["card"], url=github_url(op["link"]))
        return op
    for attachment in op.get("attachments", []):
        graphql(DELETE_ATTACHMENT, id=attachment)
    update = {"stateId": ctx["states"][op["to"]]}
    if op.get("add_labels"):
        update["addedLabelIds"] = known_labels(ctx, op["add_labels"])
    if op.get("remove_labels"):
        update["removedLabelIds"] = [
            ctx["labels"][n] for n in op["remove_labels"] if n in ctx["labels"]
        ]
    graphql(UPDATE_ISSUE, id=op["card"], input=update)
    return op


def apply(card_ops: list[dict], budget: float | None = None) -> dict:
    ops = sorted(card_ops, key=lambda op: {"create": 1, "link": 2}.get(op["op"], 0))
    result = {"applied": [], "pending": [], "error": None, "missing_labels": []}
    if not ops:
        return result
    ctx = {**context(), "missing": set()}
    deadline = time.monotonic() + (APPLY_BUDGET if budget is None else budget)
    for i, op in enumerate(ops):
        if time.monotonic() > deadline:
            result["pending"] = ops[i:]
            break
        try:
            result["applied"].append(apply_op(ctx, op))
        except RuntimeError as e:
            result["pending"], result["error"] = ops[i:], str(e)
            break
    result["missing_labels"] = sorted(ctx["missing"])
    return result


def setup() -> None:
    ctx = context(check=False)
    for name, kind in STATUSES.items():
        if name in ctx["states"]:
            continue
        try:
            ctx["states"][name] = graphql(
                CREATE_STATE,
                input={
                    "teamId": ctx["team"],
                    "name": name,
                    "type": kind,
                    "color": "#95a2b3",
                },
            )["workflowStateCreate"]["workflowState"]["id"]
            print(f"created status {name}")
        except RuntimeError as e:
            print(f"owner action: add status {name} ({kind}) to {TEAM}: {e}")
    for name in SETUP_LABELS:
        if name in ctx["labels"]:
            continue
        try:
            label_id(ctx, name)
            print(f"created label {name}")
        except RuntimeError as e:
            print(f"owner action: add label {name} to {TEAM}: {e}")
    if "Duplicate" not in ctx["states"]:
        print("owner action: add status Duplicate, then rerun setup")
        return
    nodes = issues()
    synced = {github_url(link) for n in nodes if (link := attached(n))}
    for n in nodes:
        link = imported(n)
        if (
            link
            and not attached(n)
            and github_url(link) in synced
            and n["state"]["name"] != "Duplicate"
        ):
            graphql(
                UPDATE_ISSUE, id=n["id"], input={"stateId": ctx["states"]["Duplicate"]}
            )
            print(f"marked {n['identifier']} Duplicate of {github_url(link)}")
