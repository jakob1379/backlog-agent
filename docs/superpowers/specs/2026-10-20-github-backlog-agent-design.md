# GitHub backlog agent: design

Date: 2026-10-20. Owner: jakob1379.

## Why

The owner's GitHub backlog is mostly Dependabot noise and security alerts:
33 open Dependabot PRs and 141 open alerts (5 critical, 62 high) across 46 active repos
(recon, 2026-10-20). Fixes are usually version bumps, not code. The owner wants this to clear
itself with as little human attention as possible, and to keep their attention for the items
that really need a human.

Recon showed that most of the backlog is **not** waiting on an agent. The existing
auto-merge workflow rejects it:

- The shared workflow in 6 repos only auto-merges `semver-patch` and `semver-minor`.
  `dependabot/fetch-metadata` returns `update-type: null` for grouped PRs and for Nix
  flake-input SHA bumps, so 17 of 33 PRs are always skipped.
- The workflow triggers only on `opened`/`synchronize`/`reopened`, so PRs opened before the
  workflow existed (bank-focus) were never processed.
- Canvas-Code-Correction's required `test` check runs `uv audit`, which fails on 53 lockfile
  vulnerabilities. No single bump clears it, so all 9 PRs are red forever.

So the plan has two parts: a one-time cleanup (Phase 0), then an hourly agent that only
handles what auto-merge can't (Phase 1).

## Goals

- Green Dependabot PRs merge with no human and no agent: GitHub auto-merge does it.
- An hourly agent drives red or stuck Dependabot PRs and uncovered security alerts to merged
  PRs, without anyone in the loop when the change touches only manifests, lockfiles or CI config.
- Anything that needs judgement lands with the owner as a clearly flagged review request, not
  as silent noise.

## Non-goals

- Setting up CI across repos in general. That's a separate project, later. The only exception
  is Q16 below.
- Code-scanning and secret-scanning alerts.
- The general issue and PR backlog. That's Phase 2, with Linear as the place where it's
  prioritized.
- Running when T3 is closed.

## Anti-goals (a failure even if it "works")

- Merging anything whose only "green" signal is a non-test status (CodeRabbit, neutral CodeQL)
  or that sits in a repo with no required checks.
- Dismissing a security alert. That's always the owner's call.
- Agent-made source-code changes merging without the owner reviewing them.
- Filling the owner's inbox with low-value PRs or review requests that pile up. Opening PRs
  nobody will merge is the problem being fixed, not the fix.
- Spending Opus runs on archived or dead repos.

## Scope

- In scope: non-archived, non-fork repos owned by `jakob1379` or by orgs where the owner is
  admin. Inactive but non-archived repos are in scope.
- Out of scope: archived repos (keepsake only), and forks (all have alerts disabled and no
  Dependabot PRs).
- Repos with no CI, meaning no required checks in a ruleset: the agent leaves their Dependabot
  PRs alone. The exception is Q16: pacify, br18 and brobizz-usage get a "minimal CI" task.

## Definitions

- **Green:** every status check required by the default branch's ruleset has passed. Nothing
  else counts. Required checks come only from rulesets; there is no classic branch protection
  with required checks anywhere.
- **Has CI:** the default branch's ruleset has at least one required status check.
- **Task:** exactly one of
  - one Dependabot PR
  - one security alert, or a group of alerts for the same package in the same repo
  - one "fix main" job for a repo whose default branch is red
  - one "add minimal CI" job (Q16 repos only)

## Phase 0: one-time, run in this session under the owner's own `gh` login

Each step is shown to the owner and confirmed before it runs.

1. **Archived repos with open PRs: close them.** Archived repos are read-only, so for each
   one: unarchive, close every open PR with a one-line comment, then re-archive. Current list:
   - jga-streamlit-apps/wifi-qr-generator: 51 PRs
   - gridfinity-plate-generator: 4 (3 Dependabot + 1 pre-commit-ci)
   - ruff-explain: 1
   - talk-on-nix: 1
   - who-speaks: 1

   58 PRs in total. This needs Administration rights, which the agent token deliberately
   doesn't have.
2. **Smukfestify (Q17):** close its 3 open PRs, then archive it. It's currently *not*
   archived; the last push was 2023-01-23.
