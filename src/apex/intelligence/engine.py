from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from apex.agents.models import NormalizedMarketSnapshot, SourceStatus

from .models import ConfirmationState, CrossExchangeReport

# ---- Documented deterministic thresholds -----------------------------------
# Price agreement: relative divergence (max-min)/median across venues.
PRICE_AGREE_PCT = 0.0025        # <= 0.25%  -> prices agree
PRICE_PARTIAL_PCT = 0.015       # <= 1.5%   -> partial agreement band
PRICE_DIVERGENT_PCT = 0.015     # > 1.5%    -> divergent
# Volume/liquidity agreement: relative divergence threshold.
VOLUME_DIVERGENCE_RATIO = 0.35  # venue quote volumes routinely vary; >35% flag
# Funding: absolute rate spread across venues.
FUNDING_DIVERGENCE_ABS = 0.0002  # 0.02% absolute funding spread
# Open interest: relative divergence (coarse venue-sanity signal; OI units vary
# by venue contract size, so this is intentionally a very coarse bound).
OPEN_INTEREST_DIVERGENCE_RATIO = 0.50

_QUOTE_SUFFIXES = ("USDT", "USDC", "FDUSD", "BUSD", "DAI", "BTC", "ETH")


def normalize_symbol_for(exchange: str, base: str) -> str:
    """Map a base symbol (e.g. 'BTCUSDT') to an exchange-native string.

    Deterministic and documented: 'binance'/'bybit' use 'BTCUSDT'; 'okx'
    perpetual swaps use 'BTC-USDT-SWAP'.
    """
    cleaned = (base or "").strip().upper().replace("-", "").replace("/", "")
    if exchange == "okx":
        quote = next((q for q in _QUOTE_SUFFIXES if cleaned.endswith(q) and len(cleaned) > len(q)), None)
        if quote:
            return f"{cleaned[:-len(quote)]}-{quote}-SWAP"
        return f"{cleaned}-USDT-SWAP"
    return cleaned


def relative_divergence(values: List[float]) -> Optional[float]:
    """(max - min) / median for a set of values; None for fewer than 2 values."""
    if len(values) < 2:
        return None
    ordered = sorted(values)
    n = len(ordered)
    median = ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2.0
    if median == 0:
        return None
    return (ordered[-1] - ordered[0]) / median


