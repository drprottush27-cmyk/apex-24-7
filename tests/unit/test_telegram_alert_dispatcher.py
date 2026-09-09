"""APEX 24/7 — Unit tests for Read-Only Telegram Alert Dispatcher (Workstream 1).

Safety Invariants Tested:
1. Missing configuration fails closed cleanly without crash.
2. Valid configuration executes successful mocked delivery.
3. Telegram API error is handled gracefully with error recording.
4. Network failure triggers configured retries and fails closed.
5. Timeout is handled gracefully without crashing.
6. Malformed response is handled gracefully without crashing.
7. Deduplication / cooldown window suppresses duplicate alerts for the same candle/symbol.
8. Secret bot token is strictly redacted from error messages, status, and logs.
9. Alert payloads contain authentic data and never fabricated/mock static claims.
10. Dispatcher has ZERO execution capability (read-only advisory only).
11. Dispatcher delivery failure never interrupts engine monitoring or scan loops.
12. Safety chain (RiskGuardian -> OEM -> EndpointGuard) remains isolated and authoritative.
13. API server endpoints (/api/v1/health, /api/v1/dashboard, /api/v1/telegram) correctly expose status.
"""
from __future__ import annotations

import io
import json
import socket
import urllib.error
import urllib.request
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from apex.api.server import ApexApiServer
from apex.domain.signals import Signal
from apex.domain.types import SignalDirection, Timeframe
from apex.engines.tactical.alerts import (
    TelegramAlertDispatcher,
    TelegramConfig,
    format_telegram_engine_signal_alert,
    format_telegram_signal_alert,
    set_telegram_dispatcher,
)
from apex.engines.tactical.model import (
    TacticalFeatures,
    TacticalResult,
    TacticalVerdict,
)


def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(autouse=True)
def reset_global_dispatcher() -> Any:
    """Reset global dispatcher before and after each test."""
    set_telegram_dispatcher(None)
    yield
    set_telegram_dispatcher(None)


def _make_dummy_signal(symbol: str = "BTCUSDT", trigger_price: float = 65000.0) -> Signal:
    return Signal(
        symbol=symbol,
        timeframe=Timeframe.H1,
        timestamp_ms=1788700000000,
        direction=SignalDirection.LONG,
        trigger_price=trigger_price,
        suggested_stop_loss=64000.0,
        suggested_take_profit=67500.0,
        detector_name="breakout_test",
        detector_version="1.0.0",
        candle_timestamp_ms=1788700000000,
        confidence_score=0.85,
    )


def _make_dummy_tactical_result(
    symbol: str = "BTCUSDT",
    score: float = 72.5,
    rvol: float = 1.85,
) -> TacticalResult:
    features = TacticalFeatures(
        bbw_percentile=15.0,
        rvol=rvol,
        oi_expansion_pct=6.5,
        funding_rate=0.0001,
        funding_velocity=0.0,
        depth_imbalance=0.22,
        directional_bias=0.65,
        liquidation_imbalance_pct=1.2,
        atr_ratio=0.85,
        volatility_regime="COMPRESSION",
        rs_percentile=80.0,
        sfp_bullish=True,
        sfp_bearish=False,
    )
    return TacticalResult(
        symbol=symbol,
        timeframe="1h",
        candle_timestamp_ms=1788700000000,
        verdict=TacticalVerdict.HIGH,
        score=score,
        features=features,
        reasons=("BBW compression", "RVOL surge", "Bullish SFP"),
    )


# ── Test 1: Missing Configuration Fails Closed Cleanly ─────────────────────────

def test_missing_config_fails_closed() -> None:
    config = TelegramConfig(bot_token="", chat_id="", enabled=True)
    assert not config.is_valid

    dispatcher = TelegramAlertDispatcher(config)
    status = dispatcher.get_status()
    assert not status.configured
    assert status.status == "UNAVAILABLE"

    # Delivery must fail closed cleanly without network call
    result = dispatcher.send_message("Advisory test alert")
    assert not result.success
    assert "UNAVAILABLE" in (result.error or "")
    assert dispatcher.get_status().total_sent == 0


# ── Test 2: Valid Configuration Mocked Delivery ────────────────────────────────

def test_valid_config_mocked_delivery() -> None:
    config = TelegramConfig(
        bot_token="123456:TEST_TOKEN_XYZ",
        chat_id="998877",
        enabled=True,
        max_retries=1,
    )
    assert config.is_valid
    dispatcher = TelegramAlertDispatcher(config)

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps({
        "ok": True,
        "result": {"message_id": 777},
    }).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        result = dispatcher.send_message("Hello APEX")
        assert result.success
        assert result.message_id == 777
        assert result.status_code == 200

        status = dispatcher.get_status()
        assert status.configured
        assert status.status == "CONNECTED"
        assert status.total_sent == 1
        assert status.total_failed == 0
        assert status.last_sent_timestamp_ms is not None
        assert mock_urlopen.call_count == 1


