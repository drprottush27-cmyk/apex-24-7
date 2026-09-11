import pytest
from decimal import Decimal
from src.factory.models import BotManifest, BotLifecycleState
from src.factory.engine import BotFactory

@pytest.fixture
def factory():
    return BotFactory()

def test_valid_paper_bot(factory):
    manifest = BotManifest("bot-01", "TrendStrat", "1.0.0", Decimal('1000'), "bt-log-992", BotLifecycleState.PAPER)
    errors = factory.validate_manifest(manifest)
    assert len(errors) == 0

def test_missing_backtest_evidence(factory):
    manifest = BotManifest("bot-02", "TrendStrat", "1.0.0", Decimal('1000'), "", BotLifecycleState.PAPER)
    errors = factory.validate_manifest(manifest)
    assert any("MISSING_EVIDENCE" in e for e in errors)

def test_auto_live_promotion_rejected(factory):
    manifest = BotManifest("bot-03", "TrendStrat", "1.0.0", Decimal('1000'), "bt-log-992", BotLifecycleState.LIVE)
    errors = factory.validate_manifest(manifest)
    assert any("LIVE_PROMOTION_REJECTED" in e for e in errors)
