"""Unit tests for 24/7 continuous scanning, real-time position management, and alert-then-autoclose."""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from typing import Any

import pytest

from apex.api.server import ApexApiServer
from apex.config.settings import ApexConfig
from apex.domain.positions import Position
from apex.domain.types import (
    ExitReason,
    OrderIntentType,
    OrderSide,
    PositionSide,
    PositionStatus,
)
from apex.execution.adapter import MockExecutionAdapter
from apex.execution.oem import OrderExecutionManager
from apex.runtime.autoclose import (
    AlertStatus,
    AlertThenAutoCloseManager,
    AutoCloseConfig,
    RiskAlert,
    RiskType,
)
from apex.runtime.danger import DangerAction
from apex.runtime.danger_manager import PositionDangerManager
from apex.runtime.execution_journal import ExecutionJournal
from apex.runtime.position_tracker import PositionTracker
from apex.runtime.real_time_manager import (
    RealTimePositionManager,
)
from apex.safety.kill_switch import KillSwitch

# ─── Shared position helpers & fixtures ────────────────────────────────────────

@pytest.fixture
def exec_journal() -> ExecutionJournal:
    return ExecutionJournal()


@pytest.fixture
def tracker(safe_config: ApexConfig) -> PositionTracker:
    return PositionTracker(safe_config)


@pytest.fixture
def danger_manager(
    safe_config: ApexConfig,
    oem: OrderExecutionManager,
    tracker: PositionTracker,
    exec_journal: ExecutionJournal,
) -> PositionDangerManager:
    return PositionDangerManager(
        config=safe_config,
        oem=oem,
        journal=exec_journal,
        tracker=tracker,
    )


def _open_pos(
    tracker: PositionTracker,
    *,
    symbol: str = "BTCUSDT",
    side: PositionSide = PositionSide.LONG,
    entry_price: float = 50000.0,
    quantity: float = 0.2,
    stop_loss: float = 49000.0,
    take_profit: float = 53000.0,
    risk_per_unit: float = 1000.0,
) -> Position:
    candidate = tracker.create_candidate(
        symbol=symbol,
        side=side,
        entry_price=entry_price,
        quantity=quantity,
        stop_loss=stop_loss,
        take_profit=take_profit,
        risk_per_unit=risk_per_unit,
    )
    validating = tracker.transition_to(candidate, PositionStatus.VALIDATING)
    entering = tracker.transition_to(validating, PositionStatus.ENTERING)
    return tracker.transition_to(entering, PositionStatus.OPEN)


# ─── Part 1: AutoClose Models & Config ────────────────────────────────────────

def test_autoclose_config_defaults() -> None:
    cfg = AutoCloseConfig()
    assert cfg.enabled is True
    assert cfg.grace_period_seconds == 60
    assert cfg.trailing_stop_enabled is True
    assert cfg.trailing_activation_r == 1.0
    assert cfg.trailing_step_r == 0.5
    assert cfg.adverse_liquidation_threshold_usd == 50_000.0
    assert cfg.volatility_spike_pct == 0.02

    custom = AutoCloseConfig(
        enabled=False,
        grace_period_seconds=30,
        trailing_stop_enabled=False,
        adverse_liquidation_threshold_usd=25_000.0,
    )
    assert custom.enabled is False
    assert custom.grace_period_seconds == 30
    assert custom.trailing_stop_enabled is False
    assert custom.adverse_liquidation_threshold_usd == 25_000.0


def test_risk_alert_model_expiry_and_dict() -> None:
    alert = RiskAlert(
        alert_id="alert-1",
        symbol="ETHUSDT",
        position_id="pos-123",
        risk_type=RiskType.STOP_LOSS_BREACH,
        trigger_reason="Stop loss breached",
        mark_price=2900.0,
        stop_loss=2950.0,
        unrealized_pnl=-50.0,
        triggered_at_ms=10_000,
        grace_period_seconds=60,
        expires_at_ms=70_000,
    )

    assert alert.remaining_seconds(10_000) == 60
    assert alert.remaining_seconds(40_000) == 30
    assert alert.remaining_seconds(70_000) == 0
    assert alert.remaining_seconds(80_000) == 0

    assert alert.is_active is True

    d = alert.to_dict(now_ms=40_000)
    assert d["alert_id"] == "alert-1"
    assert d["symbol"] == "ETHUSDT"
    assert d["risk_type"] == "STOP_LOSS_BREACH"
    assert d["remaining_seconds"] == 30
    assert d["status"] == "ACTIVE_GRACE_PERIOD"


