# APEX Project State

Phase: Execution & Risk Control (P1/P2)
Current milestone: Hardening order manager, safety gates, and state tracking;
autonomous-engineering orchestrator (`orch/`) operational.

Architecture:
- Execution: `OrderExecutionManager` coordinates DB/Redis/Memory/Exchange.
- Reconciliation: `StateReconciler` deterministically monitors DB, Redis, and Memory mismatch.
- Safety: `RiskGuardian` ensures limits are respected; Duplicate Protection prevents overlapping orders; Expiry drops stale pending.
- API: FastAPI with restricted CORS and API Key Auth.
- Orchestrator: `orch/` — deterministic, persistent agent pipeline
  (PLANNER→BUILDER→TESTER→SECURITY_REVIEWER→FINAL_REVIEWER) with bounded
  retries, single-builder lock, durable recovery, provider abstraction, and a
  trading-safety commit gate (enforces DRY_RUN default, LIVE disabled,
  AUTO_EXECUTE off, Risk Guardian veto authority, fail-closed CORS).
- Providers: `orch/provider.py` abstraction with OpenCode (dry adapter),
  Ollama (advisory-only local LLM), and a deterministic LocalFallbackProvider
  for offline/tests. Ollama has ZERO direct execution/trading authority — it
  only feeds advisory text to the engineering orchestrator.
- Committee: `orch/committee.py` — advisory agent committee (P2-13). Multiple
  independent review roles are run through the provider abstraction and their
  votes are aggregated by a deterministic, fail-closed consensus rule. A
  committee APPROVE is never sufficient on its own; it can only add REJECT /
  INCONCLUSIVE friction to the deterministic gates. Fully opt-in: the pipeline
  is unchanged when no committee is configured.

Stability: High. 408 tests passing, 1 skipped, 0 failures. No live-trading enabled.

Core engine stubs (P2-17, recent): inventoried the empty core-engine stubs.
The `apps/` entrypoint package (api, dashboard, scanner, execution, workers),
`core/events/`, and most empty `engines/*` and `agents/*` subpackages are NOT
on any runtime path and have no callers — they were left untouched because
implementing them would be dummy functionality with no requirement (the real
operational entrypoints are `main.py -> api.app:app`, `scripts/scan_coins.py`,
`scripts/track_coins.py`, `scripts/desk_ctl.py`). The ONE unsafe stub was
`engines/agents/ai_auditor.py`, a pre-existing auditor that made live Binance
Futures + Google Gemini network calls and read a credential from the
environment with no Risk Guardian / Kill Switch / safety gate in its path (it
is not on the api/core/main/execution runtime path; only a standalone
`telegram_bot.py` control-plane imports it, using its decision purely as
display text). P2-17 rewrote `ai_auditor.py` in-place as a deterministic,
hermetic, advisory-only, fail-closed stub: no network, no credential access,
deterministic NEUTRAL AVOID verdict, output structurally incapable of becoming
an order. 12 hermetic focused tests; full suite 286 passed / 1 skipped.
P2-18 dead-code cleanup (COMPLETE): performed a proof-before-delete inventory
of the proposed dead-code candidates. The initial deletion sweep was rejected
as unsafe because it included required package __init__.py files, the legacy
root systemd unit referenced by restart_all.sh/deployment documentation, the
manual run_desk.sh recovery/startup script, the Binance WebSocket production
adapter, ORM schema models, and the SignalStatus model. All such files were
restored. The only proven dead tracked file was setup_working_auditor.py:
an obsolete one-off generator with no current repository references that
contained superseded live Binance/Gemini setup behavior. It was removed.
The final working tree contains no other tracked P2-18 code changes.
Final suite after P2-18: 286 passed / 1 skipped / 0 failed.

