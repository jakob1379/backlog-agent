# The triage state machine lives in the agent, not in Linear

Which column a card may move to, who may move it, and when a run starts are rules in
`backlog.py`, covered by its unit tests. Linear only shows the board and is where the owner moves
cards. We considered building the flow from Linear's own workflow automations and cycles. That
would have left the rules untested and tied the agent to Linear for good. The owner wants the
agent to work with other trackers later, so all calls to Linear go through one module, and
porting means rewriting that module only.

## Consequences

- The agent only notices a move on its hourly run, so an approval can wait up to an hour
  before its build run starts.
- A status or label renamed in Linear breaks the mapping until the module is updated.
  `collect` fails loudly when a status it expects is missing, rather than guessing.
