# APEX 24/7 — Agent Constitution

## Mission

Build APEX 24/7 as a deterministic, safety-first autonomous
crypto-futures PAPER/SHADOW trading research system.

The system must never create or provide a production/live trading
shortcut.

## Repository Boundary

Primary repository:

    /home/apex/apex

Do not modify or clone unrelated repositories.

## Trading Safety

Allowed development modes:

- DRY_RUN
- SHADOW
- PAPER

Production/live trading is permanently out of scope.

The following principles are mandatory:

1. Risk Guardian has final veto authority over every order intent.
2. Endpoint isolation must prevent production trading requests.
3. Kill switch must be fail-closed.
4. Startup safety checks must pass before autonomous operation.
5. AI/LLM output is advisory only.
6. AI must never authorize an order or determine risk parameters.
7. No credential generation.
8. No production API credentials.
9. No live exchange orders.
10. No safety bypasses for testing.

## Engineering Rules

- Inspect before implementing.
- Prefer deterministic logic for trading decisions.
- Closed candles/snapshots only.
- No lookahead.
- No silent exception swallowing.
- No weakening or skipping failing tests.
- Preserve existing architecture when it exists.
- One agent edits the repository at a time.
- Keep changes scoped to the current assigned phase.
- Run relevant tests before declaring a phase complete.
- Update `.ai/` project documentation after meaningful architectural changes.

## Risk

Risk must be calculated from current equity and fixed policy.

Target deficits, winning/losing streaks, or emotional objectives must
never increase risk.

4x paper-equity growth is informational only and must never become a
risk input.

## Execution

Every order intent must follow the same safety path:

Signal
 -> Risk Guardian
 -> Order Execution Manager
 -> Endpoint Guard
 -> Allowed PAPER/SHADOW execution

Emergency exits must use the same underlying safety path.

## Autonomous Controls

The system must support:

- kill switch
- pause/resume
- reconciliation
- duplicate-event protection
- crash recovery
- data-health gating
- execution-health gating
- correlation/exposure limits
- hard SL/TP

## Learning

Learning is proposal-only.

The system may analyze historical journal data and produce proposals,
but may not autonomously:

- loosen risk limits
- increase leverage
- reduce safety thresholds
- modify production configuration
- modify code
- activate a learned strategy

Human approval is required for strategy adoption.

## Agent Handoffs

Every implementation phase must leave:

- tests
- documentation
- Git checkpoint
- `.ai/PROJECT_STATE.md`
- clear remaining TODOs

Agents must stop when their assigned phase is complete.
