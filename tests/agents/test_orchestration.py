import time
from decimal import Decimal

import pytest

from apex.audit.killswitch import KillSwitch
from apex.orchestration import (
    ChildAccountStatus,
    ChildDecision,
    MarketIntent,
    MAX_SYSTEM_LEVERAGE,
    MAX_SYSTEM_RISK_PER_TRADE,
    PaperAccountOrchestrator,
    PaperChildAccount,
    ParentSetup,
    ParentSetupStatus,
)


def _child(aid="acc-1", max_pos="10000", balance="5000", risk="0.01", leverage="1",
           enabled=True, exposure="0", guardian_approved=True, drawdown="CLEAR"):
    return PaperChildAccount(
        account_id=aid,
        exchange="binance",
        parent_setup_id="",
        paper_balance=Decimal(balance),
        risk_per_trade=Decimal(risk),
        max_position_size=Decimal(max_pos),
        max_leverage=Decimal(leverage),
        enabled=enabled,
        current_exposure=Decimal(exposure),
        drawdown_state=drawdown,
        guardian_approved=guardian_approved,
    )


def _intent(price="50000", notional="5000"):
    return MarketIntent(symbol="BTCUSDT", direction="LONG",
                        entry_price=Decimal(price), notional_usd=Decimal(notional))


def _orch(**kwargs):
    return PaperAccountOrchestrator(**kwargs)


class TestSetupLifecycle:
    def test_create_setup(self):
        orch = _orch()
        setup = orch.create_parent_setup("BTCUSDT", "long")
        assert setup.status == ParentSetupStatus.ACTIVE
        assert setup.symbol == "BTCUSDT"
        assert setup.direction == "LONG"

    def test_get_missing_setup(self):
        assert _orch().get_setup("MISSING") is None

    def test_register_child(self):
        orch = _orch()
        s = orch.create_parent_setup("BTCUSDT", "long", setup_id="S1")
        c = _child()
        orch.register_child("S1", c)
        assert "acc-1" in s.accounts

    def test_invalid_direction_raises(self):
        with pytest.raises(ValueError):
            _orch().create_parent_setup("BTCUSDT", "FLAT")

    def test_duplicate_setup_id_raises(self):
        orch = _orch()
        orch.create_parent_setup("BTCUSDT", "long", setup_id="S1")
        with pytest.raises(ValueError):
            orch.create_parent_setup("BTCUSDT", "short", setup_id="S1")


class TestIndependentEvaluation:
    def test_two_children_one_rejected_one_approved(self):
        orch = _orch()
        s = orch.create_parent_setup("BTCUSDT", "long", setup_id="S1")
        orch.register_child("S1", _child(aid="small", max_pos="100"))  # notional 5000 > 100
        orch.register_child("S1", _child(aid="big", max_pos="10000"))
        decisions = orch.evaluate_setup("S1", _intent())
        assert decisions["small"].status == ChildAccountStatus.REJECTED
        assert "POSITION_SIZE_EXCEEDED" in decisions["small"].reason
        assert decisions["big"].status == ChildAccountStatus.APPROVED
        assert decisions["big"].reason == "APPROVED"

    def test_guardian_approved_flag_blocks(self):
        orch = _orch()
        s = orch.create_parent_setup("BTCUSDT", "long", setup_id="S1")
        orch.register_child("S1", _child(aid="blocked", guardian_approved=False))
        decisions = orch.evaluate_setup("S1", _intent())
        assert decisions["blocked"].status == ChildAccountStatus.REJECTED
        assert "GUARDIAN_VETO" in decisions["blocked"].reason

    def test_killswitch_blocks_all(self):
        ks = KillSwitch()
        ks.trigger("test")
        orch = _orch(killswitch=ks)
        s = orch.create_parent_setup("BTCUSDT", "long", setup_id="S1")
        orch.register_child("S1", _child())
        decisions = orch.evaluate_setup("S1", _intent())
        assert all(d.status == ChildAccountStatus.BLOCKED and "KILLSWITCH" in d.reason for d in decisions.values())

    def test_invalidate_setup_blocks_all(self):
        orch = _orch()
        s = orch.create_parent_setup("BTCUSDT", "long", setup_id="S1")
        orch.register_child("S1", _child())
        orch.invalidate_setup("S1", "opportunity closed")
        decisions = orch.evaluate_setup("S1", _intent())
        assert all(d.status == ChildAccountStatus.BLOCKED and "PARENT_INVALIDATED" in d.reason for d in decisions.values())
        assert s.status == ParentSetupStatus.INVALIDATED

    def test_missing_setup_returns_block(self):
        decisions = _orch().evaluate_setup("MISSING", _intent())
        assert decisions["__missing_setup__"].status == ChildAccountStatus.BLOCKED

    def test_no_live_execution_methods(self):
        orch = _orch()
        for attr in ("place_order", "execute", "close_position", "set_leverage", "withdraw", "buy", "sell", "cancel_order"):
            assert not hasattr(orch, attr)


