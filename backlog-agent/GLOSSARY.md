# Backlog agent

An hourly agent that keeps the owner's GitHub repos clear of dependency and security work, and
moves the owner's own backlog through triage, approval and review.

## Language

**Owner**:
jakob1379, the one person whose repos the agent works on and whose approval it waits for.
_Avoid_: user, maintainer, admin

**In-scope repo**:
A non-archived repo owned by the owner's own GitHub account that is not a fork, or an
opted-in fork. Repos in other orgs are out of scope. All of the agent's work uses this scope.
_Avoid_: all repos, project

**Opted-in fork**:
A fork the owner has marked with the `backlog-agent` topic, which makes it an in-scope repo.

**Dependency work**:
The agent's work on Dependabot PRs, security alerts, red default branches and minimal CI. It
needs the owner only when it ends blocked or in review.
_Avoid_: Phase 1, maintenance

**Card**:
One item on the owner's board in the tracker. It stands for a backlog issue, an open pull
request with no issue behind it, or dependency work that needs the owner.
_Avoid_: ticket, Linear issue

**Health check**:
One condition on the run board that says whether work is moving, such as no card waiting more
than a day to be picked up. It never counts how much backlog is left.
_Avoid_: goal, exit criterion, checks before Phase 2

## Backlog triage

**Backlog issue**:
A GitHub issue in an in-scope repo describing work the owner wants done. Its card in the
tracker mirrors it.
_Avoid_: ticket, task, todo

**Triage run**:
One agent run that reads a backlog issue, reproduces and diagnoses it, and ends with a fix, a
plan, a question, or a ruling that it is not actionable.
_Avoid_: triage job, analysis

**Plan**:
The agent's proposal for one slice of a backlog issue: what it will change, which files, what
is out of scope, and any conflicts with recorded decisions. Once approved, the plan, not the
issue text, is the spec.
_Avoid_: proposal, design

**Approval**:
The owner moving a card from Needs approval to Approved. Nobody else's move counts, and the
agent never makes it.
_Avoid_: sign-off, accept

**Build run**:
One agent run that implements an approved plan and opens one pull request per repo it touches.
_Avoid_: implementation task, fix run

**Top-up**:
Triaging the best untriaged backlog issues, on the agent's own initiative, while nothing waits
for approval or building, until three plans wait for approval.
_Avoid_: suggestions, auto-triage

**Trusted author**:
The owner. Only issues and comments written by a trusted author can start or steer a run.

**Owner action**:
A card waiting on the owner: in Needs approval, Needs info or In Review. Dependency work that
needs the owner, such as a blocked task or an alert to rule on, is an owner action too.
_Avoid_: needs you, blocked item, todo

**Phase**:
Not a current term. Phases 0–2 were the rollout order; the agent's dependency work and backlog
triage are now one ongoing job.
_Avoid_: Phase 1, Phase 2
