"""APEX 24/7 — Standalone Read-Only API Server.

Runs the localhost-only read-only inspection HTTP server.
Can run standalone or alongside run_service.py.

Usage:
    python scripts/run_api.py --port 8765
"""
from __future__ import annotations

import argparse
import contextlib
import math
import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from apex.api.server import ApexApiServer  # noqa: E402
from apex.config.settings import ApexConfig  # noqa: E402
from apex.control import ApexControlPlane, set_control_plane  # noqa: E402
from apex.domain.types import Timeframe, TradingMode  # noqa: E402
from apex.market.client import MarketClient  # noqa: E402
from apex.market.observations import MarketObservationClient  # noqa: E402
from apex.market.transport import ResilientHTTPTransport  # noqa: E402
from apex.persistence.journal import PersistentJournal  # noqa: E402
from apex.runtime.auto_trade import AutoTradeConfig  # noqa: E402
from apex.runtime.autoclose import AutoCloseConfig  # noqa: E402
from apex.runtime.engine import ApexEngine  # noqa: E402
from apex.safety.exceptions import SafetyConfigurationError  # noqa: E402


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
    parser = argparse.ArgumentParser(description="APEX 24/7 Read-Only Inspection API Server")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Localhost bind address (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("APEX_API_PORT", "8765")),
        help="Localhost port (default: 8765)",
    )
    parser.add_argument(
        "--db-path",
        default="var/apex_service.db",
        help="Persistent SQLite journal path",
    )
    parser.add_argument(
        "--universe",
        default=os.environ.get("APEX_UNIVERSE", "auto"),
        help="Comma-separated symbols or 'auto' for dynamic volume-based discovery",
    )
    parser.add_argument(
        "--universe-top-n",
        type=int,
        default=int(os.environ.get("APEX_UNIVERSE_TOP_N", "100")),
        help="Number of top symbols by 24h volume when universe is auto (default: 100)",
    )
    parser.add_argument(
        "--min-quote-volume",
        type=float,
        default=float(os.environ.get("APEX_MIN_QUOTE_VOLUME", "10000000.0")),
        help="Minimum 24h quote volume for dynamic discovery (default: 10M)",
    )
    args = parser.parse_args()

    config = build_config()
    db_path = Path(args.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    journal = PersistentJournal(str(db_path))

    transport = ResilientHTTPTransport()
    market_client = MarketClient(http_transport=transport)
    obs_client = MarketObservationClient(http_transport=transport)

    default_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
    if args.universe.strip().lower() == "auto":
        print(f"Discovering top {args.universe_top_n} USDT-M perpetuals (min volume: ${args.min_quote_volume:,.0f})...")
        try:
            discovered = market_client.discover_top_symbols(
                top_n=args.universe_top_n,
                min_quote_turnover=args.min_quote_volume,
            )
            universe = list(discovered) if discovered else default_symbols
            print(f"Discovered {len(universe)} symbols: {universe[:5]}...{universe[-2:]}")
        except Exception as exc:
            print(f"Discovery notice ({exc}), falling back to default universe")
            universe = default_symbols
    else:
        universe = [s.strip().upper() for s in args.universe.split(",") if s.strip()]
        if not universe:
            universe = default_symbols

    def load_auto_trade_config() -> AutoTradeConfig:
        settings_file = Path("config/settings.json")
        at_cfg = {}
        try:
            if settings_file.exists():
                import json
                s_data = json.loads(settings_file.read_text(encoding="utf-8"))
                at_cfg = s_data.get("auto_trade", {})
        except Exception as exc:
            print(f"[API AutoTrade Config] Notice: {exc}")
        return AutoTradeConfig(
            enabled=bool(at_cfg.get("enabled", False)),
            min_score=float(at_cfg.get("min_score", 65.0)),
            max_daily_drawdown_pct=float(at_cfg.get("max_daily_drawdown_pct", 2.0)) / 100.0,
            max_consecutive_losses=int(at_cfg.get("max_consecutive_losses", 3)),
            max_signal_age_seconds=float(at_cfg.get("max_signal_age_seconds", 180.0)),
            max_candle_surge_pct=float(at_cfg.get("max_candle_surge_pct", 5.0)) / 100.0,
            cooldown_seconds=float(at_cfg.get("cooldown_seconds", 300.0)),
        )

    def load_autoclose_config() -> AutoCloseConfig:
        settings_file = Path("config/settings.json")
        ac_cfg = {}
        try:
            if settings_file.exists():
                import json
                s_data = json.loads(settings_file.read_text(encoding="utf-8"))
                ac_cfg = s_data.get("autoclose", {})
        except Exception as exc:
            print(f"[API AutoClose Config] Notice: {exc}")
        return AutoCloseConfig(
            enabled=bool(ac_cfg.get("enabled", True)),
            grace_period_seconds=int(ac_cfg.get("grace_period_seconds", 60)),
            trailing_stop_enabled=bool(ac_cfg.get("trailing_stop_enabled", True)),
            trailing_activation_r=float(ac_cfg.get("trailing_activation_r", 1.0)),
            trailing_step_r=float(ac_cfg.get("trailing_step_r", 0.5)),
            adverse_liquidation_threshold_usd=float(ac_cfg.get("adverse_liquidation_threshold_usd", 50000.0)),
            funding_inversion_threshold=float(ac_cfg.get("funding_inversion_threshold", 0.0005)),
            volatility_spike_pct=float(ac_cfg.get("volatility_spike_pct", 0.02)),
        )

    # Compute achievable sustainable scan cadence given Binance's 2,400 weight/min REST limit
    binance_weight_budget_per_min = 2400
    est_weight_per_pass = len(universe) * 1 + 40  # 1 weight per klines + 40 bulk ticker
    # Target <= 35% utilization (840 weight/min) leaving >65% headroom
    target_scan_weight_budget = 840
    passes_per_min = max(1.0, target_scan_weight_budget / max(1, est_weight_per_pass))
    cadence_seconds = max(15, int(math.ceil(60.0 / passes_per_min)))
    est_weight_min = int((60.0 / cadence_seconds) * est_weight_per_pass)
    utilization_pct = (est_weight_min / binance_weight_budget_per_min) * 100.0

    print(
        f"[API Scanner 24/7] Universe: {len(universe)} symbols | "
        f"Est. weight/pass: {est_weight_per_pass} | "
        f"Sustainable cadence: {cadence_seconds}s interval | "
        f"Budget: {est_weight_min}/{binance_weight_budget_per_min} weight/min ({utilization_pct:.1f}% utilization)"
    )

    from apex.engines.tactical.alerts import AlertSeverity, get_telegram_dispatcher  # noqa: E402
    from apex.journal.repository import TradeJournal  # noqa: E402

    trades_db = Path("var/trades.db")
    trades_db.parent.mkdir(parents=True, exist_ok=True)
    trade_journal = TradeJournal(str(trades_db))

    engine = ApexEngine(
        config=config,
        client=market_client,
        universe=universe,
        equity_provider=lambda: 10_000.0,
        persistent_journal=journal,
        trade_journal=trade_journal,
        timeframe=Timeframe.M5,
        observation_client=obs_client,
        auto_trade_config=load_auto_trade_config(),
        autoclose_config=load_autoclose_config(),
    )

    import threading

    engine.start()

    with contextlib.suppress(Exception):
        get_telegram_dispatcher().dispatch_system_alert(
            event_type="SERVICE_START",
            title="APEX API Service Started",
            message=f"Mode: <b>PAPER</b> | Universe: <b>{len(universe)} symbols</b> | Cadence: <b>{cadence_seconds}s</b>",
            severity=AlertSeverity.INFO,
        )

    stop_event = False

    scan_metrics = {
        "scan_latency_ms": 0,
        "consecutive_scan_failures": 0,
        "last_successful_scan_ts_ms": 0,
    }
    engine._scan_metrics = scan_metrics

    def _scan_loop() -> None:
        # Initial scan warmup immediately
        t0 = time.time()
        try:
            engine.scan_once()
            latency = int((time.time() - t0) * 1000)
            scan_metrics["scan_latency_ms"] = latency
            scan_metrics["consecutive_scan_failures"] = 0
            scan_metrics["last_successful_scan_ts_ms"] = int(time.time() * 1000)
            print(f"[API Scanner] Warmup scan complete ({latency}ms)")
        except Exception as exc:
            scan_metrics["consecutive_scan_failures"] += 1
            print(f"[API Scanner] Warmup scan notice: {exc}")

        while not stop_event:
            for _ in range(cadence_seconds * 2):  # cadence_seconds in 0.5s ticks
                if stop_event:
                    return
                time.sleep(0.5)

            # Sync configurations from settings.json if modified
            try:
                latest_at_cfg = load_auto_trade_config()
                if engine.auto_trader.config != latest_at_cfg:
                    engine.auto_trader.update_config(latest_at_cfg)
            except Exception as exc:
                print(f"[API AutoTrade Sync] Notice: {exc}")

            try:
                latest_ac_cfg = load_autoclose_config()
                if engine.autoclose_manager.config != latest_ac_cfg:
                    engine.autoclose_manager.update_config(latest_ac_cfg)
            except Exception as exc:
                print(f"[API AutoClose Sync] Notice: {exc}")

            t_start = time.time()
            try:
                engine.scan_once()
                latency = int((time.time() - t_start) * 1000)
                scan_metrics["scan_latency_ms"] = latency
                prev_fails = scan_metrics["consecutive_scan_failures"]
                scan_metrics["consecutive_scan_failures"] = 0
                scan_metrics["last_successful_scan_ts_ms"] = int(time.time() * 1000)
                if prev_fails >= 3:
                    with contextlib.suppress(Exception):
                        get_telegram_dispatcher().dispatch_system_alert(
                            event_type="SCANNER_RECOVERED",
                            title="Scanner Recovered",
                            message=f"Scanner back online. Latency: <code>{latency}ms</code>",
                            severity=AlertSeverity.SUCCESS,
                            state_key="scanner",
                            state_value="OK",
                        )
            except Exception as exc:
                scan_metrics["consecutive_scan_failures"] += 1
                fails = scan_metrics["consecutive_scan_failures"]
                print(f"[API Scanner] Periodic scan notice ({fails} consecutive fails): {exc}")
                if fails in (3, 10):
                    with contextlib.suppress(Exception):
                        get_telegram_dispatcher().dispatch_system_alert(
                            event_type="SCANNER_FAILURE",
                            title="Scanner Failure Warning",
                            message=f"Consecutive scan failures: <b>{fails}</b>\nLatest notice: <code>{exc}</code>",
                            severity=AlertSeverity.WARNING,
                            state_key="scanner",
                            state_value=f"FAIL_{fails}",
                        )

    control_plane = ApexControlPlane(engine=engine, data_dir="var")
    set_control_plane(control_plane)
    control_plane.start()

    scan_thread = threading.Thread(target=_scan_loop, name="ApexApiScanner", daemon=True)
    scan_thread.start()

    server = ApexApiServer(engine=engine, host=args.host, port=args.port, control_plane=control_plane)

    def handle_signal(signum: int, frame: object) -> None:
        nonlocal stop_event
        stop_event = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    print(f"Starting Apex Read-Only API on http://{args.host}:{args.port}")
    server.start()

    try:
        while not stop_event:
            time.sleep(0.5)
    finally:
        print("\nShutting down Apex Read-Only API...")
        with contextlib.suppress(Exception):
            get_telegram_dispatcher().dispatch_system_alert(
                event_type="SERVICE_STOP",
                title="APEX API Service Stopped",
                message="Clean shutdown executed.",
                severity=AlertSeverity.INFO,
            )
        server.stop()
        with contextlib.suppress(Exception):
            control_plane.stop()
        with contextlib.suppress(Exception):
            engine.shutdown()

    return 0


if __name__ == "__main__":
    sys.exit(main())
