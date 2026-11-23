# The agent writes to Linear as its own app, and only `backlog.py` writes

The agent reaches Linear through its own OAuth app, so every move it makes shows
up as the app's in a card's history. An approval only counts when that history
shows the owner moved the card from Needs approval to Approved. Child agents
never write to Linear: they return a result, and `backlog.py` makes every write,
the way the owner's GitLab triagebot script does.

## Considered Options

- **The owner's personal API key.** It is simpler to set up, but every move the
  agent made would look like the owner's. A bug or a prompt-injected issue could
  then approve its own plan, and the history could not tell the difference.
- **Children writing to Linear through the claude.ai connector.** Issue text
  reaches the children's prompts, so a child that can write to Linear can be
  steered into approving work.
