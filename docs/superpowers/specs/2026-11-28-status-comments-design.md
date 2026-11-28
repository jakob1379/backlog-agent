# Status comments

## Intent

Each issue or pull request a child works on gets one comment that the agent
edits in place, like CodeRabbit and cubic do. It shows which scrubber is on the
thread, its state as the animated README robot, what the owner must do next, how
far the card has come, and the latest report. One comment that changes instead
of many new ones keeps threads quiet and makes "what is going on here?"
answerable at a glance.

## Goals

- One status comment per thread, created on the first run, edited after.
- The robot and state come from `docs/readme/generate.py`, so the README art and
  the comments stay one set.
- The review verdict moves into the status comment, which removes up to three
  comments per pull request.

## Non-goals

- Status for tasks without a thread (`alert`, `fix-main`, `add-ci`, `scan`).
  Their pull requests get one once a review run or a later task touches them.
- Progress inside a run. The comment changes when a run starts and ends.
- Linear. The card already shows its lane.

## Anti-goals

- No rule may read the status comment as input, apart from the review run
  reading its own `**Feedback read to:**` line. Its text is the agent's, posted
  with the owner's token, so every check that treats an owner comment as the
  owner's word must skip it.
- The plan stays its own comment that is never edited. The owner approves that
  exact text and `build` builds it.
- A failed GitHub call never fails `start`, `finish` or `land`. The ledger is
  the record; the comment is a view of it.

## Approaches

1. **`backlog.py` writes the comment, children hand it a report file.** Picked.
   The state, progress line, "Your move" and history are decided by code that
   has tests, and the model only writes prose it already writes.
2. Children post and edit the comment themselves. Finer progress, but every
   child spends turns on it and the format drifts.
3. `backlog.py` alone, from the `RESULT` line. Too little to fill the details.

## Design

**Marker.** The comment starts with `<!-- backlog-agent:status -->`
(`STATUS_MARKER`). It does not start with `AGENT_MARKER`, and both `backlog.py`
and the child's `jq` filters drop it wherever they look for the owner's comments
or the agent's own.

**Thread for a key.**

| Key                   | Thread    | Scrubber | Progress line                             |
| --------------------- | --------- | -------- | ----------------------------------------- |
| `repo#N:triage`       | issue `N` | Probe    | Triage → Approval → Build → Review → Land |
| `repo#N:build`        | issue `N` | Rivet    | same                                      |
| `repo#N:review`       | PR `N`    | Rivet    | Build → Review → Land                     |
| `repo#N` (dependency) | PR `N`    | Patch    | Update → Review → Land                    |
| anything else         | none      |          |                                           |

**State.** `running` → working. `needs-approval`, `needs-repro`,
`unable-to-fix`, `fix-pending`, `no-land`, `land` → waiting on the owner (a
`land` verdict reads "lands no sooner than <finished + LAND_DELAY> if the land
gate still passes", with how to stop it). `not-actionable`, `done` and a landed
PR → clean. `blocked` → blocked. `failed` that will retry → idle, "retrying next
hour".

**Badges.** `generate.py` also writes
`docs/readme/badges/<scrubber>-<state>.svg` for every scrubber and `idle`,
`work`, `wait`, `happy`, `alarm`. Comments embed them from
`https://raw.githubusercontent.com/jakob1379/backlog-agent/main/docs/readme/badges/`
(the repo is public and raw serves `image/svg+xml`).

**Report file.** Before its `RESULT` line a child writes markdown to
`work/<slug>.report.md` (`slug` as for the lockfile). `finish` reads it, keeps
it as the thread's latest report and deletes the file. No file keeps the
previous report. A review run's report is the verdict comment it used to post,
`**Feedback read to:**` included.

**Ledger.** `ledger["threads"]["<repo>#<n>"]` holds `comment` (its id), `runs`
(key, kind, started, finished, status, note) and `report`.

**Writes.** `start` and `finish` update the thread and upsert the comment: PATCH
by id, POST when there is no id or the PATCH 404s. `land` updates a landed PR to
clean. A failure prints one line to stderr and moves on.

**Layout.** Badge, a one-line headline, a "Your move" quote when waiting, the
progress line in a code span, the latest report in an open `<details>`, history
in a closed `<details>` with one line per run, and an "Updated" footer. GitHub
strips colour, so state is carried by the badge and the words.

## Left to the builder

Exact wording of headlines, the `failed` idle text, and the history line format.
