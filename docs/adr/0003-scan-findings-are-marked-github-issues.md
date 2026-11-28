# Scan findings are GitHub issues marked as the agent's

A scan files each finding as a GitHub issue whose body starts with
`<!-- backlog-agent -->`. Everything after that is the existing path: the issue
gets a card, waits in Backlog, and reaches a plan and the owner's approval
through the top-up or the owner's own move. A finding's fix lands only after an
act by the owner, or on the findings record (ADR 0004). This is the first time
the agent creates issues.

The marker is needed because children post with the owner's token, so GitHub
shows the owner as the author. Without it, `plan` would send the issue straight
to triage as a new issue by the owner, outside the top-up's limit, and a triage
child would read the scanner's text as the owner's.

## Considered Options

- **Cards with no issue.** A plan is a comment on an issue and a build run's
  spec is that comment, so a finding with no issue has nowhere to carry a plan.
- **A second GitHub identity for the scanner.** Its issues would wait for the
  owner with no change to `plan`, but it adds an account and a token to keep.
- **A `scan` label.** Labels are already in the snapshot, but the agent already
  marks its comments with this line, and an issue filed with a missing label
  fails the same way as one with a missing marker.

## Consequences

- An issue the owner writes that starts with the marker line, or that the owner
  files in a repo while a scan of it runs is treated as a finding: it waits for
  the top-up and counts toward the limit of three. `plan` applies the second
  rule so that a child that forgets the marker cannot get its issue triaged as
  the owner's.
- The marker only counts on an issue the owner authored, so nobody else can fill
  the limit of three or mark an issue as the agent's.
- Findings on a public repo are public. A scan child keeps security findings out
  of issues there and ends blocked.
- A finding the owner rejects stops counting when its card is Canceled; the
  GitHub issue stays open until someone closes it.
