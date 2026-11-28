# Backlog agent: design

Date: 2026-11-21. Owner: jakob1379. Supersedes the phased design of 2026-10-20,
which stays as the record of how dependency work was built. Vocabulary:
`backlog-agent/GLOSSARY.md`. Decisions: `docs/adr/0001` to `docs/adr/0004`.

## Problem Statement

Dependency work is under control. Every in-scope repo has 0 open Dependabot PRs,
0 open alerts and a green or pending default branch, and the hourly agent keeps
it that way. The rest of the owner's backlog is not under control. There are 111
open GitHub issues across 13 repos, many a year old, plus 12 open pull requests
nobody has looked at, such as homelab#57–66. In August the owner imported most
of those issues into Linear by hand. The copies have drifted since and nobody
works from them.

So the hourly agent now spends a model run each hour to report `no change`,
while the owner's real backlog stays still. Nothing tells the owner which few
issues deserve their attention next, and nothing turns an approved idea into a
pull request without the owner doing the work. Copying more items into Linear
would only make a second list the owner also doesn't read.

## Solution

The agent takes on the owner's backlog the way the owner's GitLab triagebot
does, with an hourly run instead of CI and a Linear board instead of labels.
Dependency work and backlog triage are one ongoing job, with no phases.

Every backlog issue appears as a card on a board in the JGA team. Linear's own
GitHub Issues sync keeps the cards and the issues in step. The agent triages a
card when the owner asks or when a new issue arrives. When nothing waits for the
owner, it also triages the best untriaged issues itself, up to three plans at a
time. A triage run reproduces and diagnoses the issue. It then posts a fix, a
plan, a question, or a ruling that the issue is not actionable. A plan waits in
Needs approval until the owner moves the card to Approved. A build run then
implements exactly that plan and opens a pull request. Review runs then answer
the feedback on that pull request and give a verdict, and the land gate merges
what it clears. The rest waits for the owner's review.

Anything else that needs the owner also appears on the same board as a card, so
the board is the one list of owner actions: open pull requests with no issue
behind them, drafts included, and dependency work that ended blocked or in
review. The run board stops showing phases. It shows health checks that say
whether work is moving.

## User Stories

1. As the owner, I want every open issue in my in-scope repos to appear as a
   card on one Linear board, so that I have one place to see my backlog.
2. As the owner, I want cards and GitHub issues kept in step by Linear's own
   sync, so that neither the agent nor I maintain a copy by hand.
3. As the owner, I want my 76 forks left out unless I add the `backlog-agent`
   topic to one, so that upstream projects I only forked never fill my board.
4. As the owner, I want repos in other orgs left out, so that the agent only
   works on repos I own personally.
5. As the owner, I want the same scope rule for dependency work and backlog
   triage, so that opting a fork in gives it both.
6. As the owner, I want to move a card to Needs triage and have the agent triage
   it on its next hourly run, so that I can ask for triage without writing
   anything.
7. As the owner, I want a new issue I open on GitHub to be triaged
   automatically, so that it gets a first look without me moving it.
8. As the owner, I want the agent to triage the best untriaged issues by itself
   when nothing waits for me, so that the backlog keeps moving when I'm not
   steering it.
9. As the owner, I want that top-up to stop once three plans wait for approval,
   so that I'm never buried in plans.
10. As the owner, I want the top-up to pick security issues first, then bugs,
    then features, oldest first, so that the riskiest work comes up first.
11. As the owner, I want a triage run to reproduce and diagnose the issue before
    it proposes anything, so that its plans rest on how the code behaves, not on
    the issue's wording.
12. As the owner, I want a bug I sent to triage to be fixed straight away in a
    pull request, so that small bugs I asked about don't wait for a separate
    approval.
13. As the owner, I want a bug the top-up found to get a plan and wait for my
    approval, so that the agent never opens pull requests I didn't ask for.
    Story 63 is the one exception.
14. As the owner, I want a feature to come back as a plan for one slice, saying
    which files it touches, what's out of scope and which recorded decisions it
    conflicts with, so that I can approve a small, clear piece.
15. As the owner, I want to approve a plan by moving its card from Needs
    approval to Approved, so that approving takes one drag.
16. As the owner, I want only my own move to count as approval, so that neither
    the agent nor anyone else can approve a plan.
17. As the owner, I want a build run to implement the last plan the agent
    posted, not the issue text, so that what gets built is exactly what I
    approved.
18. As the owner, I want a change that spans several in-scope repos to come as
    one pull request per repo, all linked from the issue, so that each repo's
    checks and review stay separate.
19. As the owner, I want a card to reach Done only when all of its pull requests
    have merged, so that half-finished work doesn't look finished.
20. As the owner, I want every pull request from a build run labelled for review
    and assigned to me, and merged without me only when the land gate clears it,
    so that no agent-written source change is merged outside rules I can read in
    code.
21. As the owner, I want plans, questions and diagnoses posted as comments on
    the GitHub issue, so that the discussion stays with the repo and survives a
    change of tracker.
22. As the owner, I want to answer the agent's question with a comment on a
    Needs info card and have it triaged again, so that answering is all it takes
    to retry.
23. As the owner, I want the reason a card is in Needs info shown as a label
    (`needs-repro`, `unable-to-fix`, `failed`), so that I can see at a glance
    what the agent needs.
24. As the owner, I want to turn a plan down by moving the card to Canceled, so
    that rejecting is as easy as approving.
25. As the owner, I want only text I wrote to steer a run, and only that text or
    a review thread a bot opens on an agent pull request to start one, so that
    an issue or comment from someone else can't steer an agent that holds my
    GitHub token.
26. As the owner, I want issues opened by anyone else, including bots, to wait
    in Backlog until I move them, so that I decide what outside input the agent
    acts on.
27. As the owner, I want a crashed triage or build run retried once and then
    parked in Needs info with `failed`, so that a broken issue reaches me
    instead of burning runs.
28. As the owner, I want triage and build runs to share the 3-running and
    100-a-day caps with dependency work, so that the backlog can't run up cost.
29. As the owner, I want dependency work to rank above every triage and build
    run, so that a new critical alert is never stuck behind a feature plan.
30. As the owner, I want every open pull request that isn't from Dependabot and
    isn't linked to an issue to get a card in In Review, so that pull requests
    I've left hanging, like homelab#57–66, become visible.
31. As the owner, I want draft pull requests to get a card with a `draft` label
    rather than be skipped, so that I notice drafts I forgot to finish.
32. As the owner, I want a pull request's card closed when the pull request
    merges or closes, so that the board never shows finished work.
33. As the owner, I want dependency work that ends blocked or in review to
    appear as a card, so that the board, not only the run report, lists what's
    waiting on me.
34. As the owner, I want that card closed once GitHub shows the item is
    resolved, so that I don't have to tidy up after the agent.
35. As the owner, I want each card to carry a `kind` label (`dependency`,
    `backlog`, `pr`) next to its `repo` label, so that I can filter the board by
    what kind of work it is.
