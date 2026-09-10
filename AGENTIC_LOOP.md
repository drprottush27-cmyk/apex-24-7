# APEX / TRADE BRAIN — Agentic Development Loop

## Authority

`MASTER_BUILD_PROMPT.md` is the master specification.

All implementation, review, testing, and architectural decisions must remain consistent with the master specification and this agentic loop.

## Development Architecture

```text
OpenClaw Supervisor
        ↓
Orchestrator
        ↓
Isolated Git Worktree
        ↓
OpenCode / approved coding agent
        ↓
Tests
        ↓
Independent Review / Audit
        ↓
Human Approval
        ↓
Merge to main
        ↓
Checkpoint Commit / Tag
eof


