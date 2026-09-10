# APEX 24/7 Roadmap

## Phase 0 — Foundation

- repository structure
- configuration system
- logging
- typed domain models
- environment handling
- health checks
- error taxonomy
- test framework
- deterministic configuration validation

## Phase 1 — Market Data

- Binance adapter
- OKX adapter
- normalized market model
- WebSocket support
- REST fallback
- reconnect logic
- stale-data detection
- rate-limit handling

## Phase 2 — Market Intelligence

- 4H regime
- 1H structure
- 15M execution
- EMA
- RSI
- ADX
- RVOL
- VWAP
- pivots
- FVG
- volume profile
- trend/regime classifier
- pre-pump scanner

## Phase 3 — Signal Engine

- candidate generation
- candidate scoring
- confidence score
- liquidity filter
- overextension filter
- BTC regime filter
- R:R validation
- duplicate signal prevention

## Phase 4 — Risk Guardian

- authoritative equity
- max risk
- leverage limits
- position sizing
- stop validation
- exposure limits
- daily loss limit
- kill switch
- exchange-side safety validation

## Phase 5 — Execution

- order manager
- idempotency
- order expiry
- reconciliation
- fill tracking
- position state
- exchange error handling
- retry policy

## Phase 6 — Paper / Testnet

- paper engine
- deterministic replay
- testnet adapters
- simulation
- slippage model
- funding model
- execution audit

## Phase 7 — Observability

- PostgreSQL
- Redis
- event journal
- trade journal
- health API
- metrics
- Telegram notifications
- dashboards

## Phase 8 — Agent Committee

- planner
- strategy analyst
- risk analyst
- market-data auditor
- security auditor
- final deterministic veto

## Phase 9 — Production Hardening

- systemd
- restart policy
- backups
- disaster recovery
- secret management
- network hardening
- security audit
- load testing
- chaos testing

## Phase 10 — Live Trading Gate

LIVE TRADING MUST REMAIN DISABLED until:

- all critical tests pass
- testnet passes
- reconciliation passes
- kill switch passes
- risk limits pass
- security audit passes
- human approval is recorded

