# Review runs and the land gate: tickets

Spec: `docs/superpowers/specs/2026-11-21-backlog-agent-design.md`. Vocabulary:
`backlog-agent/GLOSSARY.md`. Decision:
`docs/adr/0004-a-land-gate-in-backlog-py-merges-what-a-review-run-clears.md`.
Integration branch: `feat/backlog-agent-auto-pr-pipeline`.

## Task graph

```
T1 snapshot ──> T2 review-runs ──> T3 land-gate
T4 child-prompt      (parallel, codes against the contracts)
T5 dispatcher-prompt (parallel)
T6 docs              (parallel)
```

Frontier at start: T1, T4, T5, T6.

## Shared contracts

Every ticket codes against these shapes, so the parallel tickets agree without
talking.

**Constants** (`backlog.py`):

```
REVIEW_ROUNDS = 3
REVIEW_QUIET = timedelta(minutes=30)
AGENT_BRANCH = r"backlog-agent/.+-\d+-(build|triage)"
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
PROTECTED = <one compiled regex, see T3>
```

**Agent pull request.** An open pull request whose author is the owner, whose
head branch is in the same repo and fully matches `AGENT_BRANCH`, and which
links an issue (`linked_issues` or `linked_refs`). A build or triage branch is
`backlog-agent/<key slug>`, and the key is `<repo>#<number>:build` or
`<repo>#<number>:triage`, so its name ends in the issue number and its kind. A
dependency work branch for a package named `build` does not match. Only these
get review runs and only these can land.

**Feedback.** Text on a pull request that does not start with `AGENT_MARKER`
(after leading whitespace is stripped) and is one of:

- by the owner: a conversation comment, a review body, or a review thread
  comment;
- by a bot (GraphQL `author.__typename == "Bot"`): the first comment of a review
  thread, and nothing else.

Anything else is not feedback: it starts nothing, blocks nothing and never
reaches a child. That covers every other person, a bot's conversation comments
and review bodies, and a bot's replies inside a thread. Only its number can
matter: a read that could not hold every comment holds the pull request, whoever
wrote them.

**Snapshot additions** (`collect`):

