# Hourly GitHub backlog dispatcher

`W=/home/jsg/.t3/worktrees/random/feat-backlog-agent` and `B="python3 $W/backlog-agent/backlog.py"`.

`backlog.py` holds every rule: ranking, holds, the 3-running and 24-a-day caps, retries, and
the ledger. Your job is to run it, start the children it names, and report. Read the state only
from its output (`running`, `plan`), and change the ledger only through its `start` and
`finish` commands.

## 0. Guard

Check that `git -C $W branch --show-current` prints `feat/backlog-agent` and that
`$W/backlog-agent/backlog.py` exists. If either check fails, reply with that one line and stop.
The runtime lives on this branch, never on `main`.

## 1. Reconcile

`$B running` lists every running entry as `{key, task_id, attempts, overdue}`. For each one,
call `task_status` with its `task_id`:

- Terminal: take the child's last `RESULT: <status> — <note>` line. If there is no such line,
  the status is `failed`.
- `task_status` errors: each run is a new thread, and only the thread that started a child can
  read it. That thread reconciles the child when it finishes, so leave the entry alone.
- `overdue` is true: call `task_cancel` and ignore any error it returns, then record `failed`,
  with the note `lost or hung task`.

Record the outcome by passing the note on stdin, so no quoting is involved:

```
$B finish '<key>' <status> - <<'NOTE_END'
<note>
NOTE_END
```

Keep the status it prints, because `failed` can come back as `blocked`.

Done when no ledger entry is `running` with a terminal task behind it.

## 2. Collect and plan

```
export GH_TOKEN=$(cat ~/.config/backlog-agent/gh-token); $B collect > $TMPDIR/candidates.json && $B plan $TMPDIR/candidates.json
```

Run it with `allowed_domains: ["api.github.com"]`. If it fails, reply with the error's last
line and stop.

## 3. Dispatch

For each task in `dispatch`, in order:

1. Call `delegate_task`, with these settings:
   - `target: {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "high"}}`
   - `mode: "async"`, `isolation: "shared"`, `role: "implementation"`
   - `title: "backlog: <key>"`
   - `clientRequestId: "<key>#<attempt>"`
   - `task: "Read and follow /home/jsg/.t3/worktrees/random/feat-backlog-agent/backlog-agent/CHILD.md exactly. Task: <the task object as JSON>"`
2. Once `delegate_task` returns a `taskId`, run `$B start '<key>' <taskId>`. If it fails,
   skip the `start`, and list the key under **Failed to start** in the report.

## 4. Report

There is something to report when any of these happened this run: a `finish` in step 1, a
failed start, a non-empty `dispatch`, `resolved` or `newly_blocked`, or `announce_cap` is
true. In that case
reply with at most 10 lines, each linking to its PR or repo:

- **Started**: the keys from `dispatch`.
- **Merged/closed**: the keys from `resolved`.
- **Needs you**: every `blocked` finish and every `newly_blocked` entry, with the owner action
  each one names. This includes admin steps from `add-ci` results.
- **Failed to start**: the keys whose `delegate_task` failed, with the error.
- **Cap**: when `announce_cap` is true, say the daily cap of 24 is reached and how many tasks
  are `queued`.

Otherwise reply with exactly `no change`.

## Child-completion wake-ups

When this thread wakes because a `backlog: <key>` task finished, run step 1 for that key only.
Then reply with one line: `<key>: <status> — <note>`.
