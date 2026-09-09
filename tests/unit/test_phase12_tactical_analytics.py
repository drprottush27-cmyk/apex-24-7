from __future__ import annotations

from collections.abc import Callable

from apex.domain.candles import Candle
from apex.domain.types import Timeframe
from apex.engines.tactical import (
    DepthLevel,
    DepthSnapshot,
    FundingPoint,
    LiquidationCluster,
    LiquidationContext,
    LiquidationPoint,
    LiquidationSource,
    OpenInterestPoint,
    TacticalConfig,
    TacticalObservations,
    TacticalScorer,
    TacticalVerdict,
    WhaleFlowContext,
    WhaleFlowLabelQuality,
    bbw_percentile_rank,
    directional_bias,
    oi_expansion_pct,
)
from apex.market.candle_series import CandleSeries
from apex.safety.exceptions import InvalidNumericalDataError

BASE_TS = 1_700_000_000_000


def candle(open_time_ms: int, close: float, volume: float, spread: float = 0.02) -> Candle:
    return Candle(
        symbol="TESTUSDT",
        timeframe=Timeframe.M5,
        open_time_ms=open_time_ms,
        close_time_ms=open_time_ms + 299_999,
        open=close - spread / 2,
        high=close + spread / 2,
        low=close - spread / 2,
        close=close,
        volume=volume,
        is_closed=True,
    )


def flat_series(count: int = 120, price: float = 100.0, volume: float = 100.0) -> CandleSeries:
    candles: list[Candle] = []
    for i in range(count):
        # small oscillation around price keeps high/low geometry valid
        wiggle = 0.01 if i % 2 == 0 else -0.01
        candles.append(
            candle(
                open_time_ms=BASE_TS + (i + 1) * 300_000,
                close=price + wiggle,
                volume=volume,
            )
        )
    return CandleSeries.from_iterable(candles)


def trending_up_series(
    count: int = 120,
    start: float = 100.0,
    step: float = 0.2,
    volume: float = 100.0,
) -> CandleSeries:
    candles: list[Candle] = []
    price = start
    for i in range(count):
        price = start + i * step
        candles.append(
            candle(open_time_ms=BASE_TS + (i + 1) * 300_000, close=price, volume=volume)
        )
    return CandleSeries.from_iterable(candles)


def assert_rejected(fn: Callable[..., object], *args: object, **kwargs: object) -> None:
    try:
        fn(*args, **kwargs)
    except (InvalidNumericalDataError, ValueError):
        return
    raise AssertionError("expected failure")


def test_bbw_percentile_rank_bounds() -> None:
    closes = tuple(100.0 + 0.01 * (i % 5) for i in range(140))
    rank = bbw_percentile_rank(closes, period=20, lookback=100)
    assert 0.0 <= rank <= 100.0


def test_bbw_percentile_rank_low_when_tight() -> None:
    # Wide oscillation, then a recent crush to near-flat -> the latest
    # bandwidth is the tightest in the trailing (last-100) window -> low
    # percentile rank.
    closes = tuple(
        100.0 + (2.0 if i % 2 == 0 else -2.0) if i < 130 else 100.0
        for i in range(165)
    )
    rank = bbw_percentile_rank(closes, period=20, lookback=100)
    assert rank < 20.0


def test_bbw_percentile_rank_requires_history() -> None:
    assert_rejected(bbw_percentile_rank, tuple(50.0 for _ in range(30)), period=20, lookback=100)


def test_directional_bias_positive_when_up() -> None:
    closes = tuple(100.0 + i * 0.1 for i in range(30))
    volumes = tuple(100.0 for _ in range(30))
    bias = directional_bias(closes, volumes)
    assert bias > 0.0


def test_directional_bias_negative_when_down() -> None:
    closes = tuple(100.0 - i * 0.1 for i in range(30))
    volumes = tuple(100.0 for _ in range(30))
    bias = directional_bias(closes, volumes)
    assert bias < 0.0


def test_directional_bias_range() -> None:
    closes = tuple(100.0 + (0.1 if i % 2 == 0 else 0.0) for i in range(30))
    volumes = tuple(100.0 for _ in range(30))
    bias = directional_bias(closes, volumes)
    assert -1.0 <= bias <= 1.0


def test_oi_expansion_positive() -> None:
    now = 1_700_000_000_000
    history = (
        OpenInterestPoint(timestamp_ms=now - 3_600_000, value=1000.0),
        OpenInterestPoint(timestamp_ms=now - 3_600_000 + 600_000, value=1200.0),
        OpenInterestPoint(timestamp_ms=now, value=1300.0),
    )
    pct = oi_expansion_pct(history, now, window_start_ms=now - 3_600_000)
    assert pct is not None
    assert pct > 0.0


