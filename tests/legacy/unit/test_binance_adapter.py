import pytest
from execution.adapters.binance.exchange_info import BinanceExchangeFilterCache
from execution.adapters.binance.client import BinanceFuturesClient


def test_binance_filter_formatting():
    cache = BinanceExchangeFilterCache()
    cache._symbol_rules["BTCUSDT"] = {
        "stepSize": 0.001,
        "quantityPrecision": 3,
        "tickSize": 0.10,
        "pricePrecision": 1,
        "minNotional": 5.0,
    }

    qty = cache.format_quantity("BTCUSDT", 0.05489)
    assert qty == 0.054

    price = cache.format_price("BTCUSDT", 65432.17)
    assert price == 65432.2

    assert cache.validate_notional("BTCUSDT", 0.01, 60000.0) is True
    assert cache.validate_notional("BTCUSDT", 0.00001, 100.0) is False


def test_binance_client_payload_signing():
    client = BinanceFuturesClient()
    client.api_secret = "mock_secret_key"
    params = {"symbol": "BTCUSDT", "side": "BUY"}
    signed_query = client._sign_payload(params)

    assert "timestamp=" in signed_query
    assert "signature=" in signed_query
    assert "symbol=BTCUSDT" in signed_query