# ─── Part 2: AlertThenAutoCloseManager Coordinator ────────────────────────────

def test_alert_manager_trigger_and_idempotency() -> None:
    ac = AlertThenAutoCloseManager(AutoCloseConfig(grace_period_seconds=45))

    alert1 = ac.trigger_risk_alert(
        symbol="BTCUSDT",
        position_id="pos-1",
        risk_type=RiskType.STOP_LOSS_BREACH,
        trigger_reason="Stop loss breached",
        mark_price=48900.0,
        stop_loss=49000.0,
        unrealized_pnl=-220.0,
        now_ms=10_000,
    )
    assert alert1 is not None
    assert alert1.symbol == "BTCUSDT"
    assert alert1.expires_at_ms == 10_000 + 45_000
    assert alert1.status == AlertStatus.ACTIVE_GRACE_PERIOD

    # Idempotent re-trigger while active
    alert2 = ac.trigger_risk_alert(
        symbol="BTCUSDT",
        position_id="pos-1",
        risk_type=RiskType.VOLATILITY_SPIKE,
        trigger_reason="Second spike",
        mark_price=48800.0,
        stop_loss=49000.0,
        unrealized_pnl=-240.0,
        now_ms=15_000,
    )
    assert alert2 is not None
    assert alert2.alert_id == alert1.alert_id
    assert alert2.expires_at_ms == alert1.expires_at_ms  # Countdown unchanged

    audit = ac.get_audit_history()
    assert len(audit) == 1
    assert audit[0]["event"] == "ALERT_TRIGGERED"


def test_alert_manager_override_hold() -> None:
    ac = AlertThenAutoCloseManager()
    alert = ac.trigger_risk_alert(
        symbol="SOLUSDT",
        position_id="pos-sol",
        risk_type=RiskType.FUNDING_FLIP,
        trigger_reason="Funding flip",
        mark_price=150.0,
        stop_loss=145.0,
        unrealized_pnl=-10.0,
        now_ms=1_000,
    )
    assert alert is not None
    assert ac.get_active_alert("SOLUSDT") is not None

    ok = ac.override_hold("SOLUSDT", reason="User decided to hold through volatility", now_ms=5_000)
    assert ok is True
    assert alert.status == AlertStatus.OVERRIDDEN_HOLD
    assert ac.get_active_alert("SOLUSDT") is None

    audit = ac.get_audit_history()
    assert len(audit) == 2
    assert audit[1]["event"] == "OVERRIDDEN_HOLD"
    assert "hold through volatility" in audit[1]["details"]


def test_alert_manager_confirm_close() -> None:
    ac = AlertThenAutoCloseManager()
    alert = ac.trigger_risk_alert(
        symbol="AVAXUSDT",
        position_id="pos-avax",
        risk_type=RiskType.ADVERSE_LIQUIDATION,
        trigger_reason="Heavy cascade",
        mark_price=30.0,
        stop_loss=29.0,
        unrealized_pnl=-20.0,
        now_ms=10_000,
    )
    assert alert is not None

    ok = ac.confirm_immediate_close("AVAXUSDT", reason="Exit now", now_ms=15_000)
    assert ok is True
    assert alert.status == AlertStatus.CONFIRMED_CLOSE
    assert ac.get_active_alert("AVAXUSDT") is None

    audit = ac.get_audit_history()
    assert len(audit) == 2
    assert audit[1]["event"] == "CONFIRMED_CLOSE"


