from typing import List
from datetime import datetime, timezone
from decimal import Decimal
from .models import MarketDataSummary, ScanCandidate, ScanResult, MarketRegime, Timeframe

class ScannerConfig:
    def __init__(
        self,
        min_liquidity_usd: Decimal,
        min_volume_24h: Decimal,
        min_score_threshold: float,
        max_staleness_seconds: int = 300,
    ):
        self.min_liquidity_usd = min_liquidity_usd
        self.min_volume_24h = min_volume_24h
        self.min_score_threshold = min_score_threshold
        self.max_staleness_seconds = max_staleness_seconds

class ScannerEngine:
    def __init__(self, config: ScannerConfig):
        self.config = config

    def scan(self, market_data_summaries: List[MarketDataSummary]) -> ScanResult:
        candidates: List[ScanCandidate] = []
        now = datetime.now(timezone.utc)

        for data in market_data_summaries:
            # 1. Data Integrity Barrier
            if not data.is_data_intact:
                continue
            if data.current_price.is_nan() or data.current_price.is_infinite() or data.current_price <= Decimal('0'):
                continue
            if data.liquidity_usd.is_nan() or data.liquidity_usd.is_infinite() or data.liquidity_usd < Decimal('0'):
                continue
            if data.volume_24h.is_nan() or data.volume_24h.is_infinite() or data.volume_24h < Decimal('0'):
                continue

            # 2. Data Freshness Barrier
            if not data.is_data_fresh:
                continue
            ts = data.timestamp if data.timestamp.tzinfo is not None else data.timestamp.replace(tzinfo=timezone.utc)
            staleness = (now - ts).total_seconds()
            if staleness > self.config.max_staleness_seconds or staleness < -60:
                continue

            # 3. Market Eligibility (Liquidity & Volume)
            if data.liquidity_usd < self.config.min_liquidity_usd:
                continue
            if data.volume_24h < self.config.min_volume_24h:
                continue

            # 4. Market Regime Eligibility
            if data.regime == MarketRegime.UNKNOWN:
                continue

            # 5. Deterministic Scoring Logic (Base Score + Regime Weight + Liquidity Bonus)
            base_score = 50.0
            if data.regime in (MarketRegime.TREND_BULL, MarketRegime.TREND_BEAR):
                base_score += 30.0
                
            # Liquidity bonus for ranking tie-breakers
            liquidity_bonus = float(data.liquidity_usd) / 10000000.0
            final_score = base_score + liquidity_bonus

            # 6. Candidate Generation
            if final_score >= self.config.min_score_threshold:
                candidate = ScanCandidate(
                    symbol=data.symbol,
                    score=final_score,
                    regime=data.regime,
                    aligned_timeframes=[Timeframe.H1, Timeframe.H4], # MTF Alignment Blueprint
                    liquidity_usd=data.liquidity_usd,
                    timestamp_utc=now
                )
                candidates.append(candidate)

        # 7. Strict Deterministic Ranking (Highest score first, tie-breaker: symbol ascending)
        candidates.sort(key=lambda c: (-c.score, c.symbol))

        return ScanResult(
            timestamp_utc=now,
            candidates=candidates
        )

