"""Tests for the read-only ApexApiServer."""
import json
import socket
import time
import urllib.error
import urllib.request
from typing import Any

import pytest

from apex.api.server import ApexApiServer
from apex.config.settings import ApexConfig
from apex.domain.types import TradingMode
from apex.runtime.health import HealthSnapshot, HealthStatus
from apex.safety.exceptions import SafetyConfigurationError


def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class DummyPosition:
    def __init__(self) -> None:
        self.symbol = "BTCUSDT"
        self.side = "BUY"
        self.entry_price = 70000.0
        self.quantity = 0.1
        self.stop_loss = 68000.0
        self.take_profit = 75000.0
        self.unrealized_pnl = 150.0
        self.status = "OPEN"


class DummyTracker:
    def __init__(self) -> None:
        self.current_equity = 10000.0
        self.daily_starting_equity = 10000.0
        self.open_positions = [DummyPosition()]

    def daily_drawdown_pct(self, eq: float) -> float:
        return 0.0


class DummyKillSwitch:
    def __init__(self, tripped: bool = False, reason: str | None = None) -> None:
        self.is_tripped = tripped
        self.tripped_reason = reason


class DummyEngineStatus:
    def __init__(self) -> None:
        self.system_state = "RUNNING"
        self.last_scan_time_ms = 1788713000000


class DummyEngine:
    def __init__(self, kill_tripped: bool = False) -> None:
        self._config = ApexConfig(
            trading_mode=TradingMode.PAPER,
            live_trading_enabled=False,
            max_concurrent_positions=2,
            max_risk_per_trade=0.01,
            max_leverage=3.0,
        )
        self._ks = DummyKillSwitch(kill_tripped, "Manual test kill" if kill_tripped else None)
        self._tracker = DummyTracker()
        self._status = DummyEngineStatus()
        self._scan_metrics: dict[str, Any] = {}

    def config(self) -> ApexConfig:
        return self._config

    def kill_switch(self) -> DummyKillSwitch:
        return self._ks

    def position_tracker(self) -> DummyTracker:
        return self._tracker

    def get_status(self) -> DummyEngineStatus:
        return self._status

    def get_health(self) -> HealthSnapshot:
        return HealthSnapshot(
            status=HealthStatus.HEALTHY,
            data_health=HealthStatus.HEALTHY,
            execution_health=HealthStatus.HEALTHY,
            consecutive_data_failures=0,
            consecutive_execution_failures=0,
            total_scans=10,
            available_symbols=2,
            total_symbols=2,
            skipped_symbols=0,
            failed_symbols=0,
        )

    def get_series(self, symbol: str) -> None:
        return None

    def get_tactical_signals(self) -> list[dict[str, Any]]:
        return [
            {
                "symbol": "BTCUSDT",
                "score": 68.0,
                "verdict": "HIGH",
                "reasons": ["volatility compression", "depth imbalance"],
                "features": {"bbw_percentile": 12.0, "rvol": 2.5},
                "component_details": {
                    "depth_imbalance": {
                        "value": -0.25,
                        "source": "binance_depth",
                        "available": True,
                        "confidence": 0.7,
                        "is_estimated": False,
                    },
                    "liquidation_imbalance": {
                        "value": 15.0,
                        "source": "proxy_estimate",
                        "available": True,
                        "confidence": 0.4,
                        "is_estimated": True,
                    },
                },
                "multi_timeframe": None,
                "advisory": True,
                "price": 79500.0,
                "timestamp_ms": 1788713500000,
            }
        ]

    def get_tactical_context(self, symbol: str) -> dict[str, Any] | None:
        if symbol == "BTCUSDT":
            return self.get_tactical_signals()[0]
        return None


def test_rejects_non_localhost_binding() -> None:
    engine = DummyEngine()
    with pytest.raises(SafetyConfigurationError, match="SECURITY VIOLATION"):
        ApexApiServer(engine, host="0.0.0.0", port=8000)

    with pytest.raises(SafetyConfigurationError, match="SECURITY VIOLATION"):
        ApexApiServer(engine, host="192.168.1.10", port=8000)


