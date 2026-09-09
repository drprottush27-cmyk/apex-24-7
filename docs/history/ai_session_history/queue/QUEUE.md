# APEX Autonomous Engineering Queue

## Rules

Tasks execute sequentially.

Only one builder may modify the repository at a time.

Every task must follow:

DISCOVER
→ PLAN
→ IMPLEMENT
→ TEST
→ SECURITY REVIEW
→ REGRESSION
→ CHECKPOINT
→ COMMIT
→ STATE UPDATE

Human approval is NOT required for routine engineering.

Human approval IS required for:
- live trading
- production exchange connectivity
- secrets/API credentials
- disabling safety controls
- changing Risk Guardian veto authority
- unknown remote repositories
- destructive repository operations

## Completed (removed from queue — already done)

- [x] P0-2 — API authentication middleware
- [x] P0-3 — Wire portfolio equity to Risk Guardian
- [x] P1-4 — Initialize OrderManager on startup
- [x] P1-5 — Risk Guardian leverage in execution
- [x] P1-6 — Duplicate order protection
- [x] P1-7 — Fix broken tests and scripts
- [x] P1-8 — Restrict CORS
- [x] P1-9 — Add order expiry
- [x] P2-10 — Implement reconciliation loop
- [x] P2-10B — Deterministic autonomous engineering orchestrator
- [x] P2-11 — Add isolated testnet support
- [x] P2-12 — Integrate Ollama
- [x] P2-13 — Build agent committee
- [x] P2-14 — Implement safety modules (real kill switch)
- [x] P2-15 — Graceful shutdown
- [x] P2-16 — Create systemd units
- [x] P2-17 — Core engine stubs
- [x] P2-18 — Remove dead code
- [x] P2-19 — Consolidate clients
- [x] P2-20 — Add rate limiting
- [x] P2-21 — Implement remaining agents

## Current queue

1. P3 — Remaining hardening and production-readiness work
