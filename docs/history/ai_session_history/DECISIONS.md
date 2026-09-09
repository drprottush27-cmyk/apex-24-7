# APEX Architecture Decisions

## Safety
Deterministic Risk Guardian has final veto authority.

## Execution
Paper trading is the default.

## Live trading
Production execution is disabled.

## AI
AI may analyze, review, propose, and modify code.
AI must never independently authorize real-money trading.

## CORS
Cross-origin browser access to the API is restricted to an explicit allowlist
(ALLOWED_ORIGINS). Default is empty = fail closed (no cross-origin). The CORS
wildcard "*" is never used.

## Development
Tasks are implemented incrementally and verified before completion.

## Context
Persistent state files are the source of continuity between AI sessions.

## P2-10: State Reconciliation Loop
- **Decision**: Reconciliation strictly detects and reports rather than attempting auto-correction.
- **Rationale**: Blindly correcting order state could be extremely dangerous (e.g. duplicating positions or silently closing orders in flight). A read-only reconciler prevents catastrophic mutation logic while providing deterministic discrepancy audits.

## P2-10B: Autonomous Engineering Orchestrator
- **Decision**: Run engineering tasks through a deterministic, persistent agent
  pipeline (PLANNER→BUILDER→TESTER→SECURITY_REVIEWER→FINAL_REVIEWER) with
  explicit state transitions, bounded retries, a single-builder lock, provider
  abstraction, and durable recovery. Workers only modify files they register;
  everything is committed locally one task at a time. P2-11 is not auto-started.
- **Rationale**: Deterministic state and durable recovery give interruption-safe,
  auditable engineering. A single-builder lock prevents concurrent writers
  corrupting a shared tree. A strict commit gate plus fail-closed safety vetoes
  (LIVE_TRADING_ENABLED, TRADING_MODE=LIVE, AUTO_EXECUTE, Risk Guardian veto
  weakening, secrets, CORS wildcard, unknown remotes) keep trading safety intact.
  P2-11 is only ever reached through an explicit operator-triggered queue-advance
  action, never implicitly.

## P2-12: Ollama Integration
- **Decision**: Add Ollama as an advisory-only provider in the existing `orch/`
  provider abstraction. Ollama may analyze, review, propose, and plan via
  advisory text; it has ZERO direct execution or trading authority.
- **Rationale**: Keeps agent-provider integration unified and consistent with
  the existing OpenCode/LocalFallback providers while strictly preserving the
  "AI may never independently authorize real-money trading" invariant. Config is
  read from the ambient environment (OLLAMA_URL/OLLAMA_MODEL) — never persisted,
  never treated as a secret. `available()` and `run()` both fail closed on any
  unreachable/malformed response. Tests inject an httpx.MockTransport so no real
  network is ever contacted.

## P2-13: Build Agent Committee
- **Decision**: Add an advisory agent committee (`orch/committee.py`) that runs
  several independent review roles through the existing provider abstraction and
  aggregates their verdict by a deterministic, fail-closed consensus rule. It is
  wired into the orchestrator's final-review gate as an opt-in advisory layer.
- **Rationale**: Multi-agent consensus strengthens independence (per the
  AGENT_PROTOCOL) without concentrating authority in a single reviewer. The
  committee is strictly advisory: it can only add REJECT / INCONCLUSIVE
  friction, and a committee APPROVE is never sufficient on its own — the
  deterministic SafetyReviewer remains authoritative. When no committee is
  configured the pipeline is byte-for-byte unchanged (backward compatible), and
  any unusable member (error/abstain/non-vote/provider failure) or unmet quorum
  fails closed to NOT-approved.

## P2-15: Graceful shutdown
- **Decision**: Add a deterministic, fail-safe shutdown sequence
  (`execution/safety/graceful_shutdown.py`) that always engages the emergency
  kill switch FIRST — an immediate, fail-closed execution halt — before stopping
  the market data feed and the trading orchestrator, and writing a best-effort
  CRITICAL `GRACEFUL_SHUTDOWN` audit trail. It is wired into the FastAPI
  lifespan teardown (so OS signals via uvicorn trigger the same sequence) and
  into a `POST /api/v1/shutdown` control-plane endpoint (requires explicit
  confirm).
- **Reverse-order guarantee**: The kill switch is engaged as the very first
  step, before any other cleanup. Rationale: once engaged, every order attempt
  is rejected regardless of whether the rest of the sequence fails or hangs, so
  the process can never be in a state where it is mid-teardown but still able to
  place orders. If any later step blocks, the switch is already engaged and the
  operator's halt demand is honored.
- **Bounded timeouts**: Each asynchronous cleanup step (`feed.stop`,
  `orchestrator.stop`) is wrapped in a bounded timeout
  (SHUTDOWN_TIMEOUT_SECONDS). Rationale: a hung WebSocket or loop must never
  prevent the process from exiting; once the kill switch is engaged, a forceful
  termination is safe.
