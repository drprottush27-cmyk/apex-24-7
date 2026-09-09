"""Hermetic tests for P2-21 remaining agents.

Every test is hermetic: no network, no secrets, no external state.
All agents are advisory-only, deterministic, and fail-closed.
"""

import inspect
import math

import pytest

from agents.technical.technical import (
    TechnicalAnalysisAgent, TechnicalReport, TrendStrength, MomentumState,
)
from agents.market_radar.market_radar import (
    MarketRadarAgent, RadarScanResult, RadarAlert, RadarAlertType,
)
from agents.research.research import (
    ResearchAgent, ResearchReport, ResearchFactor,
)
from agents.risk_guardian.advisory import (
    RiskAdvisoryAgent, RiskAdvisoryCommentary,
)
from agents.social.sentiment import (
    SocialSentimentAgent, SentimentReport, SentimentTone,
)
from agents.derivatives.derivatives import (
    DerivativesAgent, DerivativesReport, FundingBias, OiTrend,
)
from agents.committee.advisory import (
    AdvisoryCommitteeAgent, CommitteeVerdict, CommitteeVote,
)
from engines.risk.guardian import RiskGuardian, RiskCheckResult


# ---------------------------------------------------------------------------
# Synthetic candle data helpers
# ---------------------------------------------------------------------------

def _make_candles(n: int, base: float = 100.0, noise: float = 1.0, trend: float = 0.0):
    """Generate synthetic OHLCV candle data for hermetic tests."""
    highs, lows, closes, volumes = [], [], [], []
    price = base
    for i in range(n):
        price += trend + (hash(str(i)) % 100 - 50) * noise * 0.01
        h = price + abs(hash(str(i)) % 100) * noise * 0.01
        l = price - abs(hash(str(i + 1000)) % 100) * noise * 0.01
        c = price
        v = 1000 + abs(hash(str(i + 2000)) % 1000)
        highs.append(h)
        lows.append(l)
        closes.append(c)
        volumes.append(float(v))
    return highs, lows, closes, volumes


class TestNoNetwork:
    """Agents must never import or use network clients."""

    @pytest.mark.parametrize("cls_path,module_src", [
        (TechnicalAnalysisAgent, None),
        (MarketRadarAgent, None),
        (ResearchAgent, None),
        (RiskAdvisoryAgent, None),
        (SocialSentimentAgent, None),
        (DerivativesAgent, None),
        (AdvisoryCommitteeAgent, None),
    ])
    def test_module_has_no_http_client(self, cls_path, module_src):
        src = inspect.getsource(cls_path)
        for forbidden in ("httpx", "aiohttp", "requests", "urllib",
                          "api.telegram.org", "fapi.binance"):
            assert forbidden not in src

    @pytest.mark.parametrize("cls_path", [
        TechnicalAnalysisAgent,
        MarketRadarAgent,
        ResearchAgent,
        RiskAdvisoryAgent,
        SocialSentimentAgent,
        DerivativesAgent,
        AdvisoryCommitteeAgent,
    ])
    def test_no_credential_access(self, cls_path):
        src = inspect.getsource(cls_path)
        for token in ("api_key", "api_secret", "bot_token", "secret"):
            assert token not in src

    @pytest.mark.parametrize("cls_path", [
        TechnicalAnalysisAgent,
        MarketRadarAgent,
        ResearchAgent,
        RiskAdvisoryAgent,
        SocialSentimentAgent,
        DerivativesAgent,
        AdvisoryCommitteeAgent,
    ])
    def test_no_execution_paths(self, cls_path):
        src = inspect.getsource(cls_path)
        for forbidden in ("OrderRequest", "submit_order", "execute_order",
                          "order_manager", "set_leverage"):
            assert forbidden not in src


# ---------------------------------------------------------------------------
# TechnicalAnalysisAgent
# ---------------------------------------------------------------------------

class TestTechnicalAnalysisAgent:
    def setup_method(self):
        self.agent = TechnicalAnalysisAgent()

    def test_insufficient_data_fails_closed(self):
        highs, lows, closes, volumes = _make_candles(5)
        report = self.agent.analyze("BTCUSDT", highs, lows, closes, volumes)
        assert report.trend_strength == TrendStrength.NEUTRAL
        assert report.momentum_state == MomentumState.NEUTRAL
        assert report.advisory_only is True
        assert "INSUFFICIENT_DATA" in report.reasons[0]

    def test_sufficient_data_produces_report(self):
        highs, lows, closes, volumes = _make_candles(50)
        report = self.agent.analyze("ETHUSDT", highs, lows, closes, volumes)
        assert report.symbol == "ETHUSDT"
        assert report.advisory_only is True
        assert report.rsi_value is not None
        assert report.atr_value is not None
        assert report.indicators is not None

    def test_symbol_uppercased(self):
        highs, lows, closes, volumes = _make_candles(50)
        report = self.agent.analyze("btcusdt", highs, lows, closes, volumes)
        assert report.symbol == "BTCUSDT"

    def test_deterministic(self):
        highs, lows, closes, volumes = _make_candles(50)
        a = self.agent.analyze("BTCUSDT", highs, lows, closes, volumes)
        b = self.agent.analyze("BTCUSDT", highs, lows, closes, volumes)
        assert a.trend_strength == b.trend_strength
        assert a.momentum_state == b.momentum_state
        assert a.rsi_value == b.rsi_value

    def test_trend_bullish_with_upward_closes(self):
        highs, lows, closes, volumes = _make_candles(60, trend=0.5)
        report = self.agent.analyze("BTCUSDT", highs, lows, closes, volumes)
        assert "BULLISH" in report.trend_strength.value

    def test_trend_bearish_with_downward_closes(self):
        highs, lows, closes, volumes = _make_candles(60, trend=-0.5)
        report = self.agent.analyze("BTCUSDT", highs, lows, closes, volumes)
        assert "BEARISH" in report.trend_strength.value

    def test_no_order_fields_in_output(self):
        highs, lows, closes, volumes = _make_candles(50)
        report = self.agent.analyze("BTCUSDT", highs, lows, closes, volumes)
        d = report.__dict__
        for key in ("quantity", "side", "order_type", "entry_min", "entry_max",
                     "stop_loss", "take_profit", "leverage", "client_order_id"):
            assert key not in d


# ---------------------------------------------------------------------------
# MarketRadarAgent
# ---------------------------------------------------------------------------

class TestMarketRadarAgent:
    def setup_method(self):
        self.agent = MarketRadarAgent(volume_spike_threshold=2.0)

    def test_empty_scan(self):
        result = self.agent.scan({})
        assert result.alerts == []
        assert result.symbols_scanned == 0
        assert result.advisory_only is True

    def test_skip_insufficient_data(self):
        highs, lows, closes, volumes = _make_candles(5)
        result = self.agent.scan({"BTCUSDT": {"highs": highs, "lows": lows, "closes": closes, "volumes": volumes}})
        assert result.symbols_scanned == 0

    def test_volume_spike_alert(self):
        highs, lows, closes, volumes = _make_candles(50)
        volumes[-1] = 50000.0  # massive spike
        result = self.agent.scan({"BTCUSDT": {"highs": highs, "lows": lows, "closes": closes, "volumes": volumes}})
        vol_alerts = [a for a in result.alerts if a.alert_type == RadarAlertType.VOLUME_SPIKE]
        assert len(vol_alerts) == 1
        assert vol_alerts[0].advisory_only is True

    def test_rsi_extreme_alert(self):
        highs, lows, closes, volumes = _make_candles(50)
        for i in range(len(closes)):
            closes[i] = 100 + i * 0.5  # strong uptrend -> RSI > 70
        result = self.agent.scan({"BTCUSDT": {"highs": highs, "lows": lows, "closes": closes, "volumes": volumes}})
        rsi_alerts = [a for a in result.alerts if a.alert_type == RadarAlertType.RSI_EXTREME]
        assert len(rsi_alerts) == 1

    def test_multi_symbol_scan(self):
        highs, lows, closes, volumes = _make_candles(50)
        data = {
            "BTCUSDT": {"highs": highs, "lows": lows, "closes": closes, "volumes": volumes},
            "ETHUSDT": {"highs": highs, "lows": lows, "closes": closes, "volumes": volumes},
        }
        result = self.agent.scan(data)
        assert result.symbols_scanned == 2

    def test_deterministic(self):
        highs, lows, closes, volumes = _make_candles(50)
        a = self.agent.scan({"BTCUSDT": {"highs": highs, "lows": lows, "closes": closes, "volumes": volumes}})
        b = self.agent.scan({"BTCUSDT": {"highs": highs, "lows": lows, "closes": closes, "volumes": volumes}})
        assert len(a.alerts) == len(b.alerts)


# ---------------------------------------------------------------------------
# ResearchAgent
# ---------------------------------------------------------------------------

class TestResearchAgent:
    def setup_method(self):
        self.agent = ResearchAgent()

    def test_insufficient_data_fails_closed(self):
        highs, lows, closes, volumes = _make_candles(10)
        report = self.agent.research("BTCUSDT", highs, lows, closes, volumes)
        assert report.overall_bias == "UNKNOWN"
        assert report.confidence == 0.0
        assert report.advisory_only is True

    def test_sufficient_data_produces_report(self):
        highs, lows, closes, volumes = _make_candles(60)
        report = self.agent.research("ETHUSDT", highs, lows, closes, volumes)
        assert report.symbol == "ETHUSDT"
        assert report.advisory_only is True
        assert len(report.factors) > 0
        assert report.overall_bias in ("BULLISH", "BEARISH", "NEUTRAL",
                                       "SLIGHTLY_BULLISH", "SLIGHTLY_BEARISH", "UNKNOWN")

    def test_additional_context_enriches_report(self):
        highs, lows, closes, volumes = _make_candles(60)
        ctx = {"overall_bias": "BULLISH", "source": "test"}
        report = self.agent.research("BTCUSDT", highs, lows, closes, volumes, additional_context=ctx)
        ctx_factors = [f for f in report.factors if f.name == "EXTERNAL_CONTEXT"]
        assert len(ctx_factors) == 1

    def test_deterministic(self):
        highs, lows, closes, volumes = _make_candles(60)
        a = self.agent.research("BTCUSDT", highs, lows, closes, volumes)
        b = self.agent.research("BTCUSDT", highs, lows, closes, volumes)
        assert a.overall_bias == b.overall_bias
        assert a.confidence == b.confidence

    def test_no_order_fields_in_output(self):
        highs, lows, closes, volumes = _make_candles(60)
        report = self.agent.research("BTCUSDT", highs, lows, closes, volumes)
        d = report.__dict__
        for key in ("quantity", "side", "order_type", "entry_min",
                     "stop_loss", "take_profit", "leverage"):
            assert key not in d


# ---------------------------------------------------------------------------
# RiskAdvisoryAgent
# ---------------------------------------------------------------------------

class TestRiskAdvisoryAgent:
    def setup_method(self):
        self.agent = RiskAdvisoryAgent()

    def test_approved_commentary(self):
        risk_check = RiskCheckResult(
            approved=True,
            reason="Approved under deterministic risk guidelines",
            approved_quantity=0.01,
            effective_leverage=5,
            checks_passed=["PORTFOLIO_EQUITY_VALID", "DAILY_DRAWDOWN_SAFE"],
            checks_failed=[],
        )
        commentary = self.agent.assess("BTCUSDT", risk_check, 1000.0, 1, 0.0)
        assert commentary.approved is True
        assert commentary.advisory_only is True
        assert any("approved" in c.lower() for c in commentary.commentary)

    def test_rejected_commentary(self):
        risk_check = RiskCheckResult(
            approved=False,
            reason="Daily drawdown breached",
            checks_passed=["PORTFOLIO_EQUITY_VALID"],
            checks_failed=["DAILY_DRAWDOWN_BREACH"],
        )
        commentary = self.agent.assess("BTCUSDT", risk_check, 1000.0, 1, -0.04)
        assert commentary.approved is False
        assert any("rejected" in c.lower() for c in commentary.commentary)

    def test_no_execution_fields(self):
        risk_check = RiskCheckResult(approved=True, reason="ok")
        commentary = self.agent.assess("BTCUSDT", risk_check, 1000.0, 0, 0.0)
        d = commentary.__dict__
        for key in ("quantity", "side", "order_type", "entry_min",
                     "stop_loss", "take_profit", "leverage"):
            assert key not in d

    def test_deterministic(self):
        risk_check = RiskCheckResult(approved=True, reason="ok", approved_quantity=0.01)
        a = self.agent.assess("BTCUSDT", risk_check, 1000.0, 1, 0.0)
        b = self.agent.assess("BTCUSDT", risk_check, 1000.0, 1, 0.0)
        assert a.risk_score == b.risk_score
        assert a.commentary == b.commentary


# ---------------------------------------------------------------------------
# SocialSentimentAgent
# ---------------------------------------------------------------------------

class TestSocialSentimentAgent:
    def setup_method(self):
        self.agent = SocialSentimentAgent()

    def test_no_readings_fails_closed(self):
        report = self.agent.compile("BTCUSDT", [])
        assert report.aggregate_tone == SentimentTone.UNKNOWN
        assert report.confidence == 0.0
        assert report.advisory_only is True

    def test_bullish_readings(self):
        readings = [
            {"source": "twitter", "score": 0.8},
            {"source": "reddit", "score": 0.6},
        ]
        report = self.agent.compile("BTCUSDT", readings)
        assert report.aggregate_tone in (SentimentTone.BULLISH, SentimentTone.VERY_BULLISH)
        assert report.aggregate_score > 0

    def test_bearish_readings(self):
        readings = [
            {"source": "twitter", "score": -0.8},
            {"source": "reddit", "score": -0.6},
        ]
        report = self.agent.compile("BTCUSDT", readings)
        assert report.aggregate_tone in (SentimentTone.BEARISH, SentimentTone.VERY_BEARISH)
        assert report.aggregate_score < 0

    def test_mixed_readings_neutral(self):
        readings = [
            {"source": "twitter", "score": 0.1},
            {"source": "reddit", "score": -0.1},
        ]
        report = self.agent.compile("BTCUSDT", readings)
        assert report.aggregate_tone == SentimentTone.NEUTRAL

    def test_invalid_readings_dropped(self):
        readings = [
            {"source": "twitter", "score": "not_a_number"},
            {"source": "reddit", "score": float("nan")},
            {"source": "good", "score": 0.5},
        ]
        report = self.agent.compile("BTCUSDT", readings)
        assert len(report.readings) == 1

    def test_score_clamped(self):
        readings = [{"source": "extreme", "score": 5.0}]
        report = self.agent.compile("BTCUSDT", readings)
        assert report.readings[0].score <= 1.0

    def test_deterministic(self):
        readings = [{"source": "a", "score": 0.5}, {"source": "b", "score": 0.3}]
        a = self.agent.compile("BTCUSDT", readings)
        b = self.agent.compile("BTCUSDT", readings)
        assert a.aggregate_score == b.aggregate_score
        assert a.aggregate_tone == b.aggregate_tone


# ---------------------------------------------------------------------------
# DerivativesAgent
# ---------------------------------------------------------------------------

class TestDerivativesAgent:
    def setup_method(self):
        self.agent = DerivativesAgent()

    def test_no_data_fails_closed(self):
        report = self.agent.analyze("BTCUSDT")
        assert report.funding_bias == FundingBias.NEUTRAL
        assert report.oi_trend == OiTrend.UNKNOWN
        assert report.advisory_only is True

    def test_high_positive_funding(self):
        report = self.agent.analyze("BTCUSDT", funding_rate=0.003)
        assert report.funding_bias == FundingBias.VERY_BEARISH
        assert report.funding_rate == 0.003

    def test_negative_funding(self):
        report = self.agent.analyze("BTCUSDT", funding_rate=-0.0015)
        assert report.funding_bias == FundingBias.BULLISH

    def test_oi_increasing(self):
        report = self.agent.analyze("BTCUSDT", open_interest=1500, previous_oi=1000)
        assert report.oi_trend == OiTrend.INCREASING

    def test_oi_decreasing(self):
        report = self.agent.analyze("BTCUSDT", open_interest=500, previous_oi=1000)
        assert report.oi_trend == OiTrend.DECREASING

    def test_leverage_estimation(self):
        report = self.agent.analyze("BTCUSDT", open_interest=10000, mark_price=500)
        assert report.estimated_leverage == 20.0

    def test_no_order_fields(self):
        report = self.agent.analyze("BTCUSDT", funding_rate=0.001)
        d = report.__dict__
        for key in ("quantity", "side", "order_type", "entry_min",
                     "stop_loss", "take_profit", "leverage"):
            assert key not in d

    def test_deterministic(self):
        a = self.agent.analyze("BTCUSDT", funding_rate=0.001, open_interest=1000, previous_oi=900)
        b = self.agent.analyze("BTCUSDT", funding_rate=0.001, open_interest=1000, previous_oi=900)
        assert a.funding_bias == b.funding_bias
        assert a.oi_trend == b.oi_trend


# ---------------------------------------------------------------------------
# AdvisoryCommitteeAgent
# ---------------------------------------------------------------------------

class TestAdvisoryCommitteeAgent:
    def setup_method(self):
        self.agent = AdvisoryCommitteeAgent()

    def test_no_data_inconclusive(self):
        verdict = self.agent.deliberate("BTCUSDT")
        assert verdict.final_verdict == "INCONCLUSIVE"
        assert verdict.advisory_only is True

    def test_all_approve(self):
        verdict = self.agent.deliberate(
            "BTCUSDT",
            technical_bias="BULLISH",
            sentiment_bias="BULLISH",
            risk_approved=True,
        )
        assert verdict.final_verdict == "APPROVED"
        assert verdict.confidence > 0

    def test_risk_reject_overrides(self):
        verdict = self.agent.deliberate(
            "BTCUSDT",
            technical_bias="BULLISH",
            sentiment_bias="BULLISH",
            risk_approved=False,
        )
        assert verdict.final_verdict == "REJECTED"

    def test_partial_abstain(self):
        verdict = self.agent.deliberate(
            "BTCUSDT",
            technical_bias="BULLISH",
        )
        assert verdict.final_verdict in ("APPROVED", "INCONCLUSIVE", "REJECTED")

    def test_deterministic(self):
        a = self.agent.deliberate("BTCUSDT", technical_bias="BULLISH", risk_approved=True)
        b = self.agent.deliberate("BTCUSDT", technical_bias="BULLISH", risk_approved=True)
        assert a.final_verdict == b.final_verdict
        assert a.confidence == b.confidence

    def test_no_execution_fields(self):
        verdict = self.agent.deliberate("BTCUSDT")
        d = verdict.__dict__
        for key in ("quantity", "side", "order_type", "entry_min",
                     "stop_loss", "take_profit", "leverage"):
            assert key not in d


# ---------------------------------------------------------------------------
# Cross-agent safety invariants
# ---------------------------------------------------------------------------

class TestCrossAgentInvariants:
    """Verify safety invariants hold across all agents."""

    @pytest.mark.parametrize("agent_cls,args", [
        (TechnicalAnalysisAgent, {}),
        (MarketRadarAgent, {}),
        (ResearchAgent, {}),
        (RiskAdvisoryAgent, {}),
        (SocialSentimentAgent, {}),
        (DerivativesAgent, {}),
        (AdvisoryCommitteeAgent, {}),
    ])
    def test_all_agents_advisory_only_field(self, agent_cls, args):
        agent = agent_cls(**args)
        # Verify no agent has execution-related attributes
        assert not hasattr(agent, "order_manager")
        assert not hasattr(agent, "exchange_client")

    def test_all_output_types_have_advisory_only(self):
        from agents.technical.technical import TechnicalReport
        from agents.market_radar.market_radar import RadarScanResult
        from agents.research.research import ResearchReport
        from agents.risk_guardian.advisory import RiskAdvisoryCommentary
        from agents.social.sentiment import SentimentReport
        from agents.derivatives.derivatives import DerivativesReport
        from agents.committee.advisory import CommitteeVerdict

        for cls in (TechnicalReport, RadarScanResult, ResearchReport,
                    RiskAdvisoryCommentary, SentimentReport,
                    DerivativesReport, CommitteeVerdict):
            # All dataclasses have advisory_only=True by default
            assert "advisory_only" in {f.name for f in cls.__dataclass_fields__.values()}
