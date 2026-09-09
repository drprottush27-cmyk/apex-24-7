"""APEX 24/7 — Continuous Autonomous PAPER Run (Phase 10 + remediation).

Deterministic, offline, safety-first autonomous paper-trading session.

This script runs the full pipeline:
    synthetic closed candles -> scan -> signal -> RiskGuardian -> OEM ->
    EndpointGuard -> paper fill -> position tracking -> AUTONOMOUS management
    -> TradeJournal -> review-only learning proposal

Every exit — including the demo lifecycle closes — is applied by the engine's
autonomous open-position management through the OEM safety chain. The script
NEVER mutates position state directly: it only drives market prices.

Safety guarantees:
- PAPER mode only. Any attempt to enable live trading aborts.
- Persistent journal + persistent idempotency across restarts.
- No network, no credentials, no production endpoints. Fully offline.
- Graceful shutdown on SIGINT/KeyboardInterrupt.

Usage:
    python scripts/run_paper.py [--ticks N] [--data-dir DIR]
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# Ensure the package is importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from apex.config.settings import ApexConfig  # noqa: E402
from apex.domain.candles import Candle  # noqa: E402
from apex.domain.types import Timeframe, TradingMode  # noqa: E402
from apex.journal.repository import TradeJournal  # noqa: E402
from apex.learning.proposals import ProposalGenerator  # noqa: E402
from apex.market.candle_series import CandleSeries  # noqa: E402
from apex.market.client import MarketClient  # noqa: E402
from apex.market.transport import HTTPResponse  # noqa: E402
from apex.persistence.journal import PersistentJournal  # noqa: E402
from apex.runtime.engine import ApexEngine  # noqa: E402
from apex.runtime.execution_journal import ExecutionEventType  # noqa: E402
from apex.safety.exceptions import SafetyConfigurationError  # noqa: E402

# ── Synthetic deterministic market client ─────────────────────────────────────

class _NoNetworkTransport:
    """Transport that makes it impossible to touch the network."""

    def request(self, req: object) -> HTTPResponse:
        raise AssertionError("Synthetic run must never perform HTTP I/O.")


def _build_series(
    symbol: str,
    latest_open_ms: int,
    last_close: float | None = None,
) -> CandleSeries:
    """Deterministic pre-pump wave with in-bounds stop geometry.

    The latest candle opens at `latest_open_ms` (always in the past) so the
    closed-candle and signal-timestamp invariants hold. `last_close` lets the
    driver re-price the latest CLOSED candle while keeping every timestamp
    stable (so signal idempotency keys never shift).
    """
    offset = 300.0
    seq = [150, 147, 144, 141, 138, 135, 132, 129, 126, 123]
    seq += [120, 118, 116, 114, 112, 110, 108, 106, 104, 102, 100]
    seq += [103, 108, 113, 118, 123, 128, 132]
    seq += [126, 122, 119, 116]
    seq += [118, 116, 115]
    seq += [118, 124, 130, 136, 142, 148, 152]
    seq += [148, 143, 139, 135]
    seq += [138, 144, 150, 157, 165, 172, 180, 189]
    closes = [c + offset for c in seq]
    volumes = [50.0] * 54
    volumes[-2] = 180.0
    volumes[-1] = 240.0

    wick_map = {27: 8.0, 41: 10.0}
    candles: list[Candle] = []
    base_ts = latest_open_ms - (len(seq) - 1) * 300_000
    ts = base_ts
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        hi = max(c + 1.0, c + wick_map.get(i, 0.0), o)
        lo = min(c - 1.0, o, c)
        candles.append(
            Candle(
                symbol=symbol,
                timeframe=Timeframe.M5,
                open_time_ms=ts,
                close_time_ms=ts + 299_999,
                open=round(o, 4),
                high=round(hi, 4),
                low=round(lo, 4),
                close=round(c, 4),
                volume=volumes[i],
                is_closed=True,
            )
        )
        ts += 300_000

    if last_close is not None:
        last = candles[-1]
        candles[-1] = Candle(
            symbol=last.symbol,
            timeframe=last.timeframe,
            open_time_ms=last.open_time_ms,
            close_time_ms=last.close_time_ms,
            open=last.open,
            high=max(last.high, last_close),
            low=min(last.low, last_close),
            close=last_close,
            volume=last.volume,
            is_closed=True,
        )
    return CandleSeries(candles=tuple(candles))


class SyntheticMarketClient(MarketClient):
    """Offline MarketClient serving deterministic closed-candle series.

    Candle timestamps are fixed at construction, so every tick sees the same
    closed candles: a signal is emitted at most once (dedup) and the engine's
    autonomous management observes stable, fresh data. `set_price` re-prices
    only the latest CLOSED candle close — the ONLY lever the driver uses.
    """

    def __init__(self, universe: list[str], latest_open_ms: int) -> None:
        super().__init__(_NoNetworkTransport())
        self._latest_open_ms = latest_open_ms
        self._prices: dict[str, float | None] = {}

    def set_price(self, symbol: str, price: float) -> None:
        self._prices[symbol] = price

    def load_klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 500,
        start_ms: int | None = None,
        end_ms: int | None = None,
        reject_open: bool = True,
    ) -> CandleSeries:
        return _build_series(
            symbol,
            latest_open_ms=self._latest_open_ms,
            last_close=self._prices.get(symbol),
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def build_config() -> ApexConfig:
    """Build PAPER configuration. Any LIVE attempt aborts immediately."""
    mode_raw = os.environ.get("APEX_TRADING_MODE", "PAPER").strip().upper()
    if mode_raw in ("LIVE", "PRODUCTION", "REAL"):
        raise SafetyConfigurationError(
            "CRITICAL SAFETY VIOLATION: live trading is permanently prohibited."
        )
    return ApexConfig(
        trading_mode=TradingMode.PAPER,
        live_trading_enabled=False,
        max_risk_per_trade=float(os.environ.get("APEX_MAX_RISK_PCT", "0.01")),
        max_leverage=float(os.environ.get("APEX_MAX_LEVERAGE", "3.0")),
        max_concurrent_positions=int(os.environ.get("APEX_MAX_POSITIONS", "2")),
    )


def _drive_prices_to_take_profit(client: SyntheticMarketClient, engine: ApexEngine) -> None:
    """Drive each open position's symbol toward its take profit.

    Positions are closed by the ENGINE's autonomous management inside the next
    scan tick — through the exact same OEM safety chain used for entries. The
    script only moves prices; it never touches position state.
    """
    for pos in engine.position_tracker.open_positions:
        client.set_price(pos.symbol, pos.take_profit)


def main() -> int:
    parser = argparse.ArgumentParser(description="APEX 24/7 autonomous PAPER run")
    parser.add_argument("--ticks", type=int, default=5, help="scan ticks to execute")
    parser.add_argument("--universe", default="BTCUSDT,ETHUSDT", help="comma-separated symbols")
    parser.add_argument("--data-dir", default="var", help="persistence directory")
    args = parser.parse_args()

    if args.ticks < 1:
        parser.error("--ticks must be >= 1")

    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    journal_path = data_dir / "paper_journal.db"
    idempotency_path = data_dir / "idempotency.sqlite"
    trades_path = data_dir / "trades.db"

    config = build_config()
    universe = [s.strip().upper() for s in args.universe.split(",") if s.strip()]

    print(f"[APEX] PAPER autonomous run | mode={config.trading_mode.value}")
    print(f"[APEX] universe={universe} ticks={args.ticks} data_dir={data_dir}")

    persistent_journal = PersistentJournal(journal_path)
    trade_journal = TradeJournal(trades_path)
    client = SyntheticMarketClient(
        universe,
        latest_open_ms=int(time.time() * 1000) - 300_000,
    )

    engine = ApexEngine(
        config=config,
        client=client,
        universe=universe,
        equity_provider=lambda: 10_000.0,
        timeframe=Timeframe.M5,
        scan_interval_ms=60_000,
        persistent_journal=persistent_journal,
        persistent_idempotency_path=str(idempotency_path),
        trade_journal=trade_journal,
    )
    engine.start()

    try:
        for tick in range(1, args.ticks + 1):
            # From the second tick onward, the open positions are driven to
            # their take profits so the record completes a full lifecycle via
            # the engine's autonomous management.
            if tick > 1:
                _drive_prices_to_take_profit(client, engine)
            event = engine.scan_once()
            status = engine.get_status()
            fills = len(
                engine.execution_journal.events_of_type(ExecutionEventType.PAPER_FILL)
            )
            exits = len(
                engine.execution_journal.events_of_type(ExecutionEventType.FAIL_SAFE_CLOSE)
            )
            print(
                f"[tick {tick}/{args.ticks}] scanned={event.scan_result.symbols_scanned} "
                f"accepted={event.scan_result.symbols_accepted} "
                f"filled={fills} exits={exits} open={status.open_positions} "
                f"state={status.system_state}"
            )
            time.sleep(0.1)  # keep the loop non-spinning

        # NO direct close path: the trade journal is populated entirely by the
        # engine's autonomous management (`_append_closed_trade_record`).
        print(f"[APEX] lifecycle complete: {trade_journal.count()} trade(s) recorded")

        if trade_journal.count() > 0:
            proposal = ProposalGenerator(
                config=config,
                strategy_version="prepump-v1",
            ).generate(trade_journal.all_trades())
            print()
            print(proposal.summary_md)
            print(f"[APEX] review-only proposal written: {proposal.proposal_id}")
    except KeyboardInterrupt:
        print("\n[APEX] interrupted; shutting down gracefully")
    finally:
        engine.shutdown()
        engine.close()
        trade_journal.close()
        persistent_journal.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
