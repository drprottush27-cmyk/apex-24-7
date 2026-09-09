# APEX Security Invariants

1. DRY_RUN remains enabled by default.
2. AUTO_EXECUTE remains disabled by default.
3. Production endpoints must never be used.
4. Missing safety configuration fails closed.
5. Risk Guardian remains deterministic.
6. Risk Guardian retains execution veto.
7. No AI has direct execution authority.
8. Secrets must never appear in logs.
9. .env contents must never be displayed.
10. Tests must not weaken production safety.
11. Testnet must be explicitly isolated.
12. Every order path requires backend safety validation.
13. Kill switch must be fail-safe.
14. Audit events must remain traceable.
15. Invalid/missing account equity (negative, zero, NaN, inf, malformed) must
    never be accepted as valid equity; Risk Guardian fails closed.
16. OrderManager initialization failure must fail closed (no partial init, no
    order processing until initialization succeeds).
17. Execution leverage must come only from the deterministic Risk Guardian's
    approved result. Invalid/missing/non-finite/<=0 leverage (or any leverage
    diverging from the approved value) must fail closed — never substitute an
    unsafe or default leverage, and never allow execution leverage to exceed the
    approved value.
18. Duplicate order protection must prevent the same logical order from being
    submitted more than once. The duplicate identity is deterministic
    (symbol:side:setup_name) — never random UUIDs, timestamps, or object memory
    addresses. If the identity cannot be determined, the submission must fail
    closed. The check-and-register operation must be atomic under concurrency,
    and callers must not be able to bypass it through alternate OrderManager
    paths. Risk Guardian rejection and leverage validation remain authoritative
    and intact.
19. CORS must fail closed. Cross-origin browser access to the API is granted
    only for explicitly allowlisted origins (ALLOWED_ORIGINS). An empty allowlist
    grants NO cross-origin access. The "*" wildcard must never be used as an
    allow_origins value.
20. Pending orders must expire deterministically after ORDER_TTL_SECONDS. Expired orders must safely release their dedup keys, log an EXPIRED OrderStatus, and never expire protective reduce_only orders or already filled/cancelled orders. Invalid TTL configurations must fail closed (ignore expiry).
21. Rate limiting (Binance REST) must fail closed. HTTP 418 never retries and
    records a shared process-level cooldown that blocks all subsequent requests
    (public and signed). Public idempotent GET requests may retry at most once
    on HTTP 429 only with a valid, bounded Retry-After header. Signed/mutating
    requests are never automatically retried. Weight state is shared across all
    client instances.

## State Reconciliation (P2-10)
- Reconciliation functions must NEVER mutate order state, positions, or risk limits.
- Audits must log the full structure of mismatches.
- Failed DB or Redis connectivity during reconciliation must fail closed rather than assuming state consistency.

## Graceful Shutdown (P2-15)
- Shutdown must ALWAYS engage the kill switch FIRST, before any other cleanup,
  so no order can be placed during teardown.
- Shutdown must never alter DRY_RUN / LIVE_TRADING_ENABLED / Risk Guardian
  veto / advisory-only AI, and must never auto-release the kill switch.
- Shutdown cleanup steps must be bounded by a timeout so a hung resource can
  never block the process from exiting (once the kill switch is engaged, a
  forceful termination is safe).
- Shutdown must be idempotent and concurrency-safe (no duplicate side-effects).
