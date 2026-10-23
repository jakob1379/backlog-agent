# GitHub Backlog Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish Phase 0 (auto-merge workflow fix in 6 repos, kick old PRs), then ship an hourly T3 dispatcher that sends one Opus child agent per red/stuck Dependabot PR, uncovered alert, red main or missing CI.

**Architecture:** One stdlib Python script, `backlog-agent/backlog.py`, does everything that has to be deterministic: collecting candidates from GitHub, ranking them, enforcing the caps, and keeping the ledger. Two prose files drive the LLM side. `PROMPT.md` is the hourly dispatcher: it calls the script, starts children with `delegate_task`, and reports. `CHILD.md` holds the rules every child agent follows. The model never edits the ledger by hand.

**Tech Stack:** Python 3.14 stdlib only (`json`, `subprocess`, `zoneinfo`, `unittest`), `gh` 2.102 (GraphQL + REST), T3 `delegate_task` / `task_status` / `schedule_task`, GitHub Actions + `dependabot/fetch-metadata`.

**Spec:** `docs/superpowers/specs/2026-10-20-github-backlog-agent-design.md`

## Global Constraints

- **Runtime branch and worktree.** The runtime lives on branch `feat/backlog-agent` in worktree `/home/jsg/.t3/worktrees/random/feat-backlog-agent/`, never on `main`. The schedule is bound to this thread, which is attached to that worktree. `feat/backlog-agent` is never merged into `main`, and the worktree is never removed while the schedule exists. PROMPT.md step 0 enforces this.
- Every `gh` call in the agent runs with `export GH_TOKEN=$(cat ~/.config/backlog-agent/gh-token)` and `allowed_domains` that include `api.github.com`. Never print the token.
- Phase 0 runs under the owner's own `gh` login (outside the sandbox), and each outward-facing step is shown to the owner and confirmed first.
- `MAX_RUNNING = 3`, `DAILY_CAP = 24` (every start counts, retries too), `TZ = "Europe/Copenhagen"`, `MAX_ATTEMPTS = 2`.
- `STUCK_AFTER = 2h`, `STALE_DONE_AFTER = 48h`, `ADD_CI_REPOS = {"jakob1379/pacify", "jakob1379/br18", "jakob1379/brobizz-usage"}`.
- Child agent: `target = {providerInstanceId: "claudeAgent", model: "claude-opus-5-5", options: {effort: "high"}}` (check the exact option id in `orchestrator_capabilities`), `mode: "async"`, `isolation: "shared"`, `runtimeMode` unset, `clientRequestId = "<key>#<attempt>"`.
- Required checks come only from `GET repos/{o}/{r}/rules/branches/{default}` → `type == "required_status_checks"`. Passing conclusions are `SUCCESS`, `NEUTRAL` and `SKIPPED`.
- In GraphQL, a Dependabot PR's `author.login` is `"dependabot"`, not `"dependabot[bot]"`.
- Skills (`mattpocock-skills:`):
  - `tdd` for Tasks 3–5. Seams: `start`/`finish`/`used_today`, `plan`, `required_state`, `is_major`. Vertical slices, one test, then one implementation.
  - `writing-for-agents` for PROMPT.md and CHILD.md.
  - `pr` for every PR body, including the children's.
  - `diagnosing-bugs` inside CHILD.md for red-PR and fix-main tasks.
  - `code-review` for the final branch review against `main` with the spec.
  - `retro` is user-invoked only; suggest it after the supervised run.
- Conventional commits. No code comments except `shortcut:` notes and the manifest override comment the spec requires.

## Deviations from the spec (decided while planning, written back to the spec in Task 1)

