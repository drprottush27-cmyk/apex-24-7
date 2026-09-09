# APEX 24/7 — Project State & Architectural Record

**Last Updated:** 2026-09-09  
**Current Phase:** Source Tree Reconciliation & Invariant Verification Complete  
**Working Mode:** Deterministic Autonomous PAPER/SHADOW Trading Research System  
**Live Trading Invariant:** Permanently Prohibited (`live_trading_enabled=False`)  

---

## 1. Architectural Foundation

The codebase has undergone a complete, evidence-based reconciliation unifying Tree A (development quant algorithms, indicators, storage schemas) and Tree B (15-phase fail-closed safety architecture) into a canonical structure under `src/apex`.

### Core Directory Layout
- `src/apex/`: Authoritative package source.
  - `config/`: Safety constants, immutable ceilings, strongly typed `ApexConfig`.
  - `domain/`: Pure domain types (`Candle`, `Signal`, `OrderIntent`, `Position`).
  - `indicators/`: Core indicators (ATR, EMA, RSI, SFP, Fair Value Gap, MACD).
  - `engines/`: Technical analysis engines (including MTF Sniper Engine).
  - `market/`: Read-only market data feeds, normalization, exchange filter cache.
  - `risk/`: Authoritative `RiskGuardian` with non-negotiable mathematical veto.
  - `safety/`: `KillSwitch`, `EndpointGuard`, `IdempotencyGuard`, danger protocols.
  - `execution/`: `OrderExecutionManager` (OEM), paper execution adapters.
  - `runtime/`: Autonomous orchestrator, scheduler, paper trading loop.
  - `persistence/`: SQLite WAL engine journal.
  - `api/`: Local-only REST/WebSocket API control plane server.
  - `telegram/`: Zero-execution command router and fail-closed alert dispatcher.
- `webapp/`: Responsive web control plane and mini app UI.
- `tools/`: MCP test bridge (`binance_mcp_bridge.py`) and orchestrator tools (`orch/`).
- `infrastructure/`: Enterprise storage tier (`postgres/`, `redis/`).
- `scripts/`: Operational tools, CLI launchers, and legacy scripts (`scripts/legacy/`).
- `tests/`: 48+ test modules spanning unit, integration, and full lifecycle e2e tests.

---

## 2. Safety Invariants Status

- **Risk Guardian:** Absolute veto authority over all order intents. Cannot be relaxed by AI advisory confidence.
- **Endpoint Guard:** Enforces strict whitelist (`approved_endpoints`); blocks all production Binance endpoints.
- **Kill Switch:** Fail-closed design. Trips prevent new entries; cancellations and exit orders remain executable.
- **AI Advisory Boundary:** Advisory only; zero order placement authority; execution tools strictly rejected.
- **Credential Sanitization:** No production credentials in source; `.env` git-ignored; `.env.example` provides safe defaults.

---

## 3. Test Suite Verification

- **Total Test Cases:** 821 collected
- **Passed:** 820
- **Failed:** 0
- **Skipped:** 1 (external host directory check safely skipped)
- **Execution Time:** ~10.0s via `.venv/bin/pytest tests/ -v`

---

## 4. Git Checkpoint History

- `0f52c96`: `checkpoint(security-tests): add sanitized .env.example with paper defaults and verify full 820 test suite pass`
- `b6c795a`: `checkpoint(ui-telegram): verify mini app, webapp bindings, and telegram integration without host path dependencies`
- `04685ba`: `checkpoint(mcp-integrations): implement test bridge adapter and verify ai boundary with fail-closed safety`
- `7d4fd44`: `checkpoint(quant-risk-exec): merge FVG, MACD, MTF Sniper, and BinanceExchangeFilterCache with comprehensive unit tests`
- `3b216eb`: `checkpoint(integration): reconcile directory structure and establish src/apex foundation with legacy preservation`

---

## 5. Next Steps & Remaining Roadmap

1. **Remote Synchronization:** Push the 5 audited local commits to `origin/main` upon operator authorization.
2. **Paper Trading Simulation:** Run autonomous paper trading daemon in continuous SHADOW/PAPER mode.
3. **Journal & Learning Verification:** Verify proposal-only learning modules against synthetic multi-day journal data.
4. **Mini App Deployment:** Host localhost webapp interface for desktop/mobile paper monitoring.
