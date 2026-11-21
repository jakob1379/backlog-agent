# Backlog agent: design

Date: 2026-11-21. Owner: jakob1379. Supersedes the phased design of 2026-10-20, which stays as
the record of how dependency work was built. Vocabulary: `backlog-agent/GLOSSARY.md`.
Decisions: `docs/adr/0001` and `docs/adr/0002`.

## Problem Statement

Dependency work is under control. Every in-scope repo has 0 open Dependabot PRs, 0 open
alerts and a green or pending default branch, and the hourly agent keeps it that way. The rest
of the owner's backlog is not under control. There are 111 open GitHub issues across 13 repos,
many a year old, plus 12 open pull requests nobody has looked at, such as homelab#57–66. In
August the owner imported most of those issues into Linear by hand. The copies have drifted
since and nobody works from them.

So the hourly agent now spends a model run each hour to report `no change`, while the owner's
real backlog stays still. Nothing tells the owner which few issues deserve their attention next,
and nothing turns an approved idea into a pull request without the owner doing the work. Copying
more items into Linear would only make a second list the owner also doesn't read.

## Solution

The agent takes on the owner's backlog the way the owner's GitLab triagebot does, with an
hourly run instead of CI and a Linear board instead of labels. Dependency work and backlog
triage are one ongoing job, with no phases.

Every backlog issue appears as a card on a board in the JGA team. Linear's own GitHub Issues
sync keeps the cards and the issues in step. The agent triages a card when the owner asks or
when a new issue arrives. When nothing waits for the owner, it also triages the best untriaged
issues itself, up to three plans at a time. A triage run reproduces and diagnoses the issue.
It then posts a fix, a plan, a question, or a ruling that the issue is not actionable. A plan
waits in Needs approval until the owner moves the card to Approved. A build run then
implements exactly that plan and opens a pull request for review.

Anything else that needs the owner also lands on the same board as a card, so the board is the
one list of owner actions: open pull requests with no issue behind them, drafts included, and
dependency work that ended blocked or in review. The run board stops showing phases. It shows
health checks that say whether work is moving.

## User Stories

