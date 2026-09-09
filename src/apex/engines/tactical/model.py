"""APEX 24/7 — Tactical Confluence Analytics Models (Phase 12).

Advisory-only observation and configuration models for tactical confluence
scoring. NOTHING in this module can authorize, veto, size, or execute an
order. All outputs are deterministic metadata for journaling and human
review.

Consumption rules:
- Inputs are closed-candle CandleSeries plus optional observation snapshots.
- Observations are snapshots (never live streams read here); optional fields
  may be None and analytics must degrade safely to "no data".
- Thresholds are tunable, validated, advisory defaults — they are NOT proven
  trading truths and NEVER influence risk parameters.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apex.safety.exceptions import InvalidNumericalDataError


class TacticalConfig(BaseModel):
    """Tunable advisory thresholds for confluence scoring.

    These are defaults for deterministic analytics, deliberately ADVISORY:
    they are not validated trading truths and must never feed risk limits.
    All bounds enforce finite, positive, sane values at construction.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    bbw_period: int = Field(default=20, ge=2, le=200)
    bbw_lookback: int = Field(default=100, ge=20, le=1000)
    bbw_percentile_max: float = Field(default=25.0, ge=0.0, le=100.0)

    rvol_period: int = Field(default=20, ge=2, le=200)
    rvol_expansion_min: float = Field(default=1.50, ge=0.0, le=100.0)

    oi_window_ms: int = Field(default=3_600_000, ge=60_000, le=86_400_000)
    oi_expansion_min_pct: float = Field(default=5.0, ge=0.0, le=200.0)

    funding_lookback: int = Field(default=4, ge=2, le=100)
    funding_max_rate: float = Field(default=0.0005, ge=0.0, le=0.01)

    depth_band_pct: float = Field(default=0.01, gt=0.0, le=1.0)
    depth_imbalance_min: float = Field(default=0.15, ge=0.0, le=1.0)

    liquidation_window_ms: int = Field(default=3_600_000, ge=60_000, le=86_400_000)
    liquidation_notional_min: float = Field(default=100_000.0, ge=0.0)

    # Advisory scoring weights (sum may exceed 100; result is clamped).
    score_weight_compression: float = Field(default=25.0, ge=0.0, le=100.0)
    score_weight_volume: float = Field(default=20.0, ge=0.0, le=100.0)
    score_weight_oi: float = Field(default=10.0, ge=0.0, le=100.0)
    score_weight_funding: float = Field(default=10.0, ge=0.0, le=100.0)
    score_weight_depth: float = Field(default=10.0, ge=0.0, le=100.0)
    score_weight_bias: float = Field(default=10.0, ge=0.0, le=100.0)
    score_weight_liquidations: float = Field(default=15.0, ge=0.0, le=100.0)
    score_weight_sfp: float = Field(default=15.0, ge=0.0, le=100.0)
    score_weight_regime: float = Field(default=10.0, ge=0.0, le=100.0)
    score_weight_rs: float = Field(default=10.0, ge=0.0, le=100.0)

    high_threshold: float = Field(default=60.0, ge=0.0, le=100.0)
    medium_threshold: float = Field(default=35.0, ge=0.0, le=100.0)

    @model_validator(mode="after")
    def validate_threshold_ordering(self) -> TacticalConfig:
        if self.medium_threshold >= self.high_threshold:
            raise InvalidNumericalDataError(
                "medium_threshold must be below high_threshold"
            )
        return self