1. **Workflow condition.** Instead of "patch/minor OR group OR actions/nix", the workflow auto-merges when `updated-dependencies-json` is non-empty and contains no `version-update:semver-major`. This covers groups, Nix SHA bumps and Actions SHA bumps the same way, never lets a major through, and fails closed when the metadata can't be parsed. The group restriction `update-types: [minor, patch]` stays, so majors split into their own PRs instead of blocking a whole group.
2. **Alert task key.** The key is `owner/repo:dep:<ecosystem>:<package>`, because a task covers every alert for one package.
3. **Canvas facts changed.** `test` is now green on main. `SonarCloud Code Analysis` is also required and is red on main. `test` is red on all 13 Dependabot PRs. The fix-main task stays generic; there's no hardcoded relock.
4. **New call: needs-review cap.** While 5 or more open PRs carry `needs-review`, the dispatcher starts no task that can only end in `needs-review`: tasks in no-CI repos, and add-ci. Without this, about 141 alerts across mostly no-CI repos would bury the owner. That is the spec's own anti-goal.
5. **Tiers.**
   1. Critical/high alerts, plus Dependabot PRs that cover a critical/high alert
   2. fix-main in a repo with a critical/high alert
   3. medium/low alerts
   4. other fix-main
   5. red/stuck non-major PRs, oldest first
   6. major PRs
   7. add-ci

   Within a tier: severity, then oldest.

## Review Focus

1. **A no-CI repo must never get auto-merge enabled.** With no required checks it merges instantly. CHILD.md forbids it, and Task 5's dry run checks a no-CI alert task's brief carries `has_ci: false`.
2. **A required check that never reported on the commit counts as pending, not green.** `test_missing_required_check_is_pending`.
3. **Re-run duplicates.** When a check has both an older FAILURE and a newer SUCCESS for the same name, the latest by `startedAt` wins. `test_latest_rerun_wins`.
4. **Day boundary in Copenhagen, not UTC.** A start at 23:30 UTC on 2026-10-21 is 01:30 on 2026-10-22 in Copenhagen. `test_daily_cap_uses_copenhagen_day`.
5. **An alert already covered by an open Dependabot PR must not also get its own task.** If it did, the same package would get two competing PRs. `test_alert_covered_by_pr_is_not_a_task`.

---

### Task 1: Phase 0 step 3: auto-merge workflow PRs in 6 repos

Runs in this session under the owner's login. Nothing is pushed until the owner approves the diff.

**Files (in each target repo, cloned to `$TMPDIR/p0/<repo>`):**
- Modify: `.github/workflows/dependabot-auto-merge.yml` in Canvas-Code-Correction (fetch-metadata `@v3`), homelab, jakob1379.github.io, text2ics, pymdown-symbolic-snippets and bank-focus (`@v2`). Keep each repo's action version.
- Modify: `.github/dependabot.yml` in Canvas-Code-Correction (5 groups) and homelab (1 group). bank-focus has no groups, and the other 3 repos have no `dependabot.yml`. Those 4 get no change.
- Modify: the spec in this repo, recording deviations 1–5 above.

- [x] **Step 1:** Replace the `Enable auto-merge` step's `if:` with:

```yaml
        if: |
          steps.metadata.outputs.updated-dependencies-json != '[]' &&
          !contains(steps.metadata.outputs.updated-dependencies-json, 'version-update:semver-major')
```

- [x] **Step 2:** Under every `groups.<name>:` in the two `dependabot.yml` files, add `update-types: [minor, patch]`.
- [x] **Step 3: Verify.**
  - Run `nix run nixpkgs#actionlint -- .github/workflows/dependabot-auto-merge.yml` in each clone. Expected: no output, exit 0.
  - Run `nix run nixpkgs#yq-go -- . .github/dependabot.yml`. Expected: the file parses.
- [x] **Step 4:** Show the owner the 6 diffs in one message and wait for approval.
- [x] **Step 5:** On approval, in each repo (PR body via `mattpocock-skills:pr`):
  - Create branch `ci/dependabot-automerge-majors-only`.
  - Commit `ci: auto-merge all non-major dependabot updates`.
  - Push and run `gh pr create`. The body names the old filter's failure: grouped PRs and SHA bumps have `update-type: null`.
  - `link_pull_request` each PR URL.
- [x] **Step 6:** Edit the spec with deviations 1–5. Commit `docs: record planning deviations in backlog agent design`.

### Task 2: Phase 0 step 4: kick open Dependabot PRs

Runs after the owner has merged the Task 1 PRs. Done 2026-10-23 without rebase comments: the 3 remaining PRs were worked by Phase 1 and merged (see the spec status).