36. As the owner, I want the agent's moves to show up in Linear as made by the
    agent's own app, so that a card's history shows who did what.
37. As the owner, I want child agents never to write to Linear, so that a
    prompt-injected child can't move a card.
38. As the owner, I want to do only the steps that need my browser login,
    installing the GitHub sync and the agent's OAuth app, so that the agent does
    all the rest of the setup.
39. As the owner, I want the agent to create the new statuses and labels itself
    where Linear's API allows, so that setup takes me minutes.
40. As the owner, I want the agent to mark duplicates of my August import,
    matching on their "Imported from" URL, so that the board doesn't show each
    issue twice.
41. As the owner, I want an imported card the sync doesn't take over to still be
    linked to its issue through its "Imported from" URL, so that no card is
    orphaned.
42. As the owner, I want the run board to show health checks instead of phases,
    so that I can see whether work is moving.
43. As the owner, I want a health check failing when a card waits more than 24
    hours in Needs triage or Approved, so that a stalled queue is visible.
44. As the owner, I want a health check failing when a card sits in In Review
    for more than 7 days, so that reviews I'm sitting on surface.
45. As the owner, I want the health checks to include no unhandled Dependabot
    PRs or alerts and every default branch with CI green, so that dependency
    work stays watched.
46. As the owner, I want a health check failing when the top-up has put more
    than 3 cards in Needs approval, so that a top-up bug shows up at once.
47. As the owner, I want the run report to stay silent when nothing changed, so
    that quiet hours don't notify me.
48. As the owner, I want the agent's own cards and comments to start no run, so
    that the agent can't trigger itself into a loop.
49. As the owner, I want the agent to leave my personal projects in JGA, such as
    the bin insert and the speaker build, alone, so that only cards linked to an
    in-scope repo are ever triaged.
50. As the owner, I want the agent to keep working with another tracker later by
    rewriting one module, so that the rules aren't locked into Linear.
51. As the owner, I want the steward to keep this spec, the glossary and the
    prompts true to what happens, so that the docs don't drift the way the
    August import did.
52. As the owner, I want a review run to answer the feedback on a pull request
    the agent opened, so that my comments there are acted on without me starting
    anything.
53. As the owner, I want a review thread a bot opens treated as a bug report to
    check against the code, and other people's text on a pull request never
    read, so that a review app I installed helps while nobody else's text
    reaches the agent.
54. As the owner, I want a review run to run the converge review and push its
    fixes before it gives a verdict, so that a verdict rests on a change that
    has already been reviewed and fixed.
55. As the owner, I want a verdict to name one head commit and to count for that
    commit only, so that a later push never lands on an older verdict.
56. As the owner, I want the land gate's rules checked by code in `backlog.py`,
    not judged by a model, so that a steered child can't talk a pull request
    into landing.
57. As the owner, I want a landing to wait 24 hours after its verdict, and to
    stop it with the `agent:hold` label, a draft, a close, a card moved out of
    In Review or an unresolved review thread of mine, so that I can always step
    in first.
58. As the owner, I want a repo to land nothing until I add the
    `backlog-agent-land` topic to it, with its verdicts recorded and shown as
    shadow verdicts until then, so that I can judge the verdicts before any of
    them merges anything.
59. As the owner, I want a repo to stop landing once one of its landed pull
    requests is reverted, until I take the topic off and put it back, so that
    one bad landing is not followed by another.
60. As the owner, I want at most three landings a day, so that a fault in the
    land gate can't merge much before I see it.
61. As the owner, I want the agent's own repo, nix-config and converge-review
    never to land, whatever their topics, so that the agent can't change its own
    rules, the skills it runs or its own review.
62. As the owner, I want a review run never to merge and never to turn on
    auto-merge, so that the child that reads feedback is never the one that
    merges.
63. As the owner, I want a pull request for a finding to land only after I acted
    on its card, until five findings have landed in 60 days, all after the last
    revert of any landing, and from then on a top-up triage run to fix a finding
    in a repo with the land topic without waiting for me, so that the agent
    earns that step from its record and loses it on one revert.

## Implementation Decisions

- **One job, no phases.** Dependency work keeps every rule from the 2026-10-20
  design: tiers, holds, lanes, caps, retries, the ledger and lockfiles. Backlog
  triage is added beside it in the same hourly run, the same ledger and the same
  caps. The run board's phase strip and "Left before Phase 2" box are replaced
  by health checks.
- **Scope.** An in-scope repo is a non-archived repo owned by `jakob1379` that
  is not a fork, or a fork carrying the `backlog-agent` topic. No other org.
  `collect` applies this one rule to both kinds of work.