class CrossExchangeIntelligence:
    """Read-only cross-exchange confirmation service.

    Compares Binance, OKX and Bybit normalized snapshots for price/volume
    agreement, and reports funding/open-interest divergence where available.

    This is ADVISORY state, not a tradable signal: it never executes anything.
    """

    def analyze(self, snapshots: Dict[str, NormalizedMarketSnapshot]) -> CrossExchangeReport:
        now = datetime.now(timezone.utc).isoformat()
        symbol = _report_symbol(snapshots)

        available: Dict[str, NormalizedMarketSnapshot] = {
            exch: snap for exch, snap in snapshots.items() if snap.is_available
        }
        unavailable = [
            exch for exch, snap in snapshots.items()
            if not snap.is_available and snap.source_status == SourceStatus.DATA_UNAVAILABLE
        ]
        errors = {
            exch: snap.error or "DATA_UNAVAILABLE"
            for exch, snap in snapshots.items()
            if snap.error
        }

        price_refs: Dict[str, float] = {}
        volumes: Dict[str, float] = {}
        funding: Dict[str, float] = {}
        ois: Dict[str, float] = {}
        liquidity: Dict[str, str] = {}

        for exch, snap in available.items():
            ref = _price_reference(snap)
            if ref is not None:
                price_refs[exch] = ref
            if snap.volume_24h is not None:
                volumes[exch] = float(snap.volume_24h)
            if snap.funding_rate is not None:
                funding[exch] = float(snap.funding_rate)
            if snap.open_interest is not None:
                ois[exch] = float(snap.open_interest)
            liquidity[exch] = snap.liquidity_status.value

        reasons: List[str] = []
        state = self._resolve_state(available, price_refs, volumes, reasons)

        price_div = relative_divergence(list(price_refs.values()))
        volume_div = relative_divergence(list(volumes.values()))
        funding_divergent = None
        if len(funding) >= 2:
            fvals = sorted(funding.values())
            funding_divergent = (fvals[-1] - fvals[0]) > FUNDING_DIVERGENCE_ABS
            if funding_divergent:
                reasons.append(f"funding spread {fvals[-1] - fvals[0]:.6f} exceeds {FUNDING_DIVERGENCE_ABS}")
        oi_divergent = None
        if len(ois) >= 2:
            oi_rel = relative_divergence(list(ois.values()))
            oi_divergent = oi_rel is not None and oi_rel > OPEN_INTEREST_DIVERGENCE_RATIO
            if oi_divergent:
                reasons.append(f"open-interest relative spread {oi_rel:.2%} exceeds {OPEN_INTEREST_DIVERGENCE_RATIO:.0%} (coarse venue sanity)")

        return CrossExchangeReport(
            symbol=symbol,
            generated_at=now,
            state=state,
            state_reason=reasons,
            sources_available=sorted(available.keys()),
            sources_unavailable=sorted(unavailable),
            exchange_errors=errors,
            price_by_exchange=_rounded(price_refs),
            volume_by_exchange=_rounded(volumes),
            funding_by_exchange=_rounded(funding),
            open_interest_by_exchange=_rounded(ois),
            liquidity_by_exchange=liquidity,
            price_divergence_pct=_optional_pct(price_div),
            volume_divergence_pct=_optional_pct(volume_div),
            funding_divergence=funding_divergent,
            open_interest_divergence=oi_divergent,
            max_price=max(price_refs.values()) if price_refs else None,
            min_price=min(price_refs.values()) if price_refs else None,
        )

    def _resolve_state(
        self,
        available: Dict[str, NormalizedMarketSnapshot],
        price_refs: Dict[str, float],
        volumes: Dict[str, float],
        reasons: List[str],
    ) -> ConfirmationState:
        if not available:
            reasons.append("no exchange source available")
            return ConfirmationState.DATA_UNAVAILABLE

        if any(snap.source_status == SourceStatus.STALE for snap in available.values()):
            reasons.append("at least one available source is stale")
            return ConfirmationState.STALE

        if not price_refs:
            reasons.append("no venue provided a usable price")
            return ConfirmationState.DATA_UNAVAILABLE

        if len(price_refs) < 2:
            reasons.append("single source only: cannot confirm agreement across exchanges")
            return ConfirmationState.PARTIAL_CONFIRMATION

        price_div = relative_divergence(list(price_refs.values()))
        if price_div is None:
            reasons.append("price divergence undeterminable")
            return ConfirmationState.PARTIAL_CONFIRMATION

        volume_divergent = False
        if len(volumes) >= 2:
            vol_div = relative_divergence(list(volumes.values()))
            if vol_div is not None and vol_div > VOLUME_DIVERGENCE_RATIO:
                volume_divergent = True
                reasons.append(f"volume divergence {vol_div:.2%} exceeds {VOLUME_DIVERGENCE_RATIO:.0%}")

        if price_div > PRICE_DIVERGENT_PCT:
            reasons.append(f"price divergence {price_div:.3%} exceeds {PRICE_DIVERGENT_PCT:.3%}")
            return ConfirmationState.DIVERGENT
        if volume_divergent:
            reasons.append("major volume/liquidity disagreement between venues")
            return ConfirmationState.DIVERGENT
        if price_div > PRICE_AGREE_PCT:
            reasons.append(f"price divergence {price_div:.3%} within partial band")
            return ConfirmationState.PARTIAL_CONFIRMATION
        if len(volumes) < 2:
            reasons.append("price agreement confirmed but volume confirmation unavailable")
            return ConfirmationState.PARTIAL_CONFIRMATION
        reasons.append(f"price divergence {price_div:.4%} within agreement band")
        return ConfirmationState.CONFIRMED


def _price_reference(snap: NormalizedMarketSnapshot) -> Optional[float]:
    if snap.last_price is not None:
        return float(snap.last_price)
    if snap.bid is not None and snap.ask is not None:
        return float((snap.bid + snap.ask) / 2)
    return None


def _report_symbol(snapshots: Dict[str, NormalizedMarketSnapshot]) -> str:
    for snap in snapshots.values():
        if snap.symbol:
            return snap.symbol
    return "UNKNOWN"


def _rounded(values: Dict[str, float], ndigits: int = 6) -> Dict[str, float]:
    return {k: round(v, ndigits) for k, v in values.items()}


def _optional_pct(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 6)