- [ ] **Step 1:** For each of the 6 repos, list open PRs with `author.login == "dependabot"` and show the count to the owner.
- [ ] **Step 2:** Comment `@dependabot rebase` on each listed PR. Use the owner's login, since the PAT also works but this is a one-off.
- [ ] **Step 3: Verify.** After 15 minutes, `gh pr list --json number,autoMergeRequest` in each repo should show every non-major PR with auto-merge enabled, or already merged. Exception: Canvas PRs stay red on `test` until fix-main. That's expected, and it's Phase 1's first job.
- [x] **Step 4:** Update the spec status: steps 3–4 done, and list anything that didn't enable. Commit `docs: record phase 0 completion`.

### Task 3: Ledger and caps

**Files:**
- Create: `backlog-agent/backlog.py`
- Create: `backlog-agent/test_backlog.py`
- Create: `.gitignore` with `backlog-agent/ledger.json`

**Interfaces:**
- Produces:
  - Ledger shape:

    ```
    {"tasks": {key: {"status": "running"|"done"|"blocked"|"failed", "task_id": str, "attempts": int, "started": iso, "finished": iso|null, "note": str}},
     "starts": [iso, ...],
     "cap_announced": "YYYY-MM-DD"|null}
    ```

  - `load_ledger(path: Path) -> dict`: a missing file returns an empty ledger.
  - `save_ledger(path: Path, ledger: dict) -> None`: atomic write via tmp file and `os.replace`.
  - `start(ledger: dict, key: str, task_id: str, now: datetime) -> None`
  - `finish(ledger: dict, key: str, status: str, note: str, now: datetime) -> str`: returns the stored status.
  - `used_today(ledger: dict, now: datetime) -> int`
  - CLI: `backlog.py start KEY TASK_ID` and `backlog.py finish KEY done|blocked|failed NOTE`. Ledger path: `Path(__file__).with_name("ledger.json")`.

- [x] **Step 1: Write the failing tests** one slice at a time, per `mattpocock-skills:tdd` (`unittest`, using `now` as a fixed aware datetime):

```python
def test_start_counts_attempts_and_starts(self):
    start(L, "o/r#1", "t1", now); finish(L, "o/r#1", "failed", "x", now); start(L, "o/r#1", "t2", now)
    assert L["tasks"]["o/r#1"]["attempts"] == 2 and len(L["starts"]) == 2

def test_second_failure_becomes_blocked(self):
    # after two starts
    assert finish(L, "o/r#1", "failed", "boom", now) == "blocked"

def test_first_failure_stays_failed(self):
    assert finish(L, "o/r#1", "failed", "boom", now) == "failed"

def test_daily_cap_uses_copenhagen_day(self):
    L["starts"] = ["2026-10-21T23:30:00+00:00"]
    assert used_today(L, datetime(2026, 10, 22, 8, tzinfo=UTC)) == 1
    assert used_today(L, datetime(2026, 10, 21, 20, tzinfo=UTC)) == 0

def test_starts_older_than_two_days_are_trimmed_on_start(self): ...
```

- [x] **Step 2: Run it and check that it fails.** Run `python3 -m unittest discover -s backlog-agent -v`. Expected: ImportError / NameError.
- [x] **Step 3:** Implement the functions above and the two CLI subcommands with `argparse`.
- [x] **Step 4: Run the tests again.** Expected: all pass.
- [x] **Step 5: Commit** `feat(backlog-agent): add ledger with daily cap`.

### Task 4: Planner: drop, hold, derive, rank, cap

**Files:**
- Modify: `backlog-agent/backlog.py`
- Modify: `backlog-agent/test_backlog.py`

**Interfaces:**
- Consumes: the ledger functions from Task 3.
- Consumes: the candidates contract (produced by Task 5, defined here):

  ```
  {"generated": iso, "needs_review_open": int,
   "repos": [{"repo": "o/r", "has_ci": bool, "main": "green"|"red"|"pending"|null,
              "prs": [{"number": int, "title": str, "created_at": iso, "labels": [str], "mergeable": str,
                       "auto_merge_since": iso|null, "checks": "green"|"red"|"pending"|null, "major": bool}],
              "alerts": [{"ghsa": str, "severity": "critical"|"high"|"medium"|"low", "ecosystem": str, "package": str,
                          "range": str, "first_patched": str|null, "created_at": iso, "covered_by_pr": int|null}]}]}
  ```

