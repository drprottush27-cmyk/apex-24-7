"""APEX 24/7 — Comprehensive Unit & Regression Tests for Telegram Command Router & Alerts.

Tested Invariants:
1. User authorization (authorized allowed, unauthorized / None rejected).
2. Zero execution capability (buy/sell/close orders rejected immediately).
3. Security controls (shell/eval execution commands strictly rejected).
4. Full command routing for 25+ commands.
5. Natural language query normalization and translation.
6. Setup formatting, technical provenance, and PAPER / ADVISORY labeling.
7. Long / Short separation (/scan_long, /scan_short).
8. Alert categories, toggling, and CRITICAL safety alert non-suppression.
9. Stale market data failsafe for /scan.
10. Section 21 bug resolution: current run trades properly partitioned from history.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from apex.engines.tactical.alerts import (
    AlertCategory,
    AlertSeverity,
    TelegramAlertDispatcher,
    TelegramConfig,
    set_telegram_dispatcher,
)
from apex.telegram.auth import get_authorized_user_ids, is_user_authorized
from apex.telegram.router import ApexTelegramRouter


@pytest.fixture
def mock_authorized_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_AUTHORIZED_USER_IDS", "12345678,8838441128")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "8838441128")


@pytest.fixture
def router() -> ApexTelegramRouter:
    return ApexTelegramRouter(api_base_url="http://127.0.0.1:8765")


# ── 1. Authorization Tests ────────────────────────────────────────────────────

def test_authorization_gates(mock_authorized_env: None) -> None:
    authorized_ids = get_authorized_user_ids()
    assert 12345678 in authorized_ids
    assert 8838441128 in authorized_ids

    assert is_user_authorized(12345678) is True
    assert is_user_authorized("8838441128") is True
    assert is_user_authorized(99999999) is False
    assert is_user_authorized(None) is False


def test_unauthorized_user_blocked(router: ApexTelegramRouter, mock_authorized_env: None) -> None:
    res = router.handle("/status", user_id=99999999)
    assert "⛔ Unauthorized." in res

    res_none = router.handle("/status", user_id=None)
    assert "⛔ Unauthorized." in res_none


# ── 2. Safety & Zero-Execution Tests ──────────────────────────────────────────

def test_zero_execution_guarantee(router: ApexTelegramRouter, mock_authorized_env: None) -> None:
    prohibited_inputs = [
        "buy 1 btc",
        "BUY BTCUSDT",
        "sell 0.5 ETH",
        "place order long SOL",
        "cancel order 1234",
        "close position BTCUSDT",
        "flatten account",
        "trade ETHUSDT",
        "withdraw 1000 USDT",
    ]
    for inp in prohibited_inputs:
        res = router.handle(inp, user_id=12345678)
        assert "⛔ Execution rejected" in res, f"Failed to reject forbidden input: {inp}"
        assert "permanently disabled" in res


def test_security_shell_and_exec_blocked(router: ApexTelegramRouter, mock_authorized_env: None) -> None:
    shell_inputs = [
        "exec rm -rf /",
        "eval('1 + 1')",
        "bash exploit.sh",
        "sh test.sh",
        "subprocess.run('ls')",
        "os.system('id')",
    ]
    for inp in shell_inputs:
        res = router.handle(inp, user_id=12345678)
        assert "⛔ Error: Shell and code execution commands are strictly prohibited." in res


# ── 3. Natural Language Mapping ───────────────────────────────────────────────

def test_natural_language_mapping(router: ApexTelegramRouter, mock_authorized_env: None) -> None:
    mappings = [
        ("what is pumping", "prepump", []),
        ("what is dumping", "scan_short", []),
        ("best setup now", "best", []),
        ("show today's pnl", "pnl", []),
        ("give me trade setup for BTC", "setup", ["BTCUSDT"]),
        ("trade setup for ETH", "setup", ["ETHUSDT"]),
        ("show trades", "trades", []),
        ("open positions", "positions", []),
        ("risk status", "risk", []),
        ("alerts off", "alerts_off", []),
        ("alerts on", "alerts_on", []),
        ("price BTC", "price", ["BTCUSDT"]),
        ("what is BTC price", "price", ["BTCUSDT"]),
        ("what is the market doing", "market", []),
        ("scan the market", "scan", []),
    ]
    for text, expected_cmd, expected_args in mappings:
        cmd, args = router._parse_command_or_natural_language(text)
        assert cmd == expected_cmd, f"Expected '{expected_cmd}' for text '{text}', got '{cmd}'"
        assert args == expected_args, f"Expected args {expected_args} for text '{text}', got {args}"


# ── 4. Command Routing & Handlers ─────────────────────────────────────────────

def test_command_start(router: ApexTelegramRouter, mock_authorized_env: None) -> None:
    res = router.handle("/start", user_id=12345678)
    assert "APEX 24/7 COMMAND CENTER" in res
    assert "PAPER" in res
    assert "Direct trade execution via chat is permanently disabled" in res


def test_command_help(router: ApexTelegramRouter, mock_authorized_env: None) -> None:
    res = router.handle("/help", user_id=12345678)
    assert "APEX COMMAND CATALOG" in res
    assert "/status" in res
    assert "/scan" in res
    assert "/best" in res
    assert "/prepump" in res
    assert "/positions" in res
    assert "/trades" in res
    assert "/risk" in res
    assert "/alerts" in res


@patch.object(ApexTelegramRouter, "_fetch_api")
def test_command_status(mock_fetch: MagicMock, router: ApexTelegramRouter, mock_authorized_env: None) -> None:
    mock_fetch.return_value = {
        "engine_state": "SCANNING",
        "trading_mode": "PAPER",
        "current_equity": 10050.25,
        "today_pnl_usd": 50.25,
        "today_pnl_pct": 0.50,
        "open_positions": 1,
        "symbols_tracked": 100,
        "data_status": "FRESH",
        "kill_switch_active": False,
        "telegram_alerts": {"status": "CONNECTED", "enabled": True},
    }
    res = router.handle("/status", user_id=12345678)
    assert "APEX SYSTEM STATUS" in res
    assert "PAPER" in res
    assert "SCANNING" in res
    assert "+$50.25" in res
    assert "CONNECTED" in res


@patch.object(ApexTelegramRouter, "_fetch_api")
def test_command_scan_and_direction_filters(
    mock_fetch: MagicMock, router: ApexTelegramRouter, mock_authorized_env: None
) -> None:
    def fake_fetch(endpoint: str, **kwargs: Any) -> Any:
        if endpoint == "/api/v1/health":
            return {"is_data_stale": False}
        if endpoint == "/api/v1/signals":
            return [
                {
                    "symbol": "BTCUSDT",
                    "score": 85.0,
                    "price": 68000.0,
                    "suggested_stop_loss": 67000.0,
                    "suggested_take_profit": 70000.0,
                    "features": {"directional_bias": 1.0, "rvol": 1.8},
                },
                {
                    "symbol": "ETHUSDT",
                    "score": 75.0,
                    "price": 2500.0,
                    "suggested_stop_loss": 2550.0,
                    "suggested_take_profit": 2400.0,
                    "features": {"directional_bias": -1.0, "rvol": 1.2},
                },
            ]
        return {}

    mock_fetch.side_effect = fake_fetch

    # All setups
    res_all = router.handle("/scan", user_id=12345678)
    assert "BTCUSDT" in res_all
    assert "ETHUSDT" in res_all

    # Long only
    res_long = router.handle("/scan_long", user_id=12345678)
    assert "BTCUSDT" in res_long
    assert "ETHUSDT" not in res_long

    # Short only
    res_short = router.handle("/scan_short", user_id=12345678)
    assert "ETHUSDT" in res_short
    assert "BTCUSDT" not in res_short


@patch.object(ApexTelegramRouter, "_fetch_api")
def test_scan_blocked_on_stale_data(mock_fetch: MagicMock, router: ApexTelegramRouter, mock_authorized_env: None) -> None:
    mock_fetch.return_value = {
        "is_data_stale": True,
        "data_age_seconds": 210.5,
    }
    res = router.handle("/scan", user_id=12345678)
    assert "SCAN TEMPORARILY BLOCKED: STALE MARKET DATA" in res
    assert "210.5s" in res


@patch.object(ApexTelegramRouter, "_fetch_api")
def test_command_setup_formatting(mock_fetch: MagicMock, router: ApexTelegramRouter, mock_authorized_env: None) -> None:
    mock_fetch.return_value = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "entry_price": 68150.0,
        "stop_loss": 67200.0,
        "targets": [{"price": 70050.0}, {"price": 71200.0}],
        "score": 88.0,
        "verdict": "CONFLUENCE",
        "sizing": {"units": 0.5, "notional_usd": 34075.0, "risk_usd": 100.0, "risk_pct": 1.0},
        "provenance": {"real_factors_count": 6, "estimated_factors_count": 0},
    }
    res = router.handle("/setup BTC", user_id=12345678)
    assert "BTCUSDT" in res
    assert "PAPER / ADVISORY" in res
    assert "LONG" in res
    assert "68,150" in res
    assert "67,200" in res
    assert "70,050" in res
    assert "88.0/100" in res


@patch.object(ApexTelegramRouter, "_fetch_api")
def test_command_best(mock_fetch: MagicMock, router: ApexTelegramRouter, mock_authorized_env: None) -> None:
    mock_fetch.return_value = [
        {
            "symbol": "SOLUSDT",
            "score": 92.0,
            "price": 140.0,
            "suggested_stop_loss": 136.0,
            "suggested_take_profit": 148.0,
            "features": {"directional_bias": 1.0},
        }
    ]
    res = router.handle("/best", user_id=12345678)
    assert "TOP SETUPS" in res
    assert "SOLUSDT" in res
    assert "92.0" in res


@patch.object(ApexTelegramRouter, "_fetch_api")
def test_command_prepump(mock_fetch: MagicMock, router: ApexTelegramRouter, mock_authorized_env: None) -> None:
    mock_fetch.return_value = [
        {
            "symbol": "DOGEUSDT",
            "score": 85.0,
            "features": {
                "rvol": 2.8,
                "bbw_percentile": 12.0,
                "funding_rate": 0.0001,
                "depth_imbalance": 0.35,
                "directional_bias": 1.0,
                "sfp_bullish": True,
            },
        }
    ]
    res = router.handle("/prepump", user_id=12345678)
    assert "PRE-PUMP CANDIDATES" in res
    assert "DOGEUSDT" in res
    assert "85.0" in res
    assert "2.80x" in res


@patch.object(ApexTelegramRouter, "_fetch_api")
def test_command_positions(mock_fetch: MagicMock, router: ApexTelegramRouter, mock_authorized_env: None) -> None:
    def fake_fetch(endpoint: str, **kwargs: Any) -> Any:
        if endpoint == "/api/v1/positions":
            return {
                "positions": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "LONG",
                        "entry_price": 68000.0,
                        "mark_price": 68500.0,
                        "unrealized_pnl": 73.50,
                        "current_r": 1.2,
                        "peak_r": 1.5,
                        "stop_loss": 67200.0,
                        "take_profit": 70000.0,
                        "trailing_state": "ARMED",
                    }
                ]
            }
        if endpoint == "/api/v1/risk":
            return {"open_positions": 1, "max_positions": 3, "utilization_pct": 33.3}
        return {}

    mock_fetch.side_effect = fake_fetch
    res = router.handle("/positions", user_id=12345678)
    assert "OPEN PAPER POSITIONS" in res
    assert "BTCUSDT" in res
    assert "LONG" in res
    assert "+$73.50" in res


# ── 5. Section 21 Stale Trades Bug Resolution ────────────────────────────────

@patch.object(ApexTelegramRouter, "_fetch_api")
def test_section_21_stale_trade_resolution(
    mock_fetch: MagicMock, router: ApexTelegramRouter, mock_authorized_env: None
) -> None:
    """Verify current_run correctly shows 0 trades and does not expose historical fixtures."""
    def fake_fetch(endpoint: str, query: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        scope = (query or {}).get("scope", "current_run") if query else "current_run"
        if scope == "current_run":
            return {
                "current_run": {
                    "run_id": "active_run",
                    "trades_opened_count": 0,
                    "trades_closed_count": 0,
                    "realized_pnl": 0.0,
                    "closed_trades": [],
                },
                "total_historical_trades": 24,
            }
        else:
            return {
                "trades": [{"symbol": "BTCUSDT", "pnl": 100.0} for _ in range(24)],
                "count": 24,
                "scope": "all",
            }

    mock_fetch.side_effect = fake_fetch

    # /trades shows current run: strictly 0 trades
    res_trades = router.handle("/trades", user_id=12345678)
    assert "TRADE HISTORY" in res_trades
    assert "Zero trades closed in current run" in res_trades
    assert "24 trades" in res_trades

    # /history shows historical archive: 24 trades
    res_hist = router.handle("/history", user_id=12345678)
    assert "HISTORICAL ARCHIVE" in res_hist
    assert "Total Archive Trades:</b> <code>24</code>" in res_hist


# ── 6. Alert Category & Non-Suppression of Critical Alerts ────────────────────

def test_critical_alerts_never_suppressed() -> None:
    config = TelegramConfig(
        bot_token="test_token",
        chat_id="12345678",
        enabled=False,  # ALERTS GLOBALLY DISABLED
    )
    dispatcher = TelegramAlertDispatcher(config)

    # Disable all individual categories as well
    for cat in AlertCategory:
        dispatcher.update_category(cat, False)

    # Attempt to dispatch INFO alert -> Suppressed
    with patch("urllib.request.urlopen") as mock_urlopen:
        res_info = dispatcher.dispatch_alert(
            category=AlertCategory.TRADING,
            severity=AlertSeverity.INFO,
            event_type="TEST_INFO",
            title="TEST INFO",
            message="This should be suppressed.",
        )
        assert res_info.success is False
        mock_urlopen.assert_not_called()

    # Attempt to dispatch CRITICAL alert (e.g. KILL SWITCH) -> MUST NOT BE SUPPRESSED!
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"ok": True}).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        res_crit = dispatcher.dispatch_alert(
            category=AlertCategory.SYSTEM,
            severity=AlertSeverity.CRITICAL,
            event_type="KILL_SWITCH",
            title="KILL SWITCH ACTIVATED",
            message="Kill switch engaged. Trading halted.",
        )
        assert res_crit.success is True
        mock_urlopen.assert_called_once()


# ── 7. Audit Logging ──────────────────────────────────────────────────────────

def test_router_audit_logging(router: ApexTelegramRouter, mock_authorized_env: None) -> None:
    router.handle("/help", user_id=12345678)
    router.handle("buy 1 btc", user_id=12345678)
    router.handle("/status", user_id=99999999)  # unauthorized

    audit = router.get_audit_log(limit=10)
    assert len(audit) >= 3

    statuses = [entry["status"] for entry in audit]
    assert "SUCCESS" in statuses
    assert "REJECTED" in statuses


# ── 8. Control Plane Lifecycle & Management Commands ──────────────────────────

def test_control_lifecycle_commands_routing(router: ApexTelegramRouter, mock_authorized_env: None) -> None:
    mock_resp = {
        "command": "/pause",
        "actor": "telegram_operator",
        "timestamp_ms": 1788880000000,
        "previous_state": "RUNNING",
        "current_state": "PAUSED",
        "trading_mode": "PAPER",
        "status": "SUCCESS",
        "action": "PAUSE_SYSTEM",
        "message": "Autonomous actions paused.",
        "affected_components": ["scheduler"],
        "failures": [],
        "correlation_id": "cmd_test123",
        "kill_switch_active": False,
    }

    with patch.object(router, "_post_api", return_value=mock_resp) as mock_post:
        res = router.handle("/pause", user_id=12345678)
        assert "APEX CONTROL PLANE" in res
        assert "PAUSED" in res
        mock_post.assert_called_once_with("/api/v1/control/pause", {"actor": "telegram_operator", "source": "telegram", "reason": "Paused via Telegram"})

    mock_resp["command"] = "/resume"
    mock_resp["current_state"] = "RUNNING"
    with patch.object(router, "_post_api", return_value=mock_resp) as mock_post:
        res = router.handle("/resume", user_id=12345678)
        assert "RUNNING" in res
        mock_post.assert_called_once_with("/api/v1/control/resume", {"actor": "telegram_operator", "source": "telegram"})


def test_flatten_command_routing(router: ApexTelegramRouter, mock_authorized_env: None) -> None:
    mock_flatten_resp = {
        "command": "/flatten",
        "actor": "telegram_operator",
        "timestamp_ms": 1788880000000,
        "previous_state": "RUNNING",
        "current_state": "RUNNING",
        "status": "SUCCESS",
        "action": "FLATTEN_POSITIONS",
        "message": "Flattened 1 positions.",
        "closed_count": 1,
        "closed_positions": [{"symbol": "BTCUSDT"}],
        "failures": [],
        "correlation_id": "cmd_flat123",
    }

    with patch.object(router, "_post_api", return_value=mock_flatten_resp) as mock_post:
        res = router.handle("/flatten BTCUSDT", user_id=12345678)
        assert "APEX CONTROL PLANE" in res
        assert "FLATTEN_POSITIONS" in res
        mock_post.assert_called_once_with(
            "/api/v1/control/flatten",
            {"actor": "telegram_operator", "source": "telegram", "symbol": "BTCUSDT", "reason": "Flattened via Telegram"},
        )


def test_management_and_investment_commands(router: ApexTelegramRouter, mock_authorized_env: None) -> None:
    mock_team_resp = {
        "team": [
            {
                "agent_id": "risk_manager",
                "name": "Risk Manager",
                "role": "Portfolio exposure and limits enforcement",
                "status": "HEALTHY",
                "heartbeat_ms": 1788880000000,
                "current_task": "Monitoring risk envelope",
                "last_result": "Drawdown 0.0%",
            }
        ]
    }
    with patch.object(router, "_fetch_api", return_value=mock_team_resp):
        res = router.handle("/team", user_id=12345678)
        assert "APEX MANAGEMENT TEAM" in res
        assert "Risk Manager" in res

    mock_theses_resp = {
        "theses": [
            {
                "symbol": "BTCUSDT",
                "asset_name": "Bitcoin",
                "sentiment": "ACCUMULATE",
                "conviction": "HIGH",
                "current_price": 78500.0,
                "accumulation_zone_low": 72000.0,
                "accumulation_zone_high": 76000.0,
                "target_price_conservative": 95000.0,
                "invalidation_level": 64000.0,
                "catalysts": ["Institutional ETF inflows"],
            }
        ]
    }
    with patch.object(router, "_fetch_api", return_value=mock_theses_resp):
        res = router.handle("/theses", user_id=12345678)
        assert "ACTIVE INVESTMENT THESES" in res
        assert "Bitcoin" in res

    mock_intel_resp = {
        "result": {
            "source": "xAI / Grok",
            "symbol": "BTCUSDT",
            "analysis": "Macro regime bullish. Institutional liquidity inflow sustained.",
            "disclaimer": "[AI RESEARCH — NOT FINANCIAL ADVICE] [ZERO EXECUTION AUTHORITY]",
        }
    }
    with patch.object(router, "_post_api", return_value=mock_intel_resp):
        res = router.handle("/intel BTC", user_id=12345678)
        assert "APEX MARKET INTELLIGENCE" in res
        assert "xAI / Grok" in res
        assert "NOT FINANCIAL ADVICE" in res
