from decimal import Decimal

import pytest

from apex.agents.models import LiquidityStatus, NormalizedMarketSnapshot, SourceStatus
from apex.intelligence.engine import (
    CrossExchangeIntelligence,
    normalize_symbol_for,
    relative_divergence,
)
from apex.intelligence.models import ConfirmationState

NOW_MS = 1_800_000_000_000


def snap(exchange, last="50000", bid="49999", ask="50001", volume="500000000",
         funding=None, oi=None, status=SourceStatus.FRESH):
    spread = None
    if bid and ask and Decimal(ask) >= Decimal(bid):
        spread = Decimal(ask) - Decimal(bid)
    return NormalizedMarketSnapshot(
        symbol="BTCUSDT" if exchange != "okx" else "BTC-USDT-SWAP",
        exchange=exchange,
        timestamp_ms=NOW_MS,
        last_price=Decimal(last) if last is not None else None,
        bid=Decimal(bid) if bid is not None else None,
        ask=Decimal(ask) if ask is not None else None,
        volume_24h=Decimal(volume) if volume is not None else None,
        funding_rate=Decimal(funding) if funding is not None else None,
        open_interest=Decimal(oi) if oi is not None else None,
        spread=spread,
        liquidity_status=(
            LiquidityStatus.HIGH if volume and Decimal(volume) >= Decimal("100000000")
            else LiquidityStatus.DATA_UNAVAILABLE
        ),
        source_status=status,
    )


class TestSymbolNormalization:
    def test_binance_and_bybit_flat(self):
        assert normalize_symbol_for("binance", "BTCUSDT") == "BTCUSDT"
        assert normalize_symbol_for("bybit", "btcusdt") == "BTCUSDT"

    def test_okx_swap(self):
        assert normalize_symbol_for("okx", "BTCUSDT") == "BTC-USDT-SWAP"
        assert normalize_symbol_for("okx", "ETH") == "ETH-USDT-SWAP"
        assert normalize_symbol_for("okx", "SOL-USDT") == "SOL-USDT-SWAP"
        assert normalize_symbol_for("okx", "BTC-USDT") == "BTC-USDT-SWAP"

    def test_strips_noise(self):
        assert normalize_symbol_for("binance", "BTC/USDT") == "BTCUSDT"


class TestDivergence:
    def test_fewer_than_two(self):
        assert relative_divergence([]) is None
        assert relative_divergence([1.0]) is None

    def test_zero_median(self):
        assert relative_divergence([0.0, 0.0]) is None

    def test_normal(self):
        assert relative_divergence([100.0, 100.0]) == 0.0
        assert abs(relative_divergence([100.0, 200.0]) - 2 / 3) < 1e-9


INTEL = CrossExchangeIntelligence()


class TestConfirmation:
    def test_three_way_agree_confirmed(self):
        report = INTEL.analyze({
            "binance": snap("binance", "50000"),
            "okx": snap("okx", "50000.1"),
            "bybit": snap("bybit", "50000.05"),
        })
        assert report.state == ConfirmationState.CONFIRMED
        assert report.is_confirmed
        assert report.sources_available == ["binance", "bybit", "okx"]

    def test_single_source_partial(self):
        report = INTEL.analyze({"binance": snap("binance", "50000")})
        assert report.state == ConfirmationState.PARTIAL_CONFIRMATION

    def test_price_divergence_detected(self):
        report = INTEL.analyze({
            "binance": snap("binance", "50000"),
            "okx": snap("okx", "52000"),
            "bybit": snap("bybit", "50000"),
        })
        assert report.state == ConfirmationState.DIVERGENT
        assert report.price_divergence_pct is not None
        assert report.price_divergence_pct > 0.03

    def test_total_outage(self):
        report = INTEL.analyze({
            "binance": snap("binance", status=SourceStatus.DATA_UNAVAILABLE),
            "okx": snap("okx", status=SourceStatus.DATA_UNAVAILABLE, oi=None),
        })
        assert report.state == ConfirmationState.DATA_UNAVAILABLE
        assert report.sources_available == []
        assert "binance" in report.sources_unavailable

    def test_stale_source_downgrades(self):
        report = INTEL.analyze({
            "binance": snap("binance", "50000", status=SourceStatus.FRESH),
            "okx": snap("okx", "50000.1", status=SourceStatus.STALE),
        })
        assert report.state == ConfirmationState.STALE

    def test_volume_divergence_is_divergent(self):
        report = INTEL.analyze({
            "binance": snap("binance", "50000", volume="500000000"),
            "okx": snap("okx", "50000.1", volume="300000000"),
        })
        assert report.state == ConfirmationState.DIVERGENT

    def test_no_price_among_available(self):
        report = INTEL.analyze({
            "binance": snap("binance", last=None, bid=None, ask=None, volume="5"),
            "okx": snap("okx", last=None, bid=None, ask=None, volume="5"),
        })
        assert report.state == ConfirmationState.DATA_UNAVAILABLE

    def test_missing_venue_reported(self):
        okx = snap("okx", status=SourceStatus.DATA_UNAVAILABLE)
        report = INTEL.analyze({"binance": snap("binance", "50000"), "okx": okx})
        assert "okx" in report.sources_unavailable

    def test_funding_divergence_flag(self):
        report = INTEL.analyze({
            "binance": snap("binance", "50000", funding="0.0005", volume="500"),
            "okx": snap("okx", "50000.1", funding="-0.0005", volume="600"),
        })
        # 0.0010 absolute funding spread > 0.0002
        assert report.funding_divergence is True
        assert report.funding_by_exchange["binance"] == pytest.approx(0.0005)

    def test_funding_within_band(self):
        report = INTEL.analyze({
            "binance": snap("binance", "50000", funding="0.0001", volume="500"),
            "okx": snap("okx", "50000.1", funding="0.0002", volume="600"),
        })
        assert report.funding_divergence is False

    def test_open_interest_divergence_flag(self):
        report = INTEL.analyze({
            "binance": snap("binance", "50000", oi="100", volume="500000000"),
            "okx": snap("okx", "50000.1", oi="3", volume="400000000"),
        })
        assert report.open_interest_divergence is True

    def test_report_serializes_without_fabrication(self):
        report = INTEL.analyze({"binance": snap("binance", "50000")})
        d = report.to_dict()
        assert d["state"] == "PARTIAL_CONFIRMATION"
        assert d["price_by_exchange"] == {"binance": 50000.0}
        assert "okx" not in d["price_by_exchange"]


class TestPartialAvailability:
    def test_one_of_three_unavailable_keeps_binance_fresh_path(self):
        report = INTEL.analyze({
            "binance": snap("binance", "50000", volume="500000000"),
            "okx": snap("okx", "50000.1", volume="600000000"),
            "bybit": snap("bybit", status=SourceStatus.DATA_UNAVAILABLE),
        })
        assert report.state == ConfirmationState.CONFIRMED
        assert report.sources_unavailable == ["bybit"]