- Produces:
  - `plan(candidates: dict, ledger: dict, now: datetime) -> dict` mutates the ledger and returns:

    ```
    {"dispatch": [task], "held": [key], "newly_blocked": [{"key","note"}], "resolved": [key],
     "running": int, "used_today": int, "announce_cap": bool}
    ```

  - A task is `{"key", "kind": "alert"|"pr"|"fix-main"|"add-ci", "repo", "tier": int, "has_ci": bool, "pr": int?, "alerts": [alert]?}`.
  - CLI: `backlog.py plan CANDIDATES_JSON` saves the ledger and prints the result as JSON.

Rules, in order:

1. **Keys.**
   - PR: `o/r#N`
   - alert group: `o/r:dep:<eco>:<pkg>`
   - fix-main: `o/r:fix-main`
   - add-ci: `o/r:add-ci`

   fix-main exists when `has_ci and main == "red"`. add-ci exists when `repo in ADD_CI_REPOS and not has_ci`.
2. **Ledger cleanup.**
   - Any non-running ledger key that is not among the current candidate keys is removed and listed in `resolved` when its status was `done`.
   - A `done` key still present `STALE_DONE_AFTER` after `finished` becomes `blocked` with the note "still open 48h after agent finished", and goes into `newly_blocked`.
3. **Drop.**
   - Keys whose ledger status is running, done or blocked. A `failed` key is retried.
   - PRs labelled `needs-review` or `agent:blocked`.
   - PRs in no-CI repos.
   - Alerts with `covered_by_pr` set.
   - PRs with `checks == "pending"`.
   - Green PRs whose `auto_merge_since or created_at` is younger than `STUCK_AFTER`.
   - Green and older counts as stuck, and so does `mergeable == "CONFLICTING"`.
4. **Hold.** PR tasks in a repo whose fix-main key exists go into `held`, not `dispatch`.
5. **Needs-review cap.** If `needs_review_open >= 5`, drop tasks with `has_ci == False` and add-ci tasks.
6. **Rank** by `(tier, severity_rank, created_at)` using the tiers in Deviation 5. A PR's severity is the max severity of the alerts whose `covered_by_pr` equals its number.
7. **Slots.** `slots = max(0, min(MAX_RUNNING - running, DAILY_CAP - used_today))`, and `dispatch = ranked[:slots]`. `announce_cap` is true the first time in a day that `used_today >= DAILY_CAP` while tasks are waiting; set `cap_announced` to that day.

- [x] **Step 1: Write the failing tests**, building candidates with a small `repo(...)` fixture helper in the test file:

```python
test_alert_covered_by_pr_is_not_a_task          # covered_by_pr=7 → no "o/r:dep:pip:urllib3" in dispatch
test_red_main_holds_pr_tasks_not_alert_tasks    # main red, red PR #3, high alert → dispatch has fix-main + alert, held == ["o/r#3"]
test_rank_order                                 # high alert < fix-main(with high) < low alert < old red PR < newer red PR < major PR < add-ci
test_green_pr_younger_than_2h_is_dropped_older_is_stuck
test_no_ci_repo_prs_dropped_but_alerts_kept
test_needs_review_cap_drops_no_ci_and_add_ci    # needs_review_open=5
test_slots_respect_running_and_daily_cap        # 2 running → 1 dispatched; 24 starts today → 0 and announce_cap True once, False on 2nd call
test_resolved_and_stale_done                    # done key gone → resolved; done key present, finished 49h ago → newly_blocked
test_failed_is_retried_blocked_is_not
```

- [x] **Step 2: Run the tests and check that they fail.** Run `python3 -m unittest discover -s backlog-agent -v`. Expected: the new tests FAIL.
- [x] **Step 3:** Implement `plan()` and the `plan` subcommand.
- [x] **Step 4: Run the tests again.** Expected: all pass.
- [x] **Step 5: Commit** `feat(backlog-agent): add planner with ranking, hold and caps`.