P2-20 rate limiting (COMPLETE, semantics corrected): added fail-closed
rate-limit protection to the Binance USD-M Futures REST client via a single
authoritative `_process_rate_limit_response` path. Weight tracking is shared
process-level across all client instances. HTTP 418 is the global IP-ban
cooldown — the exclusive writer of the shared `_cooldown_until` (valid bounded
Retry-After else default 300s) — which blocks ALL subsequent public and signed
requests fail-closed and is never retried. HTTP 429 is distinct and never
writes the global cooldown; a public idempotent GET retries at most once only
with a valid bounded Retry-After (upper bound 60s), actually honoring the
requested delay via a patchable `_sleep`/clock abstraction; the second 429 is
terminal. Signed/mutating requests are never auto-retried; EndpointGuard remains
before every signed network I/O. 38 hermetic focused tests; full suite
336 passed / 1 skipped / 0 failed. Records: "add P2-20 rate limiting" then
corrective "fix P2-20 rate-limit semantics".
P2-21 (Implement remaining agents) COMPLETE: implemented 7 advisory-only,
deterministic, fail-closed trading agents under `agents/`:
- `agents/technical/technical.py` — TechnicalAnalysisAgent (trend, momentum,
  RSI, ATR, BB from OHLCV data)
- `agents/market_radar/market_radar.py` — MarketRadarAgent (multi-symbol
  screening: volume spikes, RSI extremes, breakouts, regime changes)
- `agents/research/research.py` — ResearchAgent (multi-factor research
  compilation: trend, momentum, volatility, volume, regime, external context)
- `agents/risk_guardian/advisory.py` — RiskAdvisoryAgent (supplementary risk
  commentary wrapping the deterministic RiskGuardian, no veto authority)
- `agents/social/sentiment.py` — SocialSentimentAgent (sentiment aggregation,
  score clamping, invalid data dropped fail-closed)
- `agents/derivatives/derivatives.py` — DerivativesAgent (funding rate bias,
  OI trend, leverage estimation from local data)
- `agents/committee/advisory.py` — AdvisoryCommitteeAgent (local advisory
  committee consensus voting, fail-closed aggregation)
All agents are advisory-only (no execution authority), deterministic, and
fail-closed to neutral/safe outcomes on invalid data. No network calls, no
credential access. 72 hermetic focused tests; full suite
408 passed / 1 skipped / 0 failed.

P2-19 client consolidation (COMPLETE): collapsed duplicate Binance REST and
WebSocket clients onto one EndpointGuard-backed REST client and one canonical
WS client. Public market-data methods share HTTP/URL resolution without the
order/account gate and without API credentials. Signed methods remain
fail-closed. Compatibility facades preserve historical import names.
Full suite: 298 passed / 1 skipped / 0 failed.

Systemd deployment (P2-16, recent): added a hardened production systemd unit at
`deployment/systemd/apex.service` with install docs (`INSTALL.md`). Only ONE
unit is justified — APEX is a single long-running FastAPI/uvicorn process (the
TradingOrchestrator, OrderExecutionManager, kill switch, and GracefulShutdown
all share that one process; `apps/` are empty stubs and DB/Redis run as Docker
containers on 127.0.0.1). The unit runs as non-root `apex`, uses an explicit
`WorkingDirectory=/home/apex/apex`, executes the venv uvicorn directly (no
shell), holds no secrets (no Environment=/EnvironmentFile=; the app loads its
own gitignored `.env` at runtime), implements bounded restart (`on-failure` +
`StartLimitBurst=5`/`StartLimitIntervalSec=300`) to prevent uncontrolled
restart loops, and applies least-privilege hardening that is compatible with
CPython and networking (`ProtectSystem=strict`, `NoNewPrivileges`,
`PrivateTmp`, `ProtectKernel*`, `RestrictSUIDSGID`, `RestrictRealtime`,
`RestrictAddressFamilies`, `CapabilityBoundingSet=`, `LockPersonality`,
`SystemCallArchitectures=native`, `UMask=0077`). Graceful shutdown runs on
SIGTERM via uvicorn -> FastAPI lifespan teardown -> GracefulShutdown
(`TimeoutStopSec=30`). `systemd-analyze verify` clean; 17 focused tests.
P2-16 is COMPLETE; P2-17 core engine stubs, P2-18 dead-code cleanup, and
P2-19 client consolidation are COMPLETE.

Graceful shutdown (P2-15, recent): added `execution/safety/graceful_shutdown.py`
with `GracefulShutdown` — a deterministic, fail-safe shutdown orchestrator.
The sequence ALWAYS engages the emergency kill switch FIRST (immediate
execution halt, fail closed), then stops the market data feed (bounded
timeout so a hung WebSocket never blocks exit), stops the trading
orchestrator, and writes a best-effort CRITICAL `GRACEFUL_SHUTDOWN` audit
trail. Shutdown is idempotent and concurrency-safe. Wired into the FastAPI
lifespan teardown (so SIGTERM/SIGINT via uvicorn triggers the same sequence)
and into a new control-plane endpoint `POST /api/v1/shutdown` (requires
explicit confirm) plus `GET /api/v1/shutdown/status`. It never alters
DRY_RUN / LIVE_TRADING_ENABLED / Risk Guardian veto / advisory-only AI, and
never auto-releases the kill switch. P2-16 (systemd units) is now COMPLETE.

Kill switch (P2-14, recent): added `execution/safety/kill_switch.py` with
`KillSwitch` + `KillSwitchEngaged` — the REAL emergency execute-path veto. The
`OrderExecutionManager` carries a `kill_switch` instance and `execute_order()`
(the single funnel every order request — orchestrator, agent, provider, direct
call, reduce-only close — must pass through) rejects with `OrderStatus.REJECTED`
and a `KILL_SWITCH:` message at its very top when the switch is engaged; there
is no bypass. `close_position()` short-circuits a vetoed close so the open
position stays fully intact (no phantom PnL/journal). Control-plane endpoints
(POST /api/v1/kill-switch, /kill-switch/release, /kill-switch/status) engage,
release, and report the switch through the real manager. P2-15 was the next
eligible queue task and is now NOT started (done).

Agent committee (P2-13, recent): added `orch/committee.py` with `ReviewerVote`,
`CommitteeVerdict`, and `AgentCommittee`. Default members (SECURITY_REVIEWER,
FINANCIAL_RISK_REVIEWER, DESIGN_REVIEWER) each run the same prompt through the
provider abstraction under a distinct role. Aggregation fails closed: fewer than
quorum usable votes, any unusable/erroring/ABSTAIN member that breaks quorum, or
any REJECT yields INCONCLUSIVE/REJECTED (NOT approved). Integrated into the
orchestrator's final-review gate as an advisory layer: when a committee is
configured its issues are folded in (can only block), but an APPROVE adds no
positive authority and the deterministic SafetyReviewer remains authoritative.
Tests use injected deterministic members; the offline registry keeps them
hermetic. P2-14 was the next eligible queue task and is now NOT started (done).

Provider abstraction (P2-12, recent): added `OllamaProvider` — an advisory-only
local LLM backend via the Ollama HTTP API, configured by ambient `OLLAMA_URL` /
`OLLAMA_MODEL` (never persisted). `available()` does a bounded, fail-closed
reachability probe; `run()` returns advisory `ProviderResult` text and never
raises on a clean outcome. Registered in the default (non-offline) registry.
Tests inject an `httpx.MockTransport` for full hermeticity. P2-13 is the next
eligible queue task and is NOT started.

Control-plane repair (recent): reconciled `.ai/queue/QUEUE.md` and hardened
`orch.orchestrator` queue selection so an already-completed roadmap task (e.g.
P1-9) can never be re-enrolled. Cleared the accidental P1 enrollment via the
sanctioned `EngineStateStore`.

Orchestrator integrity repair: added deterministic fail-closed guards so a task
can never reach COMMITTED without genuinely executing every required stage.
IMPLEMENT requires a real non-.ai application diff; TEST / SECURITY REVIEW /
REGRESSION set evidence flags and increment counters on every run (pass or
fail); FINAL_REVIEW cannot approve skipped stages; the commit gate requires all
evidence, counters >= 1, a real diff, and rejects state-only commits; a
failed/missing provider result can no longer be reported as success.