def test_api_read_only_methods_enforced() -> None:
    port = get_free_port()
    engine = DummyEngine()
    server = ApexApiServer(engine, host="127.0.0.1", port=port)
    with server:
        base_url = f"http://127.0.0.1:{port}"

        # GET succeeds
        req = urllib.request.Request(f"{base_url}/api/v1/health")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200

        # POST is rejected with 405
        post_req = urllib.request.Request(
            f"{base_url}/api/v1/health",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(post_req)
        assert exc_info.value.code == 405

        # PUT is rejected with 405
        put_req = urllib.request.Request(
            f"{base_url}/api/v1/risk",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(put_req)
        assert exc_info.value.code == 405

        # DELETE is rejected with 405
        del_req = urllib.request.Request(
            f"{base_url}/api/v1/account",
            method="DELETE",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(del_req)
        assert exc_info.value.code == 405


def test_api_health_and_risk_endpoints() -> None:
    port = get_free_port()
    engine = DummyEngine(kill_tripped=False)
    server = ApexApiServer(engine, host="127.0.0.1", port=port)
    with server:
        base_url = f"http://127.0.0.1:{port}"

        # Test health
        with urllib.request.urlopen(f"{base_url}/api/v1/health") as resp:
            data = json.loads(resp.read().decode())
            assert data["health_status"] == "HEALTHY"
            assert data["engine_state"] == "RUNNING"
            assert data["trading_mode"] == "PAPER"
            assert "scan_latency_ms" in data
            assert "consecutive_scan_failures" in data
            assert "last_successful_scan_ts_ms" in data

        # Test risk
        with urllib.request.urlopen(f"{base_url}/api/v1/risk") as resp:
            data = json.loads(resp.read().decode())
            assert not data["kill_switch_tripped"]
            assert data["can_trade"]
            assert data["trading_mode"] == "PAPER"
            assert data["max_concurrent_positions"] == 2
            assert data["open_positions_count"] == 1


def test_api_risk_when_kill_switch_tripped() -> None:
    port = get_free_port()
    engine = DummyEngine(kill_tripped=True)
    server = ApexApiServer(engine, host="127.0.0.1", port=port)
    with server:
        base_url = f"http://127.0.0.1:{port}"
        with urllib.request.urlopen(f"{base_url}/api/v1/risk") as resp:
            data = json.loads(resp.read().decode())
            assert data["kill_switch_tripped"]
            assert not data["can_trade"]
            assert data["kill_switch_reason"] == "Manual test kill"


def test_api_dashboard_endpoint_provenance() -> None:
    port = get_free_port()
    engine = DummyEngine()
    server = ApexApiServer(engine, host="127.0.0.1", port=port)
    with server:
        base_url = f"http://127.0.0.1:{port}"
        with urllib.request.urlopen(f"{base_url}/api/v1/dashboard") as resp:
            data = json.loads(resp.read().decode())
            assert "markets" in data
            assert "balance" in data
            assert "signals" in data
            assert "safety" in data

            assert data["balance"]["value"].startswith("$10,000.00 USDT")
            assert "current_equity" in data["balance"]
            assert data["balance"]["current_equity"] == 10000.0
            assert not data["safety"]["kill_switch_tripped"]
            assert data["safety"]["can_trade"]
            assert "daily_drawdown_kill_pct" in data["safety"]
            assert data["safety"]["daily_drawdown_kill_pct"] == 3.0

            signals = data["signals"]
            assert len(signals) == 1
            sig = signals[0]
            assert sig["symbol"] == "BTCUSDT"
            assert sig["score"] == 68.0
            assert sig["verdict"] == "HIGH"
            # Provenance metadata surfaced accurately
            assert sig["is_estimated"]
            assert "liquidation_imbalance" in sig["component_details"]
            assert sig["component_details"]["liquidation_imbalance"]["source"] == "proxy_estimate"
            assert sig["component_details"]["depth_imbalance"]["source"] == "binance_depth"


def test_api_root_and_signals_and_404() -> None:
    port = get_free_port()
    engine = DummyEngine()
    server = ApexApiServer(engine, host="127.0.0.1", port=port)
    with server:
        base_url = f"http://127.0.0.1:{port}"

        # Root
        with urllib.request.urlopen(f"{base_url}/") as resp:
            data = json.loads(resp.read().decode())
            assert data["mode"] == "READ_ONLY"

        # Signals all
        with urllib.request.urlopen(f"{base_url}/api/v1/signals") as resp:
            data = json.loads(resp.read().decode())
            assert len(data) == 1
            assert data[0]["symbol"] == "BTCUSDT"

        # Signals filtered by symbol
        with urllib.request.urlopen(f"{base_url}/api/v1/signals?symbol=BTCUSDT") as resp:
            data = json.loads(resp.read().decode())
            assert len(data) == 1
            assert data[0]["symbol"] == "BTCUSDT"

        # Signals filtered by non-tracked symbol
        with urllib.request.urlopen(f"{base_url}/api/v1/signals?symbol=NOTRACK") as resp:
            data = json.loads(resp.read().decode())
            assert len(data) == 0

        # Account
        with urllib.request.urlopen(f"{base_url}/api/v1/account") as resp:
            data = json.loads(resp.read().decode())
            assert data["total_equity"] == 10000.0
            assert data["open_positions_count"] == 1

        # 404
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"{base_url}/api/v1/nonexistent")
        assert exc_info.value.code == 404


def test_api_server_offline_engine_graceful() -> None:
    port = get_free_port()
    server = ApexApiServer(None, host="127.0.0.1", port=port)
    with server:
        base_url = f"http://127.0.0.1:{port}"

        with urllib.request.urlopen(f"{base_url}/api/v1/health") as resp:
            data = json.loads(resp.read().decode())
            assert data["status"] == "OFFLINE"

        with urllib.request.urlopen(f"{base_url}/api/v1/risk") as resp:
            data = json.loads(resp.read().decode())
            assert data["kill_switch_tripped"]
            assert not data["can_trade"]

        with urllib.request.urlopen(f"{base_url}/api/v1/dashboard") as resp:
            data = json.loads(resp.read().decode())
            assert data["balance"]["value"] == "Offline"
            assert data["safety"]["kill_switch_tripped"]
            assert not data["safety"]["can_trade"]

        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"{base_url}/api/v1/plan?symbol=BTCUSDT")
        assert exc_info.value.code == 503


def test_api_plan_endpoints() -> None:
    port = get_free_port()
    engine = DummyEngine()
    server = ApexApiServer(engine, host="127.0.0.1", port=port)
    with server:
        base_url = f"http://127.0.0.1:{port}"

        # 1. Query with query parameter
        with urllib.request.urlopen(f"{base_url}/api/v1/plan?symbol=BTCUSDT") as resp:
            data = json.loads(resp.read().decode())
            assert data["symbol"] == "BTCUSDT"
            assert data["entry_price"] == 79500.0
            assert "entry_zone" in data
            assert data["entry_zone"]["low"] <= data["entry_zone"]["high"]
            assert data["stop_loss"] < data["entry_price"]
            assert len(data["targets"]) == 3
            assert data["targets"][0]["r_multiple"] == 1.5
            assert data["targets"][1]["r_multiple"] == 2.5
            assert data["targets"][2]["r_multiple"] == 4.0
            assert "sizing" in data
            assert data["sizing"]["equity_usd"] == 10000.0
            assert data["sizing"]["units"] > 0
            assert data["provenance"]["overall"] == "PROXY_ESTIMATE"
            assert data["provenance"]["real_factors_count"] >= 1
            assert data["provenance"]["estimated_factors_count"] >= 1
            assert data["action_command"].startswith("PAPER LONG BTCUSDT")

        # 2. Query with path parameter /api/v1/signals/BTCUSDT/plan
        with urllib.request.urlopen(f"{base_url}/api/v1/signals/BTCUSDT/plan") as resp:
            data = json.loads(resp.read().decode())
            assert data["symbol"] == "BTCUSDT"
            assert data["entry_price"] == 79500.0

        # 3. Query without symbol -> list of plans
        with urllib.request.urlopen(f"{base_url}/api/v1/plan") as resp:
            data = json.loads(resp.read().decode())
            assert isinstance(data, list)
            assert len(data) >= 1
            assert data[0]["symbol"] == "BTCUSDT"

        # 4. Unknown symbol -> 404
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"{base_url}/api/v1/plan?symbol=NONEXISTENT")
        assert exc_info.value.code == 404

        # 5. Verify plan attached inside dashboard response
        with urllib.request.urlopen(f"{base_url}/api/v1/dashboard") as resp:
            dash = json.loads(resp.read().decode())
            assert len(dash["signals"]) > 0
            first_sig = dash["signals"][0]
            assert "plan" in first_sig
            assert first_sig["plan"]["symbol"] == "BTCUSDT"
            assert "trades" in dash
            assert "active" in dash["trades"]


