"""APEX 24/7 — Alert-Then-Autoclose Risk Override & Grace Period Protocol.

SAFETY INVARIANTS:
- Fail-closed: if KillSwitch is active, positions must already be flat/blocked.
- Protective-by-default: alert-then-autoclose defaults to ENABLED.
- Gated override: user can explicitly override to HOLD during the grace period.
- Automatic execution: if grace period expires without user response, position
  is automatically closed via the OEM / danger management safety chain.
- Complete audit logging preserving provenance for all alerts, overrides, and auto-closes.
"""

from __future__ import annotations

import collections
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class RiskType(StrEnum):
    """Categorization of genuine risk conditions triggering alert-then-autoclose."""

    STOP_LOSS_BREACH = "STOP_LOSS_BREACH"
    ADVERSE_LIQUIDATION = "ADVERSE_LIQUIDATION"
    FUNDING_FLIP = "FUNDING_FLIP"
    VOLATILITY_SPIKE = "VOLATILITY_SPIKE"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"


class AlertStatus(StrEnum):
    """Lifecycle states of a risk override alert."""

    ACTIVE_GRACE_PERIOD = "ACTIVE_GRACE_PERIOD"
    OVERRIDDEN_HOLD = "OVERRIDDEN_HOLD"
    CONFIRMED_CLOSE = "CONFIRMED_CLOSE"
    AUTOCLOSE_EXECUTED = "AUTOCLOSE_EXECUTED"
    CANCELLED = "CANCELLED"


class AutoCloseConfig(BaseModel):
    """Configuration for alert-then-autoclose risk override."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True
    grace_period_seconds: int = Field(default=60, ge=15, le=300)
    trailing_stop_enabled: bool = True
    trailing_activation_r: float = Field(default=1.0, ge=0.5, le=3.0)
    trailing_step_r: float = Field(default=0.5, ge=0.25, le=2.0)
    adverse_liquidation_threshold_usd: float = Field(default=50_000.0, ge=1_000.0)
    funding_inversion_threshold: float = Field(default=0.0005, ge=0.0001, le=0.01)
    volatility_spike_pct: float = Field(default=0.02, ge=0.005, le=0.10)


@dataclass(slots=True)
class RiskAlert:
    """Active risk alert instance governing an open position grace period."""

    alert_id: str
    symbol: str
    position_id: str
    risk_type: RiskType
    trigger_reason: str
    mark_price: float
    stop_loss: float
    unrealized_pnl: float
    triggered_at_ms: int
    grace_period_seconds: int
    expires_at_ms: int
    status: AlertStatus = AlertStatus.ACTIVE_GRACE_PERIOD
    override_reason: str | None = None
    resolved_at_ms: int | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.status == AlertStatus.ACTIVE_GRACE_PERIOD

    def remaining_seconds(self, now_ms: int) -> int:
        if not self.is_active:
            return 0
        return max(0, (self.expires_at_ms - now_ms) // 1000)

    def to_dict(self, now_ms: int | None = None) -> dict[str, Any]:
        ts = now_ms if now_ms is not None else int(time.time() * 1000)
        return {
            "alert_id": self.alert_id,
            "symbol": self.symbol,
            "position_id": self.position_id,
            "risk_type": self.risk_type.value,
            "trigger_reason": self.trigger_reason,
            "mark_price": round(self.mark_price, 6 if self.mark_price < 1.0 else 2),
            "stop_loss": round(self.stop_loss, 6 if self.stop_loss < 1.0 else 2),
            "unrealized_pnl": round(self.unrealized_pnl, 2),
            "triggered_at_ms": self.triggered_at_ms,
            "grace_period_seconds": self.grace_period_seconds,
            "expires_at_ms": self.expires_at_ms,
            "remaining_seconds": self.remaining_seconds(ts),
            "status": self.status.value,
            "override_reason": self.override_reason,
            "resolved_at_ms": self.resolved_at_ms,
            "metrics": self.metrics,
        }


@dataclass(frozen=True, slots=True)
class AutoCloseAuditEntry:
    """Historical audit record for auto-close events and user overrides."""

    timestamp_ms: int
    alert_id: str
    symbol: str
    position_id: str
    risk_type: str
    event: str  # "ALERT_TRIGGERED", "OVERRIDDEN_HOLD", "CONFIRMED_CLOSE", "AUTOCLOSE_EXECUTED", "ALERT_CANCELLED"
    details: str
    mark_price: float
    unrealized_pnl: float
    user_override: bool = False
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp_ms": self.timestamp_ms,
            "alert_id": self.alert_id,
            "symbol": self.symbol,
            "position_id": self.position_id,
            "risk_type": self.risk_type,
            "event": self.event,
            "details": self.details,
            "mark_price": self.mark_price,
            "unrealized_pnl": self.unrealized_pnl,
            "user_override": self.user_override,
            "metrics": self.metrics,
        }


class AlertThenAutoCloseManager:
    """Authoritative coordinator for alert-then-autoclose risk override protocol."""

    def __init__(
        self,
        config: AutoCloseConfig | None = None,
        clock: Any = None,
        audit_capacity: int = 500,
    ) -> None:
        self._config = config or AutoCloseConfig()
        self._clock = clock
        self._lock = threading.Lock()
        self._active_alerts: dict[str, RiskAlert] = {}  # symbol -> RiskAlert
        self._audit_log: collections.deque[AutoCloseAuditEntry] = collections.deque(maxlen=audit_capacity)

    @property
    def config(self) -> AutoCloseConfig:
        with self._lock:
            return self._config

    def update_config(self, new_config: AutoCloseConfig) -> None:
        """Safely update configuration under lock."""
        with self._lock:
            self._config = new_config
            logger.info("AlertThenAutoCloseManager configuration updated: %s", new_config)

    def _now_ms(self) -> int:
        if self._clock is not None and hasattr(self._clock, "now_ms"):
            return int(self._clock.now_ms())
        return int(time.time() * 1000)

    def get_active_alert(self, symbol: str) -> RiskAlert | None:
        """Return active risk alert for symbol, if any."""
        with self._lock:
            alert = self._active_alerts.get(symbol.upper())
            if alert and alert.is_active:
                return alert
            return None

    def get_all_active_alerts(self) -> list[RiskAlert]:
        """Return all currently active grace-period alerts."""
        with self._lock:
            return [a for a in self._active_alerts.values() if a.is_active]

    def trigger_risk_alert(
        self,
        symbol: str,
        position_id: str,
        risk_type: RiskType,
        trigger_reason: str,
        mark_price: float,
        stop_loss: float,
        unrealized_pnl: float,
        metrics: dict[str, Any] | None = None,
        now_ms: int | None = None,
    ) -> RiskAlert | None:
        """Trigger an alert-then-autoclose event.

        Starts the grace period countdown if enabled.
        Returns the RiskAlert instance, or None if disabled.
        """
        ts = now_ms if now_ms is not None else self._now_ms()
        sym = symbol.upper()

        with self._lock:
            if not self._config.enabled:
                logger.info("Alert-then-autoclose is disabled. Immediate action prescribed for %s.", sym)
                return None

            # If an active alert already exists for this symbol, do not reset countdown
            existing = self._active_alerts.get(sym)
            if existing and existing.is_active:
                return existing

            grace_sec = self._config.grace_period_seconds
            expires_at = ts + (grace_sec * 1000)
            alert_id = f"alert-{uuid.uuid4().hex[:8]}"

            alert = RiskAlert(
                alert_id=alert_id,
                symbol=sym,
                position_id=position_id,
                risk_type=risk_type,
                trigger_reason=trigger_reason,
                mark_price=mark_price,
                stop_loss=stop_loss,
                unrealized_pnl=unrealized_pnl,
                triggered_at_ms=ts,
                grace_period_seconds=grace_sec,
                expires_at_ms=expires_at,
                status=AlertStatus.ACTIVE_GRACE_PERIOD,
                metrics=metrics or {},
            )
            self._active_alerts[sym] = alert

            audit = AutoCloseAuditEntry(
                timestamp_ms=ts,
                alert_id=alert_id,
                symbol=sym,
                position_id=position_id,
                risk_type=risk_type.value,
                event="ALERT_TRIGGERED",
                details=f"Risk override triggered: {trigger_reason}. Grace period: {grace_sec}s.",
                mark_price=mark_price,
                unrealized_pnl=unrealized_pnl,
                user_override=False,
                metrics=metrics or {},
            )
            self._audit_log.append(audit)

        logger.warning(
            "[AUTOCLOSE ALERT] %s risk detected (%s): %s. Grace period %ds started. Autoclose at %d.",
            sym,
            risk_type.value,
            trigger_reason,
            grace_sec,
            expires_at,
        )
        return alert

    def override_hold(
        self,
        symbol: str,
        reason: str = "User manual override (HOLD)",
        now_ms: int | None = None,
    ) -> bool:
        """User explicitly overrides the auto-close to hold position open."""
        ts = now_ms if now_ms is not None else self._now_ms()
        sym = symbol.upper()

        with self._lock:
            alert = self._active_alerts.get(sym)
            if not alert or not alert.is_active:
                return False

            alert.status = AlertStatus.OVERRIDDEN_HOLD
            alert.override_reason = reason
            alert.resolved_at_ms = ts

            audit = AutoCloseAuditEntry(
                timestamp_ms=ts,
                alert_id=alert.alert_id,
                symbol=sym,
                position_id=alert.position_id,
                risk_type=alert.risk_type.value,
                event="OVERRIDDEN_HOLD",
                details=f"Autoclose overridden by user: {reason}. Position remains open.",
                mark_price=alert.mark_price,
                unrealized_pnl=alert.unrealized_pnl,
                user_override=True,
            )
            self._audit_log.append(audit)

        logger.info(
            "[AUTOCLOSE OVERRIDE] User overrode autoclose for %s (Alert %s): %s",
            sym,
            alert.alert_id,
            reason,
        )
        return True

    def confirm_immediate_close(
        self,
        symbol: str,
        reason: str = "User confirmed immediate close",
        now_ms: int | None = None,
    ) -> bool:
        """User confirms immediate position close during the grace period."""
        ts = now_ms if now_ms is not None else self._now_ms()
        sym = symbol.upper()

        with self._lock:
            alert = self._active_alerts.get(sym)
            if not alert or not alert.is_active:
                return False

            alert.status = AlertStatus.CONFIRMED_CLOSE
            alert.override_reason = reason
            alert.resolved_at_ms = ts

            audit = AutoCloseAuditEntry(
                timestamp_ms=ts,
                alert_id=alert.alert_id,
                symbol=sym,
                position_id=alert.position_id,
                risk_type=alert.risk_type.value,
                event="CONFIRMED_CLOSE",
                details=f"Immediate close confirmed by user: {reason}.",
                mark_price=alert.mark_price,
                unrealized_pnl=alert.unrealized_pnl,
                user_override=True,
            )
            self._audit_log.append(audit)

        logger.info(
            "[AUTOCLOSE CONFIRMED] User confirmed immediate close for %s (Alert %s)",
            sym,
            alert.alert_id,
        )
        return True

    def check_expired_alerts(self, now_ms: int | None = None) -> list[RiskAlert]:
        """Check all active alerts and return those whose grace period has expired.

        Transitions expired alerts to AUTOCLOSE_EXECUTED and logs the execution.
        """
        ts = now_ms if now_ms is not None else self._now_ms()
        expired: list[RiskAlert] = []

        with self._lock:
            for sym, alert in list(self._active_alerts.items()):
                if alert.is_active and ts >= alert.expires_at_ms:
                    alert.status = AlertStatus.AUTOCLOSE_EXECUTED
                    alert.resolved_at_ms = ts
                    expired.append(alert)

                    audit = AutoCloseAuditEntry(
                        timestamp_ms=ts,
                        alert_id=alert.alert_id,
                        symbol=sym,
                        position_id=alert.position_id,
                        risk_type=alert.risk_type.value,
                        event="AUTOCLOSE_EXECUTED",
                        details=(
                            f"Grace period expired ({alert.grace_period_seconds}s) with no user override. "
                            f"Auto-closing position. Reason: {alert.trigger_reason}."
                        ),
                        mark_price=alert.mark_price,
                        unrealized_pnl=alert.unrealized_pnl,
                        user_override=False,
                    )
                    self._audit_log.append(audit)

        for alert in expired:
            logger.warning(
                "[AUTOCLOSE EXECUTED] Grace period expired for %s (Alert %s). Automatic close executed: %s.",
                alert.symbol,
                alert.alert_id,
                alert.trigger_reason,
            )

        return expired

    def cancel_alert(self, symbol: str, reason: str = "Position closed or normalized", now_ms: int | None = None) -> None:
        """Cancel an active alert when the position is closed or normalized."""
        ts = now_ms if now_ms is not None else self._now_ms()
        sym = symbol.upper()

        with self._lock:
            alert = self._active_alerts.pop(sym, None)
            if alert and alert.is_active:
                alert.status = AlertStatus.CANCELLED
                alert.resolved_at_ms = ts
                alert.override_reason = reason

                audit = AutoCloseAuditEntry(
                    timestamp_ms=ts,
                    alert_id=alert.alert_id,
                    symbol=sym,
                    position_id=alert.position_id,
                    risk_type=alert.risk_type.value,
                    event="ALERT_CANCELLED",
                    details=f"Alert cancelled: {reason}.",
                    mark_price=alert.mark_price,
                    unrealized_pnl=alert.unrealized_pnl,
                    user_override=False,
                )
                self._audit_log.append(audit)

    def get_status_snapshot(self, now_ms: int | None = None) -> dict[str, Any]:
        """Observable snapshot of autoclose status, active alerts, and audit count."""
        ts = now_ms if now_ms is not None else self._now_ms()
        with self._lock:
            active = [a.to_dict(ts) for a in self._active_alerts.values() if a.is_active]
            return {
                "enabled": self._config.enabled,
                "grace_period_seconds": self._config.grace_period_seconds,
                "trailing_stop_enabled": self._config.trailing_stop_enabled,
                "active_alerts_count": len(active),
                "active_alerts": active,
                "audit_log_count": len(self._audit_log),
            }

    def get_audit_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """Retrieve recent audit history entries."""
        with self._lock:
            return [e.to_dict() for e in list(self._audit_log)[-limit:]]
