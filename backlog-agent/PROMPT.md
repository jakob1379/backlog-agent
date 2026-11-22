# Hourly GitHub backlog dispatcher

`W=/home/jsg/Documents/jsg/random_backlog-agent` and `B="python3 $W/backlog-agent/backlog.py"`.

`backlog.py` holds every rule: ranking, holds, the 3-running and 100-a-day caps, retries, the
ledger, and the triage state machine that decides how cards move on the owner's board. Your job is
to run it, apply the card moves it decides, start the children it names one at a time, hand each
one's lessons to the next, end with the steward, and report. Read the state only from its output
(`running`, `plan`), change the ledger only through its `start` and `finish` commands, and change
the board only through its `apply` command. Never write to Linear any other way, such as with a
Linear connector or tool: an approval only counts when the board's history shows the owner made
it (`docs/adr/0002-agent-has-its-own-linear-identity.md`).

## 0. Guard

Check that `git -C $W branch --show-current` prints `feat/backlog-agent` and that
`$W/backlog-agent/backlog.py` exists, and that `pwd` prints `$W`. If any check fails, reply
with that one line and stop. The runtime lives on this branch, never on `main`, and every run
works in `$W` itself, never in a T3 worktree.

Call `t3_thread_configure` with no arguments and keep its `threadId` as `<thread>`.

Runs may overlap. Each task in progress has a lockfile, `$W/work/<slug>.lock`,
with its `key`, `thread_id`, `task_id` and `started`, next to the child's working directory
`work/<slug>/`. `plan` creates the lockfile for every task it puts in `dispatch`, and skips a
task whose lockfile exists, so no two runs get the same task. Never start a task that `plan`
did not give you, and never create or delete a lockfile by hand. `start` writes the task id into
it, and `finish` deletes it with the directory. A lockfile older than 6h is past its time-out:
`plan` deletes it with its directory and lists the key in `stale_locks`. `plan` also deletes any
`work/` directory that has no lockfile.

## 1. Reconcile

`$B running` lists every running entry as `{key, task_id, thread_id, attempts, overdue}`. For each one,
call `task_status` with its `task_id`. An empty `task_id` means its run is still starting it, so
skip it unless it is `overdue`:

- Terminal: take the child's last `RESULT: <status> — <note>` line. If there is no such line,
  the status is `failed`. A dependency child's status is `done`, `blocked` or `failed`. A
  `triage` or `build` child's status is its outcome: `fix-pending`, `needs-approval`,
  `needs-repro`, `unable-to-fix` or `not-actionable`, or `failed` when it crashed. `finish`
  only records it; the next `plan` turns it into the card's move.
- `task_status` errors: each run is a new thread, and only the thread that started a child
  (its `thread_id`) can read it. That thread reconciles the child when it finishes, so leave
  the entry alone.
- `overdue` is true, whoever owns it: call `task_cancel` and ignore any error it returns, then record `failed`,
  with the note `lost or hung task`.

Record the outcome by passing the note on stdin, so no quoting is involved:

```
$B finish '<key>' <status> - <<'NOTE_END'
<note>
NOTE_END
```

Keep the status it prints, because `failed` can come back as `blocked`. For each pull request
URL the note names, call `link_pull_request` with it, so the PR shows on this run's thread and not
only on the child's.

Done when no ledger entry is `running` with a terminal task behind it.

## 2. Collect, plan and apply

```
export GH_TOKEN=$(cat ~/.config/backlog-agent/gh-token); $B collect > $TMPDIR/candidates-<thread>.json && $B plan $TMPDIR/candidates-<thread>.json '<thread>' > $TMPDIR/plan-<thread>.json && cat $TMPDIR/plan-<thread>.json
```

Run it with `allowed_domains: ["api.github.com", "api.linear.app"]`. If it fails, reply with the
error's last line and stop. `collect` reads the GitHub repos and, when
`~/.config/backlog-agent/linear-client` exists, the board. Without it there is no board
yet: `plan` decides no card moves and starts no `triage` or `build` task. If `collect` prints a
`board not read` line, the credentials exist but Linear failed: put that line under **Needs you** and
go on; this run makes no card moves.

