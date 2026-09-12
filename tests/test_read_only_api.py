import json
import pytest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from starlette.testclient import TestClient

from src.webhook_server import app, executor

OBSERVABILITY_ENDPOINTS = [
    "/api/v1/positions",
    "/api/v1/trades",
    "/api/v1/journal",
    "/api/v1/scanner",
    "/api/v1/guardian",
    "/api/v1/performance",
    "/api/v1/intelligence",
    "/api/v1/multiaccount",
]


@pytest.fixture(autouse=True)
def clean_state():
    executor.risk.open_positions.clear()
    executor.risk.cooldown_tracker.clear()
    executor.risk.circuit_breaker_tripped = False
    executor.risk.processed_signal_ids.clear()
    executor.risk.closed_trades.clear()
    executor.risk.balance = 10000.0
    executor.risk.initial_daily_balance = 10000.0
    obs = app.state.aegis_observability
    obs.clear_scanner()
    obs.clear_intelligence()
    obs.clear_cross_exchange()
    obs.clear_multi_account()
    yield
    executor.risk.open_positions.clear()
    executor.risk.cooldown_tracker.clear()
    executor.risk.circuit_breaker_tripped = False
    executor.risk.processed_signal_ids.clear()
    executor.risk.closed_trades.clear()
    executor.risk.balance = 10000.0
    executor.risk.initial_daily_balance = 10000.0
    obs.clear_scanner()
    obs.clear_intelligence()
    obs.clear_cross_exchange()
    obs.clear_multi_account()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth():
    return patch("src.webhook_server.get_secret_passphrase", return_value="TEST_SECRET")


def open_position(client, symbol="BTC-USDT", price=50000.0, sl=49000.0, signal_id="RO-BTC-1"):
    return client.post("/webhook", json={
        "passphrase": "TEST_SECRET",
        "symbol": symbol,
        "action": "BUY",
        "price": price,
        "defensive_sl": sl,
        "signal_id": signal_id,
    })


def test_all_observability_endpoints_are_get_only(client):
    for ep in OBSERVABILITY_ENDPOINTS:
        get_res = client.get(ep)
        assert get_res.status_code == 200, ep
        post_res = client.post(ep, json={})
        assert post_res.status_code == 405, ep


def test_health_endpoint_unchanged(client):
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"
    assert data["trading_mode"] == "PAPER"
    assert data["live_trading_enabled"] is False
    assert "passphrase" not in data


def test_positions_reflect_open_position(client, auth):
    with auth:
        open_position(client)
    res = client.get("/api/v1/positions")
    assert res.status_code == 200
    rows = res.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "BTC-USDT"
    assert row["side"] == "LONG"
    assert row["entry"] == 50000.0
    assert row["stop_loss"] == 49000.0
    assert row["take_profit"] == 52500.0
    assert row["risk_reward"] == 2.5
    assert row["status"] == "OPEN"
    assert row["current_price"] is None
    assert row["unrealized_pnl"] is None


def test_trades_and_journal_record_closed_trade(client, auth):
    with auth:
        open_position(client, signal_id="RO-CLOSE-1")
        close_res = client.post("/webhook", json={
            "passphrase": "TEST_SECRET",
            "symbol": "BTC-USDT",
            "action": "CLOSE",
            "price": 51000.0,
        })
    assert close_res.status_code == 200
    assert close_res.json()["realized_pnl"] > 0

    trades = client.get("/api/v1/trades").json()
    assert len(trades) == 1
    trade = trades[0]
    assert trade["symbol"] == "BTC-USDT"
    assert trade["direction"] == "LONG"
    assert trade["entry"] == 50000.0
    assert trade["exit"] == 51000.0
    assert trade["realized_pnl"] > 0
    assert trade["status"] == "CLOSED"
    assert trade["closed_at"] is not None

    journal = client.get("/api/v1/journal").json()
    assert len(journal) == 1
    entry = journal[0]
    assert entry["status"] == "CLOSED"
    assert entry["pnl"] == trade["realized_pnl"]


