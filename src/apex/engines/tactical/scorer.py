"""APEX 24/7 — Tactical Confluence Scorer (Phase 12).

Deterministic ADVISORY scoring of pre-pump candidates.

The scorer combines closed-candle features and optional observation
snapshots into a single 0-100 confluence score and an advisory verdict.

SAFETY INVARIANT:
  The score is metadata ONLY. It cannot authorize, veto, size, or execute
  an order, and it cannot influence RiskGuardian / OEM / EndpointGuard
  decisions. A high confluence score NEVER guarantees approval.
"""

from __future__ import annotations

from apex.engines.tactical.analytics import compute_features
from apex.engines.tactical.model import (
    TacticalConfig,
    TacticalFeatures,
    TacticalObservations,
    TacticalResult,
    TacticalVerdict,
)
from apex.market.candle_series import CandleSeries


class TacticalScorer:
    """Deterministic advisory confluence scorer."""

    def __init__(self, config: TacticalConfig | None = None) -> None:
        self.config = config or TacticalConfig()

    def evaluate(
        self,
        symbol: str,
        timeframe: str,
        series: CandleSeries,
        observations: TacticalObservations | None = None,
        observation_now_ms: int | None = None,
        performance_map: dict[str, float] | None = None,
    ) -> TacticalResult:
        features = compute_features(
            series,
            observations,
            config=self.config,
            observation_now_ms=observation_now_ms,
            performance_map=performance_map,
        )

        score, reasons = self._score(features)
        verdict = self._verdict(features, score)

        return TacticalResult(
            symbol=symbol,
            timeframe=timeframe,
            candle_timestamp_ms=series.latest.open_time_ms,
            score=round(score, 2),
            verdict=verdict,
            features=features,
            reasons=tuple(reasons),
        )

    def _score(self, f: TacticalFeatures) -> tuple[float, list[str]]:
        cfg = self.config
        score = 0.0
        reasons: list[str] = []

        # Volatility compression: tight Bollinger width historically.
        if f.bbw_percentile <= cfg.bbw_percentile_max:
            score += cfg.score_weight_compression
            reasons.append(
                f"volatility compression (BBW percentile {f.bbw_percentile:.1f} <= {cfg.bbw_percentile_max:.1f})"
            )

        # Volume expansion beyond the detector's own baseline.
        if f.rvol >= cfg.rvol_expansion_min:
            score += cfg.score_weight_volume
            reasons.append(f"volume expansion (rvol {f.rvol:.2f} >= {cfg.rvol_expansion_min:.2f})")

        # Open interest expansion (if data available).
        if f.oi_expansion_pct is not None and f.oi_expansion_pct >= cfg.oi_expansion_min_pct:
            score += cfg.score_weight_oi
            reasons.append(
                f"open interest expansion ({f.oi_expansion_pct:+.1f}% >= {cfg.oi_expansion_min_pct:.1f}%)"
            )

        # Funding rate: low/negative funding is advisorily favorable for longs.
        if f.funding_rate is not None and f.funding_rate <= cfg.funding_max_rate:
            score += cfg.score_weight_funding
            reasons.append(
                f"funding favorable ({f.funding_rate:.6f} <= {cfg.funding_max_rate:.6f})"
            )

        # Depth imbalance: bid-heavy book (negative imbalance) adds confluence.
        if f.depth_imbalance is not None:
            if f.depth_imbalance <= -cfg.depth_imbalance_min:
                score += cfg.score_weight_depth
                reasons.append(
                    f"bid-heavy depth ({f.depth_imbalance:+.3f})"
                )
            elif f.depth_imbalance >= cfg.depth_imbalance_min:
                reasons.append(
                    f"ask-heavy depth (no confluence, {f.depth_imbalance:+.3f})"
                )

        # Directional buying-pressure proxy.
        if f.directional_bias >= 0.3:
            score += cfg.score_weight_bias
            reasons.append(f"bullish directional bias ({f.directional_bias:+.2f})")
        elif f.directional_bias <= -0.3:
            reasons.append(f"bearish directional bias ({f.directional_bias:+.2f})")

        # Liquidation imbalance (short liquidations = upward pressure proxy).
        if f.liquidation_imbalance_pct is not None and f.liquidation_imbalance_pct >= 5.0:
            score += cfg.score_weight_liquidations
            reasons.append(
                f"short-liquidation pressure ({f.liquidation_imbalance_pct:+.1f}%)"
            )

        # Volatility regime: compression adds confluence
        if f.volatility_regime == "COMPRESSION":
            score += cfg.score_weight_regime
            reasons.append(
                f"volatility regime compression (ATR ratio {f.atr_ratio:.2f}, BBW {f.bbw_percentile:.1f}%)"
            )
        elif f.volatility_regime == "EXPANSION":
            reasons.append(
                f"volatility regime expansion (ATR ratio {f.atr_ratio:.2f}, BBW {f.bbw_percentile:.1f}%)"
            )

        # Relative strength: high percentile rank adds confluence
        if f.rs_percentile is not None and f.rs_percentile >= 70.0:
            score += cfg.score_weight_rs
            reasons.append(
                f"relative strength leader (RS percentile {f.rs_percentile:.1f}% >= 70.0%)"
            )
        elif f.rs_percentile is not None and f.rs_percentile <= 30.0:
            reasons.append(
                f"relative strength laggard (RS percentile {f.rs_percentile:.1f}% <= 30.0%)"
            )

        # Swing Failure Pattern (SFP)
        if f.sfp_bullish:
            score += cfg.score_weight_sfp
            reasons.append("bullish swing failure pattern (liquidity sweep of swing lows)")
        elif f.sfp_bearish:
            reasons.append("bearish swing failure pattern (liquidity sweep of swing highs)")

        return min(100.0, score), reasons

    def _verdict(self, f: TacticalFeatures, score: float) -> TacticalVerdict:
        if score < self.config.medium_threshold:
            if not (
                f.has_oi
                or f.has_funding
                or f.has_depth
                or f.has_liquidations
                or f.has_rs
                or f.has_sfp
                or f.volatility_regime != "NORMAL"
                or f.directional_bias != 0.0
                or f.rvol > 0.0
                or f.bbw_percentile < 100.0
            ):
                return TacticalVerdict.NO_DATA
            return TacticalVerdict.LOW
        if score >= self.config.high_threshold:
            return TacticalVerdict.HIGH
        return TacticalVerdict.MEDIUM
