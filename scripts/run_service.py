"""APEX 24/7 — 24/7 Continuous PAPER Service (Phase 15).

Operational wrapper around ApexEngine that provides:

- Continuous scheduled scanning (RESTART-PROOF idempotency + persistent journal)
- Structured logging (JSON) for observability
- Runtime health monitoring (data/execution gates)
- Kill switch, pause/resume controls
- Graceful shutdown on SIGINT/SIGTERM
- PAPER only; market data is OFFLINE by default, with an explicit opt-in
  read-only public Binance data client (market data only — no credentials,
  no execution)
- Completed trades are appended by the engine to the review-only TradeJournal

This is NOT a systemd daemon and does NOT add a second scheduler/engine: it
drives the SAME ApexEngine and ScanScheduler in-process in a foreground loop.

Safety:
- Any LIVE/PRODUCTION/REAL mode env aborts before startup.
- No production endpoints, no credentials, no account access.
- The service loops engine.scan_once(); all execution is routed through the
  certified OEM->RiskGuardian->EndpointGuard->PAPER path.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from apex.api.server import ApexApiServer  # noqa: E402
from apex.config.settings import ApexConfig  # noqa: E402
from apex.domain.types import Timeframe, TradingMode  # noqa: E402
from apex.journal.repository import TradeJournal  # noqa: E402
from apex.market.client import MarketClient  # noqa: E402
from apex.market.observations import MarketObservationClient  # noqa: E402
from apex.market.transport import ResilientHTTPTransport  # noqa: E402
from apex.persistence.journal import PersistentJournal  # noqa: E402
from apex.runtime.engine import ApexEngine  # noqa: E402
from apex.runtime.observability import JsonLogger  # noqa: E402
from apex.safety.exceptions import SafetyConfigurationError  # noqa: E402


class _OfflineMarketClient:
    """Minimal offline stand-in so the service runs without any network.

    The 24/7 service is PAPER and offline by default; a real market-data
    client would be wired by the operator. This place-holder keeps the
    service runnable and deterministic without external dependencies.
    """

    def load_klines(self, symbol: str, **kwargs: object) -> object:
        raise RuntimeError(
            "offline market client: no market data available. "
            "Wire a real MarketClient (or offline synthetic provider) before use."
        )


_StdlibHTTPTransport = ResilientHTTPTransport


def build_custom_market_client() -> MarketClient:
    """Build a read-only public Binance market-data client.

    Public endpoints only: klines/exchange-info/ticker. No credentials are
    ever loaded or generated. Execution is out of reach by construction.
    Synchronous bounded rate-limiting and retry backoff enabled.
    """
    return MarketClient(http_transport=ResilientHTTPTransport())


def build_observation_client() -> MarketObservationClient:
    """Build a read-only public Binance tactical observation client.

    Public endpoints only: openInterestHist, fundingRate, depth.
    No credentials are ever loaded or generated. Execution is out of reach.
    Synchronous bounded rate-limiting and retry backoff enabled.
    """
    return MarketObservationClient(http_transport=ResilientHTTPTransport())


def build_config() -> ApexConfig:
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


def main() -> int:
    parser = argparse.ArgumentParser(description="APEX 24/7 continuous PAPER service")
    parser.add_argument("--ticks", type=int, default=0, help="ticks to run (0 = indefinite)")
    parser.add_argument(
        "--universe",
        default=os.environ.get("APEX_UNIVERSE", "auto"),
        help="comma-separated symbols or 'auto' (default: auto)",
    )
    parser.add_argument(
        "--universe-top-n",
        type=int,
        default=int(os.environ.get("APEX_UNIVERSE_TOP_N", "100")),
        help="Number of top symbols when universe is auto (default: 100)",
    )
    parser.add_argument(
        "--min-quote-volume",
        type=float,
        default=float(os.environ.get("APEX_MIN_QUOTE_VOLUME", "10000000.0")),
        help="Minimum 24h quote volume for discovery (default: 10M)",
    )
    parser.add_argument("--data-dir", default="var", help="persistence directory")
    parser.add_argument("--interval-ms", type=int, default=60_000, help="scan interval")
    parser.add_argument(
        "--market-client",
        choices=("offline", "binance"),
        default=os.environ.get("APEX_MARKET_DATA_CLIENT", "offline"),
        help=(
            "market-data source (default: offline). 'binance' wires the "
            "read-only public Binance data client (market data only, no "
            "trading credentials)"
        ),
    )
    parser.add_argument(
        "--api-port",
        type=int,
        default=int(os.environ.get("APEX_API_PORT", "0")),
        help="Start read-only inspection API on localhost port (0 = disabled)",
    )
    args = parser.parse_args()

    if args.interval_ms <= 0:
        parser.error("--interval-ms must be positive")

    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    journal_path = data_dir / "service_journal.db"
    idempotency_path = data_dir / "service_idempotency.sqlite"
    trades_path = data_dir / "service_trades.db"

    config = build_config()

    if args.market_client == "binance":
        market_client_inst = build_custom_market_client()
        observation_client: object = build_observation_client()
        client: object = market_client_inst

        if args.universe.strip().lower() == "auto":
            try:
                discovered = market_client_inst.discover_top_symbols(
                    top_n=args.universe_top_n,
                    min_quote_turnover=args.min_quote_volume,
                )
                universe = list(discovered) if discovered else ["BTCUSDT", "ETHUSDT"]
            except Exception:
                universe = ["BTCUSDT", "ETHUSDT"]
        else:
            universe = [s.strip().upper() for s in args.universe.split(",") if s.strip()]
            if not universe:
                universe = ["BTCUSDT", "ETHUSDT"]
    else:
        client = _OfflineMarketClient()
        observation_client = None
        universe = (
            ["BTCUSDT", "ETHUSDT"]
            if args.universe.strip().lower() == "auto"
            else [s.strip().upper() for s in args.universe.split(",") if s.strip()] or ["BTCUSDT", "ETHUSDT"]
        )

    logger = JsonLogger()
    logger.info(
        "apex service starting",
        mode=config.trading_mode.value,
        universe=universe,
        ticks=args.ticks,
        interval_ms=args.interval_ms,
        data_dir=str(data_dir),
        market_client=args.market_client,
    )

    persistent_journal = PersistentJournal(journal_path)
    trade_journal = TradeJournal(trades_path)

    engine = ApexEngine(
        config=config,
        client=client,  # type: ignore[arg-type]
        universe=universe,
        equity_provider=lambda: 10_000.0,
        timeframe=Timeframe.M5,
        scan_interval_ms=args.interval_ms,
        persistent_journal=persistent_journal,
        persistent_idempotency_path=str(idempotency_path),
        trade_journal=trade_journal,
        observation_client=observation_client,  # type: ignore[arg-type]
    )
    engine.start()

    api_server: ApexApiServer | None = None
    if args.api_port > 0:
        api_server = ApexApiServer(engine=engine, host="127.0.0.1", port=args.api_port)
        api_server.start()
        logger.info("apex api server listening", host="127.0.0.1", port=args.api_port)

    bot_service = None
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if bot_token:
        from apex.telegram.bot import TelegramBotService
        miniapp_url = os.environ.get(
            "TELEGRAM_MINIAPP_URL",
            "https://eng-industries-sep-joshua.trycloudflare.com/algo",
        )
        bot_service = TelegramBotService(
            bot_token=bot_token,
            api_base_url=f"http://127.0.0.1:{args.api_port}" if args.api_port > 0 else "http://127.0.0.1:8765",
            miniapp_url=miniapp_url,
        )
        bot_service.start()

    stop_event = threading.Event()

    def _handle_signal(signum: int, frame: object) -> None:
        stop_event.set()
        logger.info("signal received; graceful shutdown", signal=signum)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        ticks_run = 0
        while not stop_event.is_set() and (args.ticks == 0 or ticks_run < args.ticks):
            event = engine.scan_once()
            health = engine.get_health()
            status = engine.get_status()
            logger.info(
                "scan tick",
                tick=ticks_run + 1,
                scanned=event.scan_result.symbols_scanned,
                accepted=event.scan_result.symbols_accepted,
                rejected=event.scan_result.symbols_rejected,
                errors=event.scan_result.errors,
                open_positions=status.open_positions,
                health=health.status.value,
                data_health=health.data_health.value,
                execution_health=health.execution_health.value,
                state=status.system_state,
            )
            ticks_run += 1
            if args.ticks == 0 or ticks_run < args.ticks:
                stop_event.wait(args.interval_ms / 1000.0)
        logger.info("apex service stop requested", ticks_run=ticks_run)
    except KeyboardInterrupt:
        logger.info("interrupted; shutting down gracefully")
    finally:
        if bot_service is not None:
            bot_service.stop()
        if api_server is not None:
            api_server.stop()
        engine.shutdown()
        engine.close()
        trade_journal.close()
        persistent_journal.close()
        logger.info("apex service stopped")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