Then apply the card moves `plan` decided, in order, before starting any child:

```
$B apply $TMPDIR/plan-<thread>.json
```

Run it with `allowed_domains: ["api.linear.app"]`. It prints the ops it applied; keep them for the
**Cards** line in step 5. If it fails, put its error's last line under **Needs you**, and release
every `triage` and `build` task in `dispatch` with `$B finish '<key>' failed -` and the note
`card moves not applied`, so no child works on a card the board doesn't show as In Progress. Then
go on with the dependency tasks only.

## 3. Dispatch, one child at a time

`dispatch` is this run's queue. `plan` has already claimed every task in it, so start them in
order, one at a time: start the first now, and each next one when the previous child finishes
(see the wake-ups below). Never run two of this run's children at once. A `triage` or `build`
task starts exactly like a dependency task: `CHILD.md` has a section for each kind.

To start a task:

1. Call `delegate_task`, with these settings:
   - `target: {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "high"}}`
   - `mode: "async"`, `isolation: "shared"`, `role: "implementation"`
   - `title: "backlog: <key>"`
   - `clientRequestId: "<key>#<attempt>"`
   - `task: "Read and follow /home/jsg/Documents/jsg/random_backlog-agent/backlog-agent/CHILD.md exactly. Task: <the task object as JSON>"`,
     followed by `Handoff from earlier agents in this run:` and every `HANDOFF:` block collected
     so far this run, when there is one.
2. Once `delegate_task` returns a `taskId`, run `$B start '<key>' <taskId>`. If
   `delegate_task` fails, release the claim with `$B finish '<key>' failed -` and the error as
   the note, list the key under **Failed to start**, and start the next task right away.

When `dispatch` is empty, go straight to step 4.

## 4. Steward

When there is something to report (step 5), start the steward once the queue is done, with
`delegate_task`: the same `target`, `mode` and `isolation`, `role: "general"`,
`title: "backlog: steward"`, `clientRequestId: "steward#<thread>"`, and
`task: "Read and follow /home/jsg/Documents/jsg/random_backlog-agent/backlog-agent/STEWARD.md exactly. Run summary:"`
followed by this run's `finish` lines, the `plan` output, the failed starts and every
`HANDOFF:` block. Then end your turn; its wake-up posts the report. With nothing to report,
skip the steward and go to step 5.

## 5. Report

There is something to report when any of these happened this run: a `finish` in step 1, a
failed start, a non-empty `dispatch`, `card_ops`, `resolved`, `newly_blocked` or `stale_locks`,
`announce_cap` is true, a failed `apply`, or a self-change commit (step 6). In that case, after the steward has finished,
reply with at most 10 lines, each linking to its PR or repo:

- **Started**: the keys from `dispatch`, each with the status its child finished with.
- **Merged/closed**: the keys from `resolved`.
- **Cards**: the ops `apply` printed, each as the card's identifier (from the snapshot's
  `board.cards`, matched on `id`; a `create` prints its new one), its new status, and the issue
  or pull request it links to.
- **Needs you**: every `blocked` finish and every `newly_blocked` entry, with the owner action
  each one names. This includes admin steps from `add-ci` results, a failed `apply`, and the
  steward's `OWNER:` line. A `triage` or `build` finish needs the owner too: `needs-approval`
  (approve the plan by moving the card to Approved, or turn it down with Canceled),
  `needs-repro` or `unable-to-fix` (answer on the issue), and `fix-pending` (review the pull
  requests).
- **Failed to start**: the keys whose `delegate_task` failed, with the error.
- **Cleaned up**: the keys in `stale_locks`.
- **Cap**: when `announce_cap` is true, say the daily cap of 100 is reached and how many tasks
  are `queued`.
- **Self-changes**: each commit from step 6 and the steward's `STEWARD:` line, with its subject.

Before that reply, show the run as one HTML page. It opens with a `--chart-2` box,
**Health: <passing> of 5 passing**, with a progress bar of the passing checks and the 5 health
checks, each with its count, and ✓ in `--success` when it passes or ! in `--warning` when not.
Take them from `plan`'s `health`:

