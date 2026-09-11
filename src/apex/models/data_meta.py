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
        if self.received_at_ms <= 0 or self.source_timestamp_ms <= 0:
            raise ValueError("timestamps must be positive")
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
    def is_tradeable(self) -> bool:
        return self.quality == DataQuality.GOOD and self.freshness == FreshnessStatus.FRESH


def validate_data_integrity(meta: DataIntegrity) -> bool:
    """Fail-safe: returns False if data is not tradeable."""
    return meta.is_tradeable
