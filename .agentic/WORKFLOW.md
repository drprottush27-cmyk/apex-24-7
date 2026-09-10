# APEX Agent Workflow

1. Planner defines a bounded task.
2. One implementer works in an isolated Git worktree.
3. Implementer runs tests.
4. Reviewer inspects the implementation.
5. Auditor checks safety/security invariants.
6. Human checkpoint.
7. Approved changes are landed into main.
8. Next task begins.

Never run two writers against the same worktree.
Never allow an AI agent to authorize live trading.