- **Scans.** A `scan` task (key `<repo>:scan`) reviews one repo's default branch
  with the `aggregate-code-quality-report` skill and files findings as GitHub
  issues, so they reach the board and the top-up like any backlog issue (ADR
  0003). A finding's fix lands only after an act by the owner, or on the
  findings record below.
  - **When.** `plan` offers a scan when the repo is not a fork, its default
    branch head differs from the one last scanned and the last scan started at
    least 7 days ago. Order: a scan being retried, then repos never scanned,
    then the repo scanned longest ago. Among repos never scanned, a dormant one,
    whose head commit is over 30 days old, goes last. A dormant repo's head does
    not move, so it is scanned once and again only after a push, which a merged
    fix for one of its findings is. Until then the findings its one scan did not
    file stay in its report. One scan runs at a time and it is appended after
    every other task, though once running it holds one of the three slots.
  - **Limit.** At most three findings wait for the owner. The task carries
    `limit`: three minus the open issues a scan filed whose card is not
    Canceled. No scan starts at zero, nor on a repo whose open issues would pass
    the 100 that `collect` reads. A scan entry that finished after the snapshot
    was taken waits for the next run's snapshot. No scan starts, and no
    remembered issue is forgotten, from a snapshot older than any scan's finish.
  - **Whose issue.** An issue is a scan's when the owner is its author and its
    body starts with the marker, or when the owner is its author and it was
    created in that repo while a scan of it ran. The second rule covers a child
    that forgot the marker; `plan` remembers those issues in the ledger
    (`found`) while they are open. Such an issue never takes the new-owner-issue
    path.
  - **Findings record.** `finding_record` counts the landings in the ledger that
    are marked as a finding's, have a merge commit, were made in the last 60
    days (`LAND_RECORD`) and were made after the ledger's `findings_since`,
    across all repos. A landing is marked as a finding's when the pull request
    closes a finding in its own repo; naming a finding in another repo does not
    mark it. `findings_since` is the time a revert of any landing was last
    marked in the ledger, so a revert resets the record: only findings that land
    after the revert was seen count. `findings_since` never moves earlier, and
    is never earlier than the newest landing in the ledger, so a clock that was
    wrong cannot keep the record. Lifting a repo's pause does not bring the
    record back, and neither does a reverted landing that leaves the ledger. The
    record is earned at 5 (`FINDING_RECORD`). While it is earned, the top-up
    starts the triage run of a finding with the origin `finding`, in a repo that
    has the land topic and is not a repo that never lands, and that run may fix
    a bug as a run the owner asked for may. `plan` checks the record, the land
    topic and that the issue is a finding again each time it queues that run and
    when it reads the run's result (see Bugs and the origin of a run). An issue
    the owner wrote is still started with the origin `topup`, whatever the
    record. `plan` prints the record as `findings`.
  - **Outcomes.** A failed scan is retried once. A scan that ends blocked, or
    fails twice, keeps a card in Needs info, like blocked dependency work and
    with its `kind: dependency` label, and the repo is not scanned while the
    card is open. When the owner closes the card, a scan that ran counts as
    scanned and one that failed twice is tried again. A run that reads no board
    leaves a blocked scan as it is.
  - **State.** The ledger keeps each repo's last scan (`scans`: its start, its
    finish and the head it was planned for), the open issues `plan` took for a
    scan's by their timing (`found`), and on every task whether its last attempt
    crashed. If the ledger is lost, each due repo is scanned once more, and an
    unmarked issue from an earlier scan reads as the owner's again.
  - **Incremental.** The child keeps each repo's last `findings.json` under
    `work/.scans/` and hands it to the next scan as its baseline, so that scan
    reviews only the files changed since, and in full only the lanes the last
    one did not finish. Each finding has an id built from its category, file and
    enclosing symbol, the issue carries it, and a child never files an id an
    earlier issue has. Losing `work/.scans/` costs one full scan.
  - **Profiles.** When the skill drafts a profile for a stack it has none for,
    or a fix for an existing one, the child opens one pull request on nix-config
    that adds or changes that profile, in the review lane and never auto-merged.
    One per scan at most. The task carries `profile`, false while the
    needs-review cap is full when the scan is planned; a scan already running
    may still open its one. None when the scanned repo is private and nix-config
    is public, none for a draft whose trial failed or a fix for a broken profile
    with no command that ran, and none for a stack whose profile the owner
    already has in review, merged or turned down; a fix is keyed on the
    profile's content, so it is offered once until the profile changes. The pull
    request quotes no finding from the scanned repo. It gets its own card like
    any pull request with no issue behind it. The agent picks a merged profile
    up on its next scan, which builds the skills from nix-config's newest
    default-branch commit.
  - **Skills.** The skill comes from the owner's nix-config. Each scan builds
    `github:jakob1379/nix-config#agent-skills`, the default branch's newest
    commit (`--refresh`, so no cached lookup is used). When that build fails,
    the scan runs the older copy this repo's flake holds as `.#skills` and says
    so in its result: otherwise a broken nix-config would fail each due scan
    twice and park its repo in Needs info. Accepted risk: nix-config's default
    branch reaches a scan with no second look by the owner. The skill's text
    steers the child, and a profile's commands run with the sandbox off. A
    profile pull request is never auto-merged, gets no review run and never
    lands, since nix-config is a repo that never lands, so the owner's merge
    there is the one review. Plugin skills its lanes call and the scanners it
    downloads are whatever the machine has.
- **Board.** The JGA team's statuses are Backlog, Needs triage, Needs approval,
  Approved, In Progress, In Review, Needs info, Done, Canceled and Duplicate.
  Needs triage, Needs approval, Approved and Needs info are new. In Review means
  at least one pull request is open, which the owner merges or, when the land
  gate clears it, the agent lands. Canceled means not actionable. Labels: the
  existing single-select `repo` group, a new single-select `kind` group
  (`dependency`, `backlog`, `pr`), and the Needs info reasons `needs-repro`,
  `unable-to-fix`, `failed`, plus `draft`. Only cards linked to an in-scope repo
  are ever touched.
- **State machine.** These moves are the contract, and `plan` enforces them:

  | From           | To             | Made by             | When                                                    |
  | -------------- | -------------- | ------------------- | ------------------------------------------------------- |
  | Backlog        | Needs triage   | owner, or the agent | the owner asks; a new issue by the owner; top-up        |
  | Needs triage   | In Progress    | agent               | a triage run starts                                     |
  | In Progress    | In Review      | agent               | a fix pull request was opened                           |
  | In Progress    | Needs approval | agent               | a plan was posted                                       |
  | In Progress    | Needs info     | agent               | question, can't reproduce, can't fix, or second failure |
  | In Progress    | Canceled       | agent               | not actionable                                          |
  | Needs approval | Approved       | **owner only**      | approval                                                |
  | Needs approval | Canceled       | owner               | plan turned down                                        |
  | Approved       | In Progress    | agent               | a build run starts                                      |
  | Needs info     | Needs triage   | agent               | the owner commented after the agent's last comment      |
  | In Review      | Done           | sync or agent       | every linked pull request merged, whoever merged it     |

  A move to Approved by anyone but the owner is undone back to Needs approval. A
  card that arrives in Approved any other way is ignored. A review run and a
  landing move no card: the card stays in In Review until its pull requests have
  merged.

- **Triggers.** A triage run starts for a card in Needs triage. A new issue
  authored by the owner is moved there automatically. The top-up runs only when
  Needs approval and Approved are both empty and no triage or build run is
  going. It moves the best Backlog cards to Needs triage until three plans could
  wait for approval. Its order is the `security` label, then `Bug`, then
  everything else, oldest issue first within each.
- **Trust.** Only the owner is a trusted author. `collect` records the author of
  each issue and comment. On the board, the owner is the one active human admin
  of the Linear workspace. When there is not exactly one, `collect` prints
  `board not read` and the run makes no card moves. `plan` never starts a run
  from a card whose issue was written by someone else unless the owner moved it.
  A child's prompt never includes comments from untrusted authors, with one
  exception. On an agent pull request, only feedback counts (defined under
  Review runs): the owner's text anywhere, and the first comment of a review
  thread a bot opened. A bot's thread is admitted because an app only reviews
  where the owner installed it, so the install is the owner's act. A review run
  reads it as a bug report to check against the code, never as an instruction. A
  bot's other comments, its replies and everything from other people are never
  read, because those are where someone else's text could ride along. The
  agent's own comments on a pull request start with its marker, so they never
  count as the owner's. Neither does the status comment, which starts with
  `<!-- backlog-agent:status -->`.
- **Bugs and the origin of a run.** `plan` records the origin of each triage run
  in the ledger (`origins`). It is `owner` when the owner moved the card to
  Needs triage or wrote the new issue, `topup` when the top-up picked the issue,
  and `finding` when the top-up picked a finding in a repo with the land topic
  while the findings record is earned (see Scans). The owner's move to Needs
  triage makes it `owner`. A retried run, a run for a card still in Needs triage
  and a triage started again after a reply keep the stored origin, with one
  exception. A stored `finding` is queued as `finding` only while the record is
  still earned, the repo still has the land topic and the issue is still a
  finding. Otherwise the run is queued as `topup`. A triage run with the origin
  `owner` or `finding` may open a fix pull request for a bug. With `owner` its
  `fix-pending` moves the card to In Review. With `finding` it does so only
  while those three conditions hold when `plan` reads the result. Otherwise the
  card goes to Needs approval, like a top-up result. With the origin `topup` the
  child is told to post a plan and open no pull request. That is an instruction
  to the child, not a code guarantee. What code enforces is that `plan` turns
  such a run's `fix-pending` into Needs approval, so the card does not reach In
  Review through it.