def test_alert_manager_check_expired_alerts() -> None:
    ac = AlertThenAutoCloseManager(AutoCloseConfig(grace_period_seconds=15))
    alert = ac.trigger_risk_alert(
        symbol="DOGEUSDT",
        position_id="pos-doge",
        risk_type=RiskType.STOP_LOSS_BREACH,
        trigger_reason="Stop breached",
        mark_price=0.10,
        stop_loss=0.11,
        unrealized_pnl=-5.0,
        now_ms=10_000,
    )
    assert alert is not None

    # Not expired yet (at 10s + 10s = 20s < 25s)
    expired = ac.check_expired_alerts(now_ms=20_000)
    assert len(expired) == 0
    assert ac.get_active_alert("DOGEUSDT") is not None

    # Now expired (at 10s + 16s = 26s >= 25s)
    expired = ac.check_expired_alerts(now_ms=26_000)
    assert len(expired) == 1
    assert expired[0].alert_id == alert.alert_id
    assert expired[0].status == AlertStatus.AUTOCLOSE_EXECUTED
    assert ac.get_active_alert("DOGEUSDT") is None

    # Next check should be empty
    assert len(ac.check_expired_alerts(now_ms=27_000)) == 0


def test_alert_manager_disabled() -> None:
    ac = AlertThenAutoCloseManager(AutoCloseConfig(enabled=False))
    alert = ac.trigger_risk_alert(
        symbol="BTCUSDT",
        position_id="pos-1",
        risk_type=RiskType.STOP_LOSS_BREACH,
        trigger_reason="test",
        mark_price=48000.0,
        stop_loss=49000.0,
        unrealized_pnl=-200.0,
    )
    assert alert is None
    assert ac.get_active_alert("BTCUSDT") is None


# ─── Part 3: RealTimePositionManager Trailing Stop Ratchets ───────────────────

def test_trailing_stop_ratchet_ladder_long(tracker: PositionTracker) -> None:
    pos = _open_pos(tracker, side=PositionSide.LONG, entry_price=100.0, stop_loss=90.0, risk_per_unit=10.0)
    # 1R = 10.0
    ac_mgr = AlertThenAutoCloseManager(AutoCloseConfig(trailing_stop_enabled=True))
    rt = RealTimePositionManager(tracker=tracker, autoclose_manager=ac_mgr)

    # 1. Price at +0.8R (108.0) -> below 1.0R activation -> returns None
    target = rt._calculate_trailing_stop_target(pos, 0.8)
    assert target is None

    # 2. Price at +1.1R (111.0) -> reaches >= 1.0R -> locks BE (100.0)
    target = rt._calculate_trailing_stop_target(pos, 1.1)
    assert target == 100.0

    # 3. Price at +1.6R (116.0) -> reaches >= 1.5R -> locks +0.75R (107.5)
    target = rt._calculate_trailing_stop_target(pos, 1.6)
    assert target == 107.5

    # 4. Price at +2.2R (122.0) -> reaches >= 2.0R -> locks +1.25R (112.5)
    target = rt._calculate_trailing_stop_target(pos, 2.2)
    assert target == 112.5

    # 5. Price at +3.2R (132.0) -> reaches >= 3.0R -> locks +2.0R (120.0)
    target = rt._calculate_trailing_stop_target(pos, 3.2)
    assert target == 120.0

    # Now verify live ratchet through evaluate_second
    now_ms = 1_000_000
    rt.record_mark_price("BTCUSDT", 116.0, now_ms=now_ms)
    rt.evaluate_second(now_ms=now_ms)

    open_pos = tracker.open_positions[0]
    assert open_pos.stop_loss == 107.5

    # Retrace to 110.0 -> stop loss must never loosen
    rt.record_mark_price("BTCUSDT", 110.0, now_ms=now_ms + 1000)
    rt.evaluate_second(now_ms=now_ms + 1000)

    open_pos = tracker.open_positions[0]
    assert open_pos.stop_loss == 107.5