3. **Fix the shared auto-merge workflow (Q14)** in Canvas-Code-Correction, homelab,
   jakob1379.github.io, text2ics, pymdown-symbolic-snippets and bank-focus. One PR per repo:
   - In `dependabot.yml`, restrict every `groups:` entry to `update-types: [minor, patch]`, so
     a group can never contain a major. Majors then arrive as separate, ungrouped PRs.
   - Workflow condition: auto-merge when fetch-metadata's `updated-dependencies-json` is
     non-empty and contains no `version-update:semver-major`. This covers groups and Nix and
     Actions SHA bumps the same way. It never lets a major through, and it fails closed when
     the metadata can't be parsed. (Revised 2026-10-21; the earlier draft listed
     patch/minor/group/ecosystem cases separately.)
   - **Superseded 2026-10-22.** That condition failed open. Grouped uv security updates report
     `update-type: null`, and the JSON has no `semver-major` string, so pymdown-symbolic-snippets#2
     (pymdown-extensions 10 → 11) auto-merged. The condition is now: `update-type` is
     `semver-patch` or `semver-minor`, or `package-ecosystem` is `nix` (SHA inputs with no version).
     Grouped uv updates with a null `update-type` are left to the backlog agent.
   - The owner reviews and merges these 6 PRs.
4. **Kick PRs that predate the workflow:** comment `@dependabot rebase` on those PRs (e.g.
   bank-focus), so the workflow runs on them.

Phase 0 must be finished before Phase 1 is scheduled.

Status as of 2026-10-23: **Phase 0 is done.**
- **Steps 1–2 (2026-10-20).** 68 PRs were closed and 6 repos are archived. While
  wifi-qr-generator was unarchived, Dependabot opened 10 new PRs; those were closed in a
  second short window. Lesson: keep the unarchive window as short as possible.