- **Task kinds and keys.** Two new ledger task kinds join the four from
  dependency work: `owner/repo#<issue>:triage` and `owner/repo#<issue>:build`.
  They use the same `start`, `finish`, lockfile, retry and daily-cap rules. In
  the tier order they rank after add-CI. Scans add a seventh kind,
  `owner/repo:scan`, described under Scans. Review runs add an eighth, `review`,
  with the key `owner/repo#<pr>:review`: it is keyed on the pull request, not
  the issue, and described under Review runs.
- **Child result contract.** A triage or build child ends with
  `RESULT: <outcome> — <note>`. The outcome is one of `fix-pending`,
  `needs-approval`, `needs-repro`, `unable-to-fix` or `not-actionable`, and the
  note names every pull request URL. Plans, questions and diagnoses go on the
  GitHub issue as comments. A triage, build, review or dependency `pr` child
  also writes a report file before its result line, and `finish` shows it in the
  thread's status comment
  ([status comments design](2026-11-28-status-comments-design.md)): one comment
  per issue or pull request, edited in place when a run starts and ends, showing
  the scrubber's state, the owner's next move, the card's progress, the latest
  report and the run history. A build child takes the agent's last plan comment
  as its spec, opens one pull request per in-scope repo it changes, labels each
  `needs-review`, assigns the owner, and links each one from the issue. A child
  never merges a pull request from a triage or build run and never turns on
  auto-merge for it: only the land gate merges one without the owner. A review
  child ends with
  `RESULT: land|no-land|failed — <reason> <head commit> <PR URL>; <note>`. The
  reason is one of `clean`, `not_trivial`, `off_plan`, `not_converged`,
  `possible_regression`, `removed_safety_logic`, `insufficient_tests`,
  `scope_too_large`, `unclear_intent`, `security_risk`, `breaking_change`,
  `build_or_ci_risk`, `injection_attempt` or `review_error`, and `clean` is the
  only one a `land` may carry.
- **Retries.** A crashed or timed-out run is retried once. A second failure
  sends the card to Needs info with `failed`. This is the same rule as
  dependency work, not triagebot's three failures. A review run is the
  exception: its failure is never turned into blocked and moves no card, and the
  round limit bounds its retries.
- **Review runs.** A `review` task is one run on an agent pull request: it
  answers the feedback, runs the converge review, pushes its fixes and ends with
  a verdict, land or no-land with one reason (ADR 0004). The verdict is one
  input to the land gate, and a review run never merges.
  - **Agent pull request.** An open pull request whose author is the owner,
    whose head branch is in the same repo and fully matches
    `backlog-agent/.+-\d+-(build|triage)` (`AGENT_BRANCH`), and which links an
    issue. A build or triage branch ends in the issue number and its kind, so a
    dependency work branch for a package named `build` does not match. Only
    these get review runs and only these can land. The agent posts with the
    owner's token, so the author alone does not tell its pull requests from the
    owner's own.
  - **Feedback.** Text on a pull request that does not start with the agent's
    marker, once leading whitespace is stripped, and is one of two things. By
    the owner: a conversation comment, a review body or a review thread comment.
    By a bot, an author GitHub's GraphQL API types as `Bot`: the first comment
    of a review thread, and nothing else. Anything else is not feedback: it
    starts nothing, blocks nothing and never reaches a child. That covers every
    other person, a bot's conversation comments and review bodies, and a bot's
    replies inside a thread. Only its number can matter: a pull request with
    more comments than one read holds has open feedback and does not land,
    whoever wrote them. The owner's edit of their own comment counts as new
    feedback from the time of the edit. A bot's edit does not. Every comment and
    thread reply a child posts on a pull request starts with the marker, so the
    agent's own text is never feedback. The one exception is a `@dependabot`
    command on a Dependabot pull request, which stays the bare command: feedback
    is read only on an agent pull request.
  - **When.** A review run is due when the pull request has no review entry,
    when its entry failed, when its head commit is not the one the entry
    records, when feedback is newer than the entry's start, or when its required
    checks are red and its head commit's date is newer than the entry's start.
  - **When not.** No review run starts without a board, or while a linked issue
    has no card in In Review. None starts while the pull request is a draft or
    carries the `agent:hold` or `agent:blocked` label, while its checks are
    pending, or within 30 minutes (`REVIEW_QUIET`) of the later of its head
    commit's date and its newest feedback. The head commit's date stands in for
    the push time, which GitHub does not give on this read. None starts for a
    pull request whose base is not the repo's default branch, or that is no
    longer open when it is read. None starts while a triage or build entry of a
    linked issue is running or failed. One review run runs at a time: none
    starts while any review entry is running or its lockfile is held.
  - **Rounds.** A pull request gets at most three review runs (`REVIEW_ROUNDS`).
    The entry's attempts count them, and the task carries `round`, the attempts
    plus one. After the third, another starts only when feedback by the owner is
    newer than the entry's finish.
  - **Order.** `plan` puts the one review run after dependency work and before
    build and triage runs, under the same caps. Its task is
    `{"key": "o/r#12:review", "kind": "review", "repo": "o/r", "pr": 12, "head": "<head commit>", "round": 1}`.
  - **What the child does.** It reads the pull request through a filter that
    keeps exactly the feedback, each item marked as the owner's or a bot's. The
    owner's text is an instruction within the approved plan, and an owner
    comment that objects to or questions the change means no-land. A bot's text
    is a bug report to check against the code. The child replies to every
    thread, resolves a bot's thread, and never resolves a thread the owner
    started. The spec is the approved plan on a branch that ends in `-build`,
    and the comment the triage run posted with its diagnosis on a branch that
    ends in `-triage`. The child then follows the converge-review skill with
    that spec, inside a time budget after which no further round starts. The
    subagents that skill dispatches, and the one that applies the rubric, are
    the only ones a child may start.
  - **Verdict.** The verdict is no-land with `not_converged` unless the converge
    review converged. Otherwise one fresh, report-only subagent applies the
    rubric in `CHILD.md` to the final patch and that spec: land only when every
    part of the change fits a safe class, no-land when unsure. The safe classes
    are narrow, so bug fixes with a failing test, refactors, tests and docs are
    what lands, and most feature work still waits for the owner. The child posts
    no verdict comment: it writes the verdict as its report, and `finish` shows
    it in the pull request's status comment, whose `**Feedback read to:**` line
    the next review run counts its answered feedback from. That comment and any
    label only show the verdict: the gate reads the ledger, since anything with
    write access could forge a comment or a label.
  - **Entry.** The review entry lives exactly as long as its pull request is
    open. Its finished status is `land`, `no-land` or `failed`. `finish` refuses
    an entry that is not running and stores `head`: the first 40-hex commit id
    in the note. It stores `land` only when the note starts with `clean ` and a
    40-hex commit id, and stores any other `land` as `no-land`. A verdict
    therefore covers one head commit, and a push after it makes it stale. A
    verdict that is not `land`, given for an entry that already holds a `land`,
    turns it into `no-land`: a late run can take a landing back, never give one.
    `finish` takes an optional `--task <task id>`, and the dispatcher passes it
    for every review key. For a review key whose entry is running, `finish`
    exits non-zero with
    `<key> is run by another task, so this result is dropped` when that id is
    not the entry's task id, before the entry changes and before the lock is
    released: a newer review run owns the key, and the result of the run it
    replaced is dropped. A missing `--task` counts as the empty id, which
    matches a claim that was never started.
