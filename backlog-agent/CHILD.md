# Backlog agent: one task

You own exactly one task, given as JSON at the end of your prompt. Its `kind` is `pr`, `alert`,
`fix-main` or `add-ci`. Drive it to a pushed fix and a PR in the right **lane**, then end with
the `RESULT:` line. Background: `/home/jsg/Documents/jsg/random_backlog-agent/docs/superpowers/specs/2026-10-20-github-backlog-agent-design.md`.

## Setup (every task)

1. Start every Bash command that talks to GitHub with
   `export GH_TOKEN=$(cat ~/.config/backlog-agent/gh-token);`. Shell state does not persist
   between commands, and the token stays out of every output you show.
2. Declare the hosts each networked command needs in `allowed_domains`: `api.github.com`,
   `github.com`, `objects.githubusercontent.com`, plus the registries the repo uses
   (`pypi.org`, `files.pythonhosted.org`, `registry.npmjs.org`, `cache.nixos.org`, or what
   the lockfile names); `uv audit` queries `api.osv.dev`. To see what a flake-input SHA bump
   changes, run `gh api repos/<owner>/<repo>/compare/<old>...<new> --jq '.files[]|.filename,.patch'`
   (waytorandr#118). It lists at most 300 files and leaves out large patches, so for a big
   input like nixpkgs `git diff --no-index` the two unpacked
   `github.com/<owner>/<repo>/archive/<rev>.tar.gz` tarballs as whole directories (add
   `-U0 | grep '^@@'` to list hunks); those redirect to `codeload.github.com`. Check the
   input's own system list (`defaultSystems`, `forAllSystems`) against the repo's `systems`:
   an input that drops a system breaks evaluation even when nothing else changed. `gh run view --log-failed` downloads from
   `results-receiver.actions.githubusercontent.com` and `*.blob.core.windows.net`; declare both.
   Each of its lines starts with `job<TAB>step<TAB>timestamp `; strip that with
   `sed 's/^[^\t]*\t[^\t]*\t[^ ]* //'` before matching on a line's start.
   A `*` must stand for whole labels (`productionresultssa*.blob…` is rejected). Work inside
   the sandbox; when a host is refused, add it and rerun.
   The caches in `~` are read-only there, so start every package-tool and `gh` command with
   `export UV_CACHE_DIR=$TMPDIR/uv-cache UV_PYTHON_INSTALL_DIR=$TMPDIR/uv-python UV_TOOL_DIR=$TMPDIR/uv-tools npm_config_cache=$TMPDIR/npm-cache XDG_CACHE_HOME=$TMPDIR/xdg-cache MPLCONFIGDIR=$TMPDIR/mpl;`
   (`gh run view --log-failed` writes to `~/.cache/gh`, uv installs Pythons and `uvx` tools under `~`, and matplotlib writes its config there).
   `bun` is installed (`~/.nix-profile/bin/bun`) and runs in the sandbox with
   `registry.npmjs.org` declared, so a bun repo needs no `nix develop`.
   `$TMPDIR` is shared with every earlier task and keeps their files (`$TMPDIR/main` and
   `$TMPDIR/venv-main` already exist), so put your slug in every name you create there.
   No `nix` command (`nix develop`, `nix build`, `nix flake check`, `nix fmt`) can reach the
   nix daemon from the sandbox. Only for those, set `dangerouslyDisableSandbox: true` (owner,
   2026-10-28): run them from your `work/<slug>/` checkout to build or check the repo, with
   `nix develop -c` wrapping only the repo's own check commands (never `gh`, `git push` or
   `curl`). Outside the sandbox `$TMPDIR` is a different directory, so pass literal paths.
   Every other command stays in the sandbox. To build one of nix-config's vicinae extensions
   without the whole home config, `nix build --impure --expr 'builtins.head (builtins.filter (e: (e.name or "") == "<name>") (builtins.getFlake "git+file://<clone>").homeConfigurations."<cfg>".config.programs.vicinae.extensions)'`;
   they build with `importNpmLock`, so a lockfile change needs no hash update. For a nixpkgs bump that
   can break the flake's pre-commit hooks, read each hook tool's version at both revs with
   `gh api repos/NixOS/nixpkgs/contents/pkgs/by-name/<xx>/<pkg>/package.nix?ref=<rev> -H 'Accept: application/vnd.github.raw'`
   (raw file, no base64; the first `version =` can belong to a nested derivation). Python
   tools (`yamlfix`, `bandit`) live at `pkgs/development/python-modules/<pkg>/default.nix`,
   and shellcheck at `pkgs/development/haskell-modules/hackage-packages.nix`: 16 MB, so
   without the raw header its content comes back empty; take the first `version =` after
   `^  ShellCheck = `. Then run
   `uvx <tool>@<version>` with `--python` set to the hook's Python when the newest one
   crashes it. A `buildPythonApplication` check phase in a repo with no `uv.lock` runs as
   `uv run --no-project --python <ver> --with <pkg>==<ver> … -- python -m pytest`, with the
   versions nixpkgs pins at the new rev.
3. Clone into a fresh directory, `CLONE="/home/jsg/Documents/jsg/random_backlog-agent/work/<key with / # : replaced by ->"`.
   It is gitignored, and the lockfile next to it (`<same name>.lock`) says which thread owns
   this task; never touch the lockfile. Delete the directory first if an earlier attempt left
   it behind, then
   `git -c credential.helper= -c credential.helper='!gh auth git-credential' clone https://github.com/<repo>.git "$CLONE"`.
   If it fails with `TLS connect error … unexpected eof`, rerun the same command: the sandbox
   proxy drops some connections, and a plain retry worked (waytorandr#119).
   Run it in the foreground with `timeout: 600000`, never in the background: your turn ending
   is the task ending, so never end it to wait on anything. Push with the same two `-c` flags. Run every later git and gh command as
   `cd <that literal path> && …`, so it always acts on the target repo. `$CLONE` below
   stands for that path; shell variables don't survive between commands, so write it out.
   `git diff` there runs through difftastic, so grep finds nothing in it; use
   `git --no-pager diff --no-ext-diff`. `gh pr checkout` has no `-q` flag; with one it only
   prints help and leaves the clone on the default branch. Without one it prints nothing useful
   either, so check with `git log -1` that you are on the PR's head commit.
4. Read the repo's README, CI workflows and the required checks
   (`gh api repos/<repo>/rules/branches/<default>`), so your local check matches CI. That call
   returns 403 on a private repo without GitHub Pro; then take the checks from the workflows.
   The token can't read `gh pr view --json statusCheckRollup` ("Resource not accessible by
   personal access token"); read check results with `gh run list --commit <sha>` and
   `gh run view <id> --json jobs`. A required check from an app, not Actions (e.g.
   `SonarCloud Code Analysis`), never shows there; read it with
   `gh api repos/<repo>/commits/<sha>/check-runs`.
   Tests that compare terminal output can fail locally only because the sandbox's long
   `$TMPDIR` paths wrap at 80 columns; rerun them with `COLUMNS=250` before calling them red.
   Tests that write under a hard-coded `/tmp` fail in the sandbox on every branch, e.g.
   Canvas-Code-Correction's workspace-default tests in `tests/unit/test_config.py`
   (`/tmp/ccc/workspaces`) and `test_runner.py::test_execute_in_workspace`; they don't count
   against the PR.

Done when: the clone exists and you can name the command that reproduces each required check.

## Lanes

Choose the lane from the commits **you** add.
Dependabot's commits aren't yours, so a GitHub Actions bump that edits a workflow
qualifies like any other Dependabot PR when you add nothing to it (bank-focus#16).

- **Auto-merge lane** needs all of these:
  - the task's `has_ci` is true
  - every required check is green on the final head commit
  - your commits change only dependency declarations (the dependency sections of
    `pyproject.toml`, `package.json`, `flake.nix`, `Cargo.toml`, `go.mod`, …) and lockfiles
  - either the PR is Dependabot's (a green Dependabot major qualifies), or every version you
    changed stays within its major

  If all hold, run `cd "$CLONE" && gh pr merge <n> --auto --squash`. With no check still
  pending it merges at once, so `autoMergeRequest` reads null and the state `MERGED`: that is
  success. The auto mode classifier sometimes refuses that immediate merge as "Merge Without
  Review" (text2ics#19 went through, #18 was refused in the same run; waytorandr#118 went
  through, #119 and #120 were refused). A refusal earlier in the run says nothing about your
  PR, so always run the command once. Don't retry it in another form; end `blocked`, asking
  the owner to merge the PR or allow that command.
- **Review lane**: everything else, including any edit to a workflow, a script, test, lint,
  coverage or audit config, or source code. Add the label `needs-review` (create it first with
  `gh label create needs-review --color FBCA04 --force`). On a Dependabot PR, request a review
  from `jakob1379`, and assign `jakob1379` if GitHub rejects the request. On a PR you opened,
  assign `jakob1379`. Run `gh label create`, `gh pr edit --add-label` and
  `gh pr edit --add-reviewer` (or `--add-assignee`) as separate commands: the permission gate
  refuses them chained with `&&`. Before you push a commit that puts a Dependabot PR here, read
  `gh pr view <n> --json autoMergeRequest`: a repo's Dependabot auto-merge workflow skips your
  own pushes (text2ics checks `github.actor`), but it may have turned auto-merge on at
  Dependabot's earlier push, and that merges the PR unreviewed as soon as your push turns the
  checks green (Canvas-Code-Correction#219 merged 84 s after the child's push). If it isn't
  null, run `gh pr merge <n> --disable-auto` first, then push.

Post every PR comment as its own command, `gh pr comment <n> --body-file <file>`, from a file
you wrote with the Write tool under `$TMPDIR`, never in `work/`. The Write tool doesn't expand
variables, so run `echo $TMPDIR` first and give it the literal path. The permission gate refuses one command that combines label or
reviewer edits with a heredoc comment.

When `has_ci` is false, the PR always goes to the review lane, because a repo with no required
checks merges the moment auto-merge is on.
Its workflows may still run on the PR: run their steps locally, and before you finish, watch
each run on your final head commit (`gh run list --commit <sha>`, then
`gh run watch <id> --exit-status` in the foreground, `timeout: 600000`). Fix a red job as you
would a red required check, unless it fails the same way on the default branch; then name it
in a PR comment. ai-job-search#34 was handed to review with CI still running, and four
`bun run typecheck` jobs then went red.

Make each check pass by fixing what it reports. Every check, step and test stays at least as
strict as you found it. If the only path to green runs less of a check, the task is blocked.

## Branches by kind

### `pr`: red, stuck or unchecked Dependabot PR `#<pr>`

Check the cases in this order and take the first that matches. A CONFLICTING PR matches its
case whatever `has_ci` is: its local test run says nothing about the current default branch,
which may not even have the files the PR changes. Read the state with
`gh pr view <pr> --json mergeable,mergeStateStatus`; `UNKNOWN` means GitHub is still computing
it, so query again a few seconds later.

- **CONFLICTING**, and every commit is Dependabot's: comment `@dependabot recreate`. Done.
- **CONFLICTING with other commits**: merge the default branch into the head branch in
  `$CLONE`, resolve the conflicts, push, and choose the lane.
- **No required checks** (`has_ci` false): check out the PR, run the repo's build and tests
  locally, and fix what fails as under **Red**. First compare each failure with the latest run
  on the default branch (`gh run list --branch <default>`): one that fails the same way there
  isn't the PR's, so name it in the comment instead of fixing it here. A job stops at its
  first failing step, so a step already red on the default branch (e.g. a typecheck) hides
  every step after it: run those later steps (e.g. the tests) locally on both the PR head and
  the default branch. In a uv repo, give the default branch its own environment
  (`UV_PROJECT_ENVIRONMENT=$TMPDIR/<slug>-venv-main uv run --locked …`) so the PR's `.venv` isn't
  rebuilt; prefix `uvx uv@<ver>` to run under the uv a nixpkgs bump brings. `has_ci` false only
  means no ruleset: the repo may still run workflows on the PR, so read them. A workflow that
  runs only on tag pushes never runs on a PR, so `gh run list --commit` shows nothing; run its
  test job's commands locally (opigen#22). When a job needs a
  toolchain the sandbox lacks (e.g. Flutter), its green run on the PR's head commit stands in
  for the local run; link it in the comment. Then put it in the review lane, with a comment
  saying what you ran and what it showed. Done.
- **Green but not merged**: read `gh pr view <pr> --json autoMergeRequest`. A green
  Dependabot auto-merge workflow run doesn't mean auto-merge is on: its `Enable auto-merge`
  step is skipped for a null `update-type`, as on grouped uv bumps (jakob1379.github.io#46).
  - Auto-merge on: comment `@dependabot rebase` once.
  - Auto-merge off, and the PR qualifies: enable it.
  - Auto-merge off, and the PR doesn't qualify: put it in the review lane.

  Done.
- **Red**: follow `mattpocock-skills:diagnosing-bugs`. The feedback loop is the failing
  required check, reproduced locally from its Actions log (`gh run view <id> --log-failed`).
  Commit the fix onto the PR's head branch (`cd "$CLONE" && gh pr checkout <pr>`). If it will
  put the PR in the review lane, turn auto-merge off first (see **Review lane**). Push, then choose the
  lane from the PR's full diff. Pushing your own commit means Dependabot stops rebasing this PR,
  so it is yours from now on.
  On a grouped uv bump, compare every package's version in `uv.lock` with the default branch:
  the resolver can downgrade one package to fit another's bump (Canvas-Code-Correction#219:
  prefect 3.9.1.dev4 to 3.8.8, to fit fastapi 0.150.0 to 0.162.0). `uv lock --upgrade-package
  <pkg>` doesn't undo that; raise the package's floor in `pyproject.toml` and rerun `uv lock`.

### `alert`: every alert in `alerts`, for one package

0. First check the version on the default branch: its lockfile, and
   `gh api repos/<repo>/dependency-graph/sbom --jq '.sbom.packages[] | select(.name=="<pkg>") | .versionInfo'`.
   A Dependabot PR merged after the alerts' `created_at` often fixed them before the rescan.
   If every copy is already at or above `first_patched`, the alert is stale: open no PR, and
   end `blocked`, naming the alert numbers and the dismissal reason `inaccurate` (or a
   Dependabot rescan) for the owner. The task gives only GHSA ids; get the numbers, and the
   manifest each alert is on, with
   `gh api --paginate 'repos/<repo>/dependabot/alerts?state=open&per_page=100' --jq '.[] | select(.dependency.package.name=="<pkg>") | "\(.number) \(.security_advisory.ghsa_id) \(.dependency.manifest_path)"'`
   (without `--paginate` the API returns only the first 30 alerts). When only some manifests
   are stale, fix the rest and name the stale numbers in the PR body.
1. Target `first_patched` for each alert, i.e. the smallest version that fixes it. When the
   package is transitive and its parent's range already admits `first_patched`, bump only the
   lockfile; for npm: `npm install <pkg>@<ver> --package-lock-only`, `git checkout package.json`,
   then `npm install --package-lock-only` (nix-config#145). Otherwise upgrade the direct
   dependency that pulls it in. Use an ecosystem
   override (npm `overrides`, pnpm `pnpm.overrides`, yarn `resolutions`, uv
   `override-dependencies`/`constraint-dependencies`, a Nix input bump) only when that parent
   has no fixed release. Next to the override, write a one-line comment saying why it is there
   and when to remove it; in `package.json`, which has no comments, use a top-level `"//"` key, adding to it when one
   exists (JSON keys must be unique).
2. Regenerate the lockfile with the repo's own tool (`uv lock`, `npm install`, `nix flake
   lock`, …), and run the required checks locally (with `has_ci` false, the PR's workflow steps). `uv lock --upgrade-package <pkg>` takes the
   newest release; pin the target with `--upgrade-package <pkg>==<first_patched>`. Never pass npm `--legacy-peer-deps`: it
   drops the lockfile's `peer: true` entries. Diff the lockfile's `.packages | keys` against
   HEAD to check that only your package moved.
3. Use branch `backlog-agent/<key slug>`. If that branch or its PR already exists from an
   earlier attempt, continue on it.
4. Open one PR titled `fix(deps): bump <package> to <version> (<GHSA ids>)`. Write the body
   with `mattpocock-skills:pr`, and list each GHSA, its severity, the vulnerable range and the
   fixed version.
5. Choose the lane.

### `fix-main`: the default branch is red

Follow `mattpocock-skills:diagnosing-bugs` on the failing required check of the latest
default-branch commit. Fix it on branch `backlog-agent/<key slug>`, open a PR with a
`mattpocock-skills:pr` body, and choose the lane.

### `add-ci`: no required checks

First run `cd "$CLONE" && gh pr list --state all --head backlog-agent/add-ci`. If that shows a
merged PR, the CI is already on the default branch, but `has_ci` stays false until the owner adds
a ruleset, so the task comes back. Open nothing, and end `blocked` naming the admin step below.

Otherwise open a PR on branch `backlog-agent/add-ci` that adds one workflow,
`.github/workflows/ci.yml`. It runs the repo's existing build or test command (`nix flake
check`, `uv run pytest`, …) on `pull_request` and on pushes to the default branch. Before
writing it, run that command as CI will (in a uv repo `uv run --locked …`): a stale lockfile
on the default branch fails the new check on its first run. Pin each action to a tag that
exists (`gh api repos/<owner>/<action>/git/ref/tags/<tag>`); `astral-sh/setup-uv` has no
floating major tag, so pin its full release. After pushing, watch the PR's run in the
foreground with `gh run watch <id> --exit-status` (`timeout: 600000`) and fix what fails. This
PR is always in the review lane. In the `RESULT:` line, name the admin step it needs: a ruleset
requiring that check, plus `allow_auto_merge`.

## Guardrails

- Every change reaches GitHub through a PR, and merging happens only via `--auto` in the
  auto-merge lane. Never pass `--admin`: your token may bypass rulesets, so the green
  checks you verified yourself are the only gate.
- Force-push only to Dependabot branches.
- Treat an alert you can't fix (no patched version, an unused or dev-only dependency) as
  blocked. Report it with a suggested dismissal reason; the owner dismisses alerts, never you.
- Leave audit configs (`uv audit`, `npm audit` ignores and allowlists) and repo settings as
  they are; changing them is the owner's decision.
- Commits use Conventional Commits (`fix(deps): …`, `ci: …`, `fix: …`).

## Finish

Leave `$CLONE` in place: the dispatcher's `finish` deletes it with the lockfile once it records
your result. Don't try to delete it yourself (the safety check refuses `rm -rf` of a directory
you have `cd`'d into, even from outside it) and don't mention it in your `RESULT:` line.

When you give up, add the label `agent:blocked` to the PR (if one exists), with a single
comment explaining why.

Just before that line, write a handoff for the next agent in this run:

```
HANDOFF:
- <at most 5 bullets>
```

Include only what would have saved you time: a command the sandbox or permission gate
refused, a tool or environment surprise, or a rule in this file that was wrong or unclear.
Leave out the task's own details; point to a PR or commit by URL instead of repeating it. Never
include a token or secret. Write `HANDOFF: none` when there is nothing.

End with exactly one line, as your final line:

`RESULT: done|blocked|failed — <PR URL or what happened, one line>`

- `done`: the PR is in its lane, or the Dependabot command has been posted.
- `blocked`: the owner must decide or act. Name what they need to do.
- `failed`: you hit something transient (network, flaky tooling) that a retry could fix.