def test_oi_expansion_contraction() -> None:
    now = 1_700_000_000_000
    history = (
        OpenInterestPoint(timestamp_ms=now - 3_600_000, value=1300.0),
        OpenInterestPoint(timestamp_ms=now, value=1000.0),
    )
    pct = oi_expansion_pct(history, now, window_start_ms=now - 3_600_000)
    assert pct is not None
    assert pct < 0.0


def test_oi_expansion_returns_none_with_insufficient_data() -> None:
    now = 1_700_000_000_000
    history = (OpenInterestPoint(timestamp_ms=now, value=1000.0),)
    assert oi_expansion_pct(history, now, window_start_ms=now - 3_600_000) is None


def test_oi_expansion_rejects_lookahead() -> None:
    now = 1_700_000_000_000
    history = (
        OpenInterestPoint(timestamp_ms=now, value=1000.0),
        OpenInterestPoint(timestamp_ms=now + 3_600_000, value=2000.0),
    )
    assert_rejected(oi_expansion_pct, history, now, window_start_ms=now - 3_600_000)


def test_scorer_no_data_verdict() -> None:
    series = flat_series()
    result = TacticalScorer().evaluate("TESTUSDT", "5m", series)
    assert result.verdict in (TacticalVerdict.LOW, TacticalVerdict.NO_DATA)
    assert 0.0 <= result.score <= 100.0


def test_scorer_observations_raise_score() -> None:
    series = flat_series()
    now = series.latest.open_time_ms
    obs = TacticalObservations(
        oi_history=(
            OpenInterestPoint(timestamp_ms=now - 3_600_000, value=1000.0),
            OpenInterestPoint(timestamp_ms=now, value=1500.0),
        ),
        funding_history=(FundingPoint(timestamp_ms=now, rate=0.00005),),
        liquidations=(
            LiquidationPoint(timestamp_ms=now, side="SELL", notional=2_000_000.0),
            LiquidationPoint(timestamp_ms=now - 600_000, side="SELL", notional=1_500_000.0),
        ),
        depth=DepthSnapshot(
            mid_price=100.0,
            bids=(DepthLevel(price=99.9, quantity=200.0), DepthLevel(price=99.8, quantity=200.0)),
            asks=(DepthLevel(price=100.1, quantity=50.0), DepthLevel(price=100.2, quantity=50.0)),
        ),
    )
    baseline = TacticalScorer().evaluate("TESTUSDT", "5m", series)
    enhanced = TacticalScorer().evaluate("TESTUSDT", "5m", series, observations=obs)
    assert enhanced.score >= baseline.score
    assert enhanced.verdict.value in {"LOW", "MEDIUM", "HIGH"}


def test_scorer_deterministic() -> None:
    series = flat_series()
    scorer = TacticalScorer()
    first = scorer.evaluate("TESTUSDT", "5m", series)
    second = scorer.evaluate("TESTUSDT", "5m", series)
    assert first == second


def test_scorer_custom_config() -> None:
    cfg = TacticalConfig(bbw_percentile_max=10.0, high_threshold=80.0, medium_threshold=50.0)
    series = flat_series()
    result = TacticalScorer(cfg).evaluate("TESTUSDT", "5m", series)
    assert 0.0 <= result.score <= 100.0


def test_tactical_config_rejects_invalid_threshold_order() -> None:
    assert_rejected(TacticalConfig, medium_threshold=50.0, high_threshold=40.0)


def test_depth_imbalance_direction() -> None:
    from apex.engines.tactical import depth_imbalance

    bid_heavy = DepthSnapshot(
        mid_price=100.0,
        bids=(DepthLevel(price=99.9, quantity=500.0),),
        asks=(DepthLevel(price=100.1, quantity=50.0),),
    )
    imb = depth_imbalance(bid_heavy, band_pct=0.01)
    assert imb is not None
    assert imb < 0.0


def test_features_to_metadata_json_safe() -> None:
    import json

    series = trending_up_series()
    assert series.latest.is_closed is True
    result = TacticalScorer().evaluate("TESTUSDT", "5m", series)
    json.dumps(result.to_metadata())
    assert result.candle_timestamp_ms == series.latest.open_time_ms