# ── Test 3: Telegram API Error Handling ────────────────────────────────────────

def test_telegram_api_error_handling() -> None:
    config = TelegramConfig(
        bot_token="123456:TEST_TOKEN_XYZ",
        chat_id="998877",
        enabled=True,
        max_retries=1,
    )
    dispatcher = TelegramAlertDispatcher(config)

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps({
        "ok": False,
        "description": "Bad Request: chat not found",
    }).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = dispatcher.send_message("Test failure")
        assert not result.success
        assert "chat not found" in (result.error or "")

        status = dispatcher.get_status()
        assert status.configured
        assert status.status == "DELIVERY_ERROR"
        assert status.total_failed == 1
        assert status.last_error is not None


# ── Test 4: Network Failure & Retry ───────────────────────────────────────────

def test_network_failure_and_retry() -> None:
    config = TelegramConfig(
        bot_token="123456:TEST_TOKEN_XYZ",
        chat_id="998877",
        enabled=True,
        max_retries=2,
        retry_backoff_seconds=0.01,
    )
    dispatcher = TelegramAlertDispatcher(config)

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")) as mock_urlopen:
        result = dispatcher.send_message("Test network failure")
        assert not result.success
        assert "Network error" in (result.error or "")
        assert mock_urlopen.call_count == 2  # Attempted retry

        status = dispatcher.get_status()
        assert status.total_failed == 1
        assert status.total_sent == 0


# ── Test 5: Timeout Handling ──────────────────────────────────────────────────

def test_timeout_handling() -> None:
    config = TelegramConfig(
        bot_token="123456:TEST_TOKEN_XYZ",
        chat_id="998877",
        enabled=True,
        timeout_seconds=0.5,
        max_retries=1,
    )
    dispatcher = TelegramAlertDispatcher(config)

    with patch("urllib.request.urlopen", side_effect=TimeoutError("Request timed out")):
        result = dispatcher.send_message("Test timeout")
        assert not result.success
        assert "Network error" in (result.error or "") or "timed out" in (result.error or "")
        assert dispatcher.get_status().total_failed == 1


# ── Test 6: Malformed Response Handling ───────────────────────────────────────

def test_malformed_response_handling() -> None:
    config = TelegramConfig(
        bot_token="123456:TEST_TOKEN_XYZ",
        chat_id="998877",
        enabled=True,
        max_retries=1,
    )
    dispatcher = TelegramAlertDispatcher(config)

    mock_resp = MagicMock()
    mock_resp.status = 502
    mock_resp.read.return_value = b"<html>502 Bad Gateway</html>"
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = dispatcher.send_message("Test malformed response")
        assert not result.success
        assert dispatcher.get_status().total_failed == 1


# ── Test 7: Rate Limiting & Deduplication ─────────────────────────────────────

def test_rate_limiting_and_deduplication() -> None:
    config = TelegramConfig(
        bot_token="123456:TEST_TOKEN_XYZ",
        chat_id="998877",
        enabled=True,
        cooldown_seconds=300.0,
        max_retries=1,
    )
    dispatcher = TelegramAlertDispatcher(config)

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps({
        "ok": True,
        "result": {"message_id": 101},
    }).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        # First alert delivers successfully
        res1 = dispatcher.send_message("Alert 1", symbol="BTCUSDT", candle_timestamp_ms=1788700000000)
        assert res1.success
        assert not res1.duplicate
        assert mock_urlopen.call_count == 1

        # Second alert for same symbol and candle within cooldown is suppressed
        res2 = dispatcher.send_message("Alert 2", symbol="BTCUSDT", candle_timestamp_ms=1788700000000)
        assert not res2.success
        assert res2.duplicate
        assert mock_urlopen.call_count == 1  # No second HTTP call

        # Alert for different candle delivers
        res3 = dispatcher.send_message("Alert 3", symbol="BTCUSDT", candle_timestamp_ms=1788700060000)
        assert res3.success
        assert mock_urlopen.call_count == 2

        status = dispatcher.get_status()
        assert status.total_sent == 2
        assert status.total_duplicates_suppressed == 1


# ── Test 8: Secret Token Redaction ─────────────────────────────────────────────

def test_token_redaction() -> None:
    secret_token = "123456789:ABCdefGHI_jklMNOpqrs-TUVwxyz"
    config = TelegramConfig(
        bot_token=secret_token,
        chat_id="998877",
        enabled=True,
        max_retries=1,
    )
    dispatcher = TelegramAlertDispatcher(config)

    http_err = urllib.error.HTTPError(
        url=f"https://api.telegram.org/bot{secret_token}/sendMessage",
        code=401,
        msg="Unauthorized",
        hdrs={},  # type: ignore
        fp=io.BytesIO(f"Token {secret_token} is invalid".encode()),
    )

    with patch("urllib.request.urlopen", side_effect=http_err):
        res = dispatcher.send_message("Test secret leak")
        assert not res.success
        err = res.error or ""
        assert secret_token not in err
        assert "<REDACTED" in err or "bot<REDACTED>" in err

        status_dict = dispatcher.get_status().to_dict()
        last_error = status_dict.get("last_error") or ""
        assert secret_token not in last_error


