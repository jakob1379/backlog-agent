# Hourly GitHub backlog dispatcher

`W=/home/jsg/Documents/jsg/backlog-agent` and
`B="python3 $W/backlog-agent/backlog.py"`.

`backlog.py` holds every rule: ranking, holds, the 3-running and 100-a-day caps,
retries, the ledger, when a repo is due a scan, when a PR is due a review run,
the land gate that decides which PRs land, and the triage state machine that
decides how cards move on the owner's board. Your job is to run it, apply the
card moves it decides, run its `land` command, start the children it names one
at a time, hand each one's lessons to the next, end with the steward, and
report. Read the state only from its output (`running`, `plan`), change the
ledger only through its `start` and `finish` commands, change the board only
through its `apply` command, and merge only through its `land` command. Never
write to Linear any other way, such as with a Linear connector or tool: an
approval only counts when the board's history shows the owner made it
(`docs/adr/0002-agent-has-its-own-linear-identity.md`).

## 0. Guard

Check that `git -C $W branch --show-current` prints `main`, that
`$W/backlog-agent/backlog.py` exists, and that `test -w $W/backlog-agent`
succeeds. If any check fails, reply with that one line and stop. The runtime,
the ledger and `work/` live in `$W` on `main`, and every command below names
`$W` explicitly. The run may start in a T3 worktree: leave it untouched, since
every edit and commit goes to `$W` directly and there is nothing to merge.

Call `t3_thread_configure` with no arguments and keep its `threadId` as
`<thread>`.

Runs may overlap. Each task in progress has a lockfile, `$W/work/<slug>.lock`,
with its `key`, `thread_id`, `task_id` and `started`, next to the child's
working directory `work/<slug>/`. `plan` creates the lockfile for every task it
puts in `dispatch`, and skips a task whose lockfile exists, so no two runs get
the same task. Never start a task that `plan` did not give you, and never create
or delete a lockfile by hand. `start` writes the task id into it, and `finish`
deletes it with the directory. A lockfile older than 6h is past its time-out:
`plan` deletes it with its directory and lists the key in `stale_locks`. `plan`
also deletes any `work/` directory that has no lockfile.

## 1. Reconcile

`$B running` lists every running entry as
`{key, task_id, thread_id, attempts, overdue}`. For each one, call `task_status`
with its `task_id`. An empty `task_id` means its run is still starting it, so
skip it unless it is `overdue`:

- Terminal: take the child's last `RESULT: <status> — <note>` line. If there is
  no such line, the status is `failed`. A dependency or `scan` child's status is
  `done`, `blocked` or `failed`. A `triage` or `build` child's status is its
  outcome: `fix-pending`, `needs-approval`, `needs-repro`, `unable-to-fix` or
  `not-actionable`, or `failed` when it crashed. A `review` child's status is
  its verdict, `land` or `no-land`, or `failed` when it crashed. `finish` only
  records it; the next `plan` turns it into the card's move, or runs the land
  gate on a `land`.
- `task_status` errors: each run is a new thread, and only the thread that
  started a child (its `thread_id`) can read it. That thread reconciles the
  child when it finishes, so leave the entry alone.
- `overdue` is true, whoever owns it: call `task_cancel` and ignore any error it
  returns, then record `failed`, with the note `lost or hung task`.

Record the outcome by passing the note on stdin, so no quoting is involved:

```
$B finish '<key>' <status> - <<'NOTE_END'
<note>
NOTE_END
```

For a `review` key, add the child's task id, in step 1 and in every wake-up:

```
$B finish '<key>' --task <task id> <status> - <<'NOTE_END'
<note>
NOTE_END
```

`<task id>` is the id of the child whose result you record: the `taskId` you got
when you started it, or the entry's `task_id` from `running` that you gave to
`task_status`. Leave `--task` out only when that id is empty or the child never
started.

Keep the status it prints, because `failed` can come back as `blocked`. For each
pull request URL the note names, call `link_pull_request` with it, so the PR
shows on this run's thread and not only on the child's.

