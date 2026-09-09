"""Unit tests for BinanceExchangeFilterCache in apex.market.exchange_info."""

from apex.market.exchange_info import BinanceExchangeFilterCache


def test_exchange_filter_cache_load_and_format():
    cache = BinanceExchangeFilterCache()
    sample_payload = {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "status": "TRADING",
                "contractType": "PERPETUAL",
                "pricePrecision": 2,
                "quantityPrecision": 3,
                "filters": [
                    {"filterType": "PRICE_FILTER", "minPrice": "0.10", "maxPrice": "1000000.00", "tickSize": "0.10"},
                    {"filterType": "LOT_SIZE", "minQty": "0.001", "maxQty": "1000.000", "stepSize": "0.001"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5.0"},
                ],
            },
            {
                "symbol": "DOGEUSDT",
                "status": "TRADING",
                "contractType": "PERPETUAL",
                "pricePrecision": 5,
                "quantityPrecision": 0,
                "filters": [
                    {"filterType": "PRICE_FILTER", "minPrice": "0.00001", "maxPrice": "1000.00000", "tickSize": "0.00001"},
                    {"filterType": "LOT_SIZE", "minQty": "1", "maxQty": "10000000", "stepSize": "1"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5.0"},
                ],
            },
        ]
    }

    loaded = cache.load_rules_from_payload(sample_payload)
    assert loaded == 2

    # Formatting BTC price and quantity
    formatted_btc_price = cache.format_price("BTCUSDT", 65432.149)
    assert formatted_btc_price == 65432.10

    formatted_btc_qty = cache.format_quantity("BTCUSDT", 0.12345)
    assert formatted_btc_qty == 0.123

    # Formatting DOGE price and quantity
    formatted_doge_price = cache.format_price("DOGEUSDT", 0.123456)
    assert formatted_doge_price == 0.12346

    formatted_doge_qty = cache.format_quantity("DOGEUSDT", 1542.9)
    assert formatted_doge_qty == 1542.0

    # Notional validation
    assert cache.validate_notional("BTCUSDT", 0.001, 60000.0) is True  # 60 USD >= 5
    assert cache.validate_notional("BTCUSDT", 0.00001, 60000.0) is False  # 0.6 USD < 5