### Task 5: Collector

**Files:**
- Modify: `backlog-agent/backlog.py`
- Modify: `backlog-agent/test_backlog.py`

**Interfaces:**
- Produces:
  - `required_state(contexts: list[dict], required: list[str]) -> str | None`. `contexts` are GraphQL `statusCheckRollup.contexts.nodes`. Returns None when `required` is empty.
  - `is_major(text: str) -> bool`
  - `collect() -> dict`, which outputs the candidates contract from Task 4.
  - CLI: `backlog.py collect` prints JSON to stdout.

Approach:

- **Repos.** Run one paginated GraphQL query with `viewer.repositories(ownerAffiliations: OWNER, isArchived: false, isFork: false, first: 20)`. Per repo it fetches:
  - the default branch's `statusCheckRollup.contexts(first: 100)`
  - open PRs (`first: 50`), each with `body` and the last commit's rollup
  - `vulnerabilityAlerts(states: OPEN, first: 100)`

  Run it through `gh api graphql --paginate`.
- **Required checks.** Only for repos with PRs or alerts, call `gh api repos/{o}/{r}/rules/branches/{default}`.
- **`required_state`.**
  - Per required name, take the node with that `name` (CheckRun) or `context` (StatusContext), picking the latest by `startedAt`.
  - Return `"red"` if any is completed with a non-passing conclusion. Return `"pending"` if any is missing or not completed. Otherwise return `"green"`.
- **`is_major`.**
  - Find every `from v?(\d+)[^\s]* to v?(\d+)` match in the title + body.
  - Return true if any pair's first numbers differ.
  - Return false when nothing matches (SHA bumps).
- **`covered_by_pr`.** The lowest-numbered open Dependabot PR whose title or body contains the alert's package name as a whole word, case-insensitive.
- **`needs_review_open`.** The count of open PRs across repos labelled `needs-review`.

- [x] **Step 1: Write the failing tests:**

```python
test_missing_required_check_is_pending   # required ["test"], contexts without "test" → "pending"
test_latest_rerun_wins                   # test FAILURE @10:00, test SUCCESS @11:00 → "green"
test_status_context_counts               # StatusContext {context:"ci", state:"SUCCESS"} → "green"
test_neutral_and_skipped_pass
test_is_major                            # "from 1.9.2 to 2.0.0" True; "from 4.1 to 4.2" False; "from v3 to v4" True;
                                         # "from abc123 to def456" False; group body with one 1→2 line True
```

- [x] **Step 2: Run the tests and check that they fail.** Expected: FAIL.
- [x] **Step 3:** Implement `required_state`, `is_major`, `collect` and the CLI.
- [x] **Step 4: Run the tests again.** Expected: PASS.
- [ ] **Step 5: Live smoke test** (read-only):

  ```
  export GH_TOKEN=$(cat ~/.config/backlog-agent/gh-token)
  python3 backlog-agent/backlog.py collect > $TMPDIR/c.json
  ```

  with `allowed_domains: ["api.github.com"]`. Expected:
  - Canvas-Code-Correction has `has_ci: true`, `main: "red"`, and every Dependabot PR with `checks: "red"`.
  - pacify has `has_ci: false`.
  - No archived repo appears.
  - The alert total is within a few of the live count from `gh api graphql` `vulnerabilityAlerts.totalCount` summed over repos.
- [x] **Step 6: Commit** `feat(backlog-agent): add GitHub candidate collector`.

### Task 6: CHILD.md and PROMPT.md, then a supervised first run

