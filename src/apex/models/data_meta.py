from __future__ import annotations

import enum
from dataclasses import dataclass


class FreshnessStatus(str, enum.Enum):
    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


class DataQuality(str, enum.Enum):
    GOOD = "good"
    SUSPECT = "suspect"
    BAD = "bad"


class DataState(str, enum.Enum):
    """Explicit states for data honesty (AGENTS.md / Data Honesty contract)."""
    AVAILABLE = "available"
    DATA_UNAVAILABLE = "data_unavailable"
    DATA_STALE = "data_stale"
    PROVIDER_DISCONNECTED = "provider_disconnected"


STALE_THRESHOLD_MS_DEFAULT = 60_000  # 1 minute


@dataclass(frozen=True)
class DataIntegrity:
    received_at_ms: int
    source_timestamp_ms: int
    staleness_threshold_ms: int = STALE_THRESHOLD_MS_DEFAULT
    provider_latency_ms: int | None = None
    sequence: int | None = None
    checksum_ok: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.received_at_ms, int):
            raise TypeError("received_at_ms must be an integer")
        if not isinstance(self.source_timestamp_ms, int):
            raise TypeError("source_timestamp_ms must be an integer")
        if self.received_at_ms <= 0 or self.source_timestamp_ms <= 0:
            raise ValueError("timestamps must be positive")
        if not isinstance(self.staleness_threshold_ms, int):
            raise TypeError("staleness_threshold_ms must be an integer")
        if self.staleness_threshold_ms <= 0:
            raise ValueError("staleness_threshold_ms must be positive")

    @property
    def age_ms(self) -> int:
        return self.received_at_ms - self.source_timestamp_ms

    @property
    def freshness(self) -> FreshnessStatus:
        if self.age_ms < 0:
            return FreshnessStatus.UNKNOWN
        if self.age_ms <= self.staleness_threshold_ms:
            return FreshnessStatus.FRESH
        return FreshnessStatus.STALE

    @property
    def quality(self) -> DataQuality:
        if self.freshness == FreshnessStatus.STALE:
            return DataQuality.BAD
        if self.freshness == FreshnessStatus.UNKNOWN:
            return DataQuality.SUSPECT
        if self.checksum_ok is False:
            return DataQuality.BAD
        if self.checksum_ok is None:
            return DataQuality.SUSPECT
        return DataQuality.GOOD

    @property
    def data_state(self) -> DataState:
        """Return the explicit data state for data-honesty compliance."""
        if self.freshness == FreshnessStatus.STALE:
            return DataState.DATA_STALE
        if self.quality == DataQuality.BAD:
            return DataState.DATA_UNAVAILABLE
        return DataState.AVAILABLE

    @property
    def is_tradeable(self) -> bool:
        return self.quality == DataQuality.GOOD and self.freshness == FreshnessStatus.FRESH


def validate_data_integrity(meta: DataIntegrity) -> bool:
    """Fail-safe: returns False if data is not tradeable.

    This is the reusable integrity enforcement hook for all future
    trading consumers. Any component that consumes market data for
    trading decisions MUST call this function and reject data that
    returns False.
    """
    return meta.is_tradeable


class MarketDataIntegrityGate:
    """Reusable market-data integrity enforcement hook.

    Future trading consumers (Risk Guardian, Signal Engine, Execution)
    MUST pass data through this gate before using it for any trading
    decision. This gate is fail-closed: if any check fails, the data
    is rejected.

    This class does NOT implement trading. It provides the enforcement
    point that trading components must call.
    """

    def check(self, meta: DataIntegrity) -> tuple[bool, DataState]:
        """Check data integrity and return (is_safe, state).

        Returns:
            (True, DataState.AVAILABLE) if the data is safe for trading use.
            (False, DataState.*) if the data must be rejected.
        """
        if meta.freshness == FreshnessStatus.STALE:
            return False, DataState.DATA_STALE
        if meta.freshness == FreshnessStatus.UNKNOWN:
            return False, DataState.DATA_UNAVAILABLE
        if meta.checksum_ok is False:
            return False, DataState.DATA_UNAVAILABLE
        if meta.checksum_ok is None:
            # Fail closed: no checksum means we cannot trust the data.
            return False, DataState.DATA_UNAVAILABLE
        if meta.quality != DataQuality.GOOD:
            return False, DataState.DATA_UNAVAILABLE
        return True, DataState.AVAILABLE
