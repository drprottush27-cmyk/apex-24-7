from typing import List
from datetime import datetime, timezone
from decimal import Decimal
from .models import MarketDataSummary, ScanCandidate, ScanResult, MarketRegime, Timeframe

class ScannerConfig:
    def __init__(self, min_liquidity_usd: Decimal, min_volume_24h: Decimal, min_score_threshold: float):
        self.min_liquidity_usd = min_liquidity_usd
        self.min_volume_24h = min_volume_24h
        self.min_score_threshold = min_score_threshold

class ScannerEngine:
    def __init__(self, config: ScannerConfig):
        self.config = config

    def scan(self, market_data_summaries: List[MarketDataSummary]) -> ScanResult:
        candidates: List[ScanCandidate] = []
        now = datetime.now(timezone.utc)

        for data in market_data_summaries:
            # 1. Data Integrity & Freshness Barrier
            if not data.is_data_intact or not data.is_data_fresh:
                continue

            # 2. Market Eligibility (Liquidity & Volume)
            if data.liquidity_usd < self.config.min_liquidity_usd:
                continue
            if data.volume_24h < self.config.min_volume_24h:
                continue

            # 3. Market Regime Eligibility
            if data.regime == MarketRegime.UNKNOWN:
                continue

            # 4. Deterministic Scoring Logic (Base Score + Liquidity Bonus)
            base_score = 50.0
            if data.regime in (MarketRegime.TREND_BULL, MarketRegime.TREND_BEAR):
                base_score += 30.0
                
            # Liquidity bonus for ranking tie-breakers
            liquidity_bonus = float(data.liquidity_usd) / 10000000.0
            final_score = base_score + liquidity_bonus

            # 5. Candidate Generation
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

        # 6. Strict Ranking (Highest score first)
        candidates.sort(key=lambda c: c.score, reverse=True)

        return ScanResult(
            timestamp_utc=now,
            candidates=candidates
        )