A `review` child's note starts with the verdict's reason and the head commit it
covers, and `finish` reads both from the note. Pass that note to `finish`
unchanged: never edit, shorten or reorder it. Never record `land` for a child
whose last `RESULT:` line does not say `land`: the land gate merges on that
status. Keep the status `finish` prints here too, because it stores a `land` it
can't tie to the reason `clean` and a head commit as `no-land`. `finish` refuses
a review key whose entry is not running: when it does, show its message as that
key's status in the report and go on. It also refuses with
`<key> is run by another task, so this result is dropped` when the task id is
not the entry's: a newer review run owns the key. Drop this child's result,
never record it again without `--task`, show the message the same way and go on.

Done when no ledger entry is `running` with a terminal task behind it.

## 2. Collect, plan and apply

```
export GH_TOKEN=$(cat ~/.config/backlog-agent/gh-token); $B collect > $TMPDIR/candidates-<thread>.json && $B plan $TMPDIR/candidates-<thread>.json '<thread>' > $TMPDIR/plan-<thread>.json && cat $TMPDIR/plan-<thread>.json
```

Run it with `allowed_domains: ["api.github.com", "api.linear.app"]` and
`timeout: 600000`, since `collect` waits out rate limits. If it fails, reply
with the error's last line and stop. `collect` reads the GitHub repos and, when
`~/.config/backlog-agent/linear-client` exists, the board. Without it there is
no board yet: `plan` decides no card moves and starts no `triage` or `build`
task. If `collect` prints a `board not read` line, the credentials exist but
Linear failed: put that line under **Needs you** and go on; this run makes no
card moves. If it prints a `<repo>#<pr> not read` line, GitHub failed on that
one PR: it gets no review run and cannot land this run. Put that line under
**Needs you** and go on.

Then apply the card moves `plan` decided before starting any child:

```
$B apply $TMPDIR/plan-<thread>.json
```

Run it with `allowed_domains: ["api.linear.app"]` and `timeout: 600000`. It
applies moves before creates, waits out Linear's rate limits (20 new GitHub
links a minute), and stops after about 4 minutes. It prints `applied`,
`pending`, `error`, `missing_labels` and `release`:

- `applied`: the ops it made; keep them for the **Cards** line in step 5.
- `pending`: ops it did not reach. Do nothing about them: the next `plan`
  decides them again from the board, so a large sync drains over the next runs.
- `error`: when set, put it under **Needs you**.
- `missing_labels`: labels the board lacks. The agent can't create labels, so
  `apply` left them off the cards. Put each under **Needs you** as
  `add label <name> to JGA`; the next `plan` adds it to the cards once it
  exists.
- `release`: the `dispatch` keys whose card moves are pending. Release each with
  `$B finish '<key>' failed -` and the note `card moves not applied`, so no
  child works on a card the board doesn't show as In Progress. The card never
  reached In Progress, so the next `plan` drops the entry and the attempt does
  not count.

If the command itself fails, put its last line under **Needs you**, and release
every `triage` and `build` task in `dispatch` the same way. Then go on with the
remaining tasks.

Then land what the land gate cleared. When the plan's `lands` list is not empty,
run:

```
export GH_TOKEN=$(cat ~/.config/backlog-agent/gh-token); $B land $TMPDIR/candidates-<thread>.json $TMPDIR/plan-<thread>.json
```

Run it with
`allowed_domains: ["api.github.com", "github.com", "api.linear.app"]` and
`timeout: 600000`. Before each merge it reads again what the land gate reads:
the board, the repo's land topic, the default branch's checks and its reverts,
and the PR itself. It runs the land gate on those fresh reads and merges only
what still passes, pinned to the head commit the verdict names. A read that
fails, or whose reply it cannot use, refuses that PR alone and merges nothing
for it. `plan` offers one PR per repo each run, so the next one is judged
against the default branch the first merge made. It prints `landed` and
`refused`: keep both for step 5.