def test_api_trades_endpoint() -> None:
    port = get_free_port()
    engine = DummyEngine()
    server = ApexApiServer(engine, host="127.0.0.1", port=port)
    with server:
        base_url = f"http://127.0.0.1:{port}"
        with urllib.request.urlopen(f"{base_url}/api/v1/trades") as resp:
            data = json.loads(resp.read().decode())
            assert "active_trades" in data
            assert "history" in data
            assert "total_trades" in data
            assert len(data["active_trades"]) == 1
            pos = data["active_trades"][0]
            assert pos["symbol"] == "BTCUSDT"
            assert pos["direction"] == "LONG"
            assert pos["entry_price"] == 70000.0
            assert "mark_price" in pos
            assert "unrealized_pnl" in pos
            assert "current_r" in pos
            assert "stop_loss" in pos
            assert "trailing_stop" in pos
            assert "realtime_status" in pos


def test_api_health_freshness_and_staleness_fail_closed() -> None:
    port = get_free_port()
    engine = DummyEngine()
    now_ms = int(time.time() * 1000)

    # 1. Fresh scan metrics
    engine._scan_metrics = {
        "scan_latency_ms": 1500,
        "consecutive_scan_failures": 0,
        "last_successful_scan_ts_ms": now_ms - 5000,  # 5 seconds ago
    }

    server = ApexApiServer(engine, host="127.0.0.1", port=port)
    with server:
        base_url = f"http://127.0.0.1:{port}"

        with urllib.request.urlopen(f"{base_url}/api/v1/health") as resp:
            data = json.loads(resp.read().decode())
            assert not data["is_data_stale"]
            assert data["data_age_seconds"] is not None
            assert data["data_age_seconds"] < 30.0
            assert data["health_status"] == "HEALTHY"

        with urllib.request.urlopen(f"{base_url}/api/v1/risk") as resp:
            rdata = json.loads(resp.read().decode())
            assert rdata["can_trade"]

        # 2. Simulate stale scan metrics (e.g. 250s ago, exceeding 180s threshold)
        engine._scan_metrics["last_successful_scan_ts_ms"] = now_ms - 250_000

        with urllib.request.urlopen(f"{base_url}/api/v1/health") as resp:
            stale_data = json.loads(resp.read().decode())
            assert stale_data["is_data_stale"]
            assert stale_data["data_age_seconds"] >= 240.0
            assert stale_data["health_status"] == "DEGRADED"

        # Staleness must fail closed: can_trade MUST be False
        with urllib.request.urlopen(f"{base_url}/api/v1/risk") as resp:
            stale_risk = json.loads(resp.read().decode())
            assert not stale_risk["can_trade"]


def test_api_static_asset_security() -> None:
    port = get_free_port()
    engine = DummyEngine()
    server = ApexApiServer(engine, host="127.0.0.1", port=port)
    with server:
        base_url = f"http://127.0.0.1:{port}"

        # Valid css asset should return 200
        with urllib.request.urlopen(f"{base_url}/assets/style.css") as resp:
            assert resp.status == 200
            assert "text/css" in resp.headers.get("Content-Type", "")

        # Disallowed file extensions (.env, .py) must be rejected with 404
        for bad_asset in ("secrets.env", "server.py", "database.db"):
            req = urllib.request.Request(f"{base_url}/assets/{bad_asset}")
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 404
            err_body = json.loads(exc_info.value.read().decode())
            assert err_body["error"] == "Asset not found"
