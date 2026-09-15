"""APEX 24/7 — Deterministic BTC Macro Regime Filter & Altcoin Veto.

Evaluates Bitcoin market health and provides an authoritative macro filter:
If BTC is in an acute breakdown/dump regime (UNFAVORABLE_DUMP), any new
long positions on altcoin candidates are strictly vetoed.
Invariants:
- Deterministic, lookahead-free evaluation over closed bars.
- Fail-safe: insufficient data degrades safely without throwing unhandled exceptions.
- Output is typed and testable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Sequence

from apex.domain.candles import Candle
from apex.indicators.core import ema
from apex.market.candle_series import CandleSeries


class BtcRegimeState(StrEnum):
    FAVORABLE_BULL = "FAVORABLE_BULL"
    NEUTRAL_RANGE = "NEUTRAL_RANGE"
    UNFAVORABLE_DUMP = "UNFAVORABLE_DUMP"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True, slots=True)
class BtcRegimeResult:
    state: BtcRegimeState
    btc_price: float
    ema_fast: float
    ema_slow: float
    return_1h_pct: float
    return_4h_pct: float
    is_dumping: bool
    veto_altcoin_longs: bool
    detail: str

    def to_metadata(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "btc_price": round(self.btc_price, 2),
            "ema_fast": round(self.ema_fast, 2),
            "ema_slow": round(self.ema_slow, 2),
            "return_1h_pct": round(self.return_1h_pct, 3),
            "return_4h_pct": round(self.return_4h_pct, 3),
            "is_dumping": self.is_dumping,
            "veto_altcoin_longs": self.veto_altcoin_longs,
            "detail": self.detail,
        }


def _extract_closes(bars: CandleSeries | Sequence[Any]) -> tuple[float, ...]:
    if isinstance(bars, CandleSeries):
        return tuple(float(c.close) for c in bars.candles)

    c_list: list[float] = []
    for b in bars:
        if isinstance(b, Candle):
            c_list.append(float(b.close))
        elif isinstance(b, dict):
            c_list.append(float(b.get("close", 0.0)))
        elif isinstance(b, (tuple, list)) and len(b) >= 4:
            c_list.append(float(b[3]))
        elif isinstance(b, (int, float)):
            c_list.append(float(b))
    return tuple(c_list)


def evaluate_btc_regime(
    bars: CandleSeries | Sequence[Any],
    *,
    fast_period: int = 12,
    slow_period: int = 26,
    bars_1h: int = 12,  # 12 * 5m = 1 hour
    bars_4h: int = 48,  # 48 * 5m = 4 hours
) -> BtcRegimeResult:
    """Evaluate BTC trend and market stability.

    Returns BtcRegimeResult indicating whether BTC is in a healthy uptrend,
    consolidation, or an active dump requiring an altcoin long veto.
    """
    closes = _extract_closes(bars)
    min_required = max(slow_period + 1, bars_1h + 1)
    if len(closes) < min_required or not all(math.isfinite(c) for c in closes):
        return BtcRegimeResult(
            state=BtcRegimeState.INSUFFICIENT_DATA,
            btc_price=closes[-1] if closes else 0.0,
            ema_fast=0.0,
            ema_slow=0.0,
            return_1h_pct=0.0,
            return_4h_pct=0.0,
            is_dumping=False,
            veto_altcoin_longs=False,
            detail="Insufficient BTC candles for regime evaluation.",
        )

    current_price = closes[-1]
    fast_val = ema(closes, fast_period)
    slow_val = ema(closes, slow_period)

    # 1h return
    price_1h_ago = closes[-min(bars_1h + 1, len(closes))]
    return_1h_pct = ((current_price - price_1h_ago) / price_1h_ago) * 100.0 if price_1h_ago > 0 else 0.0

    # 4h return
    price_4h_ago = closes[-min(bars_4h + 1, len(closes))]
    return_4h_pct = ((current_price - price_4h_ago) / price_4h_ago) * 100.0 if price_4h_ago > 0 else 0.0

    # Dump conditions:
    # 1. Acute 1h flash dump > 2.0%
    # 2. Strong 4h breakdown > 3.5% with fast EMA < slow EMA
    # 3. Persistent bear trend (fast EMA < slow EMA) with negative 1h momentum < -1.0%
    is_acute_dump = return_1h_pct < -2.0
    is_trend_dump = return_4h_pct < -3.5 and fast_val < slow_val
    is_bear_accelerating = fast_val < slow_val and return_1h_pct < -1.0

    is_dumping = is_acute_dump or is_trend_dump or is_bear_accelerating

    if is_dumping:
        state = BtcRegimeState.UNFAVORABLE_DUMP
        veto_altcoin = True
        detail = (
            f"BTC Dump Detected: Price=${current_price:,.1f}, 1h={return_1h_pct:+.2f}%, "
            f"4h={return_4h_pct:+.2f}%, EMA({fast_period})={fast_val:,.1f} vs EMA({slow_period})={slow_val:,.1f}."
        )
    elif fast_val > slow_val and return_4h_pct > 0.5:
        state = BtcRegimeState.FAVORABLE_BULL
        veto_altcoin = False
        detail = (
            f"BTC Favorable Bull: Price=${current_price:,.1f} above EMA({slow_period})={slow_val:,.1f}, "
            f"4h={return_4h_pct:+.2f}%."
        )
    else:
        state = BtcRegimeState.NEUTRAL_RANGE
        veto_altcoin = False
        detail = (
            f"BTC Neutral Range: Price=${current_price:,.1f}, 1h={return_1h_pct:+.2f}%, 4h={return_4h_pct:+.2f}%."
        )

    return BtcRegimeResult(
        state=state,
        btc_price=current_price,
        ema_fast=fast_val,
        ema_slow=slow_val,
        return_1h_pct=return_1h_pct,
        return_4h_pct=return_4h_pct,
        is_dumping=is_dumping,
        veto_altcoin_longs=veto_altcoin,
        detail=detail,
    )


def should_veto_altcoin_signal(
    alt_symbol: str,
    signal_direction: str,
    btc_regime: BtcRegimeResult,
) -> tuple[bool, str]:
    """Determine whether an altcoin signal must be vetoed based on BTC regime.

    Returns:
        (vetoed: bool, reason: str)
    """
    sym = (alt_symbol or "").strip().upper()
    direction = (signal_direction or "").strip().upper()

    # BTC itself is evaluated on its own merits and not vetoed by this cross-market filter
    if sym.startswith("BTC"):
        return False, "BTC signals are exempt from cross-market altcoin veto."

    if direction == "LONG" and btc_regime.veto_altcoin_longs:
        return True, f"ALTCOIN LONG VETO: BTC is in {btc_regime.state.value}. {btc_regime.detail}"

    return False, f"BTC regime permissive for {sym} ({btc_regime.state.value})."
