# A land gate in `backlog.py` merges what a review run clears

A pull request the agent opened for a backlog issue now gets review runs. A
review run answers the feedback, runs the converge review, pushes its fixes and
ends with a verdict: land or no-land. The verdict is one input. The land gate, a
rule in `plan`, decides, and `backlog.py land` makes the merge, pinned to the
commit the verdict names. A child never merges and never turns on auto-merge.
Everything the gate does not clear waits for the owner as before.

The model is Meta's RADAR (arXiv 2605.30208) and PyTorch's GreenLight
(`pytorch/test-infra`, read at `a1a8f07`). We took what carries over to one
owner with small repos: fail closed at every step, a verdict tied to one head
commit and checked again at merge time, a size limit and protected paths checked
by code, a delay in which the owner can stop the merge, a daily limit, a pause
after a revert, and shadow verdicts that land nothing. We left out what needs a
population of diffs: a trained risk score and its percentiles.

## Considered Options

- **Running GreenLight itself.** It has no reusable workflow, action or
  published package. It is wired to PyTorch's merge rules, a GitHub App, AWS
  Bedrock and a ClickHouse ledger, it never merges, and it never fixes a pull
  request. Its rubric and its safety rules are what carry over, so the verdict
  rubric in `CHILD.md` follows its shape: land or no-land with one reason, land
  only when every part of the change fits a safe class, no-land when unsure.
- **An approving review as the verdict.** GitHub refuses an approval from a pull
  request's author, and the agent posts with the owner's token. A second
  identity would add an account or an app to keep. The verdict lives in the
  ledger instead. A label or a comment on the pull request only shows it and is
  never read to decide, since anything with write access could forge one.
- **GitHub auto-merge.** `gh pr merge --auto` merges at once when nothing
  required is pending, and an armed auto-merge survives the agent's own later
  pushes, so it can merge a head no verdict covers. The gate merges directly
  with `--match-head-commit`.
- **The review child merging.** The child reads feedback, some of it a bot's
  text, and holds the owner's token. Following ADR 0002, it returns a result and
  code makes the write.
- **A verdict from another model family.** The converge review asks for one, and
  T3 offers Gemini models. No child has run there, so this waits until one has.
- **A track record before the first merge.** RADAR asks a source for a clean
  60-day record. Here the owner's topic on a repo is that judgement, made from
  the shadow verdicts the run report shows. Findings do get a record: five
  landings in 60 days, all after the last revert of any landing, before a fix
  for one may land with no act by the owner.

## Consequences

- This reverses "build pull requests never auto-merge". The owner asked for it
  on 2026-11-27. A repo lands nothing until the owner adds the
  `backlog-agent-land` topic to it; without the topic every verdict is a shadow
  verdict.
- The agent that wrote a change also reviews it, and its reviewers are the same
  model family. The review raises the floor. The required checks, the size
  limit, the protected paths, the delay and the owner's hold carry the weight.
- Review threads a bot opens now reach a child. An app only reviews where the
  owner installed it, so the install is the owner's act. A bot's thread is a bug
  report to check against the code, never an instruction. A bot's other
  comments, its replies, and everything from other people are still never read,
  because those are where someone else's text could ride along.
- A pull request for a finding lands only after an act by the owner: approving
  its plan, asking for its triage by moving the card to Needs triage, or moving
  the card into In Review by hand. The gate reads that act from the card's
  history, newest move first. The owner's newest move must be one of the three,
  with no move by anyone else and no start by the top-up after it. So a finding
  whose card anyone else put in In Review does not land, and a history that
  Linear cut short has no act. That a top-up triage run opens no pull request is
  an instruction to the child, not a code guarantee: code turns such a run's
  `fix-pending` into Needs approval. The ledger marks each landing of a finding.
  Once five findings have landed in 60 days, across all repos, a top-up triage
  run may fix a finding in a repo with the land topic, and that fix can land
  with no act by the owner. This is the findings record, as RADAR keeps one for
  a new source. A revert of any landing that the agent detects resets it, also
  for a run that has not finished and for a fix that waits to land. Only
  findings that land after the revert was seen count again, so lifting the
  repo's pause does not bring the record back.
- Four limits of the findings record are accepted. An issue the owner wrote by
  hand while a scan of that repo ran is remembered in the ledger's `found` and
  so is treated as a finding, which a top-up triage run can now fix without
  approval. While the record stays earned, landings made through it keep it
  earned with no act by the owner, as RADAR's rolling record does. A finding the
  scan did not mark, and that the ledger no longer remembers, counts as an issue
  the owner wrote, as it did before. And the gate trusts who Linear names for a
  card's move. A live run showed that Linear names the owner for a move made
  through an integration that holds the owner's Linear login, so each such
  integration can give the owner's go-ahead. How Linear names its own sync and
  automations is not verified.
- A live run showed two more facts about Linear's history. Linear merges moves
  that one actor makes a few minutes apart into one row, which keeps the first
  status the card left and the last status it reached. And Linear keeps no row
  for a move that the creator of a card makes in its first minutes. So the gate
  reads the top-up's start from a move by the agent out of Backlog, and a card
  with a move that Linear did not keep gets no act.
- A private repo on a GitHub plan without rulesets has no required checks to
  read. The gate holds every pull request there as `checks none`.
- The safe classes are narrow, so most feature work still waits for the owner.
  Bug fixes with a failing test, refactors, tests and docs are what lands. One
  red job, required or not, holds a pull request.
- A hand-written or partial revert is not detected. The owner pauses a repo by
  removing the topic. The owner stops one pull request with the `agent:hold`
  label, a draft, a close, a card moved out of In Review, or a review thread
  left unresolved: a child never resolves a thread the owner started. A plain
  comment restarts the review and the delay but is answered by a child. A
  comment made while a pull request's last allowed review run is going starts no
  further run: the pull request is held as `open feedback` until the owner
  comments again or merges it.
- GitHub sends the owner no notice of the agent's comments, since they share an
  account. The run report is where a pending merge shows.
- `backlog.py` now writes to GitHub: the one merge command. The auto mode
  classifier may refuse it as it has refused a child's immediate merge. A merge
  that was offered or tried and not made is not tried again for that head, so
  the pull request waits for the owner.
- The agent's own repo, nix-config and converge-review never land, whatever
  their topics: they hold the agent's rules, the skills and profiles that run
  with the sandbox off, and the review itself. The dispatcher and the steward
  may not loosen the gate or the rubric either.