def test_journal_mixes_open_and_closed(client, auth):
    with auth:
        open_position(client, symbol="BTC-USDT", signal_id="RO-J-1")
        open_position(client, symbol="ETH-USDT", price=3000.0, sl=2900.0, signal_id="RO-J-2")
        client.post("/webhook", json={
            "passphrase": "TEST_SECRET",
            "symbol": "BTC-USDT",
            "action": "CLOSE",
            "price": 51000.0,
        })
    journal = client.get("/api/v1/journal").json()
    by_symbol = {e["symbol"]: e for e in journal}
    assert by_symbol["BTC-USDT"]["status"] == "CLOSED"
    assert by_symbol["ETH-USDT"]["status"] == "OPEN"
    assert by_symbol["ETH-USDT"]["exit"] is None
    assert by_symbol["ETH-USDT"]["pnl"] is None


def test_guardian_armed_default(client):
    data = client.get("/api/v1/guardian").json()
    assert data["state"] == "ARMED"
    assert data["circuit_breaker"] == "ARMED"
    assert data["max_daily_drawdown_pct"] == 0.03
    assert data["active_risk_pct"] == 0.01
    assert data["open_position_count"] == 0
    assert data["max_position_count"] == 3


def test_guardian_reports_circuit_breaker_tripped(client, auth):
    with auth:
        open_position(client, signal_id="RO-CB-1")
        # Close at a large loss to exceed the 3% daily drawdown killswitch
        client.post("/webhook", json={
            "passphrase": "TEST_SECRET",
            "symbol": "BTC-USDT",
            "action": "CLOSE",
            "price": 40000.0,
        })
    data = client.get("/api/v1/guardian").json()
    assert data["circuit_breaker"] == "ENGAGED"
    assert data["state"] == "TRIPPED"
    assert data["daily_drawdown_pct"] < -0.03
    assert executor.risk.circuit_breaker_tripped is True


def test_performance_reflects_realized_pnl(client, auth):
    assert client.get("/api/v1/performance").json()["win_rate"] is None
    with auth:
        open_position(client, signal_id="RO-PROFIT-1")
        client.post("/webhook", json={
            "passphrase": "TEST_SECRET",
            "symbol": "BTC-USDT",
            "action": "CLOSE",
            "price": 51000.0,
        })
    data = client.get("/api/v1/performance").json()
    assert data["net_pnl"] > 0
    assert data["realized_pnl"] > 0
    assert data["net_pnl_pct"] > 0
    assert data["win_rate"] == 1.0
    assert data["unrealized_pnl"] is None
    assert data["balance"] > 10000.0


def test_closed_trades_persist_across_restart(tmp_path):
    from src.executor import ExecutionModule
    state_file = str(tmp_path / "risk_state.json")

    ex1 = ExecutionModule(exchange_id='mock', paper_trade=True, state_file=state_file)
    ex1.process_signal("BTC-USDT", "BUY", 50000.0, 49000.0, signal_id="RO-RESTART-1")
    ok, pnl, _ = ex1.close_position("BTC-USDT", 51000.0)
    assert ok and pnl > 0
    assert len(ex1.risk.closed_trades) == 1

    ex2 = ExecutionModule(exchange_id='mock', paper_trade=True, state_file=state_file)
    assert len(ex2.risk.closed_trades) == 1
    assert ex2.risk.closed_trades[0]["symbol"] == "BTC-USDT"
    assert ex2.risk.closed_trades[0]["exit_price"] == 51000.0
    assert "ETH-USDT" not in ex2.risk.open_positions