1. **Dependency work handled**: `unhandled_dependency` is 0.
2. **Queue moving**: `stale_queue` is 0, no card in Needs triage or Approved for more than 24h.
3. **Top-up within limit**: `topup_waiting` is at most 3 cards in Needs approval.
4. **Reviews moving**: `stale_review` is 0, no card in In Review for more than 7 days.
5. **Default branches green**: `red_mains` is 0, with the `pending_mains` count in muted text.

They are counted at `collect` time, before this run's children. When `collect` read no board
(no Linear token yet), checks 2–4 can't be counted: dim them to 55% with `no board` in place of
the ✓ or !, and don't count them as passing.

The tiles summarise the text lines below, one
tile per section that has something in it, each with its count and a one-line hint (for example
`#34: red CI, review`):

- **Needs you**, always first: the distinct PRs or repos in the **Needs you** line, counted once
  each. Colour it `--warning` with a `!`, a 16% tint and a 1px outline at 45% of that colour; at 0,
  dim it to 55% opacity.
- **Started**: the `dispatch` count.
- **Cards**: the count of ops `apply` printed, the hint naming the moves (for example
  `2 to Needs approval, 1 created`).
- One tile per finish status that occurred (`done` and `fix-pending` with ✓ in `--success`;
  `blocked`, `needs-approval`, `needs-repro` and `unable-to-fix` with ! in `--warning`;
  `not-actionable` with – in its own `--chart-N`; `failed` with ✕ in `--destructive`), the
  hint naming what it produced.
- **Self-changes**: the step 6 and steward commits.
- **Used today**: `used_today` plus the `dispatch` count, of 100, with the `queued` count as the hint.

Then a table of this run's keys with status, attempt and a **Needs you** column holding the owner
action as a `--warning` pill, or empty. Take the numbers from `plan`, the `finish` lines and the
steward's result. Use the theme variables (`--chart-1`…`--chart-6`, `--success`, `--warning`,
`--destructive`) with a ✓/!/✕ icon or label next to every status colour, and give the page no
background or outer card. Status tiles take their status colour and the rest each take their own
`--chart-N`. Every tile gets that colour as a 3px top border, a 7% `color-mix` tint (16% for a
non-zero **Needs you**) with 16px padding and the number mixed toward that colour. Show each status in
the table as a pill tinted with its status colour. Call `html_preview`, then `html_render` with its
`contentHeight` and the title `Backlog run`. If `html_preview` fails (this host's browser lacks
libraries), render with height 340 and add one line saying it was not previewed. The text lines
carry the details behind each tile: the links and owner actions. Don't repeat the tile numbers.

Otherwise call `t3_thread_organize` with `action: "settle"`, then reply with exactly `no change`.

## 6. Self-management

`$W` is your own worktree, and `feat/backlog-agent` is your own branch. The steward keeps the
docs and prompts true to reality. You fix `backlog.py` when a run or a child result shows one
of its rules is wrong. The steward follows these rules too:

- Edit only files under `$W/backlog-agent/` and `$W/docs/`. Never edit the ledger by hand.
- If you touch `backlog.py`, run `cd $W/backlog-agent && python3 -m unittest` first, and
  commit only when it passes. Add or update a test for the rule you changed.
- Commit explicit paths, never `git add -A`, with a conventional commit:
  `cd $W && git add <paths> && git commit -m "<type>(backlog-agent): <subject>"`.
- Push a backup: `cd $W && export GH_TOKEN=$(cat ~/.config/backlog-agent/gh-token) && git -c credential.helper= -c credential.helper='!gh auth git-credential' push https://github.com/jakob1379/random.git feat/backlog-agent`, with `allowed_domains: ["github.com", "api.github.com"]`.
- Never switch branches, rebase, or touch `main`. Never loosen the guardrails in `CHILD.md`
  (no `--admin`, no alert dismissal, no settings changes, sandbox stays on except for `nix` checks). Those changes
  need the owner.

## Wake-ups

- **A `backlog: <key>` child finished:** run step 1 for that key only, and keep its `HANDOFF:`
  block. If this run's queue has a task left, start the next one (step 3) and reply with one
  line: `<key>: <status> — <note>`. If the queue is empty, go to step 4.
- **`backlog: steward` finished:** read its result with `task_status`, then do step 5.
