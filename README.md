# APEX 24/7

Deterministic, safety-first autonomous crypto-futures **PAPER** trading research
system. Strictly a research environment: **production/live trading is permanently
out of scope** and is proactively blocked by architecture and code validation.

## Safety Model

Three development modes are supported. Everything else is rejected.

| Mode | Data | Execution | Purpose |
|------|------|-----------|---------|
| `DRY_RUN` | real/synthetic closed candles | none | signal-only research |
| `SHADOW` | real/synthetic closed candles | simulated | shadow tracking |
| `PAPER` | synthetic closed candles | paper fills | full lifecycle research |

Invariants enforced by code:

1. **RiskGuardian** has final veto authority over every order intent.
2. **EndpointGuard** isolates endpoints — production trading requests blocked.
3. **Kill switch** is fail-closed and never blocks flattening exits.
4. Startup safety checks must pass before autonomous operation.
5. **AI/LLM output is advisory only** — never authorizes an order and never
   determines risk parameters.
6. Closed candles/snapshots only; no lookahead.
7. Every order intent follows: Signal → RiskGuardian → OrderExecutionManager →
   EndpointGuard → PAPER execution. No bypass surface on the engine.
8. `ApexConfig` hard-rejects any `LIVE`/`PRODUCTION`/`REAL` mode or
   `live_trading_enabled=True`; the run scripts abort on such values.
9. Crash recovery replays the persistent journal; the idempotency guard rejects
   duplicate events across restarts.

## Repository Layout

```
src/apex/
  config/        ApexConfig, constants (strict mode validation)
  domain/        Candle, Signal, OrderIntent, Position, types
  safety/        KillSwitch, EndpointGuard, IdempotencyGuard, exceptions
  risk/          RiskGuardian (veto authority), PortfolioState
  execution/     OrderExecutionManager + PAPER-only adapters
  market/        MarketClient, quality gate, observations (read-only)
  indicators/    EMA, ATR, RSI, ADX, RVOL, BB, pivots
  engines/
    prepump/     deterministic 2-of-3 pre-pump detector
    tactical/    tactical confluence analytics (advisory)
  runtime/       engine, orchestrator, scanner, scheduler, position tracker,
                 danger protocol, crash recovery, health, observability
  persistence/   PersistentJournal (SQLite replayable), recovery
  journal/       TradeJournal (append-only SQLite), ClosedTradeRecord
  learning/      PerformanceAnalyzer + ProposalGenerator (review-only)
scripts/
  run_paper.py    deterministic offline autonomous PAPER session
  run_service.py  foreground 24/7 PAPER service (see docs/OPERATIONS.md)
tests/
  unit/          phase-gated unit suites (Phases 1–15)
  e2e/           full-lifecycle integration + failure injection
```

## Quick Start

```bash
# All commands assume the project venv is active:
#   source .venv/bin/activate  (or use .venv/bin/python, .venv/bin/pytest, ...)

# Full test + static verification
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
.venv/bin/mypy src
.venv/bin/mypy tests
git diff --check

# Deterministic offline autonomous PAPER session
.venv/bin/python scripts/run_paper.py --ticks 200 --data-dir /tmp/apex_run

# Foreground 24/7 PAPER service (offline by default)
.venv/bin/python scripts/run_service.py --ticks 0 --data-dir /tmp/apex_svc
```

The run scripts abort on any `APEX_TRADING_MODE` of `LIVE`/`PRODUCTION`/`REAL`.

## Verification Status

Last verified (Phase 15):

- `pytest -q`: **601 passed** (0 failed, 0 skipped, 0 xfail)
- `ruff check src tests`: clean
- `mypy src`: clean (strict, 73 files)
- `mypy tests`: clean (strict, 35 files)
- `git diff --check`: clean

## Documentation

- `.ai/PROJECT_STATE.md` — implementation status per phase
- `.ai/TODO.md` — phase checklist and follow-up work
- `.ai/AGENT_PROTOCOL.md`, `.ai/DECISIONS.md` — agent handoff + decisions
- `docs/OPERATIONS.md` — running and monitoring the 24/7 PAPER service
- `.ai/FOUNDATION_AUDIT.md`, `.ai/FINAL_NARROW_AUDIT_REPORT.md`,
  `.ai/FORENSIC_CHECKPOINT_REPORT.md` — audit records