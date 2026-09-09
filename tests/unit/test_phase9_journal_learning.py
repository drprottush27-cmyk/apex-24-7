"""Phase 9 — Trade Journal + Learning Proposal Tests.

Covers:
1. Trade journal append-only immutability (no updates/deletes, idempotent append).
2. ClosedTradeRecord validation and deterministic build.
3. Performance analyzer determinism and empty-input safety.
4. Proposal generation: REVIEW-ONLY, human-approval required, cannot loosen risk.
"""


from pathlib import Path

import pytest

from apex.config.settings import ApexConfig
from apex.domain.positions import create_position
from apex.domain.types import (
    ExitReason,
    PositionSide,
    PositionStatus,
    TradingMode,
)
from apex.journal.models import (
    ClosedTradeRecord,
    TradeContext,
    build_closed_trade_record,
    from_closed_position,
)
from apex.journal.repository import TradeJournal, TradeJournalError
from apex.learning.analyzer import PerformanceAnalyzer, TradeMetrics
from apex.learning.proposals import (
    ProposalGenerator,
    ProposalSafetyViolationError,
    StrategyProposal,
)
from apex.safety.exceptions import InvalidNumericalDataError


@pytest.fixture
def journal_path(tmp_path: Path) -> str:
    return str(tmp_path / "trade_journal.db")


@pytest.fixture
def p9_config() -> ApexConfig:
    return ApexConfig(
        trading_mode=TradingMode.PAPER,
        max_risk_per_trade=0.01,
        max_leverage=3.0,
        max_concurrent_positions=2,
    )


def make_record(
    *,
    symbol: str = "BTCUSDT",
    side: PositionSide = PositionSide.LONG,
    entry: float = 50000.0,
    exit_price: float = 51200.0,
    quantity: float = 0.2,
    stop: float = 49500.0,
    target: float = 51500.0,
    opened: int = 1000000000000,
    closed: int = 1000001800000,
    exit_reason: ExitReason | None = ExitReason.TAKE_PROFIT,
    log_index: int = 0,
    context: TradeContext | None = None,
) -> ClosedTradeRecord:
    pnl = (exit_price - entry) * quantity if side == PositionSide.LONG else (
        entry - exit_price
    ) * quantity
    return build_closed_trade_record(
        symbol=symbol,
        side=side,
        mode=TradingMode.PAPER,
        entry_price=entry,
        exit_price=exit_price,
        quantity=quantity,
        stop_loss=stop,
        take_profit=target,
        risk_per_unit=abs(entry - stop),
        realized_pnl=pnl,
        opened_at_ms=opened,
        closed_at_ms=closed,
        exit_reason=exit_reason,
        strategy_version="prepump-v1",
        log_index=log_index,
        context=context,
    )


class TestClosedTradeRecord:
    def test_build_derives_r_multiple(self) -> None:
        rec = make_record(exit_price=51200.0)
        # risk_per_unit = 500, pnl = 1200 * 0.2 = 240
        assert rec.r_multiple == pytest.approx(240.0 / 500.0)
        assert rec.duration_ms == 1800000
        assert rec.status.value == "CLOSED"

    def test_rejects_negative_prices(self) -> None:
        with pytest.raises(InvalidNumericalDataError):
            make_record(entry=-1.0)

    def test_rejects_nan_pnl(self) -> None:
        with pytest.raises(InvalidNumericalDataError):
            make_record(exit_price=float("nan"))

    def test_rejects_inconsistent_r_multiple(self) -> None:
        from apex.journal.models import ClosedTradeRecord

        rec = make_record()
        with pytest.raises(InvalidNumericalDataError):
            ClosedTradeRecord(
                **{
                    **rec.model_dump(),
                    "r_multiple": rec.r_multiple + 999.0,
                }
            )

    def test_rejects_negative_risk_per_unit(self) -> None:
        with pytest.raises(InvalidNumericalDataError):
            build_closed_trade_record(
                symbol="BTCUSDT",
                side=PositionSide.LONG,
                mode=TradingMode.PAPER,
                entry_price=50000.0,
                exit_price=51000.0,
                quantity=1.0,
                stop_loss=49500.0,
                take_profit=51500.0,
                risk_per_unit=0.0,
                realized_pnl=100.0,
                opened_at_ms=1000,
                closed_at_ms=2000,
            )

    def test_from_closed_position(self) -> None:
        position = create_position(
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            entry_price=50000.0,
            quantity=0.2,
            stop_loss=49500.0,
            take_profit=51500.0,
            mode=TradingMode.PAPER,
            status=PositionStatus.OPEN,
            opened_at_ms=1000000000000,
            risk_per_unit=500.0,
            strategy_version="prepump-v1",
            source_signal_id="BTCUSDT:999:prepump-v1",
        )
        # Simulate a realized close at the take profit price.
        rec = from_closed_position(
            position,
            exit_price=51500.0,
            closed_at_ms=1000003600000,
            realized_pnl=(51500.0 - 50000.0) * 0.2,
            exit_reason=ExitReason.TAKE_PROFIT,
        )
        assert rec.symbol == "BTCUSDT"
        assert rec.exit_price == 51500.0
        assert rec.r_multiple == pytest.approx(300.0 / 500.0)
        assert rec.source_signal_id == "BTCUSDT:999:prepump-v1"
        assert rec.strategy_version == "prepump-v1"

    def test_from_closed_position_rejects_no_position(self) -> None:
        with pytest.raises(InvalidNumericalDataError):
            from_closed_position(
                None,
                exit_price=1.0,
                closed_at_ms=2,
                realized_pnl=0.0,
            )


class TestTradeJournal:
    def test_append_and_read(self, journal_path: str) -> None:
        journal = TradeJournal(journal_path)
        rec = make_record()
        seq = journal.append(rec)
        assert seq == 1
        assert journal.count() == 1
        loaded = journal.all_trades()
        assert len(loaded) == 1
        assert loaded[0].symbol == "BTCUSDT"
        assert loaded[0].r_multiple == rec.r_multiple
        journal.close()

    def test_persistence_survives_reopen(self, journal_path: str) -> None:
        journal = TradeJournal(journal_path)
        journal.append(make_record(log_index=0))
        journal.append(make_record(log_index=1, symbol="ETHUSDT", entry=3000.0, exit_price=3090.0))
        journal.close()

        reopened = TradeJournal(journal_path)
        assert reopened.count() == 2
        assert len(reopened.trades_for_symbol("ETHUSDT")) == 1
        assert len(reopened.trades_for_strategy("prepump-v1")) == 2
        reopened.close()

    def test_duplicate_append_rejected(self, journal_path: str) -> None:
        journal = TradeJournal(journal_path)
        rec = make_record()
        journal.append(rec)
        with pytest.raises(TradeJournalError):
            journal.append(rec)

    def test_no_destructive_writes_exposed(self) -> None:
        import inspect

        src = inspect.getsource(TradeJournal).upper()
        # The repository must contain no SQL UPDATE/DELETE statements against
        # its own tables (append-only contract).
        assert "UPDATE CLOSED_TRADES" not in src
        assert "UPDATE TRADE_JOURNAL_META" not in src
        assert "DELETE FROM" not in src
        assert "DELETE FROM CLOSED_TRADES" not in src

    def test_roundtrip_context(self, journal_path: str) -> None:
        journal = TradeJournal(journal_path)
        ctx = TradeContext(
            detector_version="prepump-v1",
            timeframe="5m",
            candle_timestamp_ms=999000000,
            signal_idempotency_key="key-1",
            detected_legs=("MOMENTUM_VOLUME", "BREAKOUT"),
            leg_results={"momentum_volume": True, "breakout": True},
            indicator_values={"rvol": 2.5, "adx": 24.0},
        )
        journal.append(make_record(context=ctx))
        loaded = journal.all_trades()[0]
        assert loaded.context is not None
        assert loaded.context.detector_version == "prepump-v1"
        assert loaded.context.indicator_values["rvol"] == 2.5
        journal.close()

    def test_empty_journal_reads_safely(self, journal_path: str) -> None:
        journal = TradeJournal(journal_path)
        assert journal.count() == 0
        assert journal.all_trades() == ()
        assert journal.trades_for_symbol("BTCUSDT") == ()
        journal.close()


class TestPerformanceAnalyzer:
    def test_empty_records(self) -> None:
        metrics = PerformanceAnalyzer.compute(())
        assert isinstance(metrics, TradeMetrics)
        assert metrics.total_trades == 0
        assert metrics.win_rate == 0.0

    def test_win_loss_streak_and_drawdown(self) -> None:
        records = [
            # Two wins (R = +1 each), then 3 losses (R = -1 each).
            make_record(exit_price=50500.0, quantity=1.0, log_index=0),
            make_record(exit_price=50500.0, quantity=1.0, log_index=1),
            make_record(
                exit_price=49500.0, quantity=1.0, log_index=2,
                exit_reason=ExitReason.STOP_LOSS,
            ),
            make_record(
                exit_price=49500.0, quantity=1.0, log_index=3,
                exit_reason=ExitReason.STOP_LOSS,
            ),
            make_record(
                exit_price=49500.0, quantity=1.0, log_index=4,
                exit_reason=ExitReason.STOP_LOSS,
            ),
        ]
        metrics = PerformanceAnalyzer.compute(records)
        assert metrics.total_trades == 5
        assert metrics.wins == 2
        assert metrics.losses == 3
        assert metrics.win_rate == pytest.approx(0.4)
        assert metrics.max_consecutive_wins == 2
        assert metrics.max_consecutive_losses == 3
        # total pnl: 2*(+500) + 3*(-500) = 1000 - 1500 = -500
        assert metrics.total_realized_pnl == pytest.approx(-500.0)
        assert metrics.expectancy_r == pytest.approx(-0.2)

    def test_deterministic(self) -> None:
        records = [make_record(log_index=i) for i in range(5)]
        a = PerformanceAnalyzer.compute(records)
        b = PerformanceAnalyzer.compute(records)
        assert a == b


class TestProposalGenerator:
    def test_generates_review_only_proposal(self, p9_config: ApexConfig) -> None:
        gen = ProposalGenerator(config=p9_config, clock_ms=1700000000000)
        records = tuple(
            make_record(log_index=i) for i in range(12)
        )
        proposal = gen.generate(records)
        assert isinstance(proposal, StrategyProposal)
        assert proposal.status == "DRAFT_FOR_REVIEW"
        assert proposal.requires_human_approval is True
        assert proposal.risk_unchanged_or_reduced is True

    def test_proposal_cannot_loosen_thresholds(self, p9_config: ApexConfig) -> None:
        gen = ProposalGenerator(config=p9_config, clock_ms=1000)
        # Weak performance -> generator would want stricter; clamp must keep >= current.
        records = tuple(
            make_record(log_index=i, exit_price=49500.0, exit_reason=ExitReason.STOP_LOSS)
            for i in range(12)
        )
        proposal = gen.generate(records)
        for key, current in gen.current_thresholds.items():
            assert proposal.suggested_parameters[key] >= current

    def test_proposal_contains_no_write_capability(self, p9_config: ApexConfig) -> None:
        import inspect

        from apex.learning import proposals as proposals_module

        gen = ProposalGenerator(config=p9_config, clock_ms=1000)
        recs = tuple(make_record(log_index=i) for i in range(20))
        proposal = gen.generate(recs)
        # Proposal is a plain immutable data doc.
        assert isinstance(proposal.summary_md, str)
        assert "DRAFT_FOR_REVIEW" in proposal.summary_md
        assert "requires human approval" in proposal.summary_md.lower()
        # Module source must not contain any config/code write primitive.
        src = inspect.getsource(proposals_module)
        for forbidden in ("open(", "os.write", "pathlib", "write_text", "sqlite"):
            assert forbidden not in src

    def test_unknown_parameter_rejected(self, p9_config: ApexConfig) -> None:
        with pytest.raises(ProposalSafetyViolationError):
            ProposalGenerator(
                config=p9_config,
                current_thresholds={"max_leverage": 100.0},
            )

    def test_insufficient_sample_no_change(self, p9_config: ApexConfig) -> None:
        gen = ProposalGenerator(config=p9_config, clock_ms=2000)
        records = tuple(make_record(log_index=i) for i in range(5))
        proposal = gen.generate(records)
        for key, current in gen.current_thresholds.items():
            assert proposal.suggested_parameters[key] == current

    def test_markdown_is_deterministic(self, p9_config: ApexConfig) -> None:
        records = tuple(make_record(log_index=i) for i in range(12))
        gen1 = ProposalGenerator(config=p9_config, clock_ms=3000)
        gen2 = ProposalGenerator(config=p9_config, clock_ms=3000)
        assert gen1.generate(records).summary_md == gen2.generate(records).summary_md