def test_closed_trades_history_capped(tmp_path):
    state_file = str(tmp_path / "risk_state.json")
    risk = executor.risk
    total = 505
    for i in range(total):
        sym = f"CAP-{i}-USDT"
        risk.register_entry(sym, "BUY", 1.0, 100.0, 98.0, 105.0, signal_id=f"CAP-TRADE-{i}")
        risk.register_exit(sym, 100.0, 0.0, exit_reason="TEST")
    assert len(risk.closed_trades) == executor.risk.MAX_CLOSED_TRADES == 500
    assert risk.closed_trades[0]["symbol"] == "CAP-5-USDT"
    assert risk.closed_trades[-1]["symbol"] == "CAP-504-USDT"
    dropped = {"CAP-0-USDT", "CAP-1-USDT", "CAP-2-USDT", "CAP-3-USDT", "CAP-4-USDT"}
    symbols = {t["symbol"] for t in risk.closed_trades}
    assert dropped.isdisjoint(symbols)


def test_closed_trades_reload_trims_oversized_history(tmp_path):
    state_file = str(tmp_path / "risk_state.json")
    legacy_rows = [
        {"trade_id": f"LEGACY-{i}", "symbol": f"OLD-{i}-USDT", "realized_pnl": 1.0,
         "entry_price": 100.0, "exit_price": 101.0}
        for i in range(520)
    ]
    payload = {
        "balance": 10000.0,
        "initial_daily_balance": 10000.0,
        "last_day_reset": "2026-09-12",
        "circuit_breaker_tripped": False,
        "processed_signal_ids": [],
        "closed_trades": legacy_rows,
        "open_positions": {},
        "cooldown_tracker": {},
    }
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    from src.executor import HardenedRiskEngine
    reloaded = HardenedRiskEngine(simulated_balance=10000.0, state_file=state_file)
    assert len(reloaded.closed_trades) == 500
    assert reloaded.closed_trades[0]["symbol"] == "OLD-20-USDT"
    assert reloaded.closed_trades[-1]["symbol"] == "OLD-519-USDT"


def test_closed_trades_thread_safe(tmp_path):
    risk = executor.risk
    symbols = []
    for i in range(5):
        sym = f"C{i}-USDT"
        symbols.append(sym)
        risk.register_entry(sym, "BUY", 1.0, 100.0, 98.0, 105.0, signal_id=f"RO-THREAD-{i}")

    def close(sym):
        risk.register_exit(sym, 110.0, 10.0, exit_reason="TP")

    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(close, symbols))

    assert len(risk.open_positions) == 0
    assert len(risk.closed_trades) == 5
    closed_symbols = sorted(t["symbol"] for t in risk.closed_trades)
    assert closed_symbols == sorted(symbols)


def test_scanner_honest_unavailable_by_default(client):
    res = client.get("/api/v1/scanner")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "DATA_UNAVAILABLE"
    assert data["scanned_at"] is None
    assert data["rows"] == []


def test_scanner_published_snapshot(client):
    obs = app.state.aegis_observability
    obs.publish_scanner([
        {
            "symbol": "BTC-USDT",
            "venue": "binance-testnet",
            "price": 50000.0,
            "market_regime": "TREND_BULL",
            "trend": "UP",
            "adx": 28.0,
            "rsi": 60.0,
            "rvol": 1.2,
            "liquidity": "HIGH",
            "setup_grade": 80.0,
            "risk_status": "CLEAR",
        }
    ])
    data = client.get("/api/v1/scanner").json()
    assert data["status"] == "AVAILABLE"
    assert len(data["rows"]) == 1
    assert data["rows"][0]["symbol"] == "BTC-USDT"
    assert data["scanned_at"] is not None


def test_intelligence_honest_unavailable_by_default(client):
    res = client.get("/api/v1/intelligence")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "DATA_UNAVAILABLE"
    assert data["regime"] == "DATA_UNAVAILABLE"
    assert data["market_summary"] is None
    assert data["supporting_factors"] == []
    assert data["risk_factors"] == []


