"""APEX 24/7 — Data Quality Gate.

Deterministic validation of candle sequences for:
- Freshness
- Timestamp continuity
- Duplicate detection
- Out-of-order detection
- OHLC validity
- Volume validity
- NaN/Inf rejection
- Missing interval detection

When data is unsafe, the gate fails closed rather than fabricating data.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from apex.domain.candles import Candle
from apex.domain.types import Timeframe

# ── Constants ─────────────────────────────────────────────────────────────────

DEFAULT_STALE_THRESHOLD_MS: int = 3_600_000  # 1 hour
H1_INTERVAL_MS: int = 3_600_000  # 1 hour in milliseconds

_MS_PER_SECOND: int = 1_000

INTERVAL_MS_BY_TIMEFRAME: dict[Timeframe, int] = {
    Timeframe.M1: 60 * _MS_PER_SECOND,
    Timeframe.M5: 300 * _MS_PER_SECOND,
    Timeframe.M15: 900 * _MS_PER_SECOND,
    Timeframe.H1: 3_600 * _MS_PER_SECOND,
    Timeframe.H4: 14_400 * _MS_PER_SECOND,
    Timeframe.D1: 86_400 * _MS_PER_SECOND,
}


def timeframe_interval_ms(timeframe: Timeframe) -> int:
    """Return the candle interval for a timeframe in milliseconds."""
    return INTERVAL_MS_BY_TIMEFRAME[timeframe]


# ── Quality Check Result ──────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class QualityCheckResult:
    """Result of a data quality gate check."""

    is_valid: bool
    errors: tuple[str, ...]


# ── Individual Quality Checks ─────────────────────────────────────────────────

def check_timestamp_continuity(candles: list[Candle]) -> list[str]:
    """Verify timestamps are strictly increasing with no duplicates.

    Returns a list of error messages (empty if valid).
    """
    errors: list[str] = []
    if not candles:
        return errors

    prev_open = candles[0].open_time_ms
    for i in range(1, len(candles)):
        curr_open = candles[i].open_time_ms
        if curr_open <= prev_open:
            if curr_open == prev_open:
                errors.append(
                    f"Duplicate timestamp at index {i}: {curr_open}"
                )
            else:
                errors.append(
                    f"Out-of-order timestamp at index {i}: "
                    f"{curr_open} <= {prev_open}"
                )
        prev_open = curr_open

    return errors


def check_duplicate_timestamps(candles: list[Candle]) -> list[str]:
    """Check for exact duplicate timestamps.

    Returns a list of error messages (empty if valid).
    """
    errors: list[str] = []
    seen: set[int] = set()
    for i, c in enumerate(candles):
        if c.open_time_ms in seen:
            errors.append(f"Duplicate timestamp at index {i}: {c.open_time_ms}")
        seen.add(c.open_time_ms)
    return errors


def check_ohlc_validity(candles: list[Candle]) -> list[str]:
    """Verify OHLC price relationships for each candle.

    Enforces:
    - high >= low
    - high >= open, high >= close
    - low <= open, low <= close

    Returns a list of error messages (empty if valid).
    """
    errors: list[str] = []
    for i, c in enumerate(candles):
        if c.high < c.low:
            errors.append(
                f"Candle {i}: high ({c.high}) < low ({c.low})"
            )
        if c.high < c.open:
            errors.append(
                f"Candle {i}: high ({c.high}) < open ({c.open})"
            )
        if c.high < c.close:
            errors.append(
                f"Candle {i}: high ({c.high}) < close ({c.close})"
            )
        if c.low > c.open:
            errors.append(
                f"Candle {i}: low ({c.low}) > open ({c.open})"
            )
        if c.low > c.close:
            errors.append(
                f"Candle {i}: low ({c.low}) > close ({c.close})"
            )
    return errors


def check_volume_validity(candles: list[Candle]) -> list[str]:
    """Verify all volumes are non-negative and finite.

    Returns a list of error messages (empty if valid).
    """
    errors: list[str] = []
    for i, c in enumerate(candles):
        if not math.isfinite(c.volume):
            errors.append(f"Candle {i}: volume is not finite ({c.volume})")
        if c.volume < 0:
            errors.append(f"Candle {i}: volume is negative ({c.volume})")
    return errors


def check_nan_inf(candles: list[Candle]) -> list[str]:
    """Check for NaN or Inf in any numeric candle field.

    Returns a list of error messages (empty if valid).
    """
    errors: list[str] = []
    for i, c in enumerate(candles):
        for field_name in ("open", "high", "low", "close", "volume"):
            val = getattr(c, field_name)
            if not math.isfinite(val):
                errors.append(
                    f"Candle {i}: {field_name} is not finite ({val})"
                )
    return errors


def check_all_closed(candles: list[Candle]) -> list[str]:
    """Verify all candles are closed.

    Returns a list of error messages (empty if valid).
    """
    errors: list[str] = []
    for i, c in enumerate(candles):
        if not c.is_closed:
            errors.append(f"Candle {i}: is_closed is False")
    return errors


def check_freshness(
    candles: list[Candle],
    threshold_ms: int = DEFAULT_STALE_THRESHOLD_MS,
    *,
    now_ms: int | None = None,
) -> list[str]:
    """Verify the latest candle is not stale.

    A candle is stale if its close_time is older than threshold_ms ago.
    `now_ms` makes the check fully deterministic without a wall clock; it
    defaults to the current time for callers without an injected clock.

    Returns a list of error messages (empty if valid).
    """
    errors: list[str] = []
    if not candles:
        return errors

    clock_now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    latest = candles[-1]
    age_ms = clock_now_ms - latest.close_time_ms

    if age_ms > threshold_ms:
        errors.append(
            f"Latest candle is stale: age {age_ms}ms > threshold {threshold_ms}ms"
        )

    return errors


def check_missing_intervals(
    candles: list[Candle],
    expected_interval_ms: int = H1_INTERVAL_MS,
) -> list[str]:
    """Detect missing intervals between consecutive candles.

    Returns a list of error messages (empty if valid).
    """
    errors: list[str] = []
    if len(candles) < 2:
        return errors

    for i in range(1, len(candles)):
        gap = candles[i].open_time_ms - candles[i - 1].open_time_ms
        if gap > expected_interval_ms:
            errors.append(
                f"Missing interval at index {i}: gap {gap}ms > expected {expected_interval_ms}ms"
            )

    return errors


def check_price_positivity(candles: list[Candle]) -> list[str]:
    """Verify all OHLC prices are strictly positive.

    Returns a list of error messages (empty if valid).
    """
    errors: list[str] = []
    for i, c in enumerate(candles):
        for field_name in ("open", "high", "low", "close"):
            val = getattr(c, field_name)
            if val <= 0:
                errors.append(
                    f"Candle {i}: {field_name} must be positive, got {val}"
                )
    return errors


# ── Full Quality Gate ─────────────────────────────────────────────────────────

def run_quality_gate(
    candles: list[Candle],
    *,
    check_fresh: bool = True,
    stale_threshold_ms: int = DEFAULT_STALE_THRESHOLD_MS,
    expected_interval_ms: int = H1_INTERVAL_MS,
    now_ms: int | None = None,
) -> QualityCheckResult:
    """Run the full data quality gate on a sequence of candles.

    Performs all quality checks and returns a comprehensive result.
    Fails closed: any error makes the entire sequence invalid.

    Args:
        candles: List of Candle objects to validate.
        check_fresh: Whether to check freshness of latest candle.
        stale_threshold_ms: Maximum age for latest candle in milliseconds.
        expected_interval_ms: Expected interval between candles in milliseconds.
        now_ms: Deterministic "now" for the freshness check (defaults to the
            wall clock when not provided).

    Returns:
        QualityCheckResult with is_valid flag and all error messages.
    """
    all_errors: list[str] = []

    # Run all checks
    all_errors.extend(check_all_closed(candles))
    all_errors.extend(check_nan_inf(candles))
    all_errors.extend(check_price_positivity(candles))
    all_errors.extend(check_ohlc_validity(candles))
    all_errors.extend(check_volume_validity(candles))
    all_errors.extend(check_timestamp_continuity(candles))
    all_errors.extend(check_duplicate_timestamps(candles))
    all_errors.extend(check_missing_intervals(candles, expected_interval_ms))

    if check_fresh:
        all_errors.extend(check_freshness(candles, stale_threshold_ms, now_ms=now_ms))

    return QualityCheckResult(
        is_valid=len(all_errors) == 0,
        errors=tuple(all_errors),
    )
