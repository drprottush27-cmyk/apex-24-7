import pytest
import os
from starlette.testclient import TestClient
from unittest.mock import patch

from src.webhook_server import app, executor, _seen_signal_ids

@pytest.fixture(autouse=True)
def clean_state():
    _seen_signal_ids.clear()
    executor.risk.open_positions.clear()
    executor.risk.cooldown_tracker.clear()
    executor.risk.circuit_breaker_tripped = False
    executor.risk.processed_signal_ids.clear()

@pytest.fixture
def client():
    return TestClient(app)

def test_webhook_unauthorized_missing_passphrase(client):
    res = client.post("/webhook", json={
        "passphrase": "wrong_passphrase",
        "symbol": "BTC-USDT",
        "action": "BUY",
        "price": 50000.0,
        "defensive_sl": 49000.0
    })
    assert res.status_code == 401
    assert "Unauthorized" in res.json()["detail"]

def test_webhook_malformed_action(client):
    with patch("src.webhook_server.get_secret_passphrase", return_value="TEST_SECRET"):
        res = client.post("/webhook", json={
            "passphrase": "TEST_SECRET",
            "symbol": "BTC-USDT",
            "action": "INVALID_ACTION",
            "price": 50000.0,
            "defensive_sl": 49000.0
        })
        assert res.status_code == 422

def test_webhook_malformed_negative_price(client):
    with patch("src.webhook_server.get_secret_passphrase", return_value="TEST_SECRET"):
        res = client.post("/webhook", json={
            "passphrase": "TEST_SECRET",
            "symbol": "BTC-USDT",
            "action": "BUY",
            "price": -100.0,
            "defensive_sl": 49000.0
        })
        assert res.status_code == 422

def test_webhook_missing_mandatory_sl(client):
    with patch("src.webhook_server.get_secret_passphrase", return_value="TEST_SECRET"):
        res = client.post("/webhook", json={
            "passphrase": "TEST_SECRET",
            "symbol": "BTC-USDT",
            "action": "BUY",
            "price": 50000.0
        })
        assert res.status_code == 422
        assert "mandatory" in res.json()["detail"]

def test_webhook_successful_execution(client):
    with patch("src.webhook_server.get_secret_passphrase", return_value="TEST_SECRET"):
        res = client.post("/webhook", json={
            "passphrase": "TEST_SECRET",
            "symbol": "BTC-USDT",
            "action": "BUY",
            "price": 50000.0,
            "defensive_sl": 49000.0,
            "signal_id": "TV-BTC-001"
        })
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "executed"
        assert data["symbol"] == "BTC-USDT"
        assert data["authorized_qty"] > 0
        assert data["take_profit"] == 52500.0

def test_webhook_replay_duplicate_protection(client):
    with patch("src.webhook_server.get_secret_passphrase", return_value="TEST_SECRET"):
        # First call succeeds
        res1 = client.post("/webhook", json={
            "passphrase": "TEST_SECRET",
            "symbol": "BTC-USDT",
            "action": "BUY",
            "price": 50000.0,
            "defensive_sl": 49000.0,
            "signal_id": "TV-BTC-DUP"
        })
        assert res1.status_code == 200

        # Second call with same signal_id is rejected with 409 Conflict
        res2 = client.post("/webhook", json={
            "passphrase": "TEST_SECRET",
            "symbol": "ETH-USDT",
            "action": "BUY",
            "price": 3000.0,
            "defensive_sl": 2900.0,
            "signal_id": "TV-BTC-DUP"
        })
        assert res2.status_code == 409
        assert "Duplicate signal rejected" in res2.json()["detail"]

def test_webhook_risk_guardian_rejection_response(client):
    with patch("src.webhook_server.get_secret_passphrase", return_value="TEST_SECRET"):
        # SL too wide (> 5%)
        res = client.post("/webhook", json={
            "passphrase": "TEST_SECRET",
            "symbol": "BTC-USDT",
            "action": "BUY",
            "price": 50000.0,
            "defensive_sl": 40000.0,
            "signal_id": "TV-BTC-WIDE"
        })
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "rejected"
        assert "too wide" in data["reason"]

def test_webhook_position_close(client):
    with patch("src.webhook_server.get_secret_passphrase", return_value="TEST_SECRET"):
        # 1. Open a position
        client.post("/webhook", json={
            "passphrase": "TEST_SECRET",
            "symbol": "BTC-USDT",
            "action": "BUY",
            "price": 50000.0,
            "defensive_sl": 49000.0,
            "signal_id": "TV-BTC-OPEN"
        })

        # 2. Close the position
        res = client.post("/webhook", json={
            "passphrase": "TEST_SECRET",
            "symbol": "BTC-USDT",
            "action": "CLOSE",
            "price": 51000.0
        })
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "executed"
        assert data["realized_pnl"] > 0

def test_webhook_health_endpoints(client):
    for endpoint in ["/api/v1/health", "/health"]:
        res = client.get(endpoint)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "healthy"
        assert data["service"] == "aegis-webhook"
        assert data["trading_mode"] == "PAPER"
        assert data["live_trading_enabled"] is False
        assert "circuit_breaker_tripped" in data
        assert "open_positions_count" in data
        assert "passphrase" not in data
        assert "secret" not in str(data).lower()

def test_webhook_rr_ratio_rejection_and_acceptance(client):
    with patch("src.webhook_server.get_secret_passphrase", return_value="TEST_SECRET"):
        # 1:2 R:R (52000 TP with 49000 SL on 50000 Entry) must be REJECTED (< 1:2.5)
        res_reject = client.post("/webhook", json={
            "passphrase": "TEST_SECRET",
            "symbol": "SOL-USDT",
            "action": "BUY",
            "price": 50000.0,
            "defensive_sl": 49000.0,
            "take_profit": 52000.0,
            "signal_id": "TV-SOL-RR2"
        })
        assert res_reject.status_code == 200
        assert res_reject.json()["status"] == "rejected"
        assert "below required minimum" in res_reject.json()["reason"]

        # 1:2.5 R:R (52500 TP with 49000 SL on 50000 Entry) must be ACCEPTED
        res_accept = client.post("/webhook", json={
            "passphrase": "TEST_SECRET",
            "symbol": "SOL-USDT",
            "action": "BUY",
            "price": 50000.0,
            "defensive_sl": 49000.0,
            "take_profit": 52500.0,
            "signal_id": "TV-SOL-RR25"
        })
        assert res_accept.status_code == 200
        assert res_accept.json()["status"] == "executed"
        assert res_accept.json()["take_profit"] == 52500.0

        # Malformed negative TP must return 422
        res_bad = client.post("/webhook", json={
            "passphrase": "TEST_SECRET",
            "symbol": "SOL-USDT",
            "action": "BUY",
            "price": 50000.0,
            "defensive_sl": 49000.0,
            "take_profit": -52500.0,
            "signal_id": "TV-SOL-BAD"
        })
        assert res_bad.status_code == 422