def test_intelligence_published_snapshot(client):
    obs = app.state.aegis_observability
    obs.publish_intelligence({
        "regime": "TREND_BULL",
        "market_summary": "BTC confirmed in bullish regime with momentum.",
        "setup_explanation": "EMA 20 above EMA 50 on 1h.",
        "supporting_factors": ["Liquidity above floor", "RSI below overbought"],
        "risk_factors": ["Crowded positioning"],
        "guardian_decision": "APPROVED FOR PAPER",
    })
    data = client.get("/api/v1/intelligence").json()
    assert data["status"] == "AVAILABLE"
    assert data["regime"] == "TREND_BULL"
    assert data["guardian_decision"] == "APPROVED FOR PAPER"


def test_no_secrets_leaked_in_api_responses(client, monkeypatch, auth):
    monkeypatch.setenv("WEBHOOK_PASSPHRASE", "SUPER-SECRET-VALUE-12345")
    monkeypatch.setenv("BINANCE_API_KEY", "leaked-apikey-should-never-appear")
    monkeypatch.setenv("BINANCE_API_SECRET", "leaked-secret-should-never-appear")
    with auth:
        open_position(client, signal_id="RO-SECRET-1")
        client.post("/webhook", json={
            "passphrase": "TEST_SECRET",
            "symbol": "BTC-USDT",
            "action": "CLOSE",
            "price": 51000.0,
        })

    corpus = ""
    for ep in OBSERVABILITY_ENDPOINTS + ["/api/v1/health"]:
        res = client.get(ep)
        assert res.status_code == 200
        corpus += res.text.lower()

    assert "super-secret-value-12345" not in corpus
    assert "leaked-apikey" not in corpus
    assert "leaked-secret" not in corpus
    assert '"passphrase"' not in corpus
    assert '"api_key"' not in corpus

    for ep in OBSERVABILITY_ENDPOINTS:
        assert '"passphrase"' not in client.get(ep).text


def test_cors_restrictive_no_wildcard(client):
    for ep in OBSERVABILITY_ENDPOINTS:
        res = client.get(ep, headers={"Origin": "https://evil.example"})
        assert res.headers.get("access-control-allow-origin") is None, ep
        assert res.headers.get("access-control-allow-credentials") is None, ep


def test_cors_preflight_disallowed_origin(client):
    res = client.options(
        "/api/v1/positions",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert res.headers.get("access-control-allow-origin") is None
    assert res.headers.get("access-control-allow-credentials") is None
    assert res.status_code != 200


def test_intelligence_includes_cross_exchange_and_multiaccount_keys(client):
    data = client.get("/api/v1/intelligence").json()
    assert data["cross_exchange"] is None
    assert data["cross_exchange_status"] == "DATA_UNAVAILABLE"
    assert data["multi_account"] is None
    assert data["multi_account_status"] == "DATA_UNAVAILABLE"


def test_intelligence_cross_exchange_published(client):
    obs = app.state.aegis_observability
    obs.publish_cross_exchange({
        "symbol": "BTCUSDT",
        "state": "CONFIRMED",
        "sources_available": ["binance", "okx", "bybit"],
    })
    data = client.get("/api/v1/intelligence").json()
    assert data["cross_exchange_status"] == "AVAILABLE"
    assert data["cross_exchange"]["state"] == "CONFIRMED"


def test_multiaccount_honest_unavailable_by_default(client):
    data = client.get("/api/v1/multiaccount").json()
    assert data["status"] == "DATA_UNAVAILABLE"
    assert data["overview"] is None


def test_multiaccount_published_snapshot(client):
    obs = app.state.aegis_observability
    obs.publish_multi_account({
        "setup_count": 1,
        "child_count": 2,
        "setups": [{"setup_id": "PR-1", "symbol": "BTCUSDT", "status": "ACTIVE"}],
    })
    data = client.get("/api/v1/multiaccount").json()
    assert data["status"] == "AVAILABLE"
    assert data["overview"]["setup_count"] == 1
    assert data["generated_at"] is not None