**Files:**
- Both files are written with `mattpocock-skills:writing-for-agents`.
- Create: `backlog-agent/CHILD.md`. This is the spec's "Child agent rules" made executable:
  - **Setup and scope.**
    - The child receives one task JSON.
    - It clones into `$TMPDIR/<key-slug>` with `git -c credential.helper='!gh auth git-credential'`.
    - It works only that task.
  - **Branches.** Alert and fix-main and add-ci PRs use branch `backlog-agent/<key-slug>`. PR tasks push onto the Dependabot head branch.
  - **Auto-merge and review.**
    - Enable auto-merge only when `has_ci` is true **and** the diff touches only manifests, lockfiles or `.github/`.
    - Otherwise add the label `needs-review`, creating it if it's missing. Request jakob1379 as reviewer on Dependabot PRs; assign jakob1379 on PRs the agent authored.
  - **Skills.**
    - Red PR and fix-main: follow `mattpocock-skills:diagnosing-bugs`. The feedback loop is the failing required check, reproduced locally.
    - PR bodies: `mattpocock-skills:pr`.
  - **Never:**
    - merge, or force-push a non-Dependabot branch
    - change settings, dismiss an alert, or add an audit ignore
    - touch an archived repo
    - disable the sandbox
  - **Network.** Declare `allowed_domains` per command from the spec's list.
  - **Output.** The final line must be exactly `RESULT: done|blocked|failed — <one line>`. `blocked` also adds `agent:blocked` with one comment, when a PR exists.
- Create: `backlog-agent/PROMPT.md`. Sections:
  0. **Guard.** Check that `git -C <worktree> branch --show-current` is `feat/backlog-agent` and that `<worktree>/backlog-agent/backlog.py` exists. If either fails, post one line and stop. Never run from `main`.
  1. **Reconcile.** For each running ledger key, call `task_status(task_id)`. When it's terminal, parse the `RESULT:` line (if it's missing, record `failed`) and run `backlog.py finish`.
  2. **Collect.** Run `backlog.py collect > $TMPDIR/c.json`. If that fails, post the error once and stop.
  3. **Plan.** Run `backlog.py plan $TMPDIR/c.json`.
  4. **Dispatch.** For each `dispatch` task:
     - Call `delegate_task`, using the Global Constraints target, title `backlog: <key>`, and task text `Read and follow <abs>/CHILD.md exactly. Task: <task JSON>`.
     - Then run `backlog.py start <key> <taskId>`.
  5. **Report.** Only if a finish happened, `dispatch`/`newly_blocked`/`resolved` is non-empty, or `announce_cap` is set, post at most 10 lines with links: started, merged/closed, blocked (with the suggested owner action), and admin steps. Otherwise post nothing.
  6. **Child-completion notifications.** When woken by one, do only step 1 for that task.

- [x] **Step 1:** Write both files.
- [ ] **Step 2: Dry run.** In this session, run steps 1–3 of PROMPT.md and show the owner the `plan` output.
  - Expected: dispatch has 3 tasks with the top one in tier 1 or 2, and `held` contains the Canvas PRs.
  - Then delete `ledger.json`. The dry run must not consume the cap.
- [ ] **Step 3: One supervised child.**
  - With the owner's go, dispatch only the top task, then wait for its completion notification.
  - Check its PR/branch by hand against CHILD.md: correct label/auto-merge decision, conventional commit, no settings touched, `RESULT:` line present.
  - Run `finish`.
- [x] **Step 4:** Fix whatever the supervised run exposed in CHILD.md, then commit `feat(backlog-agent): add dispatcher and child prompts`. Suggest the owner run `/mattpocock-skills:retro` on the child's thread.
- [x] **Step 5:** Run `mattpocock-skills:code-review` with fixed point `main` and the spec path. Fix the confirmed findings, then commit.

### Task 7: Schedule

- [x] **Step 1:** With the owner's approval, call `schedule_task`:
  - `schedule: {"type": "interval", "everyMs": 3600000}`
  - `bindToCurrentThread: false` (one new thread per run)
  - `title: "GitHub backlog agent"`
  - `prompt: "Read and follow /home/jsg/.t3/worktrees/random/feat-backlog-agent/backlog-agent/PROMPT.md exactly."` (path since moved to `/home/jsg/Documents/jsg/random_backlog-agent/backlog-agent/PROMPT.md`)

  Report the returned `nextRunAt`.
- [ ] **Step 2:** After the first scheduled run, check that it posted either nothing or a summary of 10 lines or fewer, and that `ledger.json` has the expected entries.
- [x] **Step 3: Push.** Push `feat/backlog-agent` to origin as a backup. Open no PR to `main`, because the runtime stays off `main`.