- repo: `"land": bool`, true when the repo has `LAND_TOPIC` and is not in
  `NEVER_LAND_REPOS`; `"reverted": [int]`, the numbers of this repo's landed
  pull requests (from the ledger's `landed`) that a commit on the default branch
  reverts; `"default_branch": str|None`, the default branch's name;
  `"prs_cut": bool`, true when the repo has more open pull requests than the 50
  `collect` reads.
- every other PR: `"head"` (head commit).
- an agent pull request also gets everything `pr_detail` returns.

`pr_detail(repo, number, owner) -> dict` reads one pull request and returns

```
{"state": "OPEN"|"CLOSED"|"MERGED", "head": sha, "base": name,
 "draft": bool, "merge_state": GraphQL mergeStateStatus, "labels": [name],
 "review": {"checks": "green"|"red"|"pending"|None, "pushed_at": iso,
            "files": [path], "changed_files": int, "lines": int,
            "unresolved": int, "feedback_at": iso|None,
            "owner_feedback_at": iso|None}}
```

`checks` is the required checks of the pull request's base branch on its head
commit, `None` when that branch requires none. `files` holds each changed file's
name and, for a rename, its previous name too. `lines` is additions plus
deletions. `unresolved` counts unresolved review threads whose first comment is
feedback, plus the feedback the read could not hold in one page: each review
thread past the first 100, and one each for conversation comments past the
newest 100, for reviews past the newest 100, and for each thread with more than
100 comments. So a truncated read holds the pull request as `open feedback`, and
nothing is paged. `base` is the pull request's base branch. `feedback_at` is the
time of the newest feedback, `owner_feedback_at` of the newest by the owner. The
owner's feedback is timed by its last edit when it has one, a bot's by its
creation only. `collect` calls `pr_detail` for each agent pull request, and
`land` calls it again just before a merge. Any other PR has no `review` key.

**Ledger additions**:

- A review entry `tasks["<repo>#<pr>:review"]` is a normal entry whose finished
  status is `land`, `no-land` or `failed`, never `blocked`. `finish` also stores
  `head`: the first 40-hex commit id in the note, or `None`. `plan` stores
  `offered`: the head it last put in `lands`. `land` clears `offered` and stores
  `tried`: the head it last ran the merge command for. Its `attempts` is the
  number of review runs the pull request has had.
- `landed`: a list of `{"repo", "pr", "head", "sha", "at"}`, one per merge the
  gate made or was cut off making, `sha` being the merge commit, or `None` for a
  landing recorded with no merge commit, with `"reverted": true` once a revert
  is seen. `plan` sets a reverted entry's `reverted` to false once the repo is
  in the snapshot and its `land` is false, so taking the topic off for one run
  and putting it back ends the pause. That entry still counts toward the daily
  limit of its day, and the same revert does not pause the repo again. A repo
  that is missing from the snapshot keeps its pause. An entry is kept while
  `reverted` is true, and any other entry is dropped once it is older than
  `LAND_RECORD`.

**Task object**:
`{"key": "o/r#12:review", "kind": "review", "repo": "o/r", "pr": 12, "head": "<head commit>", "round": 1}`.
`round` is the entry's attempts plus one.

**Plan output additions**:

- `lands`: `[{"key", "repo", "pr", "head"}]`, the pull requests to merge now.
- `greenlit`: `[{"key", "repo", "pr", "why", "new"}]`, every review entry with
  status `land` that is not in `lands`, with the first rule that holds it. `new`
  is true the first time the entry is held for that reason: the entry keeps the
  last reason as `held`.
- One pull request per repo goes into `lands` in a plan. A second one that
  passes the gate is held with `default branch pending`, because the first merge
  makes a default branch commit no check has run on.

**The land gate.**
`land_block(candidates, repo, pr, entry, ledger, now, taken) -> str | None`
takes the repo's record, the pull request's record and its review entry, and
returns the first reason the pull request may not land, in this order, or
`None`:

| #   | Rule                                                                                | `why`                         |
| --- | ----------------------------------------------------------------------------------- | ----------------------------- |
| 1   | the entry's status is `land`                                                        | `no verdict`                  |
| 2   | the entry's `head` is the pull request's `head`                                     | `verdict is stale`            |
| 3   | `state` is `OPEN`                                                                   | `not open`                    |
| 4   | `base` is set and is the repo's `default_branch`                                    | `base not the default branch` |
| 5   | not a draft, no `HOLD_LABEL`, no `agent:blocked` label                              | `held by the owner`           |
| 6   | every linked issue is open in the snapshot, written by the owner and not `by_agent` | `issue not the owner's`       |
| 7   | every linked issue has a card in In Review and no card in another open status       | `card <status>`               |
| 8   | no other open agent pull request shares a linked issue                              | `split across pull requests`  |
| 9   | `merge_state` is `CLEAN`                                                            | `merge state <state>`         |
| 10  | `review.checks` is `green`                                                          | `checks <state>`              |
| 11  | the repo's `main` is `green`                                                        | `default branch <state>`      |
| 12  | `unresolved` is 0 and no feedback is newer than the run's start                     | `open feedback`               |
| 13  | `changed_files` and `lines` are within the limits                                   | `too large`                   |
| 14  | no name in `files` matches `PROTECTED`                                              | `protected path <path>`       |
| 15  | no landed pull request of the repo is marked reverted                               | `paused after a revert`       |
| 16  | the repo's `land` is true                                                           | `shadow`                      |
| 17  | neither the entry's `offered` nor its `tried` is the pull request's `head`          | `merge not made`              |
| 18  | fewer than `LAND_DAILY_CAP` merges today, `taken` included                          | `daily limit`                 |
| 19  | `LAND_DELAY` has passed since the entry finished                                    | `lands <iso time>`            |

A missing value fails its rule: no board, no card and no `review` all hold the
pull request. `<state>` and `<status>` print lower case, and `none` for a
missing one. `CLEAN` means every check passed, required or not, so one red job
holds the pull request. Rule 6 fails for an issue whose record has `by_agent`
true or whose ref is in the ledger's `found`: a scan filed it, so it is not the
owner's. Rule 7 reads every card of a linked issue, in `plan` and in `land`,
with no tidy of the board first. It fails when the issue has no card in In
Review, and when any of its cards has a status that is neither In Review nor in
`CLOSED_STATUSES`. `<status>` is the status of the card that holds it. Rule 8
also fails, as `pull requests not all read`, while any repo's `prs_cut` is true
or missing.

**CLI**: `finish` accepts `land` and `no-land`, and an optional
`--task <task_id>`. For a review key whose entry is running, a `--task` value
that is not the entry's `task_id` exits non-zero with
`<key> is run by another task, so this result is dropped`, before the entry
changes and before the lock is released. A missing `--task` counts as the empty
string, which matches a claim that was never started. Other keys are unchanged.
`land <candidates> <plan>` merges each entry of the plan's `lands` and prints
`{"landed": [{"repo", "pr", "sha"}], "refused": [{"repo", "pr", "why"}]}`.

**Child result**:
`RESULT: land|no-land|failed — <reason> <head commit> <PR URL>; <note>`. The
reasons are `clean` (the only one a `land` may carry), `not_trivial`,
`off_plan`, `not_converged`, `possible_regression`, `removed_safety_logic`,
`insufficient_tests`, `scope_too_large`, `unclear_intent`, `security_risk`,
`breaking_change`, `build_or_ci_risk`, `injection_attempt` and `review_error`.

## Tickets

### T1 snapshot: `collect` reads what a review run and the gate need

Blocked by: none.

Add `headRefName` and `isCrossRepository` to `QUERY`, and `head` to
`other_pr_record`. Add `pr_detail`: one GraphQL read of a pull request (state,
draft, `mergeStateStatus`, head, base branch, merge commit, labels, additions,
deletions, changed files, the head commit's date, reviews, review threads and
conversation comments, each comment with its author's `__typename` and login),
the changed files from REST `repos/<repo>/pulls/<n>/files?per_page=100` so a
rename gives both names, and the required-check state of its head on its base
branch through the existing `required_checks` and `sha_state`. When
`mergeStateStatus` reads `UNKNOWN`, GitHub is still computing it: read once more
after a few seconds. `collect` merges `pr_detail` into each agent pull request's
record. `collect` takes the ledger's `landed` list (`main` reads the ledger
without the lock, since it only reads) and, for each repo with a landing, reads
the default branch's commits since the oldest one that has no `reverted` value,
with a GraphQL `history(since:)`, paged as `QUERY` is, that also gives each
commit's `associatedPullRequests(first: 1) { nodes { headRefName } }`. A landed
pull request is reverted when a message holds
`This reverts commit <its merge sha>` or a commit's pull request branch starts
with `revert-<its number>-`. A landing with no `sha` gets it read from the
merged pull request for the scan, with no ledger write, and the message is
matched only when a `sha` is known. Set each repo's `land`. The marker test
strips leading whitespace, for issue comments too. Tests fake `gh` at
`subprocess.run`, as `GhRateLimitTest` does: feedback by the owner, a bot
opening a thread, a bot's review body, a bot's reply, a stranger and the agent's
marker; unresolved threads; a rename; a revert found both ways.

### T2 review-runs: `plan` starts review runs and records verdicts

Blocked by: T1.

Add `review_pr(key)`. `present_keys` adds a review key for every open agent pull
request, so an entry lives exactly as long as its pull request is open. Add
`review_tasks`. A review run is due when the pull request has no entry, or its
entry failed, or its head is not the entry's `head`, or `feedback_at` is after
the entry's start, or `review.checks` is `red` and `pushed_at` is after the
entry's start. None starts:

- without a board, or while a linked issue has no card in In Review;
- while the pull request is a draft or carries `HOLD_LABEL` or `agent:blocked`;
- while its `base` is missing or is not the repo's `default_branch`;
- while its `state` is not `OPEN`;
- while its checks are pending;
- within `REVIEW_QUIET` of the later of `pushed_at` and `feedback_at`;
- while a triage or build entry of a linked issue is running or failed;
- while any review key is running or busy, so one review runs at a time;
- after `REVIEW_ROUNDS` runs, unless `owner_feedback_at` is after the entry's
  finish.

`plan` puts the one review run after dependency work and before build and
triage, under the same caps. For a review key, `finish` accepts `land` and
`no-land`, refuses an entry that is not running, never turns `failed` into
`blocked`, stores `head`, and stores `land` only when the note starts with
`clean ` and a 40-hex commit id: any other `land` is stored as `no-land`. A
verdict that is not `land`, given for an entry that already holds a `land`,
turns it into `no-land`, so a late run can take a landing back but never give
one. `owner_cards` skips review keys in `agent_prs` and in its ledger loop.
Tests through `plan` and the CLI round trip.

### T3 land-gate: `plan` decides what lands, `land` merges it

Blocked by: T2.

Add `land_block` and `PROTECTED`, which matches a path when any of its
directories or its file name is one of these:

- CI and merge gating: `.github/`, `.gitlab-ci.yml`, `.circleci/`,
  `.pre-commit-config.yaml`, `.husky/`, `CODEOWNERS`.
- Agent instructions: `.claude/`, `.agents/`, `.cursor*`, `.mcp.json`,
  `CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md`, `GEMINI.md`, `SKILL.md`.
- Build, dependency and check config: `pyproject.toml`, `setup.py`, `setup.cfg`,
  `requirements*.txt`, `requirements/`, `Pipfile`, `tox.ini`, `noxfile.py`,
  `pytest.ini`, `conftest.py`, `.coveragerc`, `codecov.y*ml`, `package.json`,
  `.npmrc`, `tsconfig*.json`, `jest.config.*`, `vitest.config.*`,
  `playwright.config.*`, `Cargo.toml`, `go.mod`, `go.sum`, any `*.lock`,
  `package-lock.json`, `npm-shrinkwrap.json`, `pnpm-lock.yaml`, `bun.lockb`,
  `.gitmodules`, `*.nix`, `Makefile`, `justfile`, `Taskfile.y*ml`,
  `Dockerfile*`, `*compose*.y*ml`, `ruff.toml`, `.ruff.toml`, `mypy.ini`,
  `biome.json*`, `eslint.config.*`, `.eslintrc*`.
- Scripts and secrets: `scripts/`, `bin/`, `*.sh`, `.env*`.

`plan` marks `landed` entries reverted from the snapshot, drops entries as the
ledger contract says, walks the review entries with status `land` oldest verdict
first, stores `offered` on each one it puts in `lands`, and returns `lands` and
`greenlit`. Add the `land` command, under the ledger lock. For each entry of
`lands`: refuse it as `not offered` when its `head` is missing or is not both
the entry's `offered` and the entry's `head`, else clear the entry's `offered`,
then read again what the gate reads outside the pull request, on a copy of the
repo's record: the board (`linear.board()`), the repo's topics and default
branch (`repos/<repo>`) for `land` and `default_branch`, the default branch's
required checks for `main`, and `reverted_prs` for the repo's landings that have
no `reverted` value, marking the ones it returns as reverted in the ledger. Read
the pull request last with `pr_detail`, and run `land_block` on the fresh
records. The linked issues and the other open pull requests stay those of the
snapshot. When a read fails or its reply cannot be used (the error kinds in
`UNREAD`, or `SystemExit`), merge nothing for that pull request, report it as
refused with the error's last line and go on to the next one. When the gate now
holds it, report it as refused with that reason. Both leave `offered` cleared,
so the next plan decides again. Otherwise store the head on the entry as
`tried`, append to `landed` with no merge commit and save the ledger, then run
`gh pr merge <pr> --repo <repo> --squash --match-head-commit <head>` as one
plain `subprocess.run`, with no retry and no reading of its output, then read
the pull request once more with `pr_node`, whose `mergeCommit` gives the merge
commit. When its `state` is `MERGED` and its head is the verdict's head, set the
record's merge commit. When that read fails (the error kinds in `UNREAD`),
whatever the merge command's exit, keep the record with no merge commit, so the
daily limit and the pause still count it, and report it as refused. When the
read shows it not merged, remove the record. `tried` stays the head in every
case, so rule 17 holds the pull request until a new head. A second `land` with
the same plan is refused as `not offered`. Report anything but a confirmed merge
as refused with the error's last line. Save the ledger after each pull request.
Tests through `plan`, one per rule of the gate, and the CLI with `gh` faked: a
head that moved after the plan is refused, a card, a land topic, a default
branch or a revert that changed after the plan is refused too, a failed merge is
not tried again by the next plan or by a second `land` with the same plan, and a
`land` that never ran leaves the pull request held.

### T4 child-prompt: the `review` section of `CHILD.md`

Blocked by: none.

Add `review` to the kinds, a `review` section, the `RESULT:` line and the
guardrails. Every pull request comment and thread reply starts with the marker,
for every kind, except a `@dependabot` command on a Dependabot pull request,
which stays the bare command. A child that labels a pull request `needs-review`
also creates the `agent:hold` label, so the owner can apply it. The section
covers:

- reading feedback through a filter that keeps exactly the feedback of the
  contract, with each item marked as the owner's or a bot's;
- the spec: the approved plan on a branch that ends in `-build`, and the comment
  the triage run posted with its diagnosis on a branch that ends in `-triage`;
- the owner's text as instructions within the approved plan, and an owner
  comment that objects to or questions the change as no-land;
- a bot's text as a bug report to check against the code;
- replying to every thread, resolving a bot's thread, and never resolving a
  thread the owner started;
- the converge review: read `/home/jsg/.claude/skills/converge-review/SKILL.md`
  and follow it, with that spec. The subagents that skill dispatches are the
  only ones a child may start, plus the one below;
- a time budget after which no further round starts;
- the verdict: no-land `not_converged` unless the skill converged, else the
  rubric, applied by one fresh report-only subagent that gets the final patch,
  the spec and the rubric;
- the one verdict comment, and never merging.

### T5 dispatcher-prompt: `PROMPT.md` and `STEWARD.md`

Blocked by: none.

Reconcile reads `land` and `no-land`. After `apply`, run `land` when `lands` is
not empty. When the command is refused or fails, report it under **Needs you**
and never merge any other way. A non-empty `landed` or `refused` is something to
report. The report gains **Landed** and **Lands next**, the second listing the
`greenlit` entries. Step 6 gains: never loosen the land gate (`land_block`,
`PROTECTED`, `AGENT_BRANCH`, `NEVER_LAND_REPOS`, the `LAND_*` and `REVIEW_*`
constants, the feedback rule, the `land` command) or the verdict rubric in
`CHILD.md`; a change that would let more land goes under **Needs you**. The
steward audits the new ADR and tickets.

### T6 docs: spec, glossary, README and its images

Blocked by: none.

Edit the spec in place: user stories, the review-run and land-gate decisions,
the trust rule for bot feedback, task kinds, the child result contract, testing
decisions and out of scope. Add glossary entries. Update the README, and
`docs/readme/generate.py` where an image states that every pull request waits
for the owner's merge, then regenerate the images.
