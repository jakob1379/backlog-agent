# Backlog agent

An hourly agent that keeps the owner's GitHub repos clear of dependency and
security work, moves the owner's own backlog through triage, approval, review
and landing, and scans the repos for work nobody filed.

## Language

**Owner**: jakob1379, the one person whose repos the agent works on and whose
approval it waits for. _Avoid_: user, maintainer, admin

**In-scope repo**: A non-archived repo owned by the owner's own GitHub account
that is not a fork, or an opted-in fork. Repos in other orgs are out of scope.
All of the agent's work uses this scope. _Avoid_: all repos, project

**Opted-in fork**: A fork the owner has marked with the `backlog-agent` topic,
which makes it an in-scope repo.

**Dependency work**: The agent's work on Dependabot PRs, security alerts, red
default branches and minimal CI. It needs the owner only when it ends blocked or
in review. Its pull requests get no review run and the land gate never merges
them. _Avoid_: Phase 1, maintenance

**Card**: One item on the owner's board in the tracker. It stands for a backlog
issue, an open pull request with no issue behind it, or dependency work that
needs the owner. _Avoid_: ticket, Linear issue

**Health check**: One condition on the run board that says whether work is
moving, such as no card waiting more than a day to be picked up. It never counts
how much backlog is left. _Avoid_: goal, exit criterion, checks before Phase 2

## Backlog triage

**Backlog issue**: A GitHub issue in an in-scope repo describing work the owner
wants done. Its card in the tracker mirrors it. _Avoid_: ticket, task, todo

**Triage run**: One agent run that reads a backlog issue, reproduces and
diagnoses it, and ends with a fix, a plan, a question, or a ruling that it is
not actionable. _Avoid_: triage job, analysis

**Plan**: The agent's proposal for one slice of a backlog issue: what it will
change, which files, what is out of scope, and any conflicts with recorded
decisions. Once approved, the plan, not the issue text, is the spec. _Avoid_:
proposal, design

**Approval**: The owner moving a card from Needs approval to Approved. Nobody
else's move counts, and the agent never makes it. _Avoid_: sign-off, accept

**Build run**: One agent run that implements an approved plan and opens one pull
request per repo it touches. It never merges one: review runs and the land gate
take the pull request from there. _Avoid_: implementation task, fix run

**Top-up**: Triaging the best untriaged backlog issues, on the agent's own
initiative, while nothing waits for approval or building, until three plans wait
for approval. _Avoid_: suggestions, auto-triage

**Trusted author**: The owner. Only issues and comments written by a trusted
author can start or steer a run. On an agent pull request the owner is trusted
anywhere: in a conversation comment, a review body or a review thread. A bot is
not a trusted author. It counts in one place only, where it opens a review
thread, and that text is read as a bug report, never as an instruction.

**Owner action**: A card waiting on the owner: in Needs approval, Needs info, or
In Review with a pull request the land gate does not clear. Dependency work that
needs the owner, such as a blocked task or an alert to rule on, is an owner
action too, and so is a scan that ended blocked. _Avoid_: needs you, blocked
item, todo

**Phase**: Not a current term. Phases 0–2 were the rollout order; the agent's
dependency work and backlog triage are now one ongoing job. _Avoid_: Phase 1,
Phase 2

## Scans

**Scan**: One agent run that reviews an in-scope repo's default branch with the
quality-report skill and files the worst of what it finds. It changes no code in
that repo. A repo is due a scan when its default branch has moved since the last
scan and the last scan started at least a week ago. A dormant repo, one whose
newest commit is over 30 days old, is scanned too: its first scan comes after
the first scan of every repo that is not dormant, and before any repo is scanned
a second time. A scan that failed is retried before all of these. A repo's first
scan reviews everything; later ones review what changed since. Forks are never
scanned. _Avoid_: audit, review, quality run

**Finding**: One problem a scan found, with an id that survives edits to the
code. A scan files its worst findings as backlog issues, and no id is filed
twice. A filed finding starts with the agent's marker, so it never counts as an
issue the owner wrote: it waits in Backlog for the top-up or for the owner. At
most three wait at a time; one stops counting when its issue closes or its card
is Canceled. _Avoid_: proposal, suggestion, scan issue

**Scan skills**: The skills a scan builds from the owner's nix-config
(`nix build github:jakob1379/nix-config#agent-skills --refresh`, which takes
nix-config's newest default-branch commit): the quality-report skill and the
skills packaged beside it. This repo's flake holds an older copy as `.#skills`,
which a scan runs only when that build fails. The lanes it calls from plugins
(ponytail, superpowers, mattpocock, open-code-review) and the scanners it
downloads are whatever the machine has. _Avoid_: pinned skills

**Profile**: The skill's instructions for one stack's tools. When a scan meets a
stack with no profile, or a profile that needs a fix, it drafts one and opens a
pull request on nix-config, so the skill improves through the owner's review.
_Avoid_: proposal, draft issue

## Review and landing

**Agent pull request**: An open pull request the agent opened for a backlog
issue. Its author is the owner, since the agent posts with the owner's token,
its head branch is in the same repo and named
`backlog-agent/…-<issue number>-build` or
`backlog-agent/…-<issue number>-triage`, and it links an issue. Only these get
review runs and only these can land. _Avoid_: bot pull request, automated pull
request

**Review run**: One agent run on an agent pull request: it answers the feedback,
runs the converge review, pushes its fixes and ends with a verdict. A pull
request gets at most three, and another after that only for new feedback by the
owner. A review run never merges. _Avoid_: scan, review task, re-review

**Feedback**: The text on an agent pull request that a review run answers: the
owner's conversation comments, review bodies and review thread comments, and the
first comment of a review thread a bot opened. Text that starts with the agent's
marker is never feedback. Neither is anything from another person, a bot's
conversation comment or review body, or a bot's reply in a thread: those start
nothing, block nothing and are never read. _Avoid_: review comments, comments,
input

**Verdict**: A review run's result for one head commit: land or no-land, with
one reason. It is one input to the land gate, and it counts only while that
commit is the pull request's head. _Avoid_: approval, green light, decision

**Shadow verdict**: A verdict in a repo without the land topic,
`backlog-agent-land`. It is recorded and shown in the run report and never acted
on, so the owner can judge a repo's verdicts before giving it the topic.
_Avoid_: dry run, trial verdict

**Land gate**: The rule in `plan` that decides whether an agent pull request
lands. Code checks its rules in a fixed order, among them the verdict and its
head commit, the hold, the linked issues and their cards, the merge state, the
checks, open feedback, the size, the protected paths, a pause after a revert,
the repo's land topic, the daily limit and the delay. The first rule that fails
leaves the pull request to the owner. _Avoid_: auto-merge, merge gate, risk gate

**Landing**: The merge `backlog.py land` makes of a pull request the land gate
cleared, pinned to the commit the verdict names. To land is to be merged this
way: a merge the owner makes is not a landing. _Avoid_: auto-merge, ship, deploy

**Hold**: The owner's `agent:hold` label on a pull request. While it is there,
no review run starts and the pull request does not land. A draft pull request
and one labeled `agent:blocked` are held the same way. _Avoid_: block, freeze,
pause

**Protected path**: A file path the land gate never lets land: CI and merge
gating, agent instructions, build, dependency and check config, scripts and
secrets. A pull request that changes one waits for the owner. _Avoid_: sensitive
file, tripwire