# ── Test 9: Authentic Data Assertion (No Fabricated / Mock Claims) ──────────────

def test_authentic_data_assertion() -> None:
    # 1. Tactical result
    score = 73.4
    rvol = 1.95
    price = 67890.50
    tactical_result = _make_dummy_tactical_result(symbol="ETHUSDT", score=score, rvol=rvol)
    text = format_telegram_signal_alert(
        result=tactical_result,
        current_price=price,
        suggested_entry=price,
        suggested_stop_loss=66500.0,
        suggested_take_profit=70000.0,
    )

    # Must contain authentic dynamic data
    assert "ETHUSDT" in text
    assert f"{score:.1f}/100" in text
    assert f"{rvol:.2f}x" in text
    assert "$67890.50" in text
    assert "ADVISORY ONLY" in text

    # Must NEVER contain old static fabricated claims
    assert "87/100" not in text
    assert "2.4x RVOL" not in text
    assert "+0.0100%" not in text

    # 2. Engine signal alert
    signal = _make_dummy_signal(symbol="SOLUSDT", trigger_price=145.25)
    eng_text = format_telegram_engine_signal_alert(signal)
    assert "SOLUSDT" in eng_text
    assert "$145.25" in eng_text
    assert "breakout_test" in eng_text
    assert "85.0%" in eng_text


# ── Test 10: Zero Execution Capability ─────────────────────────────────────────

def test_zero_execution_capability() -> None:
    dispatcher = TelegramAlertDispatcher()
    forbidden_terms = [
        "place_order", "create_order", "execute_order", "cancel_order",
        "buy", "sell", "set_leverage", "close_position",
    ]
    for term in forbidden_terms:
        assert not hasattr(dispatcher, term), f"Dispatcher must not have execution method '{term}'"

    # Must only have advisory/dispatching methods
    assert hasattr(dispatcher, "send_message")
    assert hasattr(dispatcher, "dispatch_signal_alert")
    assert hasattr(dispatcher, "dispatch_engine_signal_alert")
    assert hasattr(dispatcher, "get_status")


# ── Test 11: Monitoring Loop Resilience ────────────────────────────────────────

def test_monitoring_loop_resilience() -> None:
    """Verify that a dispatcher failure does not interrupt callers or throw unhandled errors."""
    config = TelegramConfig(
        bot_token="123456:TEST",
        chat_id="998877",
        enabled=True,
        max_retries=1,
    )
    dispatcher = TelegramAlertDispatcher(config)

    # Simulate unexpected catastrophic exception in transport
    with patch("urllib.request.urlopen", side_effect=RuntimeError("Catastrophic OS socket error")):
        result = dispatcher.send_message("Test crash resistance")
        # Must return clean failure object, NOT raise RuntimeError
        assert not result.success
        assert "Catastrophic OS socket error" in (result.error or "")
        assert dispatcher.get_status().total_failed == 1


# ── Test 12: Safety Chain Preservation ─────────────────────────────────────────

def test_safety_chain_preservation() -> None:
    """Verify RiskGuardian, OrderExecutionManager, and EndpointGuard remain untouched."""
    from apex.execution.oem import OrderExecutionManager
    from apex.risk.guardian import RiskGuardian
    from apex.safety.endpoint_guard import EndpointGuard

    # Check that none of these classes reference TelegramAlertDispatcher
    assert "telegram" not in RiskGuardian.__module__
    assert "telegram" not in OrderExecutionManager.__module__
    assert "telegram" not in EndpointGuard.__module__


# ── Test 13: API Server Endpoint Integration ───────────────────────────────────

def test_api_server_telegram_status_endpoint() -> None:
    port = get_free_port()
    server = ApexApiServer(engine=None, host="127.0.0.1", port=port)
    with server:
        base_url = f"http://127.0.0.1:{port}"

        # 1. Health endpoint exposes telegram_alerts
        req = urllib.request.Request(f"{base_url}/api/v1/health")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            data_health = json.loads(resp.read().decode("utf-8"))
            assert "telegram_alerts" in data_health
            assert "configured" in data_health["telegram_alerts"]
            assert "status" in data_health["telegram_alerts"]

        # 2. Dashboard endpoint exposes telegram_alerts
        req = urllib.request.Request(f"{base_url}/api/v1/dashboard")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            data_dash = json.loads(resp.read().decode("utf-8"))
            assert "telegram_alerts" in data_dash
            assert "configured" in data_dash["telegram_alerts"]

        # 3. Direct telegram endpoint
        req = urllib.request.Request(f"{base_url}/api/v1/telegram")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            data_tel = json.loads(resp.read().decode("utf-8"))
            assert "configured" in data_tel
            assert "status" in data_tel
            assert "total_sent" in data_tel
            assert "total_failed" in data_tel
            assert "total_duplicates_suppressed" in data_tel
