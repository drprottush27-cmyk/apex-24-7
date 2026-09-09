"""APEX 24/7 — Tactical Context Aggregator (Phase 13).

Composes Phase 12 tactical confluence scoring with Phase 13 higher-timeframe
trend alignment into ONE advisory metadata payload attached to a Signal's
evidence_metadata.

SAFETY INVARIANT:
  The output is advisory metadata for journaling and human review. It does
  not authorize, veto, size, or execute any order, and it cannot influence
  RiskGuardian / OEM / EndpointGuard decisions.
"""

from __future__ import annotations

from apex.domain.types import Timeframe
from apex.engines.tactical.model import (
    TacticalConfig,
    TacticalObservations,
    TacticalResult,
)
from apex.engines.tactical.mtf import (
    HtfTrend,
    HtfTrendConfluence,
    htf_trend_confluence,
    resample_to_higher_timeframe,
)
from apex.engines.tactical.scorer import TacticalScorer
from apex.market.candle_series import CandleSeries


class TacticalContext:
    """Builds combined advisory tactical + multi-timeframe metadata."""

    def __init__(
        self,
        config: TacticalConfig | None = None,
        htf_timeframe: Timeframe = Timeframe.H1,
    ) -> None:
        self._scorer = TacticalScorer(config)
        self._htf_timeframe = htf_timeframe

    @property
    def scorer(self) -> TacticalScorer:
        return self._scorer

    def build(
        self,
        symbol: str,
        timeframe: str,
        series: CandleSeries,
        observations: TacticalObservations | None = None,
        observation_now_ms: int | None = None,
    ) -> dict[str, object]:
        """Return an advisory metadata dict combining tactical + MTF data.

        Never raises on resampling failures: if the source series cannot be
        resampled into the higher timeframe, the MTF entry degrades to
        NO_DATA rather than failing the signal.
        """
        tactical: TacticalResult = self._scorer.evaluate(
            symbol,
            timeframe,
            series,
            observations=observations,
            observation_now_ms=observation_now_ms,
        )

        mtf: HtfTrendConfluence = self._htf(series)

        metadata = tactical.to_metadata()
        metadata["multi_timeframe"] = mtf.to_metadata()
        return metadata

    def _htf(self, series: CandleSeries) -> HtfTrendConfluence:
        htf_series = resample_to_higher_timeframe(series, self._htf_timeframe)
        if htf_series is None:
            return HtfTrendConfluence(
                htf_timeframe=self._htf_timeframe.value,
                bars=0,
                trend=HtfTrend.NO_DATA,
                reason="unable to resample closed series to higher timeframe",
            )
        return htf_trend_confluence(htf_series)
