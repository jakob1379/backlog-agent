# Backlog triage: tickets

Spec: `docs/superpowers/specs/2026-11-21-backlog-agent-design.md`. Vocabulary:
`backlog-agent/GLOSSARY.md`. Decisions: `docs/adr/`. Integration branch: `feat/backlog-triage`
(from `feat/backlog-agent`, which stays the live runtime until the owner merges).

Status: all six tickets shipped (merges `68b2759` T1, `40cb5b1` T2, `892184e` T3, `4226bb6` T4,
`0575c62` T5, `fa7eeff` T6). The runtime now lives on `main` of `jakob1379/backlog-agent`, and
T5's "no live token" no longer holds: `apply` writes to the board every run.

## Task graph

```
T1 collect-github ─┐
T2 plan-triage ────┼─> T3 plan-cards ─┐
T4 health ─────────┤                  ├─> T6 prompts-and-board
T5 linear-module ──┴──────────────────┘
```

Frontier at start: T1, T2, T4, T5.

## Shared contracts

Every ticket codes against these shapes, so the parallel tickets agree without talking.

**Snapshot additions** (what `collect` prints; `plan` reads):

- `repos[]` gains `fork` (bool), and only includes in-scope repos: non-archived repos owned by
  the viewer that are not forks, plus forks with the `backlog-agent` topic.
- `repos[].issues[]`: `{number, title, author, created_at, labels: [str], comments:
  [{author, created_at, by_agent: bool}]}` for open issues. `by_agent` is true when the
  comment body starts with `<!-- backlog-agent -->`, because the agent comments with the
  owner's token and so has the owner's login.
- `repos[].other_prs[]`: `{number, title, author, draft, created_at, linked_issues: [int],
  linked_refs: ["owner/repo#n"], labels: [str]}` for open PRs not authored by Dependabot.
  `linked_refs` holds issues in the owner's other repos that the PR closes or mentions, so a
  multi-repo build keeps its card In Review until every PR is gone.
- `board`: `{statuses: [str], cards: [card]}` where `card` is `{id, identifier, title, status,
  labels: [str], link: {repo, number, type: "issue"|"pr"} | null, created_at, history:
  [{from, to, actor: "owner"|"agent"|"other", at}]}`. `history` is oldest first.
- `owner`: the owner's GitHub login.

**Plan output additions:**

- `dispatch[]` items for backlog work: `{key, kind: "triage"|"build", repo, issue, card,
  origin: "owner"|"topup", tier, attempt}`. Keys: `owner/repo#<n>:triage`,
  `owner/repo#<n>:build`. Tier 8 for build, 9 for triage (after add-CI's 7).
- `card_ops[]`, applied in order by the Linear module:
  - `{op: "move", card, to, add_labels: [], remove_labels: []}`
  - `{op: "create", link: {repo, number, type}, title, status, labels: []}`
  - `{op: "close", card, to: "Done"|"Canceled"}`
- `health` replaces `phase1`: `{unhandled_dependency, stale_queue, topup_waiting,
  stale_review, red_mains, pending_mains}`.

**Child result:** a triage or build child ends with `RESULT: <outcome> — <note>`, outcome one
of `fix-pending`, `needs-approval`, `needs-repro`, `unable-to-fix`, `not-actionable`.
`finish` accepts these outcomes as well as `done|blocked|failed`, records the outcome in the
ledger entry, and the next `plan` turns it into the card move. `finish` never talks to Linear.

## Tickets

### T1 collect-github: in-scope repos, issues and other PRs

Blocked by: none.

Extend the GitHub side of `collect` to the snapshot additions above: the fork/topic scope rule,
`issues` with comment authors and `by_agent`, `other_prs` with linked issues (from
`closingIssuesReferences`), and `owner`. Keep the existing Dependabot fields unchanged.
`collect` has no unit tests today (I/O); keep it that way, but run `collect` once against the
real API with the PAT and check the output's shape.

### T2 plan-triage: the triage state machine in `plan`

Blocked by: none.

Implement in `plan` the state-machine table, triggers, trust rule, origin, top-up (stop at 3
in Needs approval; order `security` label, then `Bug`, then the rest, oldest first), the
approval-only-by-owner rule (an Approved card whose last move's actor isn't `owner` goes back
to Needs approval), the build trigger, one retry then Needs info + `failed`, and turning
`finish` outcomes into moves. Triage and build share `MAX_RUNNING` and `DAILY_CAP` and rank
after all dependency work. Only cards whose `link` is an issue in an in-scope repo are
touched. Extend `finish` to accept the outcomes. Tests through `plan` with fixtures and
through the CLI round trip, per the spec's Testing Decisions.

### T3 plan-cards: cards for PRs and dependency owner actions

Blocked by: T2.

In `plan`, emit `create` for every `other_prs` entry with no `linked_issues` and no card
(status In Review, labels `kind: pr`, `repo`, plus `draft` when draft), and `close` (Done)
when its PR is gone from the snapshot. Do the same for ledger entries that ended blocked
(Needs info) or in the review lane (In Review) with `kind: dependency`, closed when the key
is resolved. Tests through `plan`.

### T4 health: health checks replace phase counts

Blocked by: none.

Replace `phase1` in `plan`'s output with `health` as defined above, counted from the snapshot
and ledger, with the thresholds in the spec (24h, 3, 7 days). Update the existing
`test_phase1_counts_what_is_left` into health-check tests.

### T5 linear-module: one module for all Linear I/O

Blocked by: none.

A new module that is the only code that knows about Linear (ADR 0001, ADR 0002):

- Reads the board into the `board` snapshot shape for the JGA team, using the GraphQL API and
  an OAuth app token stored next to the GitHub token. A card's `link` comes from its GitHub
  attachment, else from an "Imported from <url>" footer in its description. `actor` is
  `agent` when the app made the move, `owner` when the owner's user did, else `other`.
- Fails loudly if a status the state machine needs is missing.
- `apply`: executes `card_ops` in order.
- `setup`: one-off. Creates the four statuses and the label groups where the API allows,
  otherwise prints them as owner actions. Marks August's imported cards Duplicate when another
  card links the same GitHub URL.
- CLI: `backlog.py board`, `backlog.py apply <plan.json>`, `backlog.py setup`.

No live token exists yet, so no live run is possible. Write it against Linear's documented
GraphQL schema, keep it thin, and say in the report which calls are unverified.

### T6 prompts-and-board: prompts, run board and wiring

Blocked by: T2, T3, T4, T5.

Update `PROMPT.md` (collect with the board, `apply` after `plan`, triage and build dispatch
like other tasks, the run board's health checks instead of the phase strip, report lines for
cards), `CHILD.md` (a triage section and a build section following the spec: the trust rule,
the `<!-- backlog-agent -->` marker on every comment, plan format, one PR per repo, review
lane only, the RESULT outcomes), and `STEWARD.md` where it mentions phases. Never loosen
CHILD.md's guardrails.