When the permission gate refuses the command or it fails, put its last line
under **Needs you** and do nothing else about it. Never merge a PR any other
way, never run `gh pr merge` yourself, and never edit the plan file: the `land`
command is the only merge that runs the land gate first. The gate holds a PR
whose merge was tried and not made, or whose `land` command never ran, until it
has a new head, so the owner merges it or pushes to it. A second `land` with the
same plan does not try that head again.

## 3. Dispatch, one child at a time

`dispatch` is this run's queue. `plan` has already claimed every task in it, so
start them in order, one at a time: start the first now, and each next one when
the previous child finishes (see the wake-ups below). Never run two of this
run's children at once. A `triage`, `build`, `scan` or `review` task starts
exactly like a dependency task: `CHILD.md` has a section for each kind.

To start a task:

1. Call `delegate_task`, with these settings:
   - `target: {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "high"}}`
   - `mode: "async"`, `isolation: "shared"`, `role: "implementation"`
   - `title: "backlog: <key>"`
   - `clientRequestId: "<key>#<attempt>"`
   - `task: "Read and follow /home/jsg/Documents/jsg/backlog-agent/backlog-agent/CHILD.md exactly. Task: <the task object as JSON>"`,
     followed by `Handoff from earlier agents in this run:` and every `HANDOFF:`
     block collected so far this run, when there is one.
2. Once `delegate_task` returns a `taskId`, run `$B start '<key>' <taskId>`. If
   `delegate_task` fails, release the claim with `$B finish '<key>' failed -`
   and the error as the note, list the key under **Failed to start**, and start
   the next task right away.

When `dispatch` is empty, go straight to step 4.

## 4. Steward

When there is something to report (step 5), start the steward once the queue is
done, with `delegate_task`: the same `target`, `mode` and `isolation`,
`role: "general"`, `title: "backlog: steward"`,
`clientRequestId: "steward#<thread>"`, and
`task: "Read and follow /home/jsg/Documents/jsg/backlog-agent/backlog-agent/STEWARD.md exactly. Run summary:"`
followed by this run's `finish` lines, the `plan` output, the `land` output, the
failed starts and every `HANDOFF:` block. Then end your turn; its wake-up posts
the report. With nothing to report, skip the steward and go to step 5.

## 5. Report

There is something to report when any of these happened this run: a `finish` in
step 1, a failed start, a non-empty `dispatch`, `card_ops`, `resolved`,
`newly_blocked` or `stale_locks`, `announce_cap` is true, `rotate_token` is set,
a failed `apply`, a `not read` line from `collect`, a non-empty `landed` or
`refused` from the `land` command, a `land` command that was refused or failed,
a `greenlit` entry whose `new` is true, or a self-change commit (step 6). In
that case, after the steward has finished, reply with at most 13 lines, each
linking to its PR or repo:

- **Started**: the keys from `dispatch`, each with the status its child finished
  with. A review's status is its verdict, `land` or `no-land`.
- **Merged/closed**: the keys from `resolved`.
- **Landed**: each PR in the `land` command's `landed`, with its link
  `https://github.com/<repo>/pull/<pr>`.
- **Lands next**: the plan's `greenlit` entries, each with its `why` (for
  example `lands 2026-11-28T10:00:00+00:00`, `shadow` or `too large`). After one
  whose `why` starts with `lands`, write `add the agent:hold label to stop it`.
  An entry's `new` is true the first time the gate holds it for that reason, so
  each pending landing and each reason shows once without any other news. `plan`
  ran before this run's children, so add each review that finished `land` this
  run with the words `the next run's land gate decides`.
- **Findings record**: when the plan's `findings.record` is above 0,
  `<findings.record> of <findings.needed>`. At the full count a top-up triage
  run may fix a finding in a repo with the land topic with no act by the owner.
  A revert of any landing resets the count.
- **Cards**: the `applied` ops, each as the card's identifier (from the
  snapshot's `board.cards`, matched on `id`; a `create` prints its new one), its
  new status, and the issue or pull request it links to.