class TestChildRuleOrder:
    def test_disabled_account(self):
        orch = _orch()
        s = orch.create_parent_setup("BTCUSDT", "long", setup_id="S1")
        orch.register_child("S1", _child(enabled=False))
        d = orch.evaluate_setup("S1", _intent())["acc-1"]
        assert d.status == ChildAccountStatus.BLOCKED and "ACCOUNT_DISABLED" in d.reason

    def test_no_balance(self):
        orch = _orch()
        s = orch.create_parent_setup("BTCUSDT", "long", setup_id="S1")
        orch.register_child("S1", _child(balance="0"))
        d = orch.evaluate_setup("S1", _intent())["acc-1"]
        assert d.status == ChildAccountStatus.BLOCKED and "NO_BALANCE" in d.reason

    def test_drawdown_breach(self):
        orch = _orch()
        s = orch.create_parent_setup("BTCUSDT", "long", setup_id="S1")
        orch.register_child("S1", _child(drawdown="BREACH"))
        d = orch.evaluate_setup("S1", _intent())["acc-1"]
        assert d.status == ChildAccountStatus.BLOCKED and "DRAWDOWN_BREACH" in d.reason

    def test_invalid_risk_per_trade(self):
        orch = _orch()
        s = orch.create_parent_setup("BTCUSDT", "long", setup_id="S1")
        orch.register_child("S1", _child(risk="0.1"))
        d = orch.evaluate_setup("S1", _intent())["acc-1"]
        assert d.status == ChildAccountStatus.REJECTED and "INVALID_RISK_PARAMS" in d.reason

    def test_leverage_exceeds_ceiling(self):
        orch = _orch()
        s = orch.create_parent_setup("BTCUSDT", "long", setup_id="S1")
        orch.register_child("S1", _child(leverage="6"))
        d = orch.evaluate_setup("S1", _intent())["acc-1"]
        assert d.status == ChildAccountStatus.REJECTED and "INVALID_RISK_PARAMS" in d.reason

    def test_exposure_exceeds_max(self):
        orch = _orch()
        s = orch.create_parent_setup("BTCUSDT", "long", setup_id="S1")
        orch.register_child("S1", _child(exposure="9000"))  # 9000 + 5000 > 10000
        d = orch.evaluate_setup("S1", _intent())["acc-1"]
        assert d.status == ChildAccountStatus.REJECTED and "EXPOSURE_LIMIT" in d.reason

    def test_none_notional_defaults_to_max_position(self):
        orch = _orch()
        s = orch.create_parent_setup("BTCUSDT", "long", setup_id="S1")
        orch.register_child("S1", _child(max_pos="100"))  # explicit notional default = 100 == max_pos
        intent = MarketIntent(symbol="BTCUSDT", direction="LONG", entry_price=Decimal("50000"), notional_usd=None)
        d = orch.evaluate_setup("S1", intent)["acc-1"]
        assert d.status == ChildAccountStatus.APPROVED

    def test_guardian_review_veto(self):
        def veto(account, intent):
            return False, "sl too wide"
        orch = _orch(guardian_review=veto)
        s = orch.create_parent_setup("BTCUSDT", "long", setup_id="S1")
        orch.register_child("S1", _child())
        d = orch.evaluate_setup("S1", _intent())["acc-1"]
        assert d.status == ChildAccountStatus.REJECTED and "GUARDIAN_VETO" in d.reason

    def test_price_missing_yields_data_unavailable(self):
        orch = _orch()
        s = orch.create_parent_setup("BTCUSDT", "long", setup_id="S1")
        orch.register_child("S1", _child())
        intent = MarketIntent(symbol="BTCUSDT", direction="LONG", entry_price=None, notional_usd=Decimal("5000"))
        d = orch.evaluate_setup("S1", intent)["acc-1"]
        assert d.status == ChildAccountStatus.DATA_UNAVAILABLE and "PRICE_UNAVAILABLE" in d.reason

    def test_direction_sanity(self):
        orch = _orch()
        s = orch.create_parent_setup("BTCUSDT", "long", setup_id="S1")
        orch.register_child("S1", _child())
        intent = MarketIntent(symbol="BTCUSDT", direction="FLAT", entry_price=Decimal("50000"), notional_usd=Decimal("5000"))
        d = orch.evaluate_setup("S1", intent)["acc-1"]
        assert d.status == ChildAccountStatus.REJECTED and "INVALID_DIRECTION" in d.reason


class TestOverview:
    def test_overview_shape(self):
        orch = _orch()
        s = orch.create_parent_setup("BTCUSDT", "long", setup_id="S1")
        orch.register_child("S1", _child(aid="a"))
        orch.register_child("S1", _child(aid="b", max_pos="500"))
        orch.evaluate_setup("S1", _intent())
        ov = orch.overview()
        assert ov["setup_count"] == 1
        assert ov["child_count"] == 2
        assert ov["setups"][0]["decisions"]["a"]["status"] == "APPROVED"
        assert ov["setups"][0]["decisions"]["b"]["status"] == "REJECTED"