- **Idempotency/concurrency**: Shutdown is idempotent and concurrency-safe —
  the first call drives the sequence; concurrent or repeat calls block on an
  internal lock and return the already-computed snapshot without re-running
  side-effects.
- **No safety alteration**: Shutdown never modifies DRY_RUN /
  LIVE_TRADING_ENABLED / Risk Guardian veto / advisory-only AI, and never
  auto-releases the kill switch. An operator who wants to resume trading must
  explicitly call `release()`.
- **Signal handling**: The lifespan teardown is the single canonical shutdown
  path; uvicorn's built-in SIGTERM/SIGINT handling drives it, so no custom
  signal override is needed (avoids clobbering uvicorn's graceful handling).

## P2-14: Safety modules — REAL kill switch
- **Decision**: Make the emergency kill switch a genuine, fail-closed
  execution-path VETO. The switch lives at the top of the single execution
  funnel (`OrderExecutionManager.execute_order`) so that EVERY order request —
  from the orchestrator, an engineering agent, a provider, or a direct caller,
  including reduce-only protective closes — is rejected while the switch is
  engaged. The `/api/v1/kill-switch` control-plane endpoint drives the real veto
  via the order manager instead of merely writing an audit log.
- **Hard-halt scope**: The kill switch blocks ALL order submission, including
  reduce-only protective closes. Rationale: this is the strongest, most
  unambiguous fail-closed guarantee — an operator commanding a full emergency
  halt expects nothing in the execution path to move money, and "protective
  closes allowed during a halt" would be a bypassable distinction. An operator
  who needs to exit a position uses the explicit `release` first.
- **Kill-switch rejection format**: Use `OrderStatus.REJECTED` with a
  `KILL_SWITCH:` message prefix rather than adding a new `OrderStatus` member,
  to avoid breaking DB CHECK-constraints and persistence (no schema/migration
  churn).
- **Position-state safety**: `close_position()` short-circuits when the close
  order is vetoed/rejected (status not FILLED/SUBMITTED), so a blocked close
  leaves the open position and dedup keys fully intact and journals no phantom
  realized PnL. Fail closed at the state level too.
- **Durability**: The kill-switch state is process-authoritative in-memory
  (restart resets runtime state; the audit trail is durable). This is acceptable
  because the control plane and the order manager share the process, so an
  engaged switch is immediately effective on all paths.
- **Fail-closed default**: The switch is always available; engaging halts; it
  never auto-releases; there is no arming/disarming flag that could weaken it.
  It stays advisory-neutral (blocks execution; cannot itself place/cancel/modify
  an order) and never bypasses the Risk Guardian — it is an additional outermost
  gate.

## P2-16: Systemd units — single hardened unit
- **Decision**: Add exactly ONE production systemd unit
  (`deployment/systemd/apex.service`) plus install docs (`INSTALL.md`) for the
  APEX control plane.
- **Why one unit**: APEX runs as one long-lived process (FastAPI/uvicorn). The
  TradingOrchestrator, OrderExecutionManager (kill switch), and GracefulShutdown
  all share it; `apps/` are empty stubs and PostgreSQL/Redis are Docker
  containers on 127.0.0.1. Per the "no unnecessary services" instruction, a
  second `.service` would be an unnecessary service. (A prior restart_all.sh
  mentions other services were not part of this task's scope.)
- **Non-root execution**: `User=apex`/`Group=apex` with an explicit
  `WorkingDirectory=/home/apex/apex` and no `ProtectHome`. Rationale: the control
  plane binds only 127.0.0.1 and is a single trusted process; running as the
  dedicated non-root `apex` user with least-privilege hardening reduces blast
  radius without breaking Python/networking.
- **No shell in ExecStart**: The unit executes the venv uvicorn binary directly
  (`ExecStart=/home/apex/apex/.venv/bin/uvicorn api.app:app --host 127.0.0.1
  --port 8000`) and `ExecStartPre` are harmless `/usr/bin/test ...` guards. No
  `sh -c`, no shell interpolation. Rationale: avoids a shell invocation surface
  and is compatible with hardening.
- **Secrets live outside systemd**: The unit has NO `Environment=`/`EnvironmentFile=`.
  The app loads its own gitignored `/home/apex/apex/.env` (pydantic-settings)
  from `WorkingDirectory`. Rationale: systemd cannot expose/exfiltrate
  credentials it never holds, and `ProtectSystem=strict` can still allow the app
  itself to read its config file.
- **Bounded restart**: `Restart=on-failure` + `RestartSec=5` and
  `StartLimitBurst=5`/`StartLimitIntervalSec=300` (in `[Unit]` where systemd
  requires them). Rationale: a resilient-but-bounded restart policy prevents both
  flapping and an uncontrolled fast restart loop after repeated failures.
- **Type=simple + bounded TimeoutStopSec=30**: normal SIGTERM semantics so
  uvicorn drives the FastAPI lifespan teardown -> GracefulShutdown (kill switch
  engaged first, fail closed), while a bounded stop timeout lets systemd escalate
  safely if the sequence hangs.
- **Hardening scoped to what works**: Applied best-effort hardening compatible
  with CPython/networking (ProtectSystem, PrivateTmp, NoNewPrivileges,
  ProtectKernel*, RestrictSUIDSGID, RestrictRealtime, RestrictAddressFamilies,
  CapabilityBoundingSet, LockPersonality, SystemCallArchitectures=native, UMask).
  Deliberately omitted because they would break the service: MemoryDenyWriteExecute
  (breaks CPython JIT/runtime), PrivateNetwork (the control plane reaches
  PostgreSQL/Redis/localhost Ollama), ProtectHome blocking needed files.
- **Not started at install**: The unit is added and documented but NOT enabled/
  started in this task — live-service start is out of scope and the safety
  rules forbid contacting production endpoints during development. Validation is
  via deterministic unit-content tests + `systemd-analyze verify` only. The
  existing legacy root `apex.service` is left untouched.

## P2-17: Core engine stubs — neutralize the unsafe AI auditor
- **Decision**: After inventorying the empty core-engine stubs (`apps/`
  entrypoints api/dashboard/scanner/execution/workers, `core/events/`, and most
  empty `engines/*`/`agents/*` subpackages), determine that none are on the
  runtime path and none are required by the current architecture — so they must
  NOT be implemented with placeholder functionality. The single genuine safety
  issue was the pre-existing `engines/agents/ai_auditor.py`, an audit stub that
  performed live network calls to Binance Futures public endpoints and Google
  Gemini, read an API credential from the environment, and produced a market
  verdict with NO Risk Guardian / Kill Switch / safety gate in its path. P2-17
  rewrote it in place as a deterministic, hermetic, advisory-only, fail-closed
  stub.
- **Why not wire the empty apps/engines/agents stubs**: They have zero callers
  and are explicitly documented (README) as forward-looking entrypoints/strategy
  placeholders. Implementing them would be inventing functionality per a generic
  trading-bot template — prohibited. The real operational entrypoints are
  `main.py`/`api.app:app` and the `scripts/` utilities, which are already wired.
- **Why neutralize `ai_auditor.py` in place (not delete/defer)**: The module is
  not on the api/core/main/execution runtime path, so the safe, minimal-change
  action is to make the existing class a safe stub while preserving its
  `TradingAgentsAuditor.audit_asset(symbol)` interface (the one importer,
  `telegram_bot.py`, consumes it for display text only). This removes the live
  network + credential + unsafety-gated-verdict surface without deleting a
  referenced module or touching the broader (out-of-scope) telegram control
  plane. Any future live market-data / live model-inference integration is a
  separate task requiring explicit human approval and must still flow through
  the existing safety chain (it must never route around the Risk Guardian veto /
  Kill Switch /
  endpoint isolation).
- **Fail-closed advisory-only contract**: With live context unavailable,
  `audit_asset` returns a deterministic `NEUTRAL AVOID` verdict and adds
  `advisory_only=True`, `live_market_data=False`, `model='deterministic-stub'`.
  It NEVER fabricates a BULLISH/BEARISH execution mandate, never reads or stores
  a credential, performs no network I/O, and its output structurally cannot be
  used as an `OrderRequest`/`TradeSignal`. Providers are injected only for
  hermetic testing; any provider error/non-dict/empty/network-refused outcome
  fails closed to NEUTRAL AVOID.

## P2-19: Consolidate Binance clients
- **Decision**: Collapse the duplicate Binance REST and WebSocket clients onto
  one EndpointGuard-backed REST client (`BinanceFuturesClient`) and one
  canonical WebSocket client (`BinanceFuturesWSClient`). Keep a compatibility
  REST subclass (`BinanceFuturesRESTClient`) and a WebSocket re-export
  (`BinanceFuturesWebSocket`) so existing imports stay valid.
- **Public vs private split**: Public market-data methods share the HTTP
  session and resolved base URL but NEVER call
  `assert_can_submit_order` and NEVER attach API credentials. Signed
  order/account methods still fail closed before any network I/O. Rationale:
  consolidating HTTP must not weaken P2-11 endpoint isolation.
- **WebSocket mapping**: Official USD-M REST→WS map lives on `EndpointGuard.ws_url()`
  (`fapi.binance.com` → `wss://fstream.binance.com/ws`,
  `testnet.binancefuture.com` → `wss://stream.binancefuture.com/ws`). An
  unrecognized REST host fails closed instead of guessing production streams.
- **Out of scope**: Redis is already a singleton pool. Telegram, journal, and
  notifier HTTP clients talk to different APIs and were left unchanged.
- **No safety alteration**: DRY_RUN default, LIVE disabled, AUTO_EXECUTE off,
  Risk Guardian veto, kill switch, CORS, and signed-order isolation remain
  unchanged. No live trading, no secrets, no remotes.