- **The land gate.** The land gate is the rule in `plan` that decides which
  agent pull requests land, and `backlog.py land` makes the merge (ADR 0004).
  `land_block` checks the rules below in this order and returns the first one
  that fails as the reason the pull request is held. A pull request lands only
  when all 19 hold:

  | #   | The pull request lands only when                                                                                                                                    | Held as                                                       |
  | --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------- |
  | 1   | its review entry's status is `land`                                                                                                                                 | `no verdict`                                                  |
  | 2   | the entry's head commit is the pull request's head commit                                                                                                           | `verdict is stale`                                            |
  | 3   | it is open                                                                                                                                                          | `not open`                                                    |
  | 4   | its base is the repo's default branch                                                                                                                               | `base not the default branch`                                 |
  | 5   | it is not a draft and has neither the `agent:hold` nor the `agent:blocked` label                                                                                    | `held by the owner`                                           |
  | 6   | every linked issue is open in the snapshot and written by the owner's account; a finding also needs the owner's go-ahead on its card, or the earned findings record | `issue not the owner's`                                       |
  | 7   | every linked issue has a card in In Review and no card in another open status                                                                                       | `card <status>`                                               |
  | 8   | no other open agent pull request shares a linked issue, and every repo's open pull requests were all read                                                           | `split across pull requests`, or `pull requests not all read` |
  | 9   | GitHub's `mergeStateStatus` is `CLEAN`                                                                                                                              | `merge state <state>`                                         |
  | 10  | its required checks are green                                                                                                                                       | `checks <state>`                                              |
  | 11  | the repo's default branch is green                                                                                                                                  | `default branch <state>`                                      |
  | 12  | no thread of feedback is unresolved and no feedback is newer than the review run's start                                                                            | `open feedback`                                               |
  | 13  | it changes at most 10 files and 300 lines                                                                                                                           | `too large`                                                   |
  | 14  | no changed file is on a protected path                                                                                                                              | `protected path <path>`                                       |
  | 15  | no landed pull request of the repo is marked reverted                                                                                                               | `paused after a revert`                                       |
  | 16  | the repo has the land topic and is not a repo that never lands                                                                                                      | `shadow`                                                      |
  | 17  | the gate has neither offered this head commit for a merge nor tried to merge it                                                                                     | `merge not made`                                              |
  | 18  | fewer than 3 landings were made today, those this plan offers included                                                                                              | `daily limit`                                                 |
  | 19  | 24 hours have passed since the review entry finished                                                                                                                | `lands <iso time>`                                            |

  A missing value fails its rule: no board, no card and no review data all hold
  the pull request. `<state>` and `<status>` print in lower case, and `none` for
  a missing one, so a pull request whose base branch requires no checks is held
  as `checks none`. `CLEAN` means every check passed, required or not, so one
  red job holds the pull request. Rule 4 holds a pull request whose base is
  missing or is another branch: a review run judged it against that base, while
  rule 11 and the pause after a revert read the default branch. Rule 6 counts a
  finding, an issue a scan filed with the owner's account, like an issue the
  owner wrote, on one more condition: an act by the owner in the history of a
  card of that issue that is In Review. The owner's acts are three: approving
  the plan, asking for the triage by moving the card to Needs triage, and moving
  the card into In Review by hand. The gate reads them with `sponsor`, which
  walks the card's history backwards from its newest move. The walk ends at the
  first move by the owner: the owner acted when that move is to In Review,
  Approved or Needs triage, and did not when it is to any other status. The walk
  ends with no act at the first move by anyone who is neither the owner nor the
  agent. It ends as the top-up's start at a move by the agent to Needs triage
  from any status but Needs info, also when Linear names no status the card
  left, and at any move by the agent out of Backlog. Linear merges moves that
  one actor makes a few minutes apart into one row of history, so the top-up's
  move to Needs triage and the moves after it can read as one move out of
  Backlog. Any other move by the agent is skipped. That covers a triage the
  owner asked for and the agent started again after the owner's reply, since the
  agent's move from Needs info to Needs triage is skipped. A history with no
  move by the owner has no act. Neither has a history that Linear cut short: a
  card whose `history_cut` is true or missing has no act. Without an act the
  pull request is held as `issue not the owner's`. So a finding does not land
  when anyone else put its card in In Review, nor when the owner's newest move
  took the card somewhere else. The one exception is a finding whose triage run
  the ledger records with the origin `finding`: it passes with no act by the
  owner when the walk reaches the top-up's start and every move in the card's
  history is the agent's, and only while the findings record is still earned
  when the gate runs, in `plan` and again in `land`. So a card the owner pulled
  back, or that anyone else moved, does not land on the record. A revert between
  the fix and the landing therefore holds the pull request as
  `issue not the owner's`. An issue the owner wrote needs no such act. Rule 7
  reads every card of a linked issue, before `plan` tidies the board, since an
  issue can have two cards until `apply` drops one. The pull request is held
  when the issue has no card in In Review, and when any of its cards is in an
  open status other than In Review. `<status>` is the status of the card that
  holds it. Rule 8 means a change split across several open pull requests waits
  for the owner. It also holds every pull request, as
  `pull requests not all read`, while a repo has more open pull requests than
  the 50 `collect` reads, since a sibling could be past the cut. Rule 12 also
  holds a pull request with feedback the read could not hold in one page: more
  than 100 conversation comments, reviews or review threads, or a thread with
  more than 100 comments. Such feedback counts as unresolved, and it is not
  paged. Rule 17 holds a head commit the entry records as `offered` or as
  `tried`.

  - **Constants.** `REVIEW_ROUNDS` is 3, `REVIEW_QUIET` 30 minutes, `LAND_DELAY`
    24 hours, `LAND_MAX_FILES` 10, `LAND_MAX_LINES` 300, `LAND_DAILY_CAP` 3,
    `LAND_RECORD` 60 days and `FINDING_RECORD` 5. `LAND_TOPIC` is
    `backlog-agent-land` and `HOLD_LABEL` is `agent:hold`. Lines are additions
    plus deletions.
  - **Shadow verdicts.** A repo lands only when it has the `backlog-agent-land`
    topic and is not in `NEVER_LAND_REPOS`. Without the topic every verdict is a
    shadow verdict: recorded in the ledger, shown in the run report and never
    acted on. The owner's topic is the judgement that a repo's shadow verdicts
    were good enough.
  - **Repos that never land.** `jakob1379/backlog-agent`, `jakob1379/nix-config`
    and `jakob1379/converge-review`, whatever their topics: they hold the
    agent's rules, the skills and profiles that run with the sandbox off, and
    the review itself.
  - **Protected paths.** A path is protected when any of its directories or its
    file name is one of these. CI and merge gating: `.github/`,
    `.gitlab-ci.yml`, `.circleci/`, `.pre-commit-config.yaml`, `.husky/`,
    `CODEOWNERS`. Agent instructions: `.claude/`, `.agents/`, `.cursor*`,
    `.mcp.json`, `CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md`, `GEMINI.md`,
    `SKILL.md`. Build, dependency and check config: `pyproject.toml`,
    `setup.py`, `setup.cfg`, `requirements*.txt`, `requirements/`, `Pipfile`,
    `tox.ini`, `noxfile.py`, `pytest.ini`, `conftest.py`, `.coveragerc`,
    `codecov.y*ml`, `package.json`, `.npmrc`, `tsconfig*.json`, `jest.config.*`,
    `vitest.config.*`, `playwright.config.*`, `Cargo.toml`, `go.mod`, `go.sum`,
    any `*.lock`, `package-lock.json`, `npm-shrinkwrap.json`, `pnpm-lock.yaml`,
    `bun.lockb`, `.gitmodules`, `*.nix`, `Makefile`, `justfile`,
    `Taskfile.y*ml`, `Dockerfile*`, `*compose*.y*ml`, `ruff.toml`, `.ruff.toml`,
    `mypy.ini`, `biome.json*`, `eslint.config.*`, `.eslintrc*`. Scripts and
    secrets: `scripts/`, `bin/`, `*.sh`, `.env*`. A renamed file counts under
    both its names.
  - **Plan output.** `plan` walks the review entries with status `land`, oldest
    verdict first. `lands` lists the pull requests to merge now, as
    `{"key", "repo", "pr", "head"}`, and `plan` stores each one's head on its
    entry as `offered`. One pull request per repo goes into `lands` in a plan: a
    second one that passes the gate is held as `default branch pending`, because
    the first merge makes a default branch commit no check has run on.
    `greenlit` lists every other entry with status `land`, as
    `{"key", "repo", "pr", "why", "new"}`, with the first rule that holds it.
    `new` is true the first time the entry is held for that reason, and a run
    with a new one reports, so a pending landing and each reason it waits for
    show once. The run report shows both: what landed and what lands next.
    `findings` is `{"record", "needed"}`: the findings record and the
    `FINDING_RECORD` it takes to be earned, which the run report shows too.
  - **Landing.** After `apply`, the dispatcher runs
    `backlog.py land <candidates> <plan>` when `lands` is not empty. Under the
    ledger lock, and for each entry of `lands`, the command first checks that
    the entry has a head, and that it is both the head the ledger holds as
    `offered` and the verdict's head. If not, the plan is not the one that made
    the offer, and the pull request is refused as `not offered` with nothing
    read. The command then clears the entry's `offered` and reads again what the
    gate reads: the board, the repo's topics and default branch, that branch's
    required checks, the reverts of the repo's landings, and last the pull
    request. When a linked issue has the origin `finding`, it also reads the
    reverts of every other repo that has a landing with no `reverted` value,
    since a revert of any landing resets the findings record. It runs the gate
    on those fresh reads, so a card the owner moved, a land topic the owner
    removed, a default branch that moved and a new revert all hold the pull
    request. A revert found here is marked in the ledger, so the pause lasts,
    and any revert found here sets `findings_since`. Only the linked issues and
    the other open pull requests still come from the plan's snapshot. When one
    of these reads fails, or its reply cannot be used, the command merges
    nothing for that pull request, reports it as refused with the error's last
    line and goes on to the next one. When the gate now holds it, the command
    reports it as refused with that reason. Both leave `offered` cleared, so the
    next plan decides again. Otherwise it stores the head on the entry as
    `tried`, records the landing with no merge commit and saves the ledger, so a
    command cut off during the merge still counts toward the daily limit and is
    not tried again. The command reads the clock again for each pull request, so
    a landing counts toward the day it is made. It then runs
    `gh pr merge <pr> --repo <repo> --squash --match-head-commit <head>` once,
    with no retry, and reads the pull request once more. When the pull request
    is merged and its head is the verdict's head, the record gets the merge
    commit. When that read fails, or its reply cannot be used, the outcome is
    unknown whatever the merge command said, so the record stays with no merge
    commit and the daily limit and the pause still count it. When the read shows
    the pull request not merged, the record is removed. Anything but a confirmed
    merge is reported as refused with the error's last line, and `tried` stays
    the head, so rule 17 holds the pull request until it has a new head. A
    `land` that runs again with the same plan is refused as `not offered`, since
    the first run cleared the offer. A `land` command that never ran leaves
    `offered` set, so rule 17 holds the pull request then too. The command saves
    the ledger after each pull request and prints
    `{"landed": [{"repo", "pr", "sha"}], "refused": [{"repo", "pr", "why"}]}`.
  - **Why not auto-merge.** `gh pr merge --auto` merges at once when nothing
    required is pending, and an armed auto-merge survives later pushes, so it
    can merge a head no verdict covers. The gate merges directly, pinned to the
    verdict's head.
  - **Ledger.** `landed` is a list of
    `{"repo", "pr", "head", "sha", "at", "finding"}`, one per merge the gate
    made or was cut off making. `finding` is true when the pull request closes a
    finding in its own repo, and the findings record is counted from these
    entries. In each entry `sha` is the merge commit, or `null` for a landing
    recorded with no merge commit. An entry gets `"reverted": true` once a
    revert is seen, and `"reverted": false` once the owner lifts the pause. An
    entry is kept while `reverted` is true. Any other entry is dropped once it
    is older than 60 days. `findings_since` is an ISO time. `plan` and `land`
    set it to the current time when they first mark a landing as reverted, never
    to an earlier time than it holds, and the findings record counts only
    landings made after it. Lifting a pause does not change it.
  - **Pause after a revert.** `collect` reads the default branch's commits,
    every page of them, since the repo's oldest landing that has no `reverted`
    value, and `land` reads them the same way. A landing marked reverted, and
    one whose pause the owner lifted, is not scanned again. A landed pull
    request counts as reverted when a commit message holds
    `This reverts commit <its merge sha>`, or when a commit's pull request
    branch starts with `revert-<its number>-`. For a landing recorded with no
    merge commit, the scan reads the merge commit from the merged pull request
    and writes nothing to the ledger. The message is matched only when a merge
    commit is known. The repo then lands nothing. The owner lifts the pause by
    taking the topic off for one run and putting it back: when `plan` sees a
    repo with a reverted landing in the snapshot without the land topic, it sets
    that landing's `reverted` to false. A repo that is missing from the snapshot
    keeps its pause. The landing stays in the ledger and still counts toward the
    daily limit of its day, and the same revert does not pause the repo again.
  - **Stopping a landing.** The owner stops one pull request with the
    `agent:hold` label, a draft, a close, a card moved out of In Review, or a
    review thread left unresolved, since a child never resolves a thread the
    owner started. A plain comment restarts the review and the delay, and a
    review run answers it. A comment made while the last allowed review run is
    going starts no further run, so the pull request is held as `open feedback`
    until the owner comments again or merges it. The owner pauses a whole repo
    by removing the topic. GitHub sends the owner no notice of the agent's
    comments, since they share an account, so the run report is where a pending
    landing shows.
  - **Who may change it.** The dispatcher and the steward never loosen the land
    gate, the findings record or the verdict rubric. A change that would let
    more land is the owner's.