def test_trailing_stop_ratchet_ladder_short(tracker: PositionTracker) -> None:
    pos = _open_pos(
        tracker,
        symbol="ETHUSDT",
        side=PositionSide.SHORT,
        entry_price=100.0,
        stop_loss=110.0,
        risk_per_unit=10.0,
    )
    # 1R = 10.0
    ac_mgr = AlertThenAutoCloseManager(AutoCloseConfig(trailing_stop_enabled=True))
    rt = RealTimePositionManager(tracker=tracker, autoclose_manager=ac_mgr)

    # 1. Below 1.0R activation -> returns None
    assert rt._calculate_trailing_stop_target(pos, 0.8) is None

    # 2. Reaches >= 1.0R -> locks BE (100.0)
    assert rt._calculate_trailing_stop_target(pos, 1.1) == 100.0

    # 3. Reaches >= 1.5R -> locks +0.75R (92.5)
    assert rt._calculate_trailing_stop_target(pos, 1.6) == 92.5

    # 4. Reaches >= 2.0R -> locks +1.25R (87.5)
    assert rt._calculate_trailing_stop_target(pos, 2.2) == 87.5

    # 5. Reaches >= 3.0R -> locks +2.0R (80.0)
    assert rt._calculate_trailing_stop_target(pos, 3.2) == 80.0

    # Live ratchet through evaluate_second
    now_ms = 1_000_000
    rt.record_mark_price("ETHUSDT", 84.0, now_ms=now_ms)
    rt.evaluate_second(now_ms=now_ms)

    open_pos = tracker.open_positions[0]
    assert open_pos.stop_loss == 92.5


# ─── Part 4: 4-Factor Risk Condition Checks ───────────────────────────────────

def test_risk_check_stop_loss_breach(tracker: PositionTracker) -> None:
    _open_pos(tracker, side=PositionSide.LONG, entry_price=100.0, stop_loss=95.0, risk_per_unit=5.0)
    ac_mgr = AlertThenAutoCloseManager(AutoCloseConfig(grace_period_seconds=15))
    rt = RealTimePositionManager(tracker=tracker, autoclose_manager=ac_mgr)

    now_ms = 1_000_000
    rt.record_mark_price("BTCUSDT", 94.0, now_ms=now_ms)  # 94.0 < 95.0 (SL breached)

    alerts = rt.evaluate_second(now_ms=now_ms)
    assert len(alerts) == 1
    assert alerts[0].risk_type == RiskType.STOP_LOSS_BREACH
    assert "breached stop loss" in alerts[0].trigger_reason


def test_risk_check_adverse_liquidations(tracker: PositionTracker) -> None:
    _open_pos(tracker, side=PositionSide.LONG, entry_price=100.0, stop_loss=90.0, risk_per_unit=10.0)
    ac_mgr = AlertThenAutoCloseManager(AutoCloseConfig(adverse_liquidation_threshold_usd=50000.0))
    rt = RealTimePositionManager(tracker=tracker, autoclose_manager=ac_mgr)

    now_ms = 1_000_000
    rt.record_mark_price("BTCUSDT", 98.0, now_ms=now_ms)
    # Long position suffers if long liquidations happen (sell orders)
    rt.record_liquidation("BTCUSDT", "SELL", 50000.0, 1.5, now_ms=now_ms - 5000)  # $75,000 > $50,000

    alerts = rt.evaluate_second(now_ms=now_ms)
    assert len(alerts) == 1
    assert alerts[0].risk_type == RiskType.ADVERSE_LIQUIDATION
    assert "Adverse liquidation cascade" in alerts[0].trigger_reason


def test_risk_check_funding_flip(tracker: PositionTracker) -> None:
    _open_pos(tracker, side=PositionSide.LONG, entry_price=100.0, stop_loss=90.0, risk_per_unit=10.0)
    ac_mgr = AlertThenAutoCloseManager(AutoCloseConfig(funding_inversion_threshold=0.0005))
    rt = RealTimePositionManager(tracker=tracker, autoclose_manager=ac_mgr)

    now_ms = 1_000_000
    # Long position pays fee if funding rate is positive and high
    rt.record_mark_price("BTCUSDT", 98.0, funding_rate=0.0008, now_ms=now_ms)

    alerts = rt.evaluate_second(now_ms=now_ms)
    assert len(alerts) == 1
    assert alerts[0].risk_type == RiskType.FUNDING_FLIP
    assert "Funding rate inverted" in alerts[0].trigger_reason


