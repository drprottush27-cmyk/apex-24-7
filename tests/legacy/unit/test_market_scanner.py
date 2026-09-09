import pytest
from engines.scanner.market_scanner import MarketScannerEngine


def test_market_scanner_filters():
    scanner = MarketScannerEngine(min_24h_volume_usd=50_000_000.0)

    sample_tickers = [
        # Valid, high volume
        {"symbol": "BTCUSDT", "lastPrice": "65000.0", "priceChangePercent": "2.5", "quoteVolume": "500000000.0", "highPrice": "66000", "lowPrice": "64000"},
        # Valid, high volume
        {"symbol": "SOLUSDT", "lastPrice": "150.0", "priceChangePercent": "-1.5", "quoteVolume": "250000000.0", "highPrice": "155", "lowPrice": "148"},
        # Below volume floor ($10M < $50M)
        {"symbol": "LOWVOLUSDT", "lastPrice": "1.0", "priceChangePercent": "0.5", "quoteVolume": "10000000.0", "highPrice": "1.1", "lowPrice": "0.9"},
        # Stablecoin pair (must be filtered out)
        {"symbol": "USDCUSDT", "lastPrice": "1.0", "priceChangePercent": "0.01", "quoteVolume": "800000000.0", "highPrice": "1.001", "lowPrice": "0.999"},
        # Non-USDT pair
        {"symbol": "BTCBUSD", "lastPrice": "65000.0", "priceChangePercent": "1.0", "quoteVolume": "100000000.0", "highPrice": "66000", "lowPrice": "64000"},
    ]

    ranked = scanner.filter_universe(sample_tickers)

    symbols = [r["symbol"] for r in ranked]
    assert "BTCUSDT" in symbols
    assert "SOLUSDT" in symbols
    assert "LOWVOLUSDT" not in symbols
    assert "USDCUSDT" not in symbols
    assert "BTCBUSD" not in symbols
    assert len(ranked) == 2
    # Ensure ranked by volume descending
    assert ranked[0]["symbol"] == "BTCUSDT"