- **Cards for pull requests and owner actions.** `plan` creates a card in In
  Review, with `kind: pr`, for every open pull request in an in-scope repo that
  isn't Dependabot's and isn't linked to a backlog issue. A draft gets `draft`
  too. It also creates a card, with `kind: dependency`, for dependency work that
  ended blocked (Needs info) or in the review lane (In Review). It closes these
  cards once GitHub shows the item resolved, to Done from whichever open lane
  the card is in, so a `kind: pr` card the owner moved out of In Review still
  closes when its pull request merges or closes. A dependency card's title
  starts with its ledger key, which is how `plan` finds it again, because an
  alert, a red default branch or missing CI has no GitHub URL to link. Only a
  Dependabot pull request's card is linked. A dependency card the owner closed
  by hand comes back while the work still needs the owner.
- **A card for every backlog issue.** `plan` creates a Backlog card, with
  `kind: backlog` and its `repo` label, for every open issue in an in-scope repo
  that no card links, once the issue is an hour old. The sync only creates cards
  for issues opened after a repo is linked, and only for linked repos, so this
  covers older issues, unlinked repos and new repos. The hour gives the sync
  time to make its own card first.
- **Linear access.** One module talks to Linear, through the GraphQL API and the
  agent's own OAuth app's client ID and secret, stored next to the GitHub token
  in `linear-client`. Each run asks Linear for a client-credentials token, which
  acts as the app and lasts 30 days with no refresh token, so nothing has to be
  renewed by hand. `collect` reads the board: cards, statuses, labels, each
  card's linked issue or pull request, and its status history with who made each
  move. Writes happen in a new step after `plan`, which applies the moves `plan`
  decided. Only `backlog.py` makes writes. Children never get the Linear token.
  Per ADR 0001, nothing else in the agent knows it is Linear.
