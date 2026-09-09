"""APEX 24/7 — Learning Analyzer (Phase 9).

Reads the append-only trade journal and produces deterministic,
informational performance metrics.

STRICT INVARIANT (ADR-0005 / AGENTS.md):
- Analysis is advisory only.
- Metrics must NEVER be used as inputs to the risk engine.
- Equity targets, streaks, deficits, and emotional objectives must never
  increase risk. This module cannot modify risk parameters or code.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from apex.journal.models import ClosedTradeRecord
from apex.safety.exceptions import InvalidNumericalDataError


@dataclass(frozen=True)
class TradeMetrics:
    """Deterministic informational metrics over a set of closed trades.

    Informational only — never a risk input. All values derived from
    journal records via fixed arithmetic.
    """

    total_trades: int
    wins: int
    losses: int
    win_rate: float
    total_realized_pnl: float
    avg_realized_pnl: float
    expectancy_r: float
    profit_factor: float
    avg_win_r: float
    avg_loss_r: float
    best_r: float
    worst_r: float
    max_drawdown_pnl: float
    max_consecutive_wins: int
    max_consecutive_losses: int
    avg_duration_ms: float


class PerformanceAnalyzer:
    """Computes deterministic metrics from closed trade records.

    Computations are pure arithmetic over the immutable journal records.
    No external state, no lookahead, no randomness.
    """

    @staticmethod
    def compute(records: Sequence[ClosedTradeRecord]) -> TradeMetrics:
        total = len(records)
        if total == 0:
            return TradeMetrics(
                total_trades=0,
                wins=0,
                losses=0,
                win_rate=0.0,
                total_realized_pnl=0.0,
                avg_realized_pnl=0.0,
                expectancy_r=0.0,
                profit_factor=0.0,
                avg_win_r=0.0,
                avg_loss_r=0.0,
                best_r=0.0,
                worst_r=0.0,
                max_drawdown_pnl=0.0,
                max_consecutive_wins=0,
                max_consecutive_losses=0,
                avg_duration_ms=0.0,
            )

        wins: list[float] = []
        losses: list[float] = []
        total_pnl = 0.0
        peak_pnl = 0.0
        max_drawdown = 0.0
        max_win_streak = 0
        max_loss_streak = 0
        current_streak = 0
        streak_kind: int | None = None  # 1 = wins, -1 = losses
        total_duration = 0

        for record in records:
            r = record.r_multiple
            pnl = record.realized_pnl
            if not math.isfinite(r) or not math.isfinite(pnl):
                raise InvalidNumericalDataError(
                    f"Non-finite metric in trade record for {record.symbol}."
                )
            if r > 0.0:
                wins.append(r)
            elif r < 0.0:
                losses.append(r)

            total_pnl += pnl
            total_duration += record.duration_ms

            # Peak-to-trough drawdown over running realized PnL.
            peak_pnl = max(peak_pnl, total_pnl)
            max_drawdown = max(max_drawdown, peak_pnl - total_pnl)

            # Consecutive win/loss streaks.
            kind = 1 if r > 0.0 else (-1 if r < 0.0 else 0)
            if kind == 0:
                current_streak = 0
                streak_kind = None
                continue
            if streak_kind == kind:
                current_streak += 1
            else:
                streak_kind = kind
                current_streak = 1
            if kind == 1:
                max_win_streak = max(max_win_streak, current_streak)
            else:
                max_loss_streak = max(max_loss_streak, current_streak)

        n_wins = len(wins)
        n_losses = len(losses)
        total_r = sum(r for r in wins) + sum(r for r in losses)
        gross_win_r = sum(wins)

        profit_factor = 0.0
        avg_win_r = 0.0
        avg_loss_r = 0.0
        if n_wins:
            avg_win_r = gross_win_r / n_wins
        if n_losses:
            avg_loss_r = sum(losses) / n_losses
        if losses:
            gross_loss_r = -sum(losses)
            if gross_win_r > 0.0:
                profit_factor = gross_win_r / gross_loss_r

        return TradeMetrics(
            total_trades=total,
            wins=n_wins,
            losses=n_losses,
            win_rate=(n_wins / total) if total else 0.0,
            total_realized_pnl=total_pnl,
            avg_realized_pnl=total_pnl / total,
            expectancy_r=total_r / total,
            profit_factor=profit_factor,
            avg_win_r=avg_win_r,
            avg_loss_r=avg_loss_r,
            best_r=max(wins) if wins else 0.0,
            worst_r=min(losses) if losses else 0.0,
            max_drawdown_pnl=max_drawdown,
            max_consecutive_wins=max_win_streak,
            max_consecutive_losses=max_loss_streak,
            avg_duration_ms=(total_duration / total) if total else 0.0,
        )
