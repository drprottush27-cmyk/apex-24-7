"""APEX 24/7 — Tactical Signal Telegram Alert Formatting & Read-Only Dispatcher.

Produces structured, honest alert messages for Telegram with explicit
provenance indicators, REAL vs ESTIMATED counts, and suggested levels.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from apex.engines.tactical.model import TacticalResult

logger = logging.getLogger("apex.alerts.telegram")

TOKEN_RE = re.compile(r"bot[0-9]+:[a-zA-Z0-9_-]+")


def redact_secrets(text: str, token: str | None = None) -> str:
    """Sanitize any Telegram bot token from text, URL, or error messages."""
    if not text:
        return text
    if token and token in text:
        text = text.replace(token, "<REDACTED_BOT_TOKEN>")
    return TOKEN_RE.sub("bot<REDACTED>", text)


def format_telegram_signal_alert(
    result: TacticalResult,
    current_price: float,
    suggested_entry: float | None = None,
    suggested_stop_loss: float | None = None,
    suggested_take_profit: float | None = None,
) -> str:
    """Format an advisory signal alert for Telegram with honest provenance."""
    metadata = result.to_metadata()
    raw_details = metadata.get("component_details")
    component_details: dict[str, Any] = raw_details if isinstance(raw_details, dict) else {}

    real_count = sum(
        1
        for c in component_details.values()
        if isinstance(c, dict) and bool(c.get("available")) and not bool(c.get("is_estimated"))
    )
    proxy_count = sum(
        1
        for c in component_details.values()
        if isinstance(c, dict) and bool(c.get("available")) and bool(c.get("is_estimated"))
    )
    total_active = real_count + proxy_count

    f = result.features
    sfp_status = "BULLISH" if f.sfp_bullish else ("BEARISH" if f.sfp_bearish else "NONE")
    rs_str = f"{f.rs_percentile:.1f}%" if f.rs_percentile is not None else "N/A (Isolated)"

    def _fmt(val: float | None) -> str:
        if val is None:
            return "N/A"
        return f"${val:.6f}" if 0.0 < val < 1.0 else f"${val:.2f}"

    lines = [
        f"🚨 <b>[APEX SIGNAL ALERT] {result.symbol}</b>",
        f"<b>Verdict:</b> {result.verdict.value} | <b>Conviction Score:</b> {result.score:.1f}/100",
        f"<b>Provenance:</b> {real_count} REAL factors, {proxy_count} ESTIMATED PROXY (Active: {total_active})",
        "",
        "📊 <b>Key Technical Factors:</b>",
        f"• Volatility Regime: <code>{f.volatility_regime}</code> (BBW: {f.bbw_percentile:.1f}%, ATR Ratio: {f.atr_ratio:.2f})",
        f"• Relative Strength Rank: <code>{rs_str}</code>",
        f"• SFP Pattern: <code>{sfp_status}</code>",
        f"• Volume (RVOL): <code>{f.rvol:.2f}x</code>",
        f"• Directional Bias: <code>{f.directional_bias:+.2f}</code>",
        f"• Current Price: <code>{_fmt(current_price)}</code>",
    ]

    if suggested_entry is not None or suggested_stop_loss is not None:
        lines.extend(
            [
                "",
                "💡 <b>Suggested Levels (Advisory):</b>",
                f"• Entry Zone: <code>{_fmt(suggested_entry)}</code>",
                f"• Stop Loss: <code>{_fmt(suggested_stop_loss)}</code>",
                f"• Take Profit: <code>{_fmt(suggested_take_profit)}</code>",
            ]
        )

    lines.extend(
        [
            "",
            "⚠️ <i>ADVISORY ONLY — NOT FINANCIAL ADVICE. PAPER MODE ACTIVE.</i>",
        ]
    )

    return "\n".join(lines)


def format_telegram_engine_signal_alert(signal: Any) -> str:
    """Format an engine signal alert for Telegram with honest provenance."""
    direction = getattr(signal.direction, "value", str(signal.direction)).upper()
    entry = getattr(signal, "trigger_price", 0.0)
    sl = getattr(signal, "suggested_stop_loss", None)
    tp = getattr(signal, "suggested_take_profit", None)
    detector = getattr(signal, "detector_name", "UNKNOWN")
    score = getattr(signal, "confidence_score", 1.0) * 100.0

    def _fmt(val: float | None) -> str:
        if val is None:
            return "N/A"
        return f"${val:.6f}" if 0.0 < val < 1.0 else f"${val:.2f}"

    lines = [
        f"🚨 <b>[APEX ENGINE SIGNAL] {signal.symbol}</b>",
        f"<b>Direction:</b> {direction} | <b>Detector:</b> {detector} | <b>Confidence:</b> {score:.1f}%",
        f"<b>Candle Timestamp:</b> {signal.candle_timestamp_ms}",
        "",
        "💡 <b>Signal Parameters:</b>",
        f"• Trigger Price: <code>{_fmt(entry)}</code>",
        f"• Stop Loss: <code>{_fmt(sl)}</code>",
        f"• Take Profit: <code>{_fmt(tp)}</code>",
        "",
        "⚠️ <i>ADVISORY ONLY — NOT FINANCIAL ADVICE. PAPER MODE ACTIVE.</i>",
    ]
    return "\n".join(lines)


from enum import StrEnum


class AlertCategory(StrEnum):
    SYSTEM = "SYSTEM"
    MARKET = "MARKET"
    TRADING = "TRADING"
    RISK = "RISK"
    AUTOCLOSE = "AUTOCLOSE"
    ADMIN = "ADMIN"


class AlertSeverity(StrEnum):
    CRITICAL = "CRITICAL"  # 🔴
    WARNING = "WARNING"    # 🟠
    SIGNAL = "SIGNAL"      # 🟡
    SUCCESS = "SUCCESS"    # 🟢
    INFO = "INFO"          # 🔵


SEVERITY_EMOJI: dict[str, str] = {
    AlertSeverity.CRITICAL.value: "🔴",
    AlertSeverity.WARNING.value: "🟠",
    AlertSeverity.SIGNAL.value: "🟡",
    AlertSeverity.SUCCESS.value: "🟢",
    AlertSeverity.INFO.value: "🔵",
}


@dataclass
class AlertSettings:
    enabled: bool = True
    categories: dict[str, bool] = None  # type: ignore[assignment]
    cooldowns: dict[str, float] = None  # type: ignore[assignment]
    max_per_minute: int = 30

    def __post_init__(self) -> None:
        if self.categories is None:
            self.categories = {
                AlertCategory.SYSTEM.value: True,
                AlertCategory.MARKET.value: True,
                AlertCategory.TRADING.value: True,
                AlertCategory.RISK.value: True,
                AlertCategory.AUTOCLOSE.value: True,
                AlertCategory.ADMIN.value: True,
            }
        if self.cooldowns is None:
            self.cooldowns = {
                AlertCategory.SYSTEM.value: 60.0,
                AlertCategory.MARKET.value: 60.0,
                AlertCategory.TRADING.value: 10.0,
                AlertCategory.RISK.value: 30.0,
                AlertCategory.AUTOCLOSE.value: 15.0,
                AlertCategory.ADMIN.value: 60.0,
            }

    def is_category_enabled(self, category: str, severity: str = "INFO") -> bool:
        # CRITICAL safety alerts are NEVER suppressible by category or master switch
        if severity == AlertSeverity.CRITICAL.value:
            return True
        if not self.enabled:
            return False
        cat_upper = category.upper()
        return self.categories.get(cat_upper, True)

    def set_category(self, category: str, enabled: bool) -> bool:
        cat_upper = category.upper()
        if cat_upper in self.categories:
            self.categories[cat_upper] = enabled
            return True
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "categories": dict(self.categories),
            "cooldowns": dict(self.cooldowns),
            "max_per_minute": self.max_per_minute,
        }


# ── Telegram Alert Dispatcher (Read-Only & Advisory) ───────────────────────────


@dataclass
class TelegramConfig:
    bot_token: str = ""
    chat_id: str = ""
    enabled: bool = True
    api_base_url: str = "https://api.telegram.org"
    timeout_seconds: float = 5.0
    max_retries: int = 2
    retry_backoff_seconds: float = 0.5
    cooldown_seconds: float = 300.0

    @property
    def is_valid(self) -> bool:
        return bool(self.enabled and self.bot_token.strip() and self.chat_id.strip())

    @classmethod
    def from_env(cls) -> TelegramConfig:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        enabled_str = os.environ.get("TELEGRAM_ALERTS_ENABLED", "true").strip().lower()
        enabled = enabled_str in ("true", "1", "yes")
        return cls(bot_token=token, chat_id=chat_id, enabled=enabled)


@dataclass
class TelegramDeliveryResult:
    success: bool
    status_code: int | None = None
    error: str | None = None
    duplicate: bool = False
    message_id: int | None = None


@dataclass
class TelegramDispatcherStatus:
    configured: bool
    status: str  # "CONNECTED" | "UNAVAILABLE" | "DELIVERY_ERROR"
    total_sent: int = 0
    total_failed: int = 0
    total_duplicates_suppressed: int = 0
    last_sent_timestamp_ms: int | None = None
    last_error: str | None = None
    settings: dict[str, Any] | None = None
    audit_log_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "status": self.status,
            "total_sent": self.total_sent,
            "total_failed": self.total_failed,
            "total_duplicates_suppressed": self.total_duplicates_suppressed,
            "last_sent_timestamp_ms": self.last_sent_timestamp_ms,
            "last_error": self.last_error,
            "settings": self.settings,
            "audit_log_count": self.audit_log_count,
        }


class TelegramAlertDispatcher:
    """Production-grade read-only Telegram alert dispatcher.

    Strictly outbound advisory messaging. ZERO trade execution authority.
    Fails closed when unconfigured or when network errors occur.
    Guarantees secret redaction in all logging and error output.
    """

    def __init__(self, config: TelegramConfig | None = None, settings: AlertSettings | None = None) -> None:
        self.config = config or TelegramConfig.from_env()
        self.settings = settings or AlertSettings()
        self._sent_signatures: dict[str, int] = {}
        self._cooldown_tracker: dict[str, float] = {}
        self._state_cache: dict[str, Any] = {}
        self._rate_limit_window: list[float] = []
        self._audit_log: list[dict[str, Any]] = []
        self._total_sent = 0
        self._total_failed = 0
        self._total_duplicates = 0
        self._last_sent_ms: int | None = None
        self._last_error: str | None = None

    def get_status(self) -> TelegramDispatcherStatus:
        if not self.config.is_valid:
            status_str = "UNAVAILABLE"
        elif self._last_error and self._total_sent == 0:
            status_str = "DELIVERY_ERROR"
        else:
            status_str = "CONNECTED"

        return TelegramDispatcherStatus(
            configured=self.config.is_valid,
            status=status_str,
            total_sent=self._total_sent,
            total_failed=self._total_failed,
            total_duplicates_suppressed=self._total_duplicates,
            last_sent_timestamp_ms=self._last_sent_ms,
            last_error=self._last_error,
            settings=self.settings.to_dict(),
            audit_log_count=len(self._audit_log),
        )

    def is_duplicate(self, symbol: str, candle_timestamp_ms: int) -> bool:
        now_ms = int(time.time() * 1000)
        cooldown_ms = int(self.config.cooldown_seconds * 1000)
        cutoff = now_ms - max(cooldown_ms * 2, 3600_000)
        self._sent_signatures = {k: v for k, v in self._sent_signatures.items() if v >= cutoff}

        sig = f"{symbol}:{candle_timestamp_ms}"
        last_sent = self._sent_signatures.get(sig)
        return bool(last_sent is not None and (now_ms - last_sent) < cooldown_ms)

    def mark_sent(self, symbol: str, candle_timestamp_ms: int) -> None:
        sig = f"{symbol}:{candle_timestamp_ms}"
        self._sent_signatures[sig] = int(time.time() * 1000)

    def send_message(
        self,
        text: str,
        symbol: str | None = None,
        candle_timestamp_ms: int | None = None,
        is_critical: bool = False,
    ) -> TelegramDeliveryResult:
        if not (self.config.bot_token.strip() and self.config.chat_id.strip()):
            return TelegramDeliveryResult(
                success=False,
                error="TELEGRAM ALERTS UNAVAILABLE: Missing bot token or chat ID",
            )
        if not self.config.enabled and not is_critical:
            return TelegramDeliveryResult(
                success=False,
                error="Telegram alerts globally disabled",
            )

        if symbol and candle_timestamp_ms is not None and self.is_duplicate(symbol, candle_timestamp_ms):
            self._total_duplicates += 1
            return TelegramDeliveryResult(
                success=False,
                duplicate=True,
                error="Suppressed duplicate alert within cooldown window",
            )

        endpoint = f"{self.config.api_base_url.rstrip('/')}/bot{self.config.bot_token}/sendMessage"
        payload = {
            "chat_id": self.config.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        data_bytes = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "ApexTradingSystem/1.0",
        }

        last_err: str | None = None
        for attempt in range(1, self.config.max_retries + 1):
            req = urllib.request.Request(endpoint, data=data_bytes, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                    resp_body = resp.read().decode("utf-8")
                    parsed = json.loads(resp_body) if resp_body else {}
                    if parsed.get("ok"):
                        msg_id = parsed.get("result", {}).get("message_id")
                        now_ms = int(time.time() * 1000)
                        self._total_sent += 1
                        self._last_sent_ms = now_ms
                        self._last_error = None
                        if symbol and candle_timestamp_ms is not None:
                            self.mark_sent(symbol, candle_timestamp_ms)
                        return TelegramDeliveryResult(
                            success=True,
                            status_code=resp.status,
                            message_id=msg_id,
                        )
                    else:
                        last_err = redact_secrets(
                            f"Telegram API returned ok=false: {parsed.get('description', 'Unknown error')}",
                            self.config.bot_token,
                        )
            except urllib.error.HTTPError as exc:
                err_content = ""
                with contextlib.suppress(Exception):
                    err_content = exc.read().decode("utf-8", errors="replace")
                last_err = redact_secrets(
                    f"HTTP {exc.code}: {exc.reason} - {err_content}",
                    self.config.bot_token,
                )
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_err = redact_secrets(f"Network error: {exc}", self.config.bot_token)
            except Exception as exc:
                last_err = redact_secrets(f"Unexpected error: {exc}", self.config.bot_token)

            if attempt < self.config.max_retries:
                time.sleep(self.config.retry_backoff_seconds * attempt)

        self._total_failed += 1
        self._last_error = last_err
        logger.warning("Telegram alert delivery failed: %s", last_err)
        return TelegramDeliveryResult(success=False, error=last_err)

    def dispatch_signal_alert(
        self,
        result: TacticalResult,
        current_price: float,
        suggested_entry: float | None = None,
        suggested_stop_loss: float | None = None,
        suggested_take_profit: float | None = None,
    ) -> TelegramDeliveryResult:
        """Format and dispatch an advisory signal alert to Telegram."""
        text = format_telegram_signal_alert(
            result=result,
            current_price=current_price,
            suggested_entry=suggested_entry,
            suggested_stop_loss=suggested_stop_loss,
            suggested_take_profit=suggested_take_profit,
        )
        return self.send_message(
            text=text,
            symbol=result.symbol,
            candle_timestamp_ms=result.candle_timestamp_ms,
        )

    def dispatch_engine_signal_alert(self, signal: Any) -> TelegramDeliveryResult:
        """Format and dispatch an advisory engine signal alert to Telegram."""
        text = format_telegram_engine_signal_alert(signal)
        return self.send_message(
            text=text,
            symbol=signal.symbol,
            candle_timestamp_ms=signal.candle_timestamp_ms,
        )

    def _record_audit(
        self,
        event_type: str,
        category: str,
        severity: str,
        status: str,
        symbol: str | None = None,
        reason: str | None = None,
        message_id: int | None = None,
        title: str | None = None,
    ) -> None:
        entry = {
            "timestamp_ms": int(time.time() * 1000),
            "event_type": event_type,
            "category": category,
            "severity": severity,
            "symbol": symbol,
            "title": title or event_type,
            "status": status,
            "reason": reason,
            "message_id": message_id,
        }
        self._audit_log.append(entry)
        if len(self._audit_log) > 500:
            self._audit_log = self._audit_log[-500:]

    def get_audit_log(self, limit: int = 50) -> list[dict[str, Any]]:
        return list(reversed(self._audit_log[-limit:]))

    def update_category(self, category: str, enabled: bool) -> bool:
        return self.settings.set_category(category, enabled)

    def set_alerts_enabled(self, enabled: bool) -> None:
        self.settings.enabled = enabled

    def dispatch_alert(
        self,
        *,
        category: str | AlertCategory,
        severity: str | AlertSeverity,
        event_type: str,
        title: str,
        message: str,
        symbol: str | None = None,
        key: str | None = None,
        state_value: Any | None = None,
        candle_timestamp_ms: int | None = None,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TelegramDeliveryResult:
        cat_val = getattr(category, "value", str(category)).upper()
        sev_val = getattr(severity, "value", str(severity)).upper()
        emoji = SEVERITY_EMOJI.get(sev_val, "ℹ️")

        # 1. Category and master enable check (CRITICAL is NEVER suppressed)
        if not self.settings.is_category_enabled(cat_val, sev_val):
            self._record_audit(
                event_type=event_type,
                category=cat_val,
                severity=sev_val,
                status="SUPPRESSED_CATEGORY_DISABLED",
                symbol=symbol,
                reason=f"Alert category {cat_val} is disabled",
                title=title,
            )
            return TelegramDeliveryResult(
                success=False,
                error=f"Alert category {cat_val} is disabled",
            )

        # 2. State-change-only deduplication
        if key is not None and state_value is not None:
            cache_key = f"state:{cat_val}:{key}"
            prev_val = self._state_cache.get(cache_key)
            if prev_val == state_value:
                self._total_duplicates += 1
                self._record_audit(
                    event_type=event_type,
                    category=cat_val,
                    severity=sev_val,
                    status="SUPPRESSED_STATE_UNCHANGED",
                    symbol=symbol,
                    reason=f"State unchanged ({state_value})",
                    title=title,
                )
                return TelegramDeliveryResult(
                    success=False,
                    duplicate=True,
                    error="State unchanged",
                )
            self._state_cache[cache_key] = state_value

        # 3. Cooldown check (non-critical only)
        now_sec = time.time()
        cooldown_sec = self.settings.cooldowns.get(cat_val, self.config.cooldown_seconds)
        cd_key = f"cd:{cat_val}:{key or event_type}:{symbol or ''}"
        if sev_val != AlertSeverity.CRITICAL.value:
            last_sent = self._cooldown_tracker.get(cd_key)
            if last_sent is not None and (now_sec - last_sent) < cooldown_sec:
                rem = int(cooldown_sec - (now_sec - last_sent))
                self._total_duplicates += 1
                self._record_audit(
                    event_type=event_type,
                    category=cat_val,
                    severity=sev_val,
                    status="SUPPRESSED_COOLDOWN",
                    symbol=symbol,
                    reason=f"Cooldown active ({rem}s remaining)",
                    title=title,
                )
                return TelegramDeliveryResult(
                    success=False,
                    duplicate=True,
                    error=f"Cooldown active ({rem}s remaining)",
                )

        # 4. Rate limiting check (non-critical only)
        cutoff = now_sec - 60.0
        self._rate_limit_window = [t for t in self._rate_limit_window if t >= cutoff]
        if len(self._rate_limit_window) >= self.settings.max_per_minute and sev_val != AlertSeverity.CRITICAL.value:
            self._record_audit(
                event_type=event_type,
                category=cat_val,
                severity=sev_val,
                status="SUPPRESSED_RATE_LIMIT",
                symbol=symbol,
                reason="Max alerts per minute reached",
                title=title,
            )
            return TelegramDeliveryResult(
                success=False,
                error="Rate limit exceeded",
            )

        # 5. Format message
        import datetime
        utc_now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        sym_str = f" | <b>{symbol}</b>" if symbol else ""
        run_str = f"\n<code>Run: {run_id}</code>" if run_id else ""

        formatted_text = (
            f"{emoji} <b>[{cat_val} | {sev_val}] {title}</b>{sym_str}\n"
            f"📅 <code>{utc_now} UTC</code>{run_str}\n\n"
            f"{message}\n\n"
            f"⚠️ <i>APEX 24/7 — PAPER MODE ADVISORY</i>"
        )

        # 6. Delivery
        is_crit = (sev_val == AlertSeverity.CRITICAL.value)
        res = self.send_message(
            text=formatted_text,
            symbol=symbol,
            candle_timestamp_ms=candle_timestamp_ms,
            is_critical=is_crit,
        )

        if res.success:
            self._cooldown_tracker[cd_key] = now_sec
            self._rate_limit_window.append(now_sec)
            self._record_audit(
                event_type=event_type,
                category=cat_val,
                severity=sev_val,
                status="SENT",
                symbol=symbol,
                message_id=res.message_id,
                title=title,
            )
        elif res.duplicate:
            self._record_audit(
                event_type=event_type,
                category=cat_val,
                severity=sev_val,
                status="DUPLICATE",
                symbol=symbol,
                reason=res.error,
                title=title,
            )
        else:
            self._record_audit(
                event_type=event_type,
                category=cat_val,
                severity=sev_val,
                status="FAILED",
                symbol=symbol,
                reason=res.error,
                title=title,
            )

        return res

    def dispatch_system_alert(
        self,
        event_type: str,
        title: str,
        message: str,
        severity: str | AlertSeverity = AlertSeverity.INFO,
        state_key: str | None = None,
        state_value: Any | None = None,
        run_id: str | None = None,
    ) -> TelegramDeliveryResult:
        return self.dispatch_alert(
            category=AlertCategory.SYSTEM,
            severity=severity,
            event_type=event_type,
            title=title,
            message=message,
            key=state_key,
            state_value=state_value,
            run_id=run_id,
        )

    def dispatch_market_alert(
        self,
        event_type: str,
        symbol: str,
        title: str,
        message: str,
        severity: str | AlertSeverity = AlertSeverity.SIGNAL,
        candle_timestamp_ms: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TelegramDeliveryResult:
        return self.dispatch_alert(
            category=AlertCategory.MARKET,
            severity=severity,
            event_type=event_type,
            title=title,
            message=message,
            symbol=symbol,
            key=f"{event_type}:{symbol}",
            candle_timestamp_ms=candle_timestamp_ms,
            metadata=metadata,
        )

    def dispatch_trading_alert(
        self,
        event_type: str,
        symbol: str,
        title: str,
        message: str,
        severity: str | AlertSeverity = AlertSeverity.SUCCESS,
        key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TelegramDeliveryResult:
        return self.dispatch_alert(
            category=AlertCategory.TRADING,
            severity=severity,
            event_type=event_type,
            title=title,
            message=message,
            symbol=symbol,
            key=key or f"{event_type}:{symbol}",
            metadata=metadata,
        )

    def dispatch_risk_alert(
        self,
        event_type: str,
        title: str,
        message: str,
        symbol: str | None = None,
        severity: str | AlertSeverity = AlertSeverity.WARNING,
        key: str | None = None,
        state_value: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TelegramDeliveryResult:
        return self.dispatch_alert(
            category=AlertCategory.RISK,
            severity=severity,
            event_type=event_type,
            title=title,
            message=message,
            symbol=symbol,
            key=key or f"{event_type}:{symbol or 'system'}",
            state_value=state_value,
            metadata=metadata,
        )

    def dispatch_autoclose_alert(
        self,
        event_type: str,
        symbol: str,
        title: str,
        message: str,
        severity: str | AlertSeverity = AlertSeverity.WARNING,
        key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TelegramDeliveryResult:
        return self.dispatch_alert(
            category=AlertCategory.AUTOCLOSE,
            severity=severity,
            event_type=event_type,
            title=title,
            message=message,
            symbol=symbol,
            key=key or f"{event_type}:{symbol}",
            metadata=metadata,
        )

    def dispatch_admin_alert(
        self,
        event_type: str,
        title: str,
        message: str,
        severity: str | AlertSeverity = AlertSeverity.WARNING,
        metadata: dict[str, Any] | None = None,
    ) -> TelegramDeliveryResult:
        return self.dispatch_alert(
            category=AlertCategory.ADMIN,
            severity=severity,
            event_type=event_type,
            title=title,
            message=message,
            metadata=metadata,
        )


_global_dispatcher: TelegramAlertDispatcher | None = None


def get_telegram_dispatcher() -> TelegramAlertDispatcher:
    global _global_dispatcher
    if _global_dispatcher is None:
        _global_dispatcher = TelegramAlertDispatcher()
    return _global_dispatcher


def set_telegram_dispatcher(dispatcher: TelegramAlertDispatcher | None) -> None:
    global _global_dispatcher
    _global_dispatcher = dispatcher