- **Linking a card to its issue.** Use the sync's link when there is one.
  Otherwise use the "Imported from <url>" footer of the August import. A card
  with neither is not touched.
- **Setup.** The owner links in-scope repos to the JGA team in Linear's GitHub
  Issues sync, one-way (GitHub to Linear), and creates the agent's OAuth app.
  Two-way is never used: it would turn every card made in JGA into a GitHub
  issue. Everything else is a one-off agent job, run before the board is used:
  creating the statuses and labels where the API allows, otherwise listing them
  as an owner action, and marking August's imported cards as Duplicate when
  another card links the same URL.
- **Health checks on the run board:**
  1. no Dependabot PRs or alerts left unhandled
  2. no card in Needs triage or Approved for more than 24h
  3. at most 3 top-up cards in Needs approval
  4. no card in In Review for more than 7 days
  5. every default branch with CI green, with pending ones shown muted

  `plan` counts them from the `collect` snapshot.

- **Timing.** Everything happens on the existing hourly run, with no webhooks
  and no server.

## Testing Decisions

- A good test feeds `plan` a snapshot, the same shape `collect` prints, and
  asserts on what `plan` decides: tasks to dispatch, cards to move, create or
  close, and health-check counts. It never asserts on how `plan` gets there. A
  refactor that keeps the decisions must keep the tests green.
- **Seam 1, `plan`.** The state machine and every rule above is tested here with
  fixtures, like the existing `PlanTest` cases:
  - an approval only counts from the owner, and an Approved card moved by anyone
    else goes back
  - the top-up starts only when nothing waits and stops at three, in
    label-then-age order
  - untrusted authors start nothing
  - a top-up bug can't go straight to a fix, except a finding in a repo with the
    land topic while the findings record is earned
  - a finding's pull request is held without an act by the owner in its card's
    history, and a revert takes the findings record away
  - one retry, then Needs info with `failed`
  - triage and build rank below all dependency work and share the caps
  - pull request cards open and close
  - forks count only with the topic
  - each health check counts right
  - a review run is due, and is held back, by each rule under Review runs, with
    one at a time and the round limit
  - the land gate holds a pull request for each of its rules, one test per rule,
    and `lands` and `greenlit` name what it decided
- **Seam 2, the CLI round trip.** Like `CliTest`: `plan` claims a triage task,
  `start` records it, and `finish` with each triage outcome produces the card
  move the table above names. A review run makes the same round trip with `land`
  and `no-land`, and a `land` whose note does not start with `clean ` and a
  commit id is stored as `no-land`. The `land` command is tested here with `gh`
  faked: a head that moved after the plan is refused, a failed merge is not
  tried again by the next plan or by a second `land` with the same plan, and a
  `land` that never ran leaves the pull request held.
- **Seam 3, pull request reads with `gh` faked.** `gh` is faked at
  `subprocess.run`, as `GhRateLimitTest` does, to test what `collect` reads for
  an agent pull request: feedback by the owner, a bot opening a thread, a bot's
  review body, a bot's reply, a stranger and the agent's marker, unresolved
  threads, a rename, and a revert found both ways.
- **Not unit-tested:** the Linear module and the other GitHub calls in
  `collect`. They are I/O, as today's `gh` calls are. A broken mapping shows up
  as a failed `collect` or a red health check on the next run, and ADR 0001's
  loud failure on a missing status covers renames.
- Run the tests with `python3 -m unittest` in the agent's directory, as today.
  Committing rule changes stays gated on a passing run.

## Out of Scope

- Using Linear features beyond the board: cycles, projects for backlog work,
  Linear's own automations, triage intelligence.
- Webhooks, or any always-on server.
- Repos in other orgs, and forks without the `backlog-agent` topic.
- Agent-set Linear priorities or estimates. Ordering comes from labels and age
  only.
- Any change to dependency work's rules, apart from scope and the cards it now
  creates.
- A second tracker. The design only keeps porting to one module.
- GitHub auto-merge on anything a triage or build run produced, and a child
  merging it. Only the land gate merges one without the owner.
- A health check that counts findings, and labels or priorities set by a scan.
- Scanning forks, and writing on repos outside the scope.
- A trained risk score and its percentiles: they need a population of diffs the
  owner's repos don't have.
- A second GitHub identity, or an approving review as the verdict: GitHub
  refuses an approval from a pull request's author, and the agent posts with the
  owner's token.
- A verdict from another model family.
- An automatic track record before a repo's first landing. The owner's topic is
  that judgement.
- Detecting a hand-written or partial revert.
- Review runs for dependency work's pull requests and for profile pull requests.
- A card label for a pending landing.

## Further Notes