- **Step 3 (2026-10-22 13:37 UTC).** All 12 PRs are merged: the first workflow PR in each of
  the 6 repos, plus the fail-open fix (Canvas-Code-Correction#206, homelab#55,
  jakob1379.github.io#44, text2ics#17, pymdown-symbolic-snippets#4, bank-focus#15). This was
  before Phase 1's first run (2026-10-22 19:53 UTC).
- **Step 4 (no rebase comments needed).** The PRs that predated the workflow were handed to
  Phase 1 instead of being rebased: bank-focus#8 and #9, and Canvas-Code-Correction#205 after
  its fix-main. All three are merged, and the 6 repos have no open Dependabot PRs.

## Phase 1: hourly agent

### Runtime

- A T3 scheduled task, `{"type":"interval","everyMs":3600000}`. Each run starts a new thread
  in the `random_backlog-agent` T3 project, whose root is the fixed git worktree
  `/home/jsg/Documents/jsg/random_backlog-agent` on `feat/backlog-agent`, outside T3's worktree
  lifecycle. The task's workspace is "Use a specific checkout" with that path; otherwise every
  run gets a new T3 worktree and the PROMPT.md guard stops it. Its
  gitignored `.claude/settings.local.json` holds the agent's allow rules, so every run and its
  children get them. Nothing lives in `random`'s main checkout. The dispatcher may fix its own rules,
  prompts and docs there, committing and pushing a backup of the branch (PROMPT.md step 6). The prompt is: "Read and follow
  `<dir>/PROMPT.md` exactly." A run can only read the children it started itself, so each
  thread reconciles its own children when they finish. Later runs only take over overdue ones.
- Files live in a new `backlog-agent/` directory next to `daily-briefing/`: `PROMPT.md`, a
  ledger, and any helper script.
- GitHub credentials: a fine-grained PAT at `~/.config/backlog-agent/gh-token`, owner
  `jakob1379`, all repositories. Permissions:
  - Contents: read and write
  - Pull requests: read and write
  - Workflows: read and write
  - Issues: read and write
  - Actions: read
  - Commit statuses: read
  - Dependabot alerts: read
  - Metadata: read

  **No** Administration. It has been verified to work inside the sandbox:
  `export GH_TOKEN=$(cat …)` makes `gh` skip the keyring, which the sandbox can't reach.
  Git pushes must also use this token, e.g. by passing
  `-c credential.helper='!gh auth git-credential'` on the command line; the sandbox can't write
  `~/.gitconfig`.
- Network: api.github.com and github.com, plus whatever package registries a relock needs.

### Each hourly run (the dispatcher)

1. **Reconcile.** For every task in the ledger marked running, check its outcome
   (`task_status`) and record done, blocked or failed.
2. **Collect candidates** across the in-scope repos:
   - open Dependabot alerts
   - open Dependabot PRs and their required-check state
   - the default branch's state for each repo
3. **Drop** anything that:
   - is already running
   - has a ledger entry marked blocked
   - is auto-merge-eligible and green (auto-merge handles it)
   - is a Dependabot PR in a no-CI repo
4. **Derive the "fix main" tasks.** If a repo's default branch is red, create one "fix main"
   task for it, and hold that repo's Dependabot-PR tasks until main is green. Alert tasks
   still run; see the hold rule.
5. **Rank:**
   1. critical and high alerts
   2. "fix main" tasks blocking those alerts
   3. medium and low alerts
   4. red or stuck Dependabot PRs, oldest first
   5. majors
6. **Dispatch** the top tasks until **3** tasks are running in total (a cap on concurrent
   tasks, not only on new ones per hour). **Hard cap: 100 child runs per calendar day**
   (Europe/Copenhagen time). Retries count toward the cap. When the cap is reached, the
   dispatcher starts nothing new until midnight. It says so once in that day's summary. Each task gets its own child agent via
   `delegate_task`: provider `claudeAgent`, model `claude-opus-5-5`, effort `high`, one task
   per agent. Record the task ID, the key (`owner/repo#pr`, `owner/repo:GHSA-…`,
   `owner/repo:fix-main`, `owner/repo:add-ci`) and the start time in the ledger.
7. **Report.** Post a short summary in this thread **only if something changed**: tasks
   started, merged, blocked or needing the owner, and any admin-only steps queued for them.
   Silent hours stay silent.

### Child agent rules (one task each)

- Clone the target repo into a fresh per-task directory, `work/<key>` in the runtime worktree (see Designer's calls). Never work in the
  owner's local checkouts.
- Commits follow Conventional Commits, e.g.
  `fix(deps): bump urllib3 to 2.5.0 (GHSA-xxxx)` or `ci: …`.
- **Red Dependabot PR (Q4):** read the failing run's logs (Actions read) and push the fix
  commits onto the Dependabot branch. After that, Dependabot stops rebasing that PR, so the
  agent owns it.
  - If the fix touches only manifests, lockfiles or CI config: enable auto-merge
    (`gh pr merge --auto --squash`).
  - If the fix touches source code: no auto-merge. Add the `needs-review` label and request
    a review from `jakob1379`.
- **Major bump PR (Q3):** same as above. A major never gets auto-merge from the workflow,
  but the agent may enable it if the Q4 manifest-only rule holds and CI is green.
- **Alert with no PR (Q5), best practice:**
  - Target the alert's `first_patched_version`, i.e. the smallest version that fixes it. If
    that crosses a major, treat the change as source-touching: `needs-review`.
  - For transitive dependencies, first upgrade the direct dependency that pulls it in. Use an
    ecosystem override only when that parent has no fixed release: npm `overrides`, pnpm
    `pnpm.overrides`, yarn `resolutions`, uv `constraint-dependencies`/`override-dependencies`,
    a Nix input bump. When adding an override, leave a one-line comment in the manifest
    explaining why it's there and when to remove it.
  - Regenerate the lockfile with the repo's own tool. Never hand-edit lockfiles.
  - Open one PR per package per repo, covering every alert for that package. The PR body lists
    the GHSA/CVE IDs, severity, the vulnerable range and the fixed version.
  - Never dismiss an alert. If there's no patched version, or the dependency looks unused or
    dev-only, report it in the summary with a suggested dismissal reason for the owner. Mark
    it blocked in the ledger so it isn't retried every hour.
  - The alert closes on its own when the fix reaches the default branch. When the default
    branch is already fixed and the alert is still open (7 times by 2026-10-24: pacify
    pillow, tornado and urllib3, br18 idna, crawlee and multidict, brobizz-usage anyio), the child opens no PR and reports it blocked
    with `inaccurate` as the suggested reason.
- **Fix main (Q6):** make the default branch's required checks pass, using the same
  manifest-only versus source rule. (As of 2026-10-21, Canvas-Code-Correction's main is red
  on the required `SonarCloud Code Analysis` check, while `test` is green on main and red on
  all 13 Dependabot PRs.)
- **Add minimal CI (Q16):** only pacify, br18 and brobizz-usage. Open a PR adding one workflow
  that runs the repo's existing build/test command (e.g. `nix flake check`, `uv run pytest`).
  Label it `needs-review`. The ruleset and `allow_auto_merge` setup needs admin rights, so it
  goes into the summary as an admin step for the owner. Merging the CI PR doesn't give the repo
  `has_ci`; only the ruleset does, so until then the add-CI task keeps coming back, and a child
  that finds `backlog-agent/add-ci` already merged opens nothing and ends blocked with the admin
  step. Status 2026-10-24: pacify#5 and br18#2 are merged; neither repo has a ruleset yet, and
  `allow_auto_merge` is still false in all three. brobizz-usage#2 is open in the review lane,
  with its `test` check green.
- **Gave up:** label the PR `agent:blocked`, if there is one, with a one-comment reason, and
  record blocked in the ledger. The dispatcher won't retry blocked items. Unblocking is done
  by hand: remove the label or clear the ledger entry.
- Never merge directly, never force-push branches that aren't Dependabot's, never touch
  archived repos, never change repo settings.

## Phase 2 (later, needs Linear auth)

Linear becomes the place where the owner's human backlog is prioritized. Only items that need
a human go there: `needs-review` PRs, blocked items, admin steps, and GitHub issues. Bot PRs
don't. Linear isn't authenticated yet (neither the claude.ai Linear connector nor
`linear-server`).

## Decided by the owner

- The agent never merges green PRs; GitHub auto-merge does that. The agent handles only red
  or stuck PRs, alerts with no PR, the "fix main" tasks, and the Q16 CI tasks.
- Auto-merge is set up once (Phase 0), and only in repos whose ruleset has required checks.
- Major bumps never auto-merge through the workflow.
- Q4: the manifest-only vs source rule, plus requesting the owner as reviewer.
- Q5: follow best practices for alerts with no PR (made concrete above). Never dismiss.
- A red default branch becomes one "fix main" task that blocks that repo's other tasks.
- One child agent per task, Opus 5.5 high, at most 3 running.
- Within a run, children run one at a time (owner, 2026-10-23). Each child ends with a
  `HANDOFF:` block, and the dispatcher passes every block collected so far in the run to the
  next child. Overlapping runs can still have 3 tasks claimed in total. When the run has
  something to report, a final steward agent (`backlog-agent/STEWARD.md`) audits the spec,
  the plan and the prompts against GitHub, the ledger and the handoffs. It fixes stale docs
  itself, and only names any `backlog.py` fix it finds.
- Priority order as listed in the dispatcher.
- Phase 1 is GitHub only. Phase 2 uses Linear, and only for items that need a human.
- Runs as an hourly T3 task, one new thread per run, inside its own `random_backlog-agent`
  worktree, which it maintains itself. It only reports when something changed.
- Fine-grained PAT, not classic.
- Phase 0 workflow fix in the 6 repos.
- "Green" means the required checks in the ruleset passed.
- Minimal CI only for pacify, br18 and brobizz-usage.
- Archive Smukfestify. Close the open PRs in archived repos.
- Archived repos are ignored. Inactive repos are in scope. CI setup in general comes later.

## Facts checked after the builder review (2026-10-20)

- **Org repos:** the only non-archived org repos are Hofleverandorerne (2) and NaySten-Labs
  (1). None has open Dependabot PRs or alerts. Phase 1 is therefore limited to `jakob1379`,
  and the single PAT is enough. If an org repo gets work later, that org gets its own token.
  jga-streamlit-apps/wifi-qr-generator is archived and is handled in Phase 0 under the
  owner's login.
- `allow_auto_merge` is true in all 6 workflow repos and false in pacify, br18 and
  brobizz-usage. The Q16 admin step enables it there.
- No ruleset in these 9 repos requires an approving review. homelab has a `pull_request`
  rule with 0 required approvals. So "manifest-only, no human" can merge, and the owner can
  merge their own `needs-review` PRs.
- Every other repo with Dependabot PRs or alerts has no CI, so it's out of scope for
  auto-merge until the later CI project.

## Designer's calls (check these)

- The reviewer request (Q4) only goes on `needs-review` PRs, not on PRs that will auto-merge
  minutes later, to avoid inbox noise. GitHub rejects review requests from a PR's own author.
  Agent-opened PRs are authored by `jakob1379` through the PAT, so on those the owner is set
  as **assignee** instead, plus the `needs-review` label.
- Each child clones into the gitignored `work/<key>` of the runtime worktree, which `finish`
  deletes, rather than using T3 worktree isolation. T3 worktrees are worktrees of *this*
  repo, not of the target repo.
- Durable state is a ledger file plus GitHub labels, not anything in conversation memory.
  Runs overlap. Each task in progress has a lockfile `work/<slug>.lock` naming
  the thread and task working on it, next to the child's working directory `work/<slug>/`.
  `plan` creates lockfiles for what it dispatches and skips tasks that have one; `finish`
  deletes the lockfile and directory; `plan` deletes any lockfile older than 6h, with its
  directory, and any work directory that has no lockfile. Every ledger read-modify-write holds an `flock` on `ledger.lock`, which the
  kernel releases when a process dies.
- The cap is 3 *concurrent* tasks, so tasks that run long can't pile up.
- **Retries:** a task that fails (crash, timeout, flaky relock) is retried at most once. A
  second failure marks it blocked.
- **"Stuck" PR** means one of:
  - mergeable state is CONFLICTING
  - auto-merge is enabled and the required checks are green, but the PR hasn't merged
    within 2 hours
  - the PR predates the workflow and is still unprocessed after Phase 0
- **No audit ignores.** Adding an ignore or allowlist entry to `uv audit`, `npm audit` or
  similar counts as dismissing an alert, so the owner decides. If "fix main" can't get to
  green because a vulnerability has no patched version, the task is blocked. The summary
  names the specific advisories the owner needs to rule on.
- **Hold rule:** while a repo's "fix main" task is running or waiting on review, that repo's
  alert tasks still run. Their PRs just won't auto-merge until main is green. Only the
  repo's Dependabot-PR tasks are held. This keeps critical alerts from being frozen behind a
  slow "fix main".
- **Network:** children run in auto mode and declare the registry hosts per command
  (`allowed_domains`): pypi.org, files.pythonhosted.org, registry.npmjs.org,
  cache.nixos.org, github.com, api.github.com, objects.githubusercontent.com, plus whatever
  the repo's lockfile names. The sandbox is never disabled. On 2026-10-24 three children
  (t3code-flake#19, rich-card#29 and #35) broke this rule: the sandbox can't reach the nix
  daemon, so they ran `nix flake check`/`nix build` with the sandbox disabled, and the
  permission gate approved it. CHILD.md now says so explicitly. Whether nix checks may run
  outside the sandbox is the owner's decision.

## Planning changes (2026-10-21)

- **Alert task key:** `owner/repo:dep:<ecosystem>:<package>`, since one task covers every
  alert for that package.
- **Needs-review cap:** while 5 or more open PRs carry `needs-review`, the dispatcher starts
  no task in a no-CI repo and no add-CI task. Those can only end in `needs-review`, and
  without the cap about 141 alerts would bury the owner. Each such task, running or
  dispatched, takes one of the 5 places, so one `plan` can't overshoot the cap (on
  2026-10-24 a single yes/no gate let it go from 4 open to 7).
- **Tiers:**
  1. critical/high alerts, plus Dependabot PRs that cover one
  2. fix-main in a repo with a critical/high alert
  3. medium/low alerts
  4. other fix-main
  5. red/stuck non-major PRs, oldest first
  6. major PRs
  7. add-CI
- **Lane rule tightened (from the final review):** the agent's own commits may auto-merge
  only when they change dependency declarations and lockfiles. Edits to workflows, scripts, or
  test/lint/coverage/audit config always go to review, because a PR's workflow edit can
  weaken the very check that gates its auto-merge. Green Dependabot majors may still
  auto-merge (Q3).
- **No-CI Dependabot PRs are tasks (owner, 2026-10-21: "fix everything over time"):** every
  open Dependabot PR in a repo without required checks is worked once: the child builds and
  tests it locally, fixes what fails, and puts it in the review lane. So alerts covered by
  such PRs get worked too, paced by the needs-review cap.
- **Admin bypass (owner, 2026-10-21):** some rulesets let admins bypass required checks, and
  the agent's token is the owner's. The child never passes `--admin`, and enables auto-merge
  only after it has seen every required check green itself.
- **Runtime:** branch `feat/backlog-agent` in its T3 worktree, never `main`. The branch is
  never merged into `main`, and the worktree stays while the schedule exists.

## Left to the builder

- Ledger format and location inside `backlog-agent/`.
- Exactly how candidates are collected (GraphQL vs REST) and how it's paced.
- Summary wording and format.
- How to detect "touches only manifests, lockfiles or CI config" for each ecosystem.
