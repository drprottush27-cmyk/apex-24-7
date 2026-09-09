"""Unit tests for fail-safe Kill Switch primitive."""

import pytest

from apex.safety.exceptions import KillSwitchActiveError
from apex.safety.kill_switch import KillSwitch


class TestKillSwitch:
    """Test suite verifying KillSwitch fail-safe invariants."""

    def test_default_state_inactive_when_specified(self) -> None:
        ks = KillSwitch(initial_active=False)
        assert ks.is_active is False
        # validate_can_enter should succeed without error
        ks.validate_can_enter()

    def test_default_state_active_when_initialized_safe(self) -> None:
        ks = KillSwitch(initial_active=True, reason="Cold boot hold")
        assert ks.is_active is True
        with pytest.raises(KillSwitchActiveError, match="Cold boot hold"):
            ks.validate_can_enter()

    def test_activation_state_transition_and_audit(self) -> None:
        ks = KillSwitch(initial_active=False)
        ks.activate(reason="Excessive slippage detected", actor="guardian")

        assert ks.is_active is True
        state = ks.state
        assert state.is_active is True
        assert state.reason == "Excessive slippage detected"
        assert state.actor == "guardian"
        assert state.timestamp_ms > 0

        with pytest.raises(KillSwitchActiveError, match="Excessive slippage detected"):
            ks.validate_can_enter()

    def test_deactivation_state_transition_and_audit(self) -> None:
        ks = KillSwitch(initial_active=True, reason="Drift alert")
        ks.deactivate(reason="Operator verified testnet balance", actor="operator_alice")

        assert ks.is_active is False
        state = ks.state
        assert state.is_active is False
        assert state.reason == "Operator verified testnet balance"
        assert state.actor == "operator_alice"

        ks.validate_can_enter()

    def test_cancellation_and_flattening_never_blocked_by_kill_switch(self) -> None:
        """Mandatory rule: cancellation/flattening paths must never depend on disabling the kill switch."""
        ks = KillSwitch(initial_active=True, reason="Emergency halt")
        assert ks.is_active is True

        # New entries must be blocked
        with pytest.raises(KillSwitchActiveError):
            ks.validate_can_enter()

        # But cancellation/flattening must always be permitted
        assert ks.can_cancel_or_flatten() is True