- **Home.** On 2026-11-22 the agent moved from branch `feat/backlog-agent` of
  `jakob1379/random` to its own private repo, `jakob1379/backlog-agent`, checked
  out at `/home/jsg/Documents/jsg/backlog-agent`. It runs from `main` there.
  Being a non-fork repo of the owner's, it is in scope for its own dependency
  work and backlog triage.
- **Linear's GitHub Issues sync.** Ten repos with open issues were linked
  one-way to JGA on 2026-11-22, and the other 37 in-scope repos on 2026-11-23,
  so every repo in scope is linked. A repo created or opted in later needs
  linking by hand (Settings → Integrations → GitHub), and its `repo: <name>`
  label added to JGA by the owner, since the agent can't create labels: on
  2026-11-24 `apply` created the card for backlog-agent#1 without its `repo`
  label and listed `repo: backlog-agent` in `missing_labels`. One-way only
  governs where issues are created: updates to a synced issue are meant to go
  both ways, so a card the sync made, moved to Done or Canceled, should close
  its GitHub issue, and comments in its synced thread should reach GitHub.
  Neither has been seen yet. Closing a card has not closed its issue so far: on
  2026-11-23 `apply` moved the cards for homelab#3 and jakob1379.github.io#6 to
  Canceled and both issues stayed open, so a not-actionable issue stays open on
  GitHub until the owner closes it. The sync makes no cards for issues that
  existed before a repo was linked, which is why `plan` creates them. Linear
  also posts link-back comments on GitHub issues in private repos; they are not
  the owner's, so the trust rule ignores them. Still unverified: how the sync's
  own moves appear in a card's history, and whether closing a GitHub issue moves
  its card to Done. By 2026-11-23 the sync had made no card and no move on JGA.
  It made its first on 2026-11-26: AMAS#4, #5 and #6, filed by a scan at 20:05,
  had cards (JGA-294 to JGA-296) by the 20:52 run, inside the hour in which
  `plan` creates none. They sat in Backlog with neither `kind: backlog` nor
  `repo: AMAS`, so `plan` added both. So the sync's cards land in Backlog
  unlabelled, and the next `plan` labels them like its own.
- **What the first live run showed (2026-11-23).** A label-only move sends the
  card's current status, and Linear records no status change for it: the 72
  relabelled cards show only the label change, so the agent does not become the
  card's last mover. The app actor can run `issueCreate` and
  `attachmentLinkURL`. It cannot create labels or statuses, so `apply` leaves a
  label the board lacks off the card and lists it in `missing_labels` for the
  owner to add; the next `plan` adds it once it exists.
- **Linear's rate limit on links.** Linear allows 20 attachment links a minute
  and answers HTTP 429 past that. The first board sync on 2026-11-23 had 53
  creates and failed partway on it, so the Linear module now waits a minute on a
  429 and retries. `apply` makes moves before creates and stops after about 4
  minutes, so it ends inside the dispatcher's 10-minute command timeout. Only a
  429 is retried: Linear did not run that call. A timeout or a 5xx may have run
  it, so the op stays pending, and the next `plan` sees a card that did land. It
  keeps no outbox: the ops it did not reach come back in the next `plan`, which
  reads the board, so a large sync drains over a few runs. A replayed outbox
  could undo a move the owner made since.
- **A Linear error that is not a 429 stops `apply`.** On 2026-11-26 the run's
  one op, a create for nix-config#154, failed with the GraphQL error
  `usage limit exceeded`. It is not a 429, so it was not retried: `apply`
  stopped there and the op stayed pending. The next `plan` emits it again, so
  each run hits the same error until its cause is gone. A later run that day
  narrowed it: its one move, closing JGA-200 to Done, went through, and the
  first of its 24 creates failed with the same error. So the limit refuses new
  cards, not moves. It does not stop the sync: the sync made three cards that
  evening, while the agent's create for nix-config#157 still failed with it. The
  owner then upgraded the workspace's Linear plan, and the next `apply` created
  that card, JGA-298, with no error. So the limit was the old plan's. The agent
  can't read the workspace's limits, so if the error comes back it goes under
  **Needs you** for the owner to check.
- **No card is lost, and none is doubled.** Every run, `plan` takes the open
  cards that link the same in-scope issue or pull request and keeps one: the one
  the owner has moved out of Backlog, then one with its GitHub link attached,
  then the oldest. The others are dropped: `apply` removes their GitHub link
  first, so the sync can't close the issue, and then moves them to Duplicate. A
  kept card without its GitHub link attached, known only by its "Imported from"
  footer, gets the link attached. That retries a link that failed after a
  create, and it also gives a footer-only card that never had one, such as an
  August import, its first. A run on 2026-11-23 linked 74 cards this way and
  `apply` reported all 74 done. If a later `plan` emits `link` for the same
  cards, the link did not land where `collect` reads attachments. An open issue
  whose only cards are Duplicate counts as having no card, so it gets a new one.
  So a lost or half-made op is redone, and any doubling it causes is dropped on
  the next run.
- **The GitHub token.** `~/.config/backlog-agent/gh-token` is a fine-grained
  token of the owner's with only what the agent and its children use: access to
  all of the owner's repositories (new repos are found by `collect`), Metadata
  read, Actions read (`gh run`), Checks read, Commit statuses read, Dependabot
  alerts read, Contents read and write (clone, push, merge, the backup push),
  Issues read and write (comments, labels), Pull requests read and write (open,
  label, review, auto-merge) and Workflows read and write (`add-ci` and action
  pin bumps). No account permissions and no Administration. `collect` reads its
  expiry from GitHub's `github-authentication-token-expiration` header, and
  `plan` sets `rotate_token` once a day in the last 7 days, so the run asks the
  owner to rotate it. Git in the main checkout uses the same token, so T3's
  fetch before each run doesn't depend on the owner's keyring. Review runs and
  the land gate need no new permission: the gate's merge uses Contents write,
  and replying to a review thread and resolving one use Pull requests write with
  Contents write. Resolving a thread with this token is still unverified.
- **Top-up order is only as good as the labels.** Many issues have no labels,
  such as homelab's unauthenticated Traefik API. The owner labelling a few
  `security` or `Bug` is the cheapest way to steer the top-up.
- Triagebot's protected-paths tripwire is not copied into triage or build runs.
  The land gate checks protected paths instead, so a pull request that touches
  one waits for the owner's review. Dependency work's lane rule, which sends
  workflow and test-config edits to review, still applies.
- Every child already holds the owner's GitHub token. The trust rule limits
  whose text reaches a child. It does not limit what a steered child could do on
  GitHub. A review child reads feedback and holds that token, which is why it
  never merges and why code checks the land gate's rules.
- **Review runs and the land gate (2026-11-27).** The feature has not run yet:
  no review run has started and nothing has landed. Still unverified: whether
  the token can resolve a review thread, how the auto mode classifier treats the
  `land` command (it has refused a child's immediate merge), and whether
  `mergeStateStatus` reads `CLEAN` on the owner's rulesets. Where it does not,
  the gate holds every pull request as `merge state <state>`.
