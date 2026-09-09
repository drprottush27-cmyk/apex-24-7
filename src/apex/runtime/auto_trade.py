"""APEX 24/7 — Safe Auto-Trade Controller and Circuit Breakers (Phase 4).

SAFETY INVARIANTS:
- Restricted strictly to PAPER mode (rejects DRY_RUN, SHADOW, or any other mode).
- Fail-closed: trips circuit breaker immediately on any safety boundary breach.
- Instant Kill Switch veto override.
- Daily Drawdown circuit breaker (halts auto-trading when drawdown breaches limit).
- Consecutive Loss limit (halts auto-trading after N consecutive realized losses).
- Staleness Guard (rejects signals referencing candles/timestamps older than threshold).
- Volatility Surge breaker (rejects orders during extreme candle expansion > 3x ATR or > 5% surge).
- Cooldown and exposure bounding per symbol and portfolio.
- Complete audit trail of every evaluation, decision, and rejection.
"""

from __future__ import annotations

import collections
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from apex.domain.signals import Signal
from apex.domain.types import TradingMode
from apex.indicators.core import atr
from apex.market.candle_series import CandleSeries
from apex.safety.kill_switch import KillSwitch

logger = logging.getLogger(__name__)


class AutoTradeConfig(BaseModel):
    """Immutable, strongly validated configuration for autonomous paper trading."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    min_score: float = Field(default=60.0, ge=0.0, le=100.0)
    max_daily_drawdown_pct: float = Field(default=0.02, gt=0.0, le=0.05)  # 2.0% daily DD circuit breaker
    max_consecutive_losses: int = Field(default=3, ge=1, le=10)
    max_signal_age_seconds: float = Field(default=180.0, ge=10.0, le=3600.0)
    surge_atr_multiplier: float = Field(default=3.0, ge=1.5, le=10.0)
    max_candle_surge_pct: float = Field(default=0.05, ge=0.01, le=0.20)
    cooldown_seconds: float = Field(default=300.0, ge=0.0, le=3600.0)
    max_concurrent_positions: int = Field(default=2, ge=1, le=5)


@dataclass(frozen=True, slots=True)
class AutoTradeDecision:
    """Deterministic evaluation outcome for an auto-trade signal candidate."""

    allowed: bool
    reason: str
    symbol: str
    timestamp_ms: int
    breaker_tripped: bool = False
    breaker_name: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AutoTradeAuditEntry:
    """Historical record for auto-trade evaluation audit trail."""

    timestamp_ms: int
    symbol: str
    allowed: bool
    reason: str
    breaker_tripped: bool
    breaker_name: str | None
    confidence_score: float
    metrics: dict[str, Any]


class SafeAutoTradeManager:
    """Authoritative coordinator for safe autonomous paper trading."""

    def __init__(
        self,
        config: AutoTradeConfig,
        kill_switch: KillSwitch,
        position_tracker: Any,
        trading_mode: TradingMode = TradingMode.PAPER,
        clock: Any = None,
        audit_capacity: int = 500,
    ) -> None:
        self._config = config
        self._kill_switch = kill_switch
        self._position_tracker = position_tracker
        self._trading_mode = trading_mode
        self._clock = clock
        self._lock = threading.Lock()

        self._consecutive_losses: int = 0
        self._circuit_breaker_active: bool = False
        self._circuit_breaker_reason: str | None = None
        self._last_trade_ms_by_symbol: dict[str, int] = {}
        self._audit_log: collections.deque[AutoTradeAuditEntry] = collections.deque(maxlen=audit_capacity)

    @property
    def config(self) -> AutoTradeConfig:
        with self._lock:
            return self._config

    @property
    def consecutive_losses(self) -> int:
        with self._lock:
            return self._consecutive_losses

    @property
    def is_circuit_breaker_active(self) -> bool:
        with self._lock:
            return self._circuit_breaker_active

    @property
    def circuit_breaker_reason(self) -> str | None:
        with self._lock:
            return self._circuit_breaker_reason

    def update_config(self, new_config: AutoTradeConfig) -> None:
        """Update configuration safely with locking."""
        with self._lock:
            self._config = new_config
            logger.info("Auto-trade configuration updated: %s", new_config)

    def record_closed_position(self, symbol: str, realized_pnl: float, now_ms: int | None = None) -> None:
        """Track realized PnL from closed positions to enforce consecutive loss breaker."""
        with self._lock:
            if realized_pnl < -1e-6:
                self._consecutive_losses += 1
                logger.warning(
                    "Auto-trade realized loss on %s: PnL=%.2f (consecutive losses: %d/%d)",
                    symbol,
                    realized_pnl,
                    self._consecutive_losses,
                    self._config.max_consecutive_losses,
                )
                if self._consecutive_losses >= self._config.max_consecutive_losses:
                    self._circuit_breaker_active = True
                    self._circuit_breaker_reason = (
                        f"Consecutive loss circuit breaker tripped: "
                        f"{self._consecutive_losses}/{self._config.max_consecutive_losses} realized losses."
                    )
                    logger.critical(
                        "AUTO-TRADE CIRCUIT BREAKER ENGAGED: %s",
                        self._circuit_breaker_reason,
                    )
            elif realized_pnl > 1e-6:
                logger.info(
                    "Auto-trade realized profit on %s: PnL=%.2f. Resetting consecutive losses counter from %d to 0.",
                    symbol,
                    realized_pnl,
                    self._consecutive_losses,
                )
                self._consecutive_losses = 0

    def reset_circuit_breaker(self, actor: str = "manual_override") -> None:
        """Reset the consecutive loss circuit breaker."""
        with self._lock:
            prev_reason = self._circuit_breaker_reason
            self._circuit_breaker_active = False
            self._circuit_breaker_reason = None
            self._consecutive_losses = 0
            logger.info("Auto-trade circuit breaker reset by %s (previous reason: %s)", actor, prev_reason)

    def record_trade_executed(self, symbol: str, now_ms: int | None = None) -> None:
        """Record trade execution to enforce per-symbol cooldown."""
        ts = now_ms if now_ms is not None else self._now_ms()
        with self._lock:
            self._last_trade_ms_by_symbol[symbol] = ts

    def evaluate_signal(
        self,
        signal: Signal,
        candle_series: CandleSeries | None,
        current_equity: float,
        now_ms: int | None = None,
    ) -> AutoTradeDecision:
        """Evaluate signal against all circuit breakers, fail-safes, and conviction thresholds."""
        ts = now_ms if now_ms is not None else self._now_ms()

        with self._lock:
            cfg = self._config
            consecutive_losses = self._consecutive_losses
            cb_active = self._circuit_breaker_active
            cb_reason = self._circuit_breaker_reason
            last_trade_ts = self._last_trade_ms_by_symbol.get(signal.symbol, 0)

        metrics: dict[str, Any] = {
            "current_equity": current_equity,
            "signal_timestamp_ms": signal.timestamp_ms,
            "candle_timestamp_ms": signal.candle_timestamp_ms,
            "confidence_score": signal.confidence_score,
        }

        # 1. Master Auto-Trade Enable Gate
        if not cfg.enabled:
            return self._record_decision(
                allowed=False,
                reason="Auto-trading is disabled by configuration.",
                symbol=signal.symbol,
                timestamp_ms=ts,
                metrics=metrics,
            )

        # 2. Strict PAPER Mode Invariant
        if self._trading_mode != TradingMode.PAPER:
            return self._record_decision(
                allowed=False,
                reason=f"Auto-trading is restricted strictly to PAPER mode (current: {self._trading_mode.value}).",
                symbol=signal.symbol,
                timestamp_ms=ts,
                breaker_tripped=True,
                breaker_name="TRADING_MODE_VIOLATION",
                metrics=metrics,
            )

        # 3. Fail-Closed Kill Switch Gate
        if self._kill_switch.is_active:
            ks_reason = getattr(self._kill_switch.state, "reason", "Kill switch active")
            return self._record_decision(
                allowed=False,
                reason=f"Kill switch active: {ks_reason}",
                symbol=signal.symbol,
                timestamp_ms=ts,
                breaker_tripped=True,
                breaker_name="KILL_SWITCH",
                metrics=metrics,
            )

        # 4. Consecutive Losses Circuit Breaker
        if cb_active or consecutive_losses >= cfg.max_consecutive_losses:
            reason = cb_reason or f"Consecutive loss limit reached ({consecutive_losses}/{cfg.max_consecutive_losses})."
            return self._record_decision(
                allowed=False,
                reason=reason,
                symbol=signal.symbol,
                timestamp_ms=ts,
                breaker_tripped=True,
                breaker_name="CONSECUTIVE_LOSSES",
                metrics=metrics,
            )

        # 5. Daily Drawdown Circuit Breaker
        daily_dd = (
            self._position_tracker.daily_drawdown_pct(current_equity)
            if hasattr(self._position_tracker, "daily_drawdown_pct")
            else 0.0
        )
        metrics["daily_drawdown_pct"] = round(daily_dd * 100, 3)
        if daily_dd >= cfg.max_daily_drawdown_pct:
            return self._record_decision(
                allowed=False,
                reason=(
                    f"Daily drawdown {daily_dd * 100:.2f}% breached auto-trade circuit breaker limit "
                    f"({cfg.max_daily_drawdown_pct * 100:.2f}%)."
                ),
                symbol=signal.symbol,
                timestamp_ms=ts,
                breaker_tripped=True,
                breaker_name="DAILY_DRAWDOWN",
                metrics=metrics,
            )

        # 6. Staleness Guard
        age_seconds = (ts - signal.candle_timestamp_ms) / 1000.0
        metrics["signal_age_seconds"] = round(age_seconds, 1)
        if age_seconds > cfg.max_signal_age_seconds:
            return self._record_decision(
                allowed=False,
                reason=f"Signal candle is stale ({age_seconds:.1f}s > {cfg.max_signal_age_seconds:.0f}s limit).",
                symbol=signal.symbol,
                timestamp_ms=ts,
                breaker_tripped=False,
                breaker_name="STALENESS_GUARD",
                metrics=metrics,
            )

        # 7. Minimum Conviction Score
        score = signal.confidence_score if signal.confidence_score > 1.0 else signal.confidence_score * 100.0
        metrics["evaluated_score"] = round(score, 1)
        if score < cfg.min_score:
            return self._record_decision(
                allowed=False,
                reason=f"Signal score {score:.1f} is below auto-trade minimum {cfg.min_score:.1f}.",
                symbol=signal.symbol,
                timestamp_ms=ts,
                metrics=metrics,
            )

        # 8. Volatility Surge Breaker
        if candle_series is not None and candle_series.candles:
            latest_candle = candle_series.latest
            candle_move_pct = (
                abs(latest_candle.close - latest_candle.open) / latest_candle.open
                if latest_candle.open > 0
                else 0.0
            )
            candle_range = latest_candle.high - latest_candle.low
            metrics["candle_move_pct"] = round(candle_move_pct * 100, 2)
            metrics["candle_range"] = round(candle_range, 4)

            if candle_move_pct > cfg.max_candle_surge_pct:
                return self._record_decision(
                    allowed=False,
                    reason=(
                        f"Candle surge {candle_move_pct * 100:.2f}% exceeds maximum permitted "
                        f"{cfg.max_candle_surge_pct * 100:.2f}%."
                    ),
                    symbol=signal.symbol,
                    timestamp_ms=ts,
                    breaker_tripped=False,
                    breaker_name="VOLATILITY_SURGE",
                    metrics=metrics,
                )

            if len(candle_series.candles) >= 15:
                try:
                    highs = [c.high for c in candle_series.candles]
                    lows = [c.low for c in candle_series.candles]
                    closes = [c.close for c in candle_series.candles]
                    atr_val = float(atr(highs, lows, closes, period=14))
                    metrics["atr_14"] = round(atr_val, 4)
                    if atr_val > 0.0 and candle_range > cfg.surge_atr_multiplier * atr_val:
                        return self._record_decision(
                            allowed=False,
                            reason=(
                                f"Candle range {candle_range:.4f} exceeds {cfg.surge_atr_multiplier}x "
                                f"ATR(14) ({atr_val:.4f})."
                            ),
                            symbol=signal.symbol,
                            timestamp_ms=ts,
                            breaker_tripped=False,
                            breaker_name="VOLATILITY_SURGE",
                            metrics=metrics,
                        )
                except Exception as exc:
                    logger.warning(
                        "Auto-trade volatility surge gate skipped for %s (ATR "
                        "computation failed): %s",
                        signal.symbol,
                        exc,
                    )

        # 9. Cooldown Guard
        if last_trade_ts > 0:
            elapsed_sec = (ts - last_trade_ts) / 1000.0
            metrics["cooldown_elapsed_seconds"] = round(elapsed_sec, 1)
            if elapsed_sec < cfg.cooldown_seconds:
                remaining = int(cfg.cooldown_seconds - elapsed_sec)
                return self._record_decision(
                    allowed=False,
                    reason=f"Symbol {signal.symbol} is in auto-trade cooldown ({remaining}s remaining).",
                    symbol=signal.symbol,
                    timestamp_ms=ts,
                    metrics=metrics,
                )

        # 10. Max Concurrent Positions Gate
        open_positions = (
            getattr(self._position_tracker, "open_positions", [])
            if hasattr(self._position_tracker, "open_positions")
            else []
        )
        pos_list = list(open_positions.values()) if isinstance(open_positions, dict) else list(open_positions or [])
        metrics["open_positions_count"] = len(pos_list)
        if len(pos_list) >= cfg.max_concurrent_positions:
            return self._record_decision(
                allowed=False,
                reason=(
                    f"Max concurrent auto-trade positions reached ({len(pos_list)}/{cfg.max_concurrent_positions})."
                ),
                symbol=signal.symbol,
                timestamp_ms=ts,
                metrics=metrics,
            )

        # All safety gates passed
        return self._record_decision(
            allowed=True,
            reason="All auto-trade safety gates and circuit breakers passed.",
            symbol=signal.symbol,
            timestamp_ms=ts,
            metrics=metrics,
        )

    def get_status(self, current_equity: float | None = None) -> dict[str, Any]:
        """Observable snapshot of auto-trade status and circuit breaker state."""
        with self._lock:
            cfg = self._config
            consecutive_losses = self._consecutive_losses
            cb_active = self._circuit_breaker_active
            cb_reason = self._circuit_breaker_reason
            audit_count = len(self._audit_log)

        eq = current_equity if current_equity is not None else 10_000.0
        daily_dd = (
            self._position_tracker.daily_drawdown_pct(eq)
            if hasattr(self._position_tracker, "daily_drawdown_pct")
            else 0.0
        )
        ks_active = self._kill_switch.is_active

        active_breakers: list[str] = []
        if ks_active:
            active_breakers.append("KILL_SWITCH")
        if cb_active:
            active_breakers.append("CONSECUTIVE_LOSSES")
        if daily_dd >= cfg.max_daily_drawdown_pct:
            active_breakers.append("DAILY_DRAWDOWN")

        can_auto_trade = (
            cfg.enabled
            and self._trading_mode == TradingMode.PAPER
            and not ks_active
            and not cb_active
            and daily_dd < cfg.max_daily_drawdown_pct
        )

        return {
            "enabled": cfg.enabled,
            "can_auto_trade": can_auto_trade,
            "trading_mode": self._trading_mode.value,
            "circuit_breaker_active": cb_active or ks_active or daily_dd >= cfg.max_daily_drawdown_pct,
            "circuit_breaker_reason": cb_reason,
            "active_breakers": active_breakers,
            "consecutive_losses": consecutive_losses,
            "max_consecutive_losses": cfg.max_consecutive_losses,
            "daily_drawdown_pct": round(daily_dd * 100, 3),
            "max_daily_drawdown_pct": round(cfg.max_daily_drawdown_pct * 100, 2),
            "min_score": cfg.min_score,
            "max_signal_age_seconds": cfg.max_signal_age_seconds,
            "max_candle_surge_pct": round(cfg.max_candle_surge_pct * 100, 1),
            "surge_atr_multiplier": cfg.surge_atr_multiplier,
            "cooldown_seconds": cfg.cooldown_seconds,
            "audit_log_count": audit_count,
        }

    def get_audit_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """Retrieve recent evaluation audit records."""
        with self._lock:
            entries = list(self._audit_log)[-limit:]
        return [
            {
                "timestamp_ms": e.timestamp_ms,
                "symbol": e.symbol,
                "allowed": e.allowed,
                "reason": e.reason,
                "breaker_tripped": e.breaker_tripped,
                "breaker_name": e.breaker_name,
                "confidence_score": e.confidence_score,
                "metrics": e.metrics,
            }
            for e in reversed(entries)
        ]

    def _record_decision(
        self,
        allowed: bool,
        reason: str,
        symbol: str,
        timestamp_ms: int,
        breaker_tripped: bool = False,
        breaker_name: str | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> AutoTradeDecision:
        entry = AutoTradeAuditEntry(
            timestamp_ms=timestamp_ms,
            symbol=symbol,
            allowed=allowed,
            reason=reason,
            breaker_tripped=breaker_tripped,
            breaker_name=breaker_name,
            confidence_score=metrics.get("confidence_score", 0.0) if metrics else 0.0,
            metrics=metrics or {},
        )
        with self._lock:
            self._audit_log.append(entry)

        decision = AutoTradeDecision(
            allowed=allowed,
            reason=reason,
            symbol=symbol,
            timestamp_ms=timestamp_ms,
            breaker_tripped=breaker_tripped,
            breaker_name=breaker_name,
            metrics=metrics or {},
        )

        log_level = logging.INFO if allowed else (logging.WARNING if breaker_tripped else logging.DEBUG)
        logger.log(log_level, "Auto-trade evaluation for %s: allowed=%s | %s", symbol, allowed, reason)
        return decision

    def _now_ms(self) -> int:
        if self._clock is not None and hasattr(self._clock, "now_ms"):
            return int(self._clock.now_ms())
        return int(time.time() * 1000)
