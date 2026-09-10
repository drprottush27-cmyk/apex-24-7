# APEX Architecture

## Mission
APEX is a safety-first crypto market intelligence and paper-trading system.

## Initial scope
- Binance USDⓈ-M Futures market data
- OKX market data
- Multi-timeframe market scanner
- Pre-pump / momentum detection
- Telegram alerting
- Paper/testnet execution
- Deterministic risk controls
- Trade journal and audit trail

## Safety invariants
- DRY_RUN is the default.
- AUTO_EXECUTE is false by default.
- LIVE_TRADING_ENABLED is false by default.
- AI agents have no direct authority to execute trades.
- Risk Guardian has final veto authority.
- No strategy may bypass risk controls.
- No live API keys belong in source control.
- Every order decision must be auditable.
- Production/live trading requires an explicit human-controlled gate.

## Agent isolation
Each coding agent operates in its own Git worktree/branch.
No two agents should modify the same worktree simultaneously.
The main branch is only updated after review and verification.
