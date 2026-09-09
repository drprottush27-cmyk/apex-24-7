"""APEX 24/7 — Real-Time Active Trade Management (Phase Addendum).

Manages per-second real-time streaming and active risk control for open paper positions:
- Connects to Binance Futures WebSocket stream specifically for open positions:
    - {symbol}@markPrice@1s (sub-second mark price & funding rate)
    - {symbol}@aggTrade (trade volume & CVD)
    - {symbol}@forceOrder (liquidation cascades)
- Subscribes dynamically on position open; unsubscribes on position close.
- 1-Second Real-Time Evaluation:
    - Live mark-to-market unrealized P&L
    - Dynamic trailing stop ratchet ladder (+1.0R -> BE, +1.5R -> +0.75R, +2.0R -> +1.25R, +3.0R -> +2.0R)
    - Factor re-evaluation: SL breaches, adverse liquidation cascades, funding flips, volatility surges
    - Triggers AlertThenAutoCloseManager grace-period countdown
    - Executes deterministic fail-safe close via OEM safety chain upon grace period expiry
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import json
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from apex.domain.positions import Position
from apex.domain.types import ExitReason, PositionSide
from apex.runtime.autoclose import (
    AlertThenAutoCloseManager,
    AutoCloseConfig,
    RiskAlert,
    RiskType,
)
from apex.runtime.position_tracker import PositionTracker

logger = logging.getLogger(__name__)

BINANCE_FSTREAM_URL = "wss://fstream.binance.com/stream"


@dataclass(slots=True)
class RealTimeSymbolMetrics:
    """Live streaming metrics for an open position symbol."""

    symbol: str
    mark_price: float = 0.0
    funding_rate: float = 0.0
    next_funding_time_ms: int = 0
    cvd: float = 0.0
    recent_trades: collections.deque[tuple[int, float, float, bool]] = field(
        default_factory=lambda: collections.deque(maxlen=200)
    )
    recent_liquidations: collections.deque[dict[str, Any]] = field(
        default_factory=lambda: collections.deque(maxlen=200)
    )
    price_history_1m: collections.deque[tuple[int, float]] = field(
        default_factory=lambda: collections.deque(maxlen=120)
    )
    peak_r_multiple: float = 0.0
    trailing_stop_ratchet_count: int = 0
    last_update_ts_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "mark_price": round(self.mark_price, 6 if 0.0 < self.mark_price < 1.0 else 2),
            "funding_rate": round(self.funding_rate, 6),
            "next_funding_time_ms": self.next_funding_time_ms,
            "cvd": round(self.cvd, 4),
            "liquidations_count": len(self.recent_liquidations),
            "peak_r_multiple": round(self.peak_r_multiple, 3),
            "trailing_stop_ratchet_count": self.trailing_stop_ratchet_count,
            "last_update_ts_ms": self.last_update_ts_ms,
        }


class RealTimePositionManager:
    """Manages real-time WebSocket feeds and 1-second active trade evaluations."""

    def __init__(
        self,
        tracker: PositionTracker,
        autoclose_manager: AlertThenAutoCloseManager,
        engine: Any | None = None,
        ws_url: str = BINANCE_FSTREAM_URL,
        clock: Any = None,
    ) -> None:
        self._tracker = tracker
        self._autoclose_manager = autoclose_manager
        self._engine = engine
        self._ws_url = ws_url
        self._clock = clock
        self._lock = threading.Lock()

        self._symbol_metrics: dict[str, RealTimeSymbolMetrics] = {}
        self._subscribed_symbols: set[str] = set()
        self._ws_connected: bool = False
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def autoclose_manager(self) -> AlertThenAutoCloseManager:
        return self._autoclose_manager

    @property
    def config(self) -> AutoCloseConfig:
        return self._autoclose_manager.config

    def _now_ms(self) -> int:
        if self._clock is not None and hasattr(self._clock, "now_ms"):
            return int(self._clock.now_ms())
        return int(time.time() * 1000)

    def get_symbol_metrics(self, symbol: str) -> RealTimeSymbolMetrics | None:
        with self._lock:
            return self._symbol_metrics.get(symbol.upper())

    def record_mark_price(
        self,
        symbol: str,
        mark_price: float,
        funding_rate: float = 0.0,
        next_funding_time_ms: int = 0,
        now_ms: int | None = None,
    ) -> None:
        """Record a mark price update for a symbol."""
        if not math.isfinite(mark_price) or mark_price <= 0.0:
            return
        ts = now_ms if now_ms is not None else self._now_ms()
        sym = symbol.upper()

        with self._lock:
            metrics = self._symbol_metrics.get(sym)
            if metrics is None:
                metrics = RealTimeSymbolMetrics(symbol=sym)
                self._symbol_metrics[sym] = metrics

            metrics.mark_price = mark_price
            if funding_rate != 0.0:
                metrics.funding_rate = funding_rate
            if next_funding_time_ms > 0:
                metrics.next_funding_time_ms = next_funding_time_ms
            metrics.last_update_ts_ms = ts
            metrics.price_history_1m.append((ts, mark_price))

            # Prune price history older than 60 seconds
            cutoff = ts - 60_000
            while metrics.price_history_1m and metrics.price_history_1m[0][0] < cutoff:
                metrics.price_history_1m.popleft()

    def record_trade(
        self,
        symbol: str,
        price: float,
        qty: float,
        is_buyer: bool,
        now_ms: int | None = None,
    ) -> None:
        """Record an aggregate trade for CVD tracking."""
        if not math.isfinite(price) or price <= 0.0 or not math.isfinite(qty) or qty <= 0.0:
            return
        ts = now_ms if now_ms is not None else self._now_ms()
        sym = symbol.upper()

        with self._lock:
            metrics = self._symbol_metrics.get(sym)
            if metrics is None:
                metrics = RealTimeSymbolMetrics(symbol=sym)
                self._symbol_metrics[sym] = metrics

            signed_vol = qty if is_buyer else -qty
            metrics.cvd += signed_vol
            metrics.recent_trades.append((ts, price, qty, is_buyer))
            metrics.last_update_ts_ms = ts

    def record_liquidation(
        self,
        symbol: str,
        side: str,
        price: float,
        qty: float,
        now_ms: int | None = None,
    ) -> None:
        """Record a forceOrder liquidation event."""
        if not math.isfinite(price) or price <= 0.0 or not math.isfinite(qty) or qty <= 0.0:
            return
        ts = now_ms if now_ms is not None else self._now_ms()
        sym = symbol.upper()
        usd_value = price * qty

        with self._lock:
            metrics = self._symbol_metrics.get(sym)
            if metrics is None:
                metrics = RealTimeSymbolMetrics(symbol=sym)
                self._symbol_metrics[sym] = metrics

            liq_entry = {
                "ts": ts,
                "side": side.upper(),
                "price": price,
                "qty": qty,
                "usd_value": usd_value,
            }
            metrics.recent_liquidations.append(liq_entry)
            metrics.last_update_ts_ms = ts

            # Prune liquidations older than 120 seconds
            cutoff = ts - 120_000
            while metrics.recent_liquidations and metrics.recent_liquidations[0]["ts"] < cutoff:
                metrics.recent_liquidations.popleft()

    def _calculate_trailing_stop_target(
        self, position: Position, peak_r: float
    ) -> float | None:
        """Calculate target stop loss price based on peak R-multiple ladder."""
        cfg = self.config
        if not cfg.trailing_stop_enabled or position.risk_per_unit <= 0.0:
            return None

        # Tiered trailing stop ladder:
        # +3.0R -> lock +2.0R
        # +2.0R -> lock +1.25R
        # +1.5R -> lock +0.75R
        # +1.0R -> lock breakeven (entry price)
        if peak_r >= 3.0:
            step_r = 2.0
        elif peak_r >= 2.0:
            step_r = 1.25
        elif peak_r >= 1.5:
            step_r = 0.75
        elif peak_r >= 1.0:
            step_r = 0.0  # Breakeven
        else:
            return None

        r_unit = position.risk_per_unit
        if position.side == PositionSide.LONG:
            target_stop = position.entry_price + (step_r * r_unit)
        else:
            target_stop = position.entry_price - (step_r * r_unit)

        return target_stop

    def evaluate_second(self, now_ms: int | None = None) -> list[RiskAlert]:
        """Perform 1-second active evaluation across all open positions.

        Returns list of newly triggered alerts during this evaluation pass.
        """
        ts = now_ms if now_ms is not None else self._now_ms()
        new_alerts: list[RiskAlert] = []
        cfg = self.config

        # 1. Sync tracking symbols with currently open positions
        open_positions = self._tracker.open_positions
        active_symbols = {p.symbol for p in open_positions}

        # 2. Cancel alerts for closed positions
        with self._lock:
            for alert in self._autoclose_manager.get_all_active_alerts():
                if alert.symbol not in active_symbols:
                    self._autoclose_manager.cancel_alert(
                        alert.symbol, reason="Position closed", now_ms=ts
                    )

        # 3. Evaluate each active position
        for pos in open_positions:
            sym = pos.symbol
            metrics = self.get_symbol_metrics(sym)
            if metrics is None or metrics.mark_price <= 0.0:
                fallback_price = 0.0
                if self._engine is not None and hasattr(self._engine, "get_cached_series"):
                    series = self._engine.get_cached_series(sym)
                    if series is not None and getattr(series, "candles", None):
                        fallback_price = float(series.latest.close)
                if fallback_price <= 0.0:
                    fallback_price = pos.entry_price

                if fallback_price > 0.0:
                    self.record_mark_price(sym, fallback_price, now_ms=ts)
                    metrics = self.get_symbol_metrics(sym)

            if metrics is None or metrics.mark_price <= 0.0:
                continue

            current_price = metrics.mark_price

            # a. Mark to market
            self._tracker.mark_to_market(pos, current_price)

            # b. Compute current R-multiple
            risk_unit = pos.risk_per_unit if pos.risk_per_unit > 0.0 else abs(pos.entry_price - pos.stop_loss)
            if risk_unit > 0.0:
                if pos.side == PositionSide.LONG:
                    current_r = (current_price - pos.entry_price) / risk_unit
                else:
                    current_r = (pos.entry_price - current_price) / risk_unit

                with self._lock:
                    metrics.peak_r_multiple = max(metrics.peak_r_multiple, current_r)
                    peak_r = metrics.peak_r_multiple

                # c. Trailing stop ratcheting
                target_stop = self._calculate_trailing_stop_target(pos, peak_r)
                if target_stop is not None:
                    should_ratchet = False
                    if pos.side == PositionSide.LONG and target_stop > pos.stop_loss or pos.side == PositionSide.SHORT and target_stop < pos.stop_loss:
                        should_ratchet = True

                    if should_ratchet:
                        try:
                            updated_pos = self._tracker.update_stop_loss(
                                pos,
                                target_stop,
                                reason=f"Trailing stop ratcheted to {target_stop:.4f} at +{peak_r:.2f}R",
                            )
                            pos = updated_pos
                            with self._lock:
                                metrics.trailing_stop_ratchet_count += 1
                            logger.info(
                                "[TRAILING STOP] %s stop updated to %.4f (peak +%.2fR)",
                                sym,
                                target_stop,
                                peak_r,
                            )
                        except Exception as exc:
                            logger.error("[TRAILING STOP] Failed to ratchet stop for %s: %s", sym, exc)

            # d. Risk condition checks (only if no active alert already exists)
            active_alert = self._autoclose_manager.get_active_alert(sym)
            if active_alert is None:
                risk_detected: tuple[RiskType, str] | None = None

                # Check 1: Stop loss breach
                sl_breached = False
                if pos.side == PositionSide.LONG and current_price <= pos.stop_loss or pos.side == PositionSide.SHORT and current_price >= pos.stop_loss:
                    sl_breached = True

                if sl_breached:
                    risk_detected = (
                        RiskType.STOP_LOSS_BREACH,
                        f"Mark price {current_price} breached stop loss {pos.stop_loss}.",
                    )

                # Check 2: Adverse liquidation cluster in the last 60s
                if risk_detected is None:
                    cutoff_60s = ts - 60_000
                    with self._lock:
                        adverse_side = "SELL" if pos.side == PositionSide.LONG else "BUY"
                        adverse_liq_usd = sum(
                            liq["usd_value"]
                            for liq in metrics.recent_liquidations
                            if liq["ts"] >= cutoff_60s and liq["side"] == adverse_side
                        )
                    if adverse_liq_usd >= cfg.adverse_liquidation_threshold_usd:
                        risk_detected = (
                            RiskType.ADVERSE_LIQUIDATION,
                            (
                                f"Adverse liquidation cascade of ${adverse_liq_usd:,.0f} "
                                f"detected in last 60s (threshold ${cfg.adverse_liquidation_threshold_usd:,.0f})."
                            ),
                        )

                # Check 3: Funding rate flip against position
                if risk_detected is None and metrics.funding_rate != 0.0:
                    funding_threshold = cfg.funding_inversion_threshold
                    if pos.side == PositionSide.LONG and metrics.funding_rate >= funding_threshold:
                        risk_detected = (
                            RiskType.FUNDING_FLIP,
                            f"Funding rate inverted to +{metrics.funding_rate*100:.3f}% (longs pay penalty).",
                        )
                    elif pos.side == PositionSide.SHORT and metrics.funding_rate <= -funding_threshold:
                        risk_detected = (
                            RiskType.FUNDING_FLIP,
                            f"Funding rate inverted to {metrics.funding_rate*100:.3f}% (shorts pay penalty).",
                        )

                # Check 4: Volatility spike against position
                if risk_detected is None:
                    with self._lock:
                        if len(metrics.price_history_1m) >= 2:
                            first_price = metrics.price_history_1m[0][1]
                            pct_delta = (current_price - first_price) / first_price
                        else:
                            pct_delta = 0.0

                    if pos.side == PositionSide.LONG and pct_delta <= -cfg.volatility_spike_pct:
                        risk_detected = (
                            RiskType.VOLATILITY_SPIKE,
                            f"Rapid volatility drop of {pct_delta*100:.2f}% detected over last 60s.",
                        )
                    elif pos.side == PositionSide.SHORT and pct_delta >= cfg.volatility_spike_pct:
                        risk_detected = (
                            RiskType.VOLATILITY_SPIKE,
                            f"Rapid volatility surge of +{pct_delta*100:.2f}% detected over last 60s.",
                        )

                # Trigger risk alert if a genuine risk condition was identified
                if risk_detected is not None:
                    rtype, rmsg = risk_detected
                    pos_id = f"{pos.symbol}:{pos.entry_price}:{pos.opened_at_ms}"
                    unrealized = getattr(pos, "unrealized_pnl", 0.0)
                    triggered = self._autoclose_manager.trigger_risk_alert(
                        symbol=sym,
                        position_id=pos_id,
                        risk_type=rtype,
                        trigger_reason=rmsg,
                        mark_price=current_price,
                        stop_loss=pos.stop_loss,
                        unrealized_pnl=unrealized,
                        now_ms=ts,
                    )
                    if triggered is not None:
                        new_alerts.append(triggered)

        # 4. Check expired alerts and execute fail-safe auto-close
        expired_alerts = self._autoclose_manager.check_expired_alerts(now_ms=ts)
        for alert in expired_alerts:
            self._execute_autoclose(alert, now_ms=ts)

        return new_alerts

    def _execute_autoclose(self, alert: RiskAlert, now_ms: int) -> None:
        """Execute fail-safe position close upon grace period expiry."""
        sym = alert.symbol
        pos_id = alert.position_id
        logger.warning(
            "[AUTOCLOSE EXECUTION] Grace period expired for %s. Executing fail-safe close: %s.",
            sym,
            alert.trigger_reason,
        )

        if self._engine is not None and hasattr(self._engine, "force_close_position"):
            try:
                self._engine.force_close_position(
                    pos_id,
                    alert.mark_price,
                    reason=ExitReason.FAIL_SAFE,
                    details=f"Alert-then-autoclose expired: {alert.trigger_reason}",
                    now_ms=now_ms,
                )
                logger.info("[AUTOCLOSE EXECUTION] Position %s closed successfully via OEM.", pos_id)
            except Exception as exc:
                logger.error(
                    "[AUTOCLOSE EXECUTION] Failed to execute close for position %s: %s",
                    pos_id,
                    exc,
                )
        else:
            # No engine wired: FAIL-CLOSED by design. A direct tracker close here
            # would bypass the OEM/RiskGuardian/EndpointGuard safety chain, which is
            # never permitted. Surface the gap loudly for reconciliation instead.
            logger.critical(
                "[AUTOCLOSE EXECUTION] FAIL-CLOSED: no engine wired for %s; refusing "
                "unguarded direct close. Reconciliation required. Trigger: %s",
                pos_id,
                alert.trigger_reason,
            )

    def get_realtime_status(self) -> dict[str, Any]:
        """Observable status snapshot for API dashboard and WebSocket consumers."""
        with self._lock:
            symbols_data = {
                sym: m.to_dict() for sym, m in self._symbol_metrics.items()
            }
            return {
                "ws_connected": self._ws_connected,
                "subscribed_symbols": sorted(self._subscribed_symbols),
                "symbols_count": len(self._subscribed_symbols),
                "metrics": symbols_data,
                "autoclose": self._autoclose_manager.get_status_snapshot(),
            }

    # --- Background WebSocket Streaming Loop ---

    def _dispatch_ws_message(self, raw_msg: str) -> None:
        """Parse and dispatch raw Binance WebSocket stream frame."""
        try:
            msg = json.loads(raw_msg)
        except Exception:
            return

        if not isinstance(msg, dict):
            return

        if "data" in msg and "stream" in msg:
            stream = msg.get("stream", "")
            data = msg.get("data", {})
        else:
            data = msg
            stream = data.get("e", "")

        sym = data.get("s", "").upper()
        if not sym and "o" in data and isinstance(data["o"], dict):
            sym = data["o"].get("s", "").upper()

        if not sym:
            return

        ts = int(time.time() * 1000)
        event_type = data.get("e", "")

        if event_type == "markPriceUpdate" or "@markPrice" in stream:
            p_val = data.get("p")
            r_val = data.get("r")
            t_val = data.get("T", 0)
            if p_val is not None:
                try:
                    mark_p = float(p_val)
                    funding = float(r_val) if r_val is not None else 0.0
                    next_t = int(t_val) if t_val else 0
                    self.record_mark_price(sym, mark_p, funding, next_t, now_ms=ts)
                except (ValueError, TypeError):
                    pass
        elif event_type == "aggTrade" or "@aggTrade" in stream:
            p_val = data.get("p")
            q_val = data.get("q")
            is_buyer = not data.get("m", False)  # 'm' is buyer maker (seller was aggressor)
            if p_val is not None and q_val is not None:
                try:
                    price = float(p_val)
                    qty = float(q_val)
                    self.record_trade(sym, price, qty, is_buyer, now_ms=ts)
                except (ValueError, TypeError):
                    pass
        elif "@forceOrder" in stream:
            order_data = data.get("o", {})
            side = order_data.get("S", "")
            p_val = order_data.get("p")
            q_val = order_data.get("q")
            if p_val is not None and q_val is not None:
                try:
                    price = float(p_val)
                    qty = float(q_val)
                    self.record_liquidation(sym, side, price, qty, now_ms=ts)
                except (ValueError, TypeError):
                    pass

    async def _sleep_check(self, seconds: float) -> None:
        """Sleep with responsive check for stop event."""
        end = time.time() + seconds
        while time.time() < end and not self._stop_event.is_set():
            await asyncio.sleep(min(0.1, max(0.01, end - time.time())))

    async def _run_ws_loop(self) -> None:
        """Continuous background WebSocket client with dynamic subscription."""
        try:
            import websockets
        except ImportError:
            logger.warning("websockets package not available; running offline evaluation mode.")
            while not self._stop_event.is_set():
                self.evaluate_second()
                await self._sleep_check(1.0)
            return

        while not self._stop_event.is_set():
            # Get symbols for active open positions
            open_symbols = sorted({p.symbol.lower() for p in self._tracker.open_positions})
            if not open_symbols:
                with self._lock:
                    self._subscribed_symbols.clear()
                    self._ws_connected = False
                self.evaluate_second()
                await self._sleep_check(1.0)
                continue

            streams = []
            for s in open_symbols:
                streams.append(f"{s}@markPrice@1s")
                streams.append(f"{s}@aggTrade")
                streams.append(f"{s}@forceOrder")

            uri = f"{self._ws_url}?streams={'/'.join(streams)}"
            with self._lock:
                self._subscribed_symbols = {s.upper() for s in open_symbols}

            try:
                logger.info("[WS CLIENT] Connecting to Binance streams for %s...", open_symbols)
                async with websockets.connect(uri, ping_interval=20, close_timeout=5) as ws:
                    with self._lock:
                        self._ws_connected = True
                    logger.info("[WS CLIENT] Connected to %s", uri)

                    last_eval_time = time.time()
                    while not self._stop_event.is_set():
                        # Check if open symbols changed
                        curr_symbols = sorted({p.symbol.lower() for p in self._tracker.open_positions})
                        if curr_symbols != open_symbols:
                            logger.info("[WS CLIENT] Active positions changed (%s -> %s), reconnecting...", open_symbols, curr_symbols)
                            break

                        try:
                            # Non-blocking or short-timeout recv
                            raw_msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
                            if isinstance(raw_msg, str):
                                self._dispatch_ws_message(raw_msg)
                        except TimeoutError:
                            pass

                        # 1-second cadence evaluation
                        now_sec = time.time()
                        if now_sec - last_eval_time >= 1.0:
                            last_eval_time = now_sec
                            self.evaluate_second()

            except Exception as exc:
                with self._lock:
                    self._ws_connected = False
                if not self._stop_event.is_set():
                    logger.warning("[WS CLIENT] Disconnected (%s). Reconnecting in 3s...", exc)
                # Keep evaluating even while WS is reconnecting
                self.evaluate_second()
                await self._sleep_check(3.0)

    def start(self) -> None:
        """Start the real-time manager background worker thread."""
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return
        self._stop_event.clear()

        def _thread_target() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(self._run_ws_loop())
            finally:
                with contextlib.suppress(Exception):
                    self._loop.close()

        self._worker_thread = threading.Thread(
            target=_thread_target, name="ApexRealTimeWS", daemon=True
        )
        self._worker_thread.start()
        logger.info("Apex RealTimePositionManager started.")

    def stop(self) -> None:
        """Signal and stop the real-time manager."""
        self._stop_event.set()
        if self._worker_thread is not None and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=3.0)
        logger.info("Apex RealTimePositionManager stopped.")