def test_scorer_never_authorizes() -> None:
    # Structurally assert the advisory contract: a TacticalResult exposes no
    # execution, risk, or authorization surface.
    result = TacticalScorer().evaluate("TESTUSDT", "5m", flat_series())
    metadata = result.to_metadata()
    assert metadata["advisory"] is True
    for forbidden in ("approved", "quantity", "execute", "authorize", "risk"):
        assert forbidden not in metadata


def test_observations_validate() -> None:
    assert_rejected(
        OpenInterestPoint, timestamp_ms=-1, value=100.0
    )
    assert_rejected(
        DepthLevel, price=0.0, quantity=100.0
    )
    assert_rejected(
        LiquidationPoint, timestamp_ms=1, side="HODL", notional=100.0
    )


def test_liquidation_context_model() -> None:
    cluster1 = LiquidationCluster(
        price_level=65000.0,
        estimated_notional=1_500_000.0,
        side="SHORT_LIQ",
        is_estimated=True,
    )
    cluster2 = LiquidationCluster(
        price_level=62000.0,
        estimated_notional=2_200_000.0,
        side="LONG_LIQ",
        is_estimated=True,
    )
    ctx = LiquidationContext(
        symbol="btcusdt",
        timestamp_ms=1_700_000_000_000,
        source=LiquidationSource.PROXY,
        clusters=(cluster1, cluster2),
        is_estimated=True,
        confidence=0.75,
    )
    assert ctx.symbol == "BTCUSDT"
    assert ctx.source == LiquidationSource.PROXY
    assert ctx.is_estimated is True
    assert ctx.confidence == 0.75
    assert len(ctx.clusters) == 2

    meta = ctx.to_metadata()
    assert meta["advisory"] is True
    assert meta["source"] == "PROXY"
    assert meta["is_estimated"] is True
    assert meta["cluster_count"] == 2
    assert len(meta["clusters"]) == 2  # type: ignore[arg-type]


def test_liquidation_context_rejects_invalid_values() -> None:
    assert_rejected(
        LiquidationCluster, price_level=-10.0, estimated_notional=100.0, side="LONG_LIQ"
    )
    assert_rejected(
        LiquidationCluster, price_level=100.0, estimated_notional=-1.0, side="LONG_LIQ"
    )
    assert_rejected(
        LiquidationCluster, price_level=100.0, estimated_notional=100.0, side="INVALID_SIDE"
    )
    assert_rejected(
        LiquidationContext, symbol="", timestamp_ms=100, source=LiquidationSource.PROXY
    )
    assert_rejected(
        LiquidationContext, symbol="BTCUSDT", timestamp_ms=-1, source=LiquidationSource.PROXY
    )
    assert_rejected(
        LiquidationContext, symbol="BTCUSDT", timestamp_ms=100, source=LiquidationSource.PROXY, confidence=1.5
    )


def test_whale_flow_context_model() -> None:
    ctx = WhaleFlowContext(
        symbol="ethusdt",
        timestamp_ms=1_700_000_000_000,
        net_flow=-15_000_000.0,
        large_inflow=5_000_000.0,
        large_outflow=20_000_000.0,
        label_quality=WhaleFlowLabelQuality.VERIFIED_EXCHANGE_LABEL,
        source="ON_CHAIN_INDEXER",
        confidence=0.90,
    )
    assert ctx.symbol == "ETHUSDT"
    assert ctx.net_flow == -15_000_000.0
    assert ctx.label_quality == WhaleFlowLabelQuality.VERIFIED_EXCHANGE_LABEL
    assert ctx.confidence == 0.90

    meta = ctx.to_metadata()
    assert meta["advisory"] is True
    assert meta["label_quality"] == "VERIFIED_EXCHANGE_LABEL"
    assert meta["net_flow"] == -15_000_000.0


def test_whale_flow_context_rejects_invalid_values() -> None:
    assert_rejected(
        WhaleFlowContext, symbol="", timestamp_ms=100, net_flow=0.0
    )
    assert_rejected(
        WhaleFlowContext, symbol="ETHUSDT", timestamp_ms=-1, net_flow=0.0
    )
    assert_rejected(
        WhaleFlowContext, symbol="ETHUSDT", timestamp_ms=100, net_flow=float("nan")
    )
    assert_rejected(
        WhaleFlowContext, symbol="ETHUSDT", timestamp_ms=100, net_flow=0.0, large_inflow=-1.0
    )
    assert_rejected(
        WhaleFlowContext, symbol="ETHUSDT", timestamp_ms=100, net_flow=0.0, confidence=2.0
    )