1. As the owner, I want every open issue in my in-scope repos to appear as a card on one Linear board, so that I have one place to see my backlog.
2. As the owner, I want cards and GitHub issues kept in step by Linear's own sync, so that neither the agent nor I maintain a copy by hand.
3. As the owner, I want my 76 forks left out unless I add the `backlog-agent` topic to one, so that upstream projects I only forked never fill my board.
4. As the owner, I want repos in other orgs left out, so that the agent only works on repos I own personally.
5. As the owner, I want the same scope rule for dependency work and backlog triage, so that opting a fork in gives it both.
6. As the owner, I want to move a card to Needs triage and have the agent triage it on its next hourly run, so that I can ask for triage without writing anything.
7. As the owner, I want a new issue I open on GitHub to be triaged automatically, so that it gets a first look without me moving it.
8. As the owner, I want the agent to triage the best untriaged issues by itself when nothing waits for me, so that the backlog keeps moving when I'm not steering it.
9. As the owner, I want that top-up to stop once three plans wait for approval, so that I'm never buried in plans.
10. As the owner, I want the top-up to pick security issues first, then bugs, then features, oldest first, so that the riskiest work comes up first.
11. As the owner, I want a triage run to reproduce and diagnose the issue before it proposes anything, so that its plans rest on how the code behaves, not on the issue's wording.
12. As the owner, I want a bug I sent to triage to be fixed straight away in a pull request, so that small bugs I asked about don't wait for a separate approval.
13. As the owner, I want a bug the top-up found to get a plan and wait for my approval, so that the agent never opens pull requests I didn't ask for.
14. As the owner, I want a feature to come back as a plan for one slice, saying which files it touches, what's out of scope and which recorded decisions it conflicts with, so that I can approve a small, clear piece.
15. As the owner, I want to approve a plan by moving its card from Needs approval to Approved, so that approving takes one drag.
16. As the owner, I want only my own move to count as approval, so that neither the agent nor anyone else can approve a plan.
17. As the owner, I want a build run to implement the last plan the agent posted, not the issue text, so that what gets built is exactly what I approved.
18. As the owner, I want a change that spans several in-scope repos to come as one pull request per repo, all linked from the issue, so that each repo's checks and review stay separate.
19. As the owner, I want a card to reach Done only when all of its pull requests have merged, so that half-finished work doesn't look finished.
20. As the owner, I want every pull request from a build run to go to review, never to auto-merge, so that no agent-written source change lands without my review.
21. As the owner, I want plans, questions and diagnoses posted as comments on the GitHub issue, so that the discussion stays with the repo and survives a change of tracker.
22. As the owner, I want to answer the agent's question with a comment on a Needs info card and have it triaged again, so that answering is all it takes to retry.
23. As the owner, I want the reason a card is in Needs info shown as a label (`needs-repro`, `unable-to-fix`, `failed`), so that I can see at a glance what the agent needs.
24. As the owner, I want to turn a plan down by moving the card to Canceled, so that rejecting is as easy as approving.
25. As the owner, I want only text I wrote to start or steer a run, so that an issue or comment from someone else can't steer an agent that holds my GitHub token.
26. As the owner, I want issues opened by anyone else, including bots, to wait in Backlog until I move them, so that I decide what outside input the agent acts on.
27. As the owner, I want a crashed triage or build run retried once and then parked in Needs info with `failed`, so that a broken issue reaches me instead of burning runs.
28. As the owner, I want triage and build runs to share the 3-running and 100-a-day caps with dependency work, so that the backlog can't run up cost.
29. As the owner, I want dependency work to rank above every triage and build run, so that a new critical alert is never stuck behind a feature plan.
30. As the owner, I want every open pull request that isn't from Dependabot and isn't linked to an issue to get a card in In Review, so that pull requests I've left hanging, like homelab#57–66, become visible.
31. As the owner, I want draft pull requests to get a card with a `draft` label rather than be skipped, so that I notice drafts I forgot to finish.
32. As the owner, I want a pull request's card closed when the pull request merges or closes, so that the board never shows finished work.
33. As the owner, I want dependency work that ends blocked or in review to appear as a card, so that the board, not only the run report, lists what's waiting on me.
34. As the owner, I want that card closed once GitHub shows the item is resolved, so that I don't have to tidy up after the agent.
35. As the owner, I want each card to carry a `kind` label (`dependency`, `backlog`, `pr`) next to its `repo` label, so that I can filter the board by what kind of work it is.
36. As the owner, I want the agent's moves to show up in Linear as made by the agent's own app, so that a card's history shows who did what.
37. As the owner, I want child agents never to write to Linear, so that a prompt-injected child can't move a card.
38. As the owner, I want to do only the steps that need my browser login, installing the GitHub sync and the agent's OAuth app, so that the agent does all the rest of the setup.
39. As the owner, I want the agent to create the new statuses and labels itself where Linear's API allows, so that setup takes me minutes.
40. As the owner, I want the agent to mark duplicates of my August import, matching on their "Imported from" URL, so that the board doesn't show each issue twice.
41. As the owner, I want an imported card the sync doesn't take over to still be linked to its issue through its "Imported from" URL, so that no card is orphaned.
42. As the owner, I want the run board to show health checks instead of phases, so that I can see whether work is moving.
43. As the owner, I want a health check failing when a card waits more than 24 hours in Needs triage or Approved, so that a stalled queue is visible.
44. As the owner, I want a health check failing when a card sits in In Review for more than 7 days, so that reviews I'm sitting on surface.
45. As the owner, I want the health checks to include no unhandled Dependabot PRs or alerts and every default branch with CI green, so that dependency work stays watched.
46. As the owner, I want a health check failing when the top-up has put more than 3 cards in Needs approval, so that a top-up bug shows up at once.
47. As the owner, I want the run report to stay silent when nothing changed, so that quiet hours don't notify me.
48. As the owner, I want the agent's own cards and comments to start no run, so that the agent can't trigger itself into a loop.
49. As the owner, I want the agent to leave my personal projects in JGA, such as the bin insert and the speaker build, alone, so that only cards linked to an in-scope repo are ever triaged.
50. As the owner, I want the agent to keep working with another tracker later by rewriting one module, so that the rules aren't locked into Linear.
51. As the owner, I want the steward to keep this spec, the glossary and the prompts true to what happens, so that the docs don't drift the way the August import did.

## Implementation Decisions

- **One job, no phases.** Dependency work keeps every rule from the 2026-10-20 design: tiers,
  holds, lanes, caps, retries, the ledger and lockfiles. Backlog triage is added beside it in
  the same hourly run, the same ledger and the same caps. The run board's phase strip and
  "Left before Phase 2" box are replaced by health checks.
- **Scope.** An in-scope repo is a non-archived repo owned by `jakob1379` that is not a fork,
  or a fork carrying the `backlog-agent` topic. No other org. `collect` applies this one rule
  to both kinds of work.
- **Board.** The JGA team's statuses are Backlog, Needs triage, Needs approval, Approved,
  In Progress, In Review, Needs info, Done, Canceled and Duplicate. Needs triage, Needs approval,
  Approved and Needs info are new. In Review means at least one pull request is open, and
  Canceled means not actionable. Labels: the existing single-select `repo` group, a new
  single-select `kind` group (`dependency`, `backlog`, `pr`), and the Needs info reasons
  `needs-repro`, `unable-to-fix`, `failed`, plus `draft`. Only cards linked to an in-scope repo
  are ever touched.
- **State machine.** These moves are the contract, and `plan` enforces them:

  | From | To | Made by | When |
  |---|---|---|---|
  | Backlog | Needs triage | owner, or the agent | the owner asks; a new issue by the owner; top-up |
  | Needs triage | In Progress | agent | a triage run starts |
  | In Progress | In Review | agent | a fix pull request was opened |
  | In Progress | Needs approval | agent | a plan was posted |
  | In Progress | Needs info | agent | question, can't reproduce, can't fix, or second failure |
  | In Progress | Canceled | agent | not actionable |
  | Needs approval | Approved | **owner only** | approval |
  | Needs approval | Canceled | owner | plan turned down |
  | Approved | In Progress | agent | a build run starts |
  | Needs info | Needs triage | agent | the owner commented after the agent's last comment |
  | In Review | Done | sync or agent | every linked pull request merged |

  A move to Approved by anyone but the owner is undone back to Needs approval. A card that
  arrives in Approved any other way is ignored.
- **Triggers.** A triage run starts for a card in Needs triage. A new issue authored by the
  owner is moved there automatically. The top-up runs only when Needs approval and Approved are
  both empty and no triage or build run is going. It moves the best Backlog cards to Needs
  triage until three plans could wait for approval. Its order is the `security` label, then
  `Bug`, then everything else, oldest issue first within each.
- **Trust.** Only the owner is a trusted author. `collect` records the author of each issue and
  comment. `plan` never starts a run from a card whose issue was written by someone else
  unless the owner moved it. A child's prompt never includes comments from untrusted authors.
- **Bugs and the origin of a run.** `plan` records whether the owner or the top-up put a card in
  Needs triage. A triage run started by the owner may open a fix pull request for a bug. One
  started by the top-up must end with a plan.
- **Task kinds and keys.** Two new ledger task kinds join the four from dependency work:
  `owner/repo#<issue>:triage` and `owner/repo#<issue>:build`. They use the same `start`,
  `finish`, lockfile, retry and daily-cap rules. In the tier order they rank after add-CI.
- **Child result contract.** A triage or build child ends with `RESULT: <outcome> — <note>`.
  The outcome is one of `fix-pending`, `needs-approval`, `needs-repro`, `unable-to-fix` or
  `not-actionable`, and the note names every pull request URL. Plans, questions and diagnoses
  go on the GitHub issue as comments. A build child takes the agent's last plan comment as its
  spec, opens one pull request per in-scope repo it changes, labels each `needs-review`,
  assigns the owner, and links each one from the issue. Build pull requests never auto-merge.
- **Retries.** A crashed or timed-out run is retried once. A second failure sends the card to
  Needs info with `failed`. This is the same rule as dependency work, not triagebot's three
  failures.
- **Cards for pull requests and owner actions.** `plan` creates a card in In Review, with
  `kind: pr`, for every open pull request in an in-scope repo that isn't Dependabot's and isn't
  linked to a backlog issue. A draft gets `draft` too. It also creates a card, with
  `kind: dependency`, for dependency work that ended blocked (Needs info) or in the review lane
  (In Review). It closes these cards once GitHub shows the item resolved.
  A dependency card's title starts with its ledger key, which is how `plan` finds it again,
  because an alert, a red default branch or missing CI has no GitHub URL to link. Only a
  Dependabot pull request's card is linked. A dependency card the owner closed by hand comes
  back while the work still needs the owner.
- **Linear access.** One module talks to Linear, through the GraphQL API and the agent's own
  OAuth app token, stored next to the GitHub token. `collect` reads the board: cards, statuses,
  labels, each card's linked issue or pull request, and its status history with who made each
  move. Writes happen in a new step after `plan`, which applies the moves `plan` decided. Only
  `backlog.py` makes writes. Children never get the Linear token. Per ADR 0001, nothing else
  in the agent knows it is Linear.
- **Linking a card to its issue.** Use the sync's link when there is one. Otherwise use the
  "Imported from <url>" footer of the August import. A card with neither is not touched.
- **Setup.** The owner installs the Linear GitHub Issues sync for the in-scope repos and the
  agent's OAuth app. Everything else is a one-off agent job, run before the board is used:
  creating the statuses and labels where the API allows, otherwise listing them as an owner
  action, and marking August's imported cards as Duplicate when the sync made a second card
  for the same URL.
- **Health checks on the run board:**
  1. no Dependabot PRs or alerts left unhandled
  2. no card in Needs triage or Approved for more than 24h
  3. at most 3 top-up cards in Needs approval
  4. no card in In Review for more than 7 days
  5. every default branch with CI green, with pending ones shown muted

  `plan` counts them from the `collect` snapshot.
- **Timing.** Everything happens on the existing hourly run, with no webhooks and no server.

## Testing Decisions

- A good test feeds `plan` a snapshot, the same shape `collect` prints, and asserts on what
  `plan` decides: tasks to dispatch, cards to move, create or close, and health-check counts.
  It never asserts on how `plan` gets there. A refactor that keeps the decisions must keep the
  tests green.
- **Seam 1, `plan`.** The state machine and every rule above is tested here with fixtures, like
  the existing `PlanTest` cases:
  - an approval only counts from the owner, and an Approved card moved by anyone else goes back
  - the top-up starts only when nothing waits and stops at three, in label-then-age order
  - untrusted authors start nothing
  - a top-up bug can't go straight to a fix
  - one retry, then Needs info with `failed`
  - triage and build rank below all dependency work and share the caps
  - pull request cards open and close
  - forks count only with the topic
  - each health check counts right
- **Seam 2, the CLI round trip.** Like `CliTest`: `plan` claims a triage task, `start` records
  it, and `finish` with each triage outcome produces the card move the table above names.
- **Not unit-tested:** the Linear module and the GitHub calls in `collect`. They are I/O, as
  today's `gh` calls are. A broken mapping shows up as a failed `collect` or a red health check
  on the next run, and ADR 0001's loud failure on a missing status covers renames.
- Run the tests with `python3 -m unittest` in the agent's directory, as today. Committing
  rule changes stays gated on a passing run.

## Out of Scope

- Using Linear features beyond the board: cycles, projects for backlog work, Linear's own
  automations, triage intelligence.
- Webhooks, or any always-on server.
- Repos in other orgs, and forks without the `backlog-agent` topic.
- Agent-set Linear priorities or estimates. Ordering comes from labels and age only.
- Any change to dependency work's rules, apart from scope and the cards it now creates.
- A second tracker. The design only keeps porting to one module.
- Auto-merging anything a triage or build run produced.

## Further Notes

- **Unverified facts about Linear's GitHub Issues sync.** None of these could be confirmed from
  the docs, so setup has to check them before the board is used:
  - whether it syncs comments both ways
  - how it maps custom statuses to GitHub's open and closed
  - whether it takes over issues already imported in August or creates new cards
- **Top-up order is only as good as the labels.** Many issues have no labels, such as homelab's
  unauthenticated Traefik API. The owner labelling a few `security` or `Bug` is the cheapest
  way to steer the top-up.
- Triagebot's protected-paths tripwire is not copied. Every build pull request goes to review
  anyway. Dependency work's lane rule, which sends workflow and test-config edits to review,
  still applies.
- Every child already holds the owner's GitHub token. The trust rule limits whose text reaches
  a child. It does not limit what a steered child could do on GitHub.
