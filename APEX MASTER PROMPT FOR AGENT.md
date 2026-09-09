# APEX Mini App — Master Execution Prompt (Grounded Audit Edition)

**Read this whole file before touching code.** This supersedes
`APEX_TELEGRAM_FULL_AUDIT_FOR_CLAUDE.md/.txt` — that file was a generic
37-section spec written *before* anyone actually opened the repo. This file
was written *after* a real audit of the actual codebase (`APEX_CLEAN_PROJECT_FOR_CLAUDE.zip`).
Use this as your primary source of truth; use the original spec only for its
non-negotiable safety rules (carried forward below) and its section
numbering as a checklist.

---

## 0. WHY THIS FILE EXISTS

A prior chat-based audit (done in a non-persistent chat environment, no git,
no long-running execution) confirmed the following **ground truth** by
reading actual source, not documentation or the AGY report. You are picking
up from here. Do not re-litigate what's already verified below — spend your
budget on the unverified parts (Section 3) and the actual implementation
work (Section 5).

---

## 1. VERIFIED GROUND TRUTH — SAFETY ARCHITECTURE (confirmed by reading code)

- `src/apex/config/settings.py`: `live_trading_enabled: bool = False`,
  hard-rejected by a `field_validator` — any `True` value raises
  `"CRITICAL SAFETY VIOLATION: live_trading_enabled is permanently
  prohibited."` Mode strings `LIVE`/`PRODUCTION`/`REAL` are also rejected.
  **This is real code enforcement, not just docs.**
- `src/apex/safety/kill_switch.py`: fail-closed, thread-safe, immutable
  state snapshots (`KillSwitchState` dataclass, frozen). Activation always
  possible; flattening pathways are documented as never depending on
  disabling it (verify this claim in `execution/oem.py` — not yet
  independently traced end-to-end).
- `src/apex/api/server.py` (1854 lines, stdlib `http.server`, not FastAPI):
  - `do_PUT`, `do_DELETE`, `do_PATCH` unconditionally return
    `405 METHOD_NOT_ALLOWED` with "Apex API is read-only."
  - `do_POST` only accepts a whitelist: `/control/start`, `/control/stop`,
    `/control/pause`, `/control/resume`, `/control/restart`,
    `/control/emergency_stop`, `/control/flatten`, `/alerts/ack`,
    `/autoclose/override`, `/alerts/settings` (`/toggle`),
    `/control/intelligence`. Everything else on POST is rejected with
    "POST is strictly prohibited for non-control operations."
  - Full `do_GET` route table (this is the authoritative list of what the
    frontend may consume — do not invent new backend truth in JS):
    `/health`, `/status`, `/risk`, `/account` (`/balance`), `/positions`,
    `/autoclose`, `/realtime`, `/signals`, `/dashboard`, `/plan`,
    `/auto-trade`, `/telegram`, `/alerts` (`/alerts/settings`), `/trades`
    (`/history`), `/control/status`, `/team`, `/investment`, `/reports`,
    `/audit`, `/control/intelligence` (`/intelligence`), `/ui` (`/app`,
    `/command-center`), `/assets/*`.
  - **There is no `/ws/chat` or any WebSocket route anywhere in
    `server.py`.** The server is a synchronous `BaseHTTPRequestHandler` —
    no async, no socket upgrade handling of any kind.
- Repo layout confirms modular safety separation exactly as `README.md`
  claims: `config/`, `domain/`, `safety/`, `risk/`, `execution/`, `market/`,
  `indicators/`, `engines/{prepump,tactical}`, `runtime/`, `persistence/`,
  `journal/`, `learning/`. This matches the "single execution pathway,
  single risk authority" claim structurally — not yet verified line-by-line
  that nothing in `runtime/` bypasses `risk/guardian.py`.
- 44 test files exist under `tests/unit/` + `tests/e2e/` + `tests/test_phase4_runtime.py`.
  **Nobody has actually run them yet in this audit.** Do not trust any
  claimed pass count from the AGY report until you run
  `pytest tests/ -v` yourself and paste the real output into the handoff
  report.
- No `.git` directory shipped in the zip (`APEX_CLEAN_PROJECT_FOR_CLAUDE.zip`
  was stripped of git history). **You must `git init` and make your first
  commit a clean baseline before changing anything**, or git-diff-based
  review (spec section 35) is impossible.

## 2. VERIFIED GROUND TRUTH — FRONTEND (`webapp/`)

- `index.html` (946 lines) already implements all 9 sections the spec
  asks for: `home-screen` (Command/Overview), `scanner-screen` (Market
  Radar), `trades-screen`, `risk-screen`, `system-screen`, `team-screen`,
  `investment-screen`, `alerts-screen`, `audit-screen`. **This is not a
  blank slate — do not rebuild from scratch.**
- Master Control Bar already exists (`master-control-bar` section) with
  START/PAUSE/RESUME/STOP/FLATTEN/EMERGENCY STOP buttons, a state dot, mode
  pill, equity pill, risk pill.
- Dangerous actions already gated with `confirm()` in `app.js` (lines
  ~1718-1724): STOP, FLATTEN, EMERGENCY STOP all show a native confirm
  dialog with explicit text before dispatching. Spec section 29 is already
  substantially satisfied — verify the confirm copy is good enough, don't
  assume it needs replacing wholesale.
- `style.css` (2060 lines): dark navy institutional palette already in
  place (`--bg: #090d14`, `--accent: #2563eb`, `--positive: #10b981`,
  `--danger: #ef4444`), not neon/gamer styling. `--tg-viewport-h`,
  `--safe-top/bottom/left/right` CSS custom properties already wired to
  `env(safe-area-inset-*)` with a `100vh/100svh/100dvh` fallback stack
  already present on `body` and `.app-shell`. No arbitrary
  `max-width: 820px`-style desktop-caging found; existing `max-width` rules
  are legitimate (modal widths ~560-600px, badge widths).
- `app.js` (1750 lines) already handles Telegram WebApp init: reads
  `tg.viewportHeight`, `tg.safeAreaInset`, calls `tg.expand()`,
  `tg.requestFullscreen()` (feature-detected), `tg.disableVerticalSwipes()`
  (feature-detected), listens for `viewportChanged`/`safeAreaChanged`
  events. Section 5/27 of the original spec is largely already done —
  audit for edge cases (iPad split-screen, Stage Manager) rather than
  reimplementing.

## 3. CONFIRMED BUG — FIX THIS FIRST (real, reproduced by static trace)

**Dead/fake WebSocket path.** `app.js` line ~199-230, `connectWebSocket()`:

```js
const wsUrl = `${protocol}://${location.host}/ws/chat?init_data=${...}`;
socket = new WebSocket(wsUrl);
```

This is called on every home-screen render (`renderHomeScreen()` calls
`connectWebSocket()` unconditionally). **`server.py` has no `/ws/chat`
route or any WebSocket upgrade handling at all** — confirmed by reading
the entire route table and confirming the handler is a synchronous
`BaseHTTPRequestHandler` subclass. The only `websockets` usage in the repo
is a WebSocket **client** in `runtime/real_time_manager.py` connecting
*outbound* to an exchange feed — unrelated to this.

Effect: every page load attempts a WS handshake that will fail or hang,
`onerror`/`onclose` fires, `fallbackHttpPoll()` presumably kicks in (verify
this function actually exists and works — locate it, it wasn't traced in
this audit), and `onclose` schedules `setTimeout(connectWebSocket, 2000)`
— an infinite reconnect loop against a route that will never exist unless
you build it. This directly violates spec section 20: *"Do not invent fake
WebSocket data."*

**You must choose one of two fixes, not silently leave it:**

- **(A) Remove it.** Delete `connectWebSocket()` and its call sites; rely
  entirely on the existing 5-second HTTP polling
  (`setInterval(() => requestDashboard(), 5000)` at the bottom of
  `app.js`). Simplest, safest, matches what the backend actually supports
  today.
- **(B) Build it for real.** Implement an actual WebSocket upgrade in
  `server.py` (this requires moving off bare `http.server` or bolting on
  a WS library) that streams real dashboard/position updates. This is a
  real backend architecture change — falls under spec section 31's "if
  you discover architectural limitations that require backend changes"
  clause, and is **not** in the "do not touch" list (it's not trading
  execution/risk/auth), so it's allowed, but it's a bigger job. Only do
  this if genuinely justified by a real need for sub-5-second updates
  (e.g. live position PnL during volatile moves) — verify with the user
  first since it changes backend architecture, not just UI.

Default recommendation: **(A)**, unless the user explicitly wants
real-time push updates enough to justify the backend work.

## 4. WHAT IS *NOT* YET VERIFIED — DO THIS NEXT

The prior audit ran out of scope/time before completing these. Do them
before writing any handoff report claiming things are "done":

1. **Full data-binding audit, screen by screen.** For each of the 9
   screens, trace every DOM element ID in `index.html` back to the exact
   field in the exact API response it's populated from in `app.js`. Flag
   any: (a) hardcoded/placeholder values left in HTML that JS never
   overwrites, (b) JS reading a field name that doesn't exist in the
   actual `_handle_*` response in `server.py`, (c) any client-side
   recomputation of authoritative state (equity, PnL, KillSwitch, mode)
   that could drift from backend truth — spec section 28 forbids this.
2. **Run the real test suite** (`pytest tests/ -v` from repo root, `pip
   install -e .` first if needed) and record exact pass/fail/count. Do not
   report a number you haven't seen with your own eyes this session.
3. **Trace the RiskGuardian → OEM → EndpointGuard → PAPER chain** in
   `src/apex/execution/oem.py` and `src/apex/risk/guardian.py` end to end
   for one order intent, confirming no code path skips RiskGuardian. This
   is the single most safety-critical claim in the whole project and it
   has only been spot-checked (kill switch fail-closed, config validator),
   not fully traced.
4. **`fallbackHttpPoll()`** — locate this function in `app.js`, confirm it
   actually exists and actually works, since `connectWebSocket()`'s error
   paths depend on it.
5. **Loading/error/empty states** (spec section 25) — actually trigger
   each backend error condition (stop the API, return malformed JSON,
   etc.) and watch what each of the 9 screens does. Don't assume from
   reading code that a `try/catch` produces a good UX.
6. **Accessibility pass** (spec section 23) — contrast ratios, focus
   states, screen reader labels — not touched at all yet.
7. **iPad-specific behavior** (split-screen, Stage Manager) — cannot be
   verified without a physical device or simulator; mark explicitly as
   `CODE VERIFIED — PHYSICAL DEVICE VERIFICATION PENDING` per spec section
   34, do not claim more.

## 5. EXECUTION PLAN (do in this order)

**Phase 0 — Baseline.**
`git init`, commit the repo exactly as received as `chore: baseline
import`. This is your diff anchor for everything after.

**Phase 1 — Fix the confirmed bug.**
Section 3 above. Pick (A) or (B), implement, test manually (`python
scripts/run_api.py` or equivalent, load the Mini App, watch network tab —
confirm no failed WS handshake spam / confirm real streaming if you did
(B)).

**Phase 2 — Full data-binding audit.**
Section 4.1 above. Fix anything broken. Commit per screen
(`fix(webapp): correct risk screen field bindings`, etc.) rather than one
giant commit — this makes review possible.

**Phase 3 — Run and report the real test suite.**
Section 4.2. If anything fails, fix it or explicitly document why not (do
not silently ignore failures).

**Phase 4 — Safety chain trace.**
Section 4.3. This is the highest-stakes verification in the project —
budget real time for it. If you find a bypass, STOP and flag it loudly
before doing anything else; do not quietly patch a safety hole without
telling the user.

**Phase 5 — Polish pass.**
Loading/error/empty states, accessibility, and any visual refinement
(spec sections 6-9, 21-26) — but only *after* correctness is verified.
Don't reorder this ahead of Phases 1-4; a beautiful UI showing wrong data
is worse than a plain one showing right data.

**Phase 6 — Handoff report.**
Per original spec sections 32-33, create:
- `reports/APEX_MINIAPP_CLAUDE_HANDOFF_FOR_AGY.md` (+ `.txt`)
- `reports/APEX_MINIAPP_CLAUDE_HANDOFF_FOR_AGY_SUMMARY.md`

Every claim in these files must be something *you personally ran or read
this session* — carry forward Section 1/2 of this document as "previously
verified," and add your own new findings with the same rigor (exact test
counts from an actual run, exact commit hash, exact files changed).
Distinguish code-verified / unit-test-verified / integration-test-verified
/ runtime-verified / physical-device-verified per spec section 30 — do not
collapse these.

## 6. NON-NEGOTIABLE SAFETY RULES (carried forward, unchanged)

- LIVE trading stays disabled. Do not touch `live_trading_enabled` or the
  mode validator in `config/settings.py`.
- Do not create a second execution engine, risk engine, position/account
  state, or signal truth.
- Do not bypass RiskGuardian, OrderExecutionManager, EndpointGuard, or
  KillSwitch, and do not weaken the fail-closed behavior of any of them.
- Do not add direct Telegram trade execution, arbitrary shell execution,
  or any new mutation endpoint beyond the existing whitelist without
  explicit user sign-off (this is a security-boundary change).
- Never fabricate market data, trades, PnL, positions, or signals in the
  frontend — if the backend doesn't provide it, show
  "unavailable"/"planned," not a placeholder that looks real.
- If a proposed change touches trading execution, risk authority,
  KillSwitch, authentication, a security boundary, or LIVE enablement:
  **stop and get explicit sign-off before making it.** Document the
  proposal instead of just doing it.

## 7. STYLE / SCOPE GUARDRAILS

- Don't rebuild what's already correct (Sections 1-2 above). Every
  section of the original 37-point spec that's already satisfied should
  be *verified*, not *reimplemented*.
- Small, reviewable commits over one giant diff.
- No new `max-width` desktop caps, no new emoji-heavy decoration beyond
  what already exists (spec section 6 — restrained quant-terminal
  aesthetic, not gamer UI).
- If you're unsure whether the backend actually supports something the
  original spec asks for (e.g. sections 18/19's search/command interface,
  section 19's charts), check `server.py`'s route table (Section 1 above)
  first. If there's no backend data for it, don't build a frontend feature
  that has to fake it — say so in the handoff report as a documented
  limitation instead.