@dataclass(frozen=True, slots=True)
class OpenInterestPoint:
    """Single open-interest observation at a point in time."""

    timestamp_ms: int
    value: float

    def __post_init__(self) -> None:
        if self.timestamp_ms < 0:
            raise InvalidNumericalDataError("OI timestamp must be non-negative")
        if not isfinite(self.value) or self.value < 0.0:
            raise InvalidNumericalDataError("OI value must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class FundingPoint:
    """Single funding-rate observation."""

    timestamp_ms: int
    rate: float

    def __post_init__(self) -> None:
        if self.timestamp_ms < 0:
            raise InvalidNumericalDataError("funding timestamp must be non-negative")
        if not isfinite(self.rate):
            raise InvalidNumericalDataError("funding rate must be finite")


@dataclass(frozen=True, slots=True)
class DepthLevel:
    """A single order-book level (price, notional quantity)."""

    price: float
    quantity: float

    def __post_init__(self) -> None:
        if not isfinite(self.price) or self.price <= 0.0:
            raise InvalidNumericalDataError("depth price must be positive and finite")
        if not isfinite(self.quantity) or self.quantity < 0.0:
            raise InvalidNumericalDataError("depth quantity must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class DepthSnapshot:
    """Best-bid/ask book snapshot within a price band."""

    mid_price: float
    bids: tuple[DepthLevel, ...]
    asks: tuple[DepthLevel, ...]

    def __post_init__(self) -> None:
        if not isfinite(self.mid_price) or self.mid_price <= 0.0:
            raise InvalidNumericalDataError("mid price must be positive and finite")
        if not self.bids or not self.asks:
            raise InvalidNumericalDataError("depth snapshot requires bids and asks")


@dataclass(frozen=True, slots=True)
class LiquidationPoint:
    """Single liquidation event observation (advisory direction proxy)."""

    timestamp_ms: int
    side: str  # "BUY" or "SELL"
    notional: float

    def __post_init__(self) -> None:
        side = self.side.strip().upper()
        if side not in ("BUY", "SELL"):
            raise InvalidNumericalDataError("liquidation side must be BUY or SELL")
        if self.timestamp_ms < 0:
            raise InvalidNumericalDataError("liquidation timestamp must be non-negative")
        if not isfinite(self.notional) or self.notional < 0.0:
            raise InvalidNumericalDataError("liquidation notional must be finite and non-negative")
        object.__setattr__(self, "side", side)


@dataclass(frozen=True, slots=True)
class TacticalObservations:
    """Optional tactical observations attached to a signal evaluation.

    Every field is optional; analytics must treat missing data as "no
    evidence" rather than failure.
    """

    oi_history: tuple[OpenInterestPoint, ...] = ()
    funding_history: tuple[FundingPoint, ...] = ()
    depth: DepthSnapshot | None = None
    liquidations: tuple[LiquidationPoint, ...] = ()


@dataclass(frozen=True, slots=True)
class TacticalFeatures:
    """Raw deterministic feature values computed for a signal candidate."""

    bbw_percentile: float
    rvol: float
    oi_expansion_pct: float | None
    funding_rate: float | None
    funding_velocity: float | None
    depth_imbalance: float | None
    directional_bias: float
    liquidation_imbalance_pct: float | None
    atr_ratio: float = 1.0
    volatility_regime: str = "NORMAL"
    rs_percentile: float | None = None
    sfp_bullish: bool = False
    sfp_bearish: bool = False

    @property
    def has_oi(self) -> bool:
        return self.oi_expansion_pct is not None

    @property
    def has_funding(self) -> bool:
        return self.funding_rate is not None

    @property
    def has_funding_velocity(self) -> bool:
        return self.funding_velocity is not None

    @property
    def has_depth(self) -> bool:
        return self.depth_imbalance is not None

    @property
    def has_liquidations(self) -> bool:
        return self.liquidation_imbalance_pct is not None

    @property
    def has_rs(self) -> bool:
        return self.rs_percentile is not None

    @property
    def has_sfp(self) -> bool:
        return self.sfp_bullish or self.sfp_bearish


class TacticalVerdict(StrEnum):
    """Advisory confluence verdict tiers."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NO_DATA = "NO_DATA"


class TacticalResult(BaseModel):
    """Advisory confluence scoring result.

    Strictly ADVISORY. This metadata NEVER authorizes an order, never alters
    risk parameters, and never bypasses RiskGuardian/OEM/EndpointGuard.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    timeframe: str
    candle_timestamp_ms: int
    score: float = Field(ge=0.0, le=100.0)
    verdict: TacticalVerdict
    features: TacticalFeatures
    reasons: tuple[str, ...] = ()

    def to_metadata(self) -> dict[str, object]:
        """Serialize to a JSON-safe advisory metadata dict with full explainability."""
        features: dict[str, object] = {
            "bbw_percentile": self.features.bbw_percentile,
            "rvol": self.features.rvol,
            "oi_expansion_pct": self.features.oi_expansion_pct,
            "funding_rate": self.features.funding_rate,
            "funding_velocity": self.features.funding_velocity,
            "depth_imbalance": self.features.depth_imbalance,
            "directional_bias": self.features.directional_bias,
            "liquidation_imbalance_pct": self.features.liquidation_imbalance_pct,
            "atr_ratio": self.features.atr_ratio,
            "volatility_regime": self.features.volatility_regime,
            "rs_percentile": self.features.rs_percentile,
            "sfp_bullish": self.features.sfp_bullish,
            "sfp_bearish": self.features.sfp_bearish,
        }

        # Per-component explainability: source, availability, confidence, is_estimated, weight
        component_details: dict[str, dict[str, object]] = {
            "volatility_compression": {
                "value": self.features.bbw_percentile,
                "source": "closed_candles",
                "available": True,
                "confidence": 1.0,
                "is_estimated": False,
                "weight": self.config.score_weight_compression if hasattr(self, "config") else None,
            },
            "volume_expansion": {
                "value": self.features.rvol,
                "source": "closed_candles",
                "available": True,
                "confidence": 1.0,
                "is_estimated": False,
                "weight": self.config.score_weight_volume if hasattr(self, "config") else None,
            },
            "oi_expansion": {
                "value": self.features.oi_expansion_pct,
                "source": "binance_open_interest_hist" if self.features.oi_expansion_pct is not None else "unavailable",
                "available": self.features.has_oi,
                "confidence": 0.9 if self.features.has_oi else 0.0,
                "is_estimated": False,
                "weight": self.config.score_weight_oi if hasattr(self, "config") else None,
            },
            "funding_rate": {
                "value": self.features.funding_rate,
                "source": "binance_funding_rate" if self.features.funding_rate is not None else "unavailable",
                "available": self.features.has_funding,
                "confidence": 0.9 if self.features.has_funding else 0.0,
                "is_estimated": False,
                "weight": self.config.score_weight_funding if hasattr(self, "config") else None,
            },
            "funding_velocity": {
                "value": self.features.funding_velocity,
                "source": "binance_funding_rate" if self.features.funding_velocity is not None else "unavailable",
                "available": self.features.has_funding_velocity,
                "confidence": 0.8 if self.features.has_funding_velocity else 0.0,
                "is_estimated": False,
                "weight": None,  # Not currently scored
            },
            "depth_imbalance": {
                "value": self.features.depth_imbalance,
                "source": "binance_depth" if self.features.depth_imbalance is not None else "unavailable",
                "available": self.features.has_depth,
                "confidence": 0.7 if self.features.has_depth else 0.0,
                "is_estimated": False,
                "weight": self.config.score_weight_depth if hasattr(self, "config") else None,
            },
            "directional_bias": {
                "value": self.features.directional_bias,
                "source": "closed_candles",
                "available": True,
                "confidence": 0.7,
                "is_estimated": False,
                "weight": self.config.score_weight_bias if hasattr(self, "config") else None,
            },
            "liquidation_imbalance": {
                "value": self.features.liquidation_imbalance_pct,
                "source": "proxy_estimate" if self.features.liquidation_imbalance_pct is not None else "unavailable",
                "available": self.features.has_liquidations,
                "confidence": 0.4 if self.features.has_liquidations else 0.0,  # Conservative for proxy
                "is_estimated": True,
                "weight": self.config.score_weight_liquidations if hasattr(self, "config") else None,
            },
            "volatility_regime": {
                "value": self.features.volatility_regime,
                "atr_ratio": self.features.atr_ratio,
                "source": "closed_candles",
                "available": True,
                "confidence": 1.0,
                "is_estimated": False,
                "weight": self.config.score_weight_regime if hasattr(self, "config") else None,
            },
            "relative_strength": {
                "value": self.features.rs_percentile,
                "source": "cross_sectional" if self.features.rs_percentile is not None else "unavailable",
                "available": self.features.has_rs,
                "confidence": 0.9 if self.features.has_rs else 0.0,
                "is_estimated": False,
                "weight": self.config.score_weight_rs if hasattr(self, "config") else None,
            },
            "swing_failure_pattern": {
                "value": "BULLISH" if self.features.sfp_bullish else ("BEARISH" if self.features.sfp_bearish else "NONE"),
                "source": "closed_candles",
                "available": self.features.has_sfp,
                "confidence": 1.0 if self.features.has_sfp else 0.0,
                "is_estimated": False,
                "weight": self.config.score_weight_sfp if hasattr(self, "config") else None,
            },
        }

        return {
            "score": self.score,
            "verdict": self.verdict.value,
            "features": features,
            "component_details": component_details,
            "reasons": list(self.reasons),
            "advisory": True,
        }


# ── Future Market Intelligence Interfaces (Phases 16/17) ──────────────────────


class LiquidationSource(StrEnum):
    """Origin of liquidation intelligence."""

    DIRECT = "DIRECT"
    PROXY = "PROXY"
    EXTERNAL = "EXTERNAL"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class LiquidationCluster:
    """Estimated liquidation price level and approximate volume pool.

    Always labeled ESTIMATED when derived from proxy indicators (OI, funding, volatility).
    """

    price_level: float
    estimated_notional: float
    side: str  # "LONG_LIQ" or "SHORT_LIQ"
    is_estimated: bool = True

    def __post_init__(self) -> None:
        if not isfinite(self.price_level) or self.price_level <= 0.0:
            raise InvalidNumericalDataError("liquidation cluster price must be positive and finite")
        if not isfinite(self.estimated_notional) or self.estimated_notional < 0.0:
            raise InvalidNumericalDataError("estimated notional must be finite and non-negative")
        side_norm = self.side.strip().upper()
        if side_norm not in ("LONG_LIQ", "SHORT_LIQ"):
            raise InvalidNumericalDataError("liquidation cluster side must be LONG_LIQ or SHORT_LIQ")
        object.__setattr__(self, "side", side_norm)


@dataclass(frozen=True, slots=True)
class LiquidationContext:
    """Advisory liquidation intelligence context.

    Provides bounded, explainable liquidation context. Never fabricates data
    and explicitly marks proxies as ESTIMATED.
    """

    symbol: str
    timestamp_ms: int
    source: LiquidationSource
    clusters: tuple[LiquidationCluster, ...] = ()
    is_estimated: bool = True
    confidence: float = 0.0  # Bounded 0.0 to 1.0

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise InvalidNumericalDataError("symbol cannot be empty")
        if self.timestamp_ms < 0:
            raise InvalidNumericalDataError("timestamp must be non-negative")
        if not isfinite(self.confidence) or not (0.0 <= self.confidence <= 1.0):
            raise InvalidNumericalDataError("confidence must be a finite float in [0.0, 1.0]")
        object.__setattr__(self, "symbol", self.symbol.strip().upper())

    def to_metadata(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "timestamp_ms": self.timestamp_ms,
            "source": self.source.value,
            "is_estimated": self.is_estimated,
            "confidence": self.confidence,
            "cluster_count": len(self.clusters),
            "clusters": [
                {
                    "price_level": c.price_level,
                    "estimated_notional": c.estimated_notional,
                    "side": c.side,
                    "is_estimated": c.is_estimated,
                }
                for c in self.clusters
            ],
            "advisory": True,
        }


class WhaleFlowLabelQuality(StrEnum):
    """Quality tier of whale wallet attribution."""

    VERIFIED_EXCHANGE_LABEL = "VERIFIED_EXCHANGE_LABEL"
    HEURISTIC = "HEURISTIC"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class WhaleFlowContext:
    """Advisory on-chain / large exchange transfer intelligence.

    Never fabricates exchange wallet labels. Distinguishes verified
    exchange labels from heuristic or unknown sources.
    """

    symbol: str
    timestamp_ms: int
    net_flow: float  # Positive = net inflow, negative = net outflow
    large_inflow: float = 0.0
    large_outflow: float = 0.0
    label_quality: WhaleFlowLabelQuality = WhaleFlowLabelQuality.UNKNOWN
    source: str = "UNAVAILABLE"
    confidence: float = 0.0

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise InvalidNumericalDataError("symbol cannot be empty")
        if self.timestamp_ms < 0:
            raise InvalidNumericalDataError("timestamp must be non-negative")
        if not isfinite(self.net_flow):
            raise InvalidNumericalDataError("net_flow must be finite")
        if not isfinite(self.large_inflow) or self.large_inflow < 0.0:
            raise InvalidNumericalDataError("large_inflow must be finite and non-negative")
        if not isfinite(self.large_outflow) or self.large_outflow < 0.0:
            raise InvalidNumericalDataError("large_outflow must be finite and non-negative")
        if not isfinite(self.confidence) or not (0.0 <= self.confidence <= 1.0):
            raise InvalidNumericalDataError("confidence must be in [0.0, 1.0]")
        object.__setattr__(self, "symbol", self.symbol.strip().upper())

    def to_metadata(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "timestamp_ms": self.timestamp_ms,
            "net_flow": self.net_flow,
            "large_inflow": self.large_inflow,
            "large_outflow": self.large_outflow,
            "label_quality": self.label_quality.value,
            "source": self.source,
            "confidence": self.confidence,
            "advisory": True,
        }

    @classmethod
    def unavailable(cls, symbol: str, timestamp_ms: int) -> WhaleFlowContext:
        """Create an explicit UNAVAILABLE whale flow context.

        Use when no reliable on-chain / exchange attribution data exists.
        This is the default for production paper operation — no fabricated data.
        """
        return cls(
            symbol=symbol,
            timestamp_ms=timestamp_ms,
            net_flow=0.0,
            large_inflow=0.0,
            large_outflow=0.0,
            label_quality=WhaleFlowLabelQuality.UNKNOWN,
            source="UNAVAILABLE",
            confidence=0.0,
        )
