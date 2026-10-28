# Hourly GitHub backlog dispatcher

`W=/home/jsg/Documents/jsg/random_backlog-agent` and `B="python3 $W/backlog-agent/backlog.py"`.

`backlog.py` holds every rule: ranking, holds, the 3-running and 100-a-day caps, retries, and
the ledger. Your job is to run it, start the children it names one at a time, hand each one's lessons
to the next, end with the steward, and report. Read the state only
from its output (`running`, `plan`), and change the ledger only through its `start` and
`finish` commands.

## 0. Guard

Check that `git -C $W branch --show-current` prints `feat/backlog-agent` and that
`$W/backlog-agent/backlog.py` exists, and that `pwd` prints `$W`. If any check fails, reply
with that one line and stop. The runtime lives on this branch, never on `main`, and every run
works in `$W` itself, never in a T3 worktree.

Call `t3_thread_configuration` and keep its `threadId` as `<thread>`.

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
  the status is `failed`.
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

Keep the status it prints, because `failed` can come back as `blocked`. If the note names a
pull request URL, call `link_pull_request` with it, so the PR shows on this run's thread and not
only on the child's.

Done when no ledger entry is `running` with a terminal task behind it.

## 2. Collect and plan

```
export GH_TOKEN=$(cat ~/.config/backlog-agent/gh-token); $B collect > $TMPDIR/candidates.json && $B plan $TMPDIR/candidates.json '<thread>'
```

Run it with `allowed_domains: ["api.github.com"]`. If it fails, reply with the error's last
line and stop.

## 3. Dispatch, one child at a time

`dispatch` is this run's queue. `plan` has already claimed every task in it, so start them in
order, one at a time: start the first now, and each next one when the previous child finishes
(see the wake-ups below). Never run two of this run's children at once.

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
failed start, a non-empty `dispatch`, `resolved`, `newly_blocked` or `stale_locks`, `announce_cap` is
true, or a self-change commit (step 6). In that case, after the steward has finished,
reply with at most 10 lines, each linking to its PR or repo:

- **Started**: the keys from `dispatch`, each with the status its child finished with.
- **Merged/closed**: the keys from `resolved`.
- **Needs you**: every `blocked` finish and every `newly_blocked` entry, with the owner action
  each one names. This includes admin steps from `add-ci` results, and the steward's `OWNER:` line.
- **Failed to start**: the keys whose `delegate_task` failed, with the error.
- **Cleaned up**: the keys in `stale_locks`.
- **Cap**: when `announce_cap` is true, say the daily cap of 100 is reached and how many tasks
  are `queued`.
- **Self-changes**: each commit from step 6 and the steward's `STEWARD:` line, with its subject.

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