def test_risk_check_volatility_spike(tracker: PositionTracker) -> None:
    _open_pos(tracker, side=PositionSide.LONG, entry_price=100.0, stop_loss=80.0, risk_per_unit=20.0)
    ac_mgr = AlertThenAutoCloseManager(AutoCloseConfig(volatility_spike_pct=0.02))
    rt = RealTimePositionManager(tracker=tracker, autoclose_manager=ac_mgr)

    now_ms = 1_000_000
    rt.record_mark_price("BTCUSDT", 100.0, now_ms=now_ms - 30_000)
    rt.record_mark_price("BTCUSDT", 97.0, now_ms=now_ms)  # 3% drop > 2% spike threshold

    alerts = rt.evaluate_second(now_ms=now_ms)
    assert len(alerts) == 1
    assert alerts[0].risk_type == RiskType.VOLATILITY_SPIKE
    assert "Rapid volatility drop" in alerts[0].trigger_reason


# ─── Part 5: DangerManager.force_close & Fail-Safe Close OEM Chain ────────────

def test_danger_manager_force_close(
    tracker: PositionTracker,
    danger_manager: PositionDangerManager,
    mock_adapter: MockExecutionAdapter,
) -> None:
    pos = _open_pos(tracker, symbol="BTCUSDT", entry_price=50000.0, stop_loss=49000.0)
    assert pos.status == PositionStatus.OPEN

    result = danger_manager.force_close(
        pos,
        current_price=48500.0,
        now_ms=2_000_000,
        equity=10_000.0,
        reason=ExitReason.FAIL_SAFE,
        details="Alert-then-autoclose grace expired",
    )

    assert result.action_taken == DangerAction.FAIL_SAFE_CLOSE
    assert result.position.status == PositionStatus.CLOSED
    assert len(mock_adapter.executed_intents) == 1
    intent = mock_adapter.executed_intents[0]
    assert intent.symbol == "BTCUSDT"
    assert intent.side == OrderSide.SELL
    assert intent.intent_type == OrderIntentType.EXIT


def test_danger_manager_force_close_kill_switch_engaged(
    tracker: PositionTracker,
    danger_manager: PositionDangerManager,
    kill_switch: KillSwitch,
    mock_adapter: MockExecutionAdapter,
) -> None:
    pos = _open_pos(tracker, symbol="ETHUSDT", entry_price=3000.0, stop_loss=2900.0)
    kill_switch.activate(reason="Emergency kill switch engaged", actor="test")
    assert kill_switch.is_active is True

    # Fail-closed kill switch must never block flattening emergency exits
    result = danger_manager.force_close(
        pos,
        current_price=2800.0,
        now_ms=2_500_000,
        equity=10_000.0,
        reason=ExitReason.FAIL_SAFE,
        details="Kill-switch concurrent fail-safe close",
    )

    assert result.action_taken == DangerAction.FAIL_SAFE_CLOSE
    assert result.position.status == PositionStatus.CLOSED
    assert len(mock_adapter.executed_intents) == 1


# ─── Part 6: Integrated Evaluate Second Cycle ──────────────────────────────────

