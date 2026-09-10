# APEX 24/7 — Agent Operating Contract

## Mission

Build a production-grade, safety-first crypto market intelligence and
execution platform.

The system must prioritize:

1. capital preservation
2. deterministic risk controls
3. honest data states
4. reproducibility
5. testability
6. observability
7. graceful failure
8. human approval before dangerous actions

## Critical Rule

NO agent has authority to enable live trading.

Agents may:

- inspect
- design
- implement
- test
- simulate
- audit
- document

Agents must NOT:

- enable live trading
- create exchange API credentials
- expose secrets
- bypass Risk Guardian
- weaken authentication
- remove safety checks to make tests pass
- silently change risk limits
- silently change leverage limits
- merge directly into main
- modify another teammate's worktree

## Git Rule

Main branch is protected by process.

Every implementation task must use:

    orchestrator/<role>

and an isolated Git worktree.

A reviewer NEVER edits the implementation worktree.

A coder NEVER edits the reviewer's worktree.

Integration happens only after review.

## Agent Roles

planner
    Architecture and task decomposition.
    No implementation.

builder
    Implementation in an isolated worktree.

tester
    Tests, regression analysis and failure reproduction.

reviewer
    Read-only code review and security review.

security-auditor
    Authentication, authorization, secrets, attack surface.

trading-auditor
    Trading logic, risk controls and execution safety.

integration
    Final merge preparation only.

## Trading Safety

Default:

    DRY_RUN=true
    AUTO_EXECUTE=false
    LIVE_TRADING_ENABLED=false

No agent may change those defaults without explicit human approval.

## Exchange Credentials

Never commit:

- API keys
- API secrets
- passphrases
- Telegram bot tokens
- private keys
- wallet seed phrases
- cookies
- OAuth secrets

Use environment variables or secret stores.

## Risk Guardian

Risk Guardian must be deterministic.

AI may propose a trade.

AI may NOT override the deterministic final veto.

Execution path:

    Strategy
        |
        v
    Signal
        |
        v
    Risk Guardian
        |
        +---- REJECT
        |
        v
    Execution Safety
        |
        v
    Order Manager
        |
        v
    Exchange

## Testing Rule

Every implementation task must include:

- unit tests
- integration tests where applicable
- negative tests
- failure-path tests

Never remove a test because it fails.

Fix the implementation or explicitly document why the test is invalid.

## Data Honesty

Never invent market data.

When data is unavailable:

    DATA_UNAVAILABLE

When data is stale:

    DATA_STALE

When a provider is disconnected:

    PROVIDER_DISCONNECTED

Never display fabricated balances, prices, signals or performance.

## Completion Rule

A task is complete only when:

1. implementation exists
2. tests exist
3. tests pass
4. reviewer has reviewed
5. security impact is assessed
6. Git diff is clean enough to merge
7. checkpoint/commit exists
8. task state is updated

## Stop Conditions

Immediately stop and report if:

- tests regress unexpectedly
- security controls change unexpectedly
- live trading becomes enabled
- secrets are discovered
- destructive commands are required
- architecture conflicts with the safety contract
- an agent attempts to modify another agent's worktree
