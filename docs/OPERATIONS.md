# APEX 24/7 — Operations Guide

This document covers the foreground 24/7 PAPER service (`scripts/run_service.py`)
introduced in Phase 15.

## Purpose

`run_service.py` is a deterministic, restart-proof **PAPER** service that drives
the existing `ApexEngine` + `ScanScheduler` in-process. It is NOT a daemon and
does NOT add a second scheduler or engine. It is a thin operational wrapper that:

- runs scheduled scan cycles against a symbol universe,
- emits structured **JSON logs** for observability,
- reports **runtime health** (data gate + execution gate) after every tick,
- performs **graceful shutdown** on SIGINT/SIGTERM,
- is **restart-proof** via the persistent journal + persistent idempotency guard,
- is **offline by default** (no network, no credentials, no exchange access),
  with an explicit opt-in **read-only public market-data** client.

## Safety Contract

- The mode is forced to `PAPER`; any `APEX_TRADING_MODE` of `LIVE`,
  `PRODUCTION`, or `REAL` aborts before startup.
- All execution flows through the certified path
  Signal → RiskGuardian → OrderExecutionManager → EndpointGuard → PAPER.
- The offline placeholder market client returns NO market data: without a wired
  data provider every scan yields `SKIPPED` per symbol (fail-closed, never a
  fabricated candle). Operators must supply a real closed-candle provider
  before any PAPER fills can occur.
- Autonomous open-position management (mark-to-market, hard stop-loss,
  take-profit, and fail-safe closes) runs inside every scan tick and routes
  ALL exits through the same certified path. Prices are never fabricated
  (`NO_DATA` is reported and the position is left untouched).
- The `--market-client binance` option wires a **read-only** public Binance
  data client (klines/exchange-info/ticker). It carries NO signing material:
  no credentials are generated, loaded, or referenced anywhere, and the
  transport refuses any non-GET request. Execution remains out of reach.
- No production endpoints, no credentials, no account access anywhere in the
  service.

## Usage

```bash
.venv/bin/python scripts/run_service.py [options]
```

| Option | Default | Meaning |
|--------|---------|---------|
| `--ticks` | `0` | number of scan ticks to run (`0` = indefinite) |
| `--universe` | `BTCUSDT,ETHUSDT` | comma-separated symbols |
| `--data-dir` | `var` | persistence directory (journal + idempotency DB) |
| `--interval-ms` | `60000` | scan interval in milliseconds |
| `--market-client` | `offline` | `offline` or `binance` (read-only public data) |

Environment:

| Variable | Default | Meaning |
|----------|---------|---------|
| `APEX_TRADING_MODE` | `PAPER` | must stay `PAPER`; LIVE/PRODUCTION/REAL aborts |
| `APEX_MAX_RISK_PCT` | `0.01` | per-trade risk cap |
| `APEX_MAX_LEVERAGE` | `3.0` | leverage cap |
| `APEX_MAX_POSITIONS` | `2` | concurrent position cap |
| `APEX_MARKET_DATA_CLIENT` | `offline` | same choices as `--market-client` |

## Logging & Health

Every tick emits one JSON line, e.g.:

```json
{"accepted": 0, "data_health": "HEALTHY", "errors": 0,
 "execution_health": "HEALTHY", "health": "HEALTHY", "level": "INFO",
 "message": "scan tick", "open_positions": 0, "rejected": 0,
 "scanned": 2, "state": "SCANNING", "tick": 1, ...}
```

Health semantics (`runtime/health.py`):

- `data_health` degrades after consecutive data-fetch/data-quality failures and
  turns `UNHEALTHY` at the (conservative) threshold. A successful scan resets
  the counter. When a degradation trigger coincides with **zero usable
  symbols** the gate is `NO_DATA` (complete coverage loss) — still not
  `HEALTHY`, and it escalates to `UNHEALTHY` if unchecked.
- Management outcomes (missing market data for an open position, equity
  provider failures) are folded into the same gates so a failed management
  cycle can never be masked by a healthy scan.
- `execution_health` degrades on consecutive execution-path failures (e.g. the
  equity provider throwing).
- `overall` is the worst of the two gates. Health is **observational only** —
  it degrades/records but never authorizes or executes anything.

## Shutdown & Restart

- SIGINT (Ctrl+C) or SIGTERM triggers graceful shutdown: scan loop stops,
  `engine.shutdown()` + `engine.close()` run, resources are released.
- On restart the persistent journal is replayed and the idempotency guard
  rejects duplicate events, so interrupted runs resume cleanly.
- If the persistent journal contains corrupt position snapshots (or recovery
  fails to reconcile a position), `engine.start()` **aborts** — fail-closed —
  rather than risk autonomous operation on untrustworthy state.

## Limitations

- The default market client is an offline placeholder; wiring `--market-client
  binance` is an operator decision and is read-only public market data only.
- No alerting/notification wiring (log sink only).
- Running under `systemd`/PM2 is possible but unconfigured; the service is a
  foreground loop by design.