class DummyMockEngine:
    def __init__(
        self,
        config: ApexConfig,
        tracker: PositionTracker,
        danger_mgr: PositionDangerManager,
        ac_mgr: AlertThenAutoCloseManager,
        kill_sw: KillSwitch | None = None,
    ) -> None:
        self.config = config
        self.position_tracker = tracker
        self.danger_manager = danger_mgr
        self.autoclose_manager = ac_mgr
        self.kill_switch = kill_sw
        self.current_equity = 10_000.0
        self.closed_positions: list[tuple[str, float, str]] = []

    def get_open_position_by_id(self, position_id: str) -> Position | None:
        return self.position_tracker.all_positions.get(position_id)

    def force_close_position(
        self,
        position_id: str,
        current_price: float,
        *,
        reason: ExitReason = ExitReason.FAIL_SAFE,
        details: str = "",
        now_ms: int | None = None,
    ) -> Any:
        pos = self.get_open_position_by_id(position_id)
        if pos is None:
            return None
        self.closed_positions.append((position_id, current_price, details))
        return self.danger_manager.force_close(
            pos,
            current_price,
            now_ms=now_ms if now_ms is not None else int(time.time() * 1000),
            equity=self.current_equity,
            reason=reason,
            details=details,
        )

    def get_cached_series(self, symbol: str) -> None:
        return None

    def get_status(self) -> Any:
        class _Status:
            system_state = "RUNNING"
            last_scan_time_ms = 1_700_000_000_000
            kill_switch_active = False
        return _Status()

    def get_tactical_signals(self) -> list[dict[str, Any]]:
        return []


def test_evaluate_second_grace_period_expiration(
    safe_config: ApexConfig,
    tracker: PositionTracker,
    danger_manager: PositionDangerManager,
) -> None:
    # Set 15 seconds grace period
    ac_mgr = AlertThenAutoCloseManager(AutoCloseConfig(grace_period_seconds=15))
    mock_engine = DummyMockEngine(safe_config, tracker, danger_manager, ac_mgr)
    rt = RealTimePositionManager(tracker=tracker, autoclose_manager=ac_mgr, engine=mock_engine)

    pos = _open_pos(tracker, symbol="NEARUSDT", entry_price=5.0, stop_loss=4.8, risk_per_unit=0.2)
    pos_id = f"{pos.symbol}:{pos.entry_price}:{pos.opened_at_ms}"

    # Cycle 1: Price drops below stop loss -> triggers alert
    now_ms = 1_000_000
    rt.record_mark_price("NEARUSDT", 4.7, now_ms=now_ms)

    alerts = rt.evaluate_second(now_ms=now_ms)
    assert len(alerts) == 1
    assert alerts[0].symbol == "NEARUSDT"
    assert len(mock_engine.closed_positions) == 0

    # Cycle 2: Time advances beyond grace period -> evaluate_second automatically forces close
    rt.evaluate_second(now_ms=now_ms + 16_000)

    assert len(mock_engine.closed_positions) == 1
    closed_id, close_price, reason = mock_engine.closed_positions[0]
    assert closed_id == pos_id
    assert close_price == 4.7
    assert tracker.all_positions[pos_id].status == PositionStatus.CLOSED
    assert len(tracker.open_positions) == 0
    assert ac_mgr.get_active_alert("NEARUSDT") is None


def test_evaluate_second_user_override_prevents_autoclose(
    safe_config: ApexConfig,
    tracker: PositionTracker,
    danger_manager: PositionDangerManager,
) -> None:
    ac_mgr = AlertThenAutoCloseManager(AutoCloseConfig(grace_period_seconds=15))
    mock_engine = DummyMockEngine(safe_config, tracker, danger_manager, ac_mgr)
    rt = RealTimePositionManager(tracker=tracker, autoclose_manager=ac_mgr, engine=mock_engine)

    pos = _open_pos(tracker, symbol="SUIUSDT", entry_price=1.0, stop_loss=0.95, risk_per_unit=0.05)
    now_ms = 1_000_000
    rt.record_mark_price("SUIUSDT", 0.94, now_ms=now_ms)

    # Cycle 1: Triggers alert
    alerts = rt.evaluate_second(now_ms=now_ms)
    assert len(alerts) == 1
    assert ac_mgr.get_active_alert("SUIUSDT") is not None

    # User manually overrides HOLD
    ac_mgr.override_hold("SUIUSDT", reason="User decided to hold")

    # Cycle 2: Time passes grace period
    rt.evaluate_second(now_ms=now_ms + 16_000)

    # Position remains open
    assert len(mock_engine.closed_positions) == 0
    assert pos.status == PositionStatus.OPEN


def test_evaluate_second_no_engine_fails_closed(tracker: PositionTracker) -> None:
    """Without an engine, expired alert-then-autoclose must never close directly.

    A direct tracker close would bypass the OEM/RiskGuardian/EndpointGuard safety
    chain. The autoclose must fail closed instead (position stays open, gap
    surfaced for reconciliation).
    """
    ac_mgr = AlertThenAutoCloseManager(AutoCloseConfig(grace_period_seconds=15))
    rt = RealTimePositionManager(tracker=tracker, autoclose_manager=ac_mgr, engine=None)

    pos = _open_pos(tracker, symbol="OPUSDT", entry_price=1.0, stop_loss=0.95, risk_per_unit=0.05)
    now_ms = 1_000_000
    rt.record_mark_price("OPUSDT", 0.94, now_ms=now_ms)

    alerts = rt.evaluate_second(now_ms=now_ms)
    assert len(alerts) == 1
    assert ac_mgr.get_active_alert("OPUSDT") is not None

    # Grace period expires; no engine wired
    rt.evaluate_second(now_ms=now_ms + 16_000)

    assert tracker.open_count == 1
    assert tracker.all_positions[f"{pos.symbol}:{pos.entry_price}:{pos.opened_at_ms}"].status == PositionStatus.OPEN
    assert ac_mgr.get_active_alert("OPUSDT") is None


# ─── Part 7: API Server Autoclose Endpoints ────────────────────────────────────

def _get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_api_autoclose_endpoints(
    safe_config: ApexConfig,
    tracker: PositionTracker,
    danger_manager: PositionDangerManager,
    kill_switch: KillSwitch,
) -> None:
    ac_mgr = AlertThenAutoCloseManager(AutoCloseConfig(grace_period_seconds=30))
    engine = DummyMockEngine(safe_config, tracker, danger_manager, ac_mgr, kill_sw=kill_switch)
    rt = RealTimePositionManager(tracker=tracker, autoclose_manager=ac_mgr, engine=engine)
    engine.real_time_manager = rt  # type: ignore[attr-defined]

    pos = _open_pos(tracker, symbol="PEPEUSDT", entry_price=0.000010, stop_loss=0.000009, risk_per_unit=0.000001)
    ac_mgr.trigger_risk_alert(
        symbol="PEPEUSDT",
        position_id=f"{pos.symbol}:{pos.entry_price}:{pos.opened_at_ms}",
        risk_type=RiskType.STOP_LOSS_BREACH,
        trigger_reason="SL breached",
        mark_price=0.000008,
        stop_loss=0.000009,
        unrealized_pnl=-0.5,
    )

    port = _get_free_port()
    server = ApexApiServer(engine, host="127.0.0.1", port=port)

    with server:
        base_url = f"http://127.0.0.1:{port}"

        # 1. GET /api/v1/positions
        req = urllib.request.Request(f"{base_url}/api/v1/positions")
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert data["count"] == 1
            assert data["positions"][0]["symbol"] == "PEPEUSDT"
            assert data["positions"][0]["active_alert"] is not None

        # 2. GET /api/v1/autoclose
        req = urllib.request.Request(f"{base_url}/api/v1/autoclose")
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert data["enabled"] is True
            assert data["active_alerts_count"] == 1

        # 3. GET /api/v1/realtime
        req = urllib.request.Request(f"{base_url}/api/v1/realtime")
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert "ws_connected" in data

        # 4. POST /api/v1/autoclose/override with HOLD
        post_data = json.dumps({"symbol": "PEPEUSDT", "action": "HOLD", "reason": "User override"}).encode()
        req = urllib.request.Request(
            f"{base_url}/api/v1/autoclose/override",
            data=post_data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert data["success"] is True
            assert data["action"] == "HOLD"
            assert ac_mgr.get_active_alert("PEPEUSDT") is None