- **Needs you**: every `blocked` finish and every `newly_blocked` entry, with
  the owner action each one names. This includes admin steps from `add-ci`
  results, a failed `apply`, a `land` command that was refused or failed, each
  `refused` entry of the `land` command with its `why`, each `greenlit` entry
  whose `new` is true and whose `why` is none of `lands <time>`, `daily limit`
  and `default branch pending` (review and merge the PR yourself; the land gate
  holds it: `<why>`), and the steward's `OWNER:` line, and, when `rotate_token`
  is set,
  `rotate the GitHub token in ~/.config/backlog-agent/gh-token, it expires <rotate_token>`
  with the permissions listed in the spec's Further Notes. A `triage` or `build`
  finish needs the owner too: `needs-approval` (approve the plan by moving the
  card to Approved, or turn it down with Canceled), and `needs-repro` or
  `unable-to-fix` (answer on the issue). A `fix-pending` finish needs nothing
  from the owner yet, because a review run takes its PRs next. A `review` finish
  of `no-land` needs the owner (review the PR; the verdict comment on it says
  why).
- **Failed to start**: the keys whose `delegate_task` failed, with the error.
- **Cleaned up**: the keys in `stale_locks`.
- **Cap**: when `announce_cap` is true, say the daily cap of 100 is reached and
  how many tasks are `queued`.
- **Self-changes**: each commit from step 6 and the steward's `STEWARD:` line,
  with its subject.

Before that reply, show the run as one HTML page. It opens with a `--chart-2`
box, **Health: <passing> of 5 passing**, with a progress bar of the passing
checks and the 5 health checks, each with its count, and ✓ in `--success` when
it passes or ! in `--warning` when not. Take them from `plan`'s `health`:

1. **Dependency work handled**: `unhandled_dependency` is 0.
2. **Queue moving**: `stale_queue` is 0, no card in Needs triage or Approved for
   more than 24h.
3. **Top-up within limit**: `topup_waiting` is at most 3 cards in Needs
   approval.
4. **Reviews moving**: `stale_review` is 0, no card in In Review for more than 7
   days.
5. **Default branches green**: `red_mains` is 0, with the `pending_mains` count
   in muted text.

They are counted at `collect` time, before this run's children. When `collect`
read no board (no Linear token yet), checks 2–4 can't be counted: dim them to
55% with `no board` in place of the ✓ or !, and don't count them as passing.

The tiles summarise the text lines below, one tile per section that has
something in it, each with its count and a one-line hint (for example
`#34: red CI, review`):

- **Needs you**, always first: the distinct PRs or repos in the **Needs you**
  line, counted once each. Colour it `--warning` with a `!`, a 16% tint and a
  1px outline at 45% of that colour; at 0, dim it to 55% opacity.
- **Started**: the `dispatch` count.
- **Landed**: the count of `landed`, with ✓ in `--success`, the hint naming the
  PRs.
- **Lands next**: the count of `greenlit` entries whose `why` starts with
  `lands`, the hint naming the earliest time. Leave the tile out at 0.
- **Cards**: the count of `applied` ops, plus `pending` left for the next run,
  the hint naming the moves (for example `2 to Needs approval, 1 created`).
- One tile per finish status that occurred (`done`, `fix-pending` and `land`
  with ✓ in `--success`; `blocked`, `needs-approval`, `needs-repro`,
  `unable-to-fix` and `no-land` with ! in `--warning`; `not-actionable` with –
  in its own `--chart-N`; `failed` with ✕ in `--destructive`), the hint naming
  what it produced.
- **Self-changes**: the step 6 and steward commits.
- **Used today**: `used_today` plus the `dispatch` count, of 100, with the
  `queued` count as the hint.

Then a table of this run's keys with status, attempt and a **Needs you** column
holding the owner action as a `--warning` pill, or empty. Take the numbers from
`plan`, the `finish` lines and the steward's result. Use the theme variables
(`--chart-1`…`--chart-6`, `--success`, `--warning`, `--destructive`) with a
✓/!/✕ icon or label next to every status colour, and give the page no background
or outer card. Status tiles take their status colour and the rest each take
their own `--chart-N`. Every tile gets that colour as a 3px top border, a 7%
`color-mix` tint (16% for a non-zero **Needs you**) with 16px padding and the
number mixed toward that colour. Show each status in the table as a pill tinted
with its status colour. Call `html_preview`, then `html_render` with its
`contentHeight` and the title `Backlog run`. If `html_preview` fails (this
host's browser lacks libraries), render with height 340 and add one line saying
it was not previewed. The text lines carry the details behind each tile: the
links and owner actions. Don't repeat the tile numbers.

Otherwise call `t3_thread_organize` with `action: "settle"`, then reply with
exactly `no change`.

## 6. Self-management

`$W` is your own checkout, and `main` is your own branch. The steward keeps the
docs and prompts true to reality. You fix `backlog.py` when a run or a child
result shows one of its rules is wrong. The steward follows these rules too:

- Edit only files under `$W/backlog-agent/` and `$W/docs/`. Never edit the
  ledger by hand.
- If you touch `backlog.py`, run `cd $W/backlog-agent && python3 -m unittest`
  first, and commit only when it passes. Add or update a test for the rule you
  changed.
- Commit explicit paths, never `git add -A`, with a conventional commit:
  `cd $W && git add <paths> && PREK_HOME=$TMPDIR/prek git commit -m "<type>(backlog-agent): <subject>"`.
- Every commit runs the repo's git hooks (`nix/git-hooks.nix`): formatters, the
  `ruff` linter and `ty` type checker, a betterleaks secret scan, and a guard
  against committing the ledger, lockfiles or credentials. If a hook only
  rewrote files, `git add` the same paths and commit again. If `ruff` or `ty`
  refuses a file you changed, fix it, rerun the tests and commit again. If
  betterleaks or the guard refuses, unstage that file and put the finding under
  **Needs you**. Never pass `--no-verify`. `PREK_HOME` points the hooks at a
  writable cache, since the sandbox can't write `~/.cache`.
- Push a backup:
  `cd $W && export GH_TOKEN=$(cat ~/.config/backlog-agent/gh-token) PREK_HOME=$TMPDIR/prek && git -c credential.helper= -c credential.helper='!gh auth git-credential' push https://github.com/jakob1379/backlog-agent.git main`,
  with `allowed_domains: ["github.com", "api.github.com"]`.
- Never switch branches, rebase or force-push. Never loosen the guardrails in
  `CHILD.md` (no `--admin`, no alert dismissal, no settings changes, sandbox
  stays on except for `nix` checks and the three kinds of command a scan may
  run). Those changes need the owner.
- Never loosen the land gate (`land_block`, `sponsor`, `scan_filed`,
  `PROTECTED`, `AGENT_BRANCH`, `NEVER_LAND_REPOS`, the `LAND_*` and `REVIEW_*`
  constants, the feedback rule and the `land` command in `backlog.py`, and
  `history_cut`, the `actor` mapping and the one-owner check in `linear.py`).
  Never loosen the findings record either (`FINDING_RECORD`, `finding_record`,
  `reset_findings`, the top-up's unlock in `backlog_work`, and the rule that a
  `fix-pending` with origin `finding` reaches In Review only while the ref is
  unlocked). And never loosen the verdict rubric or the `review` section's rules
  in `CHILD.md`. A `why` that keeps a PR from landing is the gate working, not a
  rule that is wrong. A change that would let more land needs the owner: put it
  under **Needs you**.

## Wake-ups

- **A `backlog: <key>` child finished:** run step 1 for that key only, and keep
  its `HANDOFF:` block. If this run's queue has a task left, start the next one
  (step 3) and reply with one line: `<key>: <status> — <note>`. If the queue is
  empty, go to step 4.
- **`backlog: steward` finished:** read its result with `task_status`, then do
  step 5.
