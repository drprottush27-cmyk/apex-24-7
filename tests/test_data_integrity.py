import time

import pytest

from apex.models.data_meta import (
    DataIntegrity,
    DataQuality,
    FreshnessStatus,
    validate_data_integrity,
)


def _now() -> int:
    return int(time.time() * 1000)


class TestFreshness:
    def test_fresh_data(self):
        now = _now()
        meta = DataIntegrity(
            received_at_ms=now,
            source_timestamp_ms=now - 1000,
            staleness_threshold_ms=60_000,
            checksum_ok=True,
        )
        assert meta.freshness == FreshnessStatus.FRESH
        assert meta.is_tradeable is True

    def test_stale_data_detected(self):
        now = _now()
        meta = DataIntegrity(
            received_at_ms=now,
            source_timestamp_ms=now - 120_000,
            staleness_threshold_ms=60_000,
        )
        assert meta.freshness == FreshnessStatus.STALE
        assert meta.is_tradeable is False

    def test_future_source_timestamp_is_unknown(self):
        now = _now()
        meta = DataIntegrity(
            received_at_ms=now,
            source_timestamp_ms=now + 5000,
            staleness_threshold_ms=60_000,
        )
        assert meta.freshness == FreshnessStatus.UNKNOWN
        assert meta.is_tradeable is False

    def test_latency_recorded(self):
        now = _now()
        meta = DataIntegrity(
            received_at_ms=now,
            source_timestamp_ms=now - 300,
            staleness_threshold_ms=60_000,
            provider_latency_ms=300,
        )
        assert meta.provider_latency_ms == 300

    def test_invalid_timestamps_rejected(self):
        with pytest.raises(ValueError):
            DataIntegrity(received_at_ms=0, source_timestamp_ms=100)


class TestQuality:
    def test_good_quality_with_checksum(self):
        now = _now()
        meta = DataIntegrity(
            received_at_ms=now,
            source_timestamp_ms=now - 1000,
            checksum_ok=True,
        )
        assert meta.quality == DataQuality.GOOD

    def test_bad_quality_with_failed_checksum(self):
        now = _now()
        meta = DataIntegrity(
            received_at_ms=now,
            source_timestamp_ms=now - 1000,
            checksum_ok=False,
        )
        assert meta.quality == DataQuality.BAD
        assert meta.is_tradeable is False

    def test_suspect_quality_without_checksum(self):
        now = _now()
        meta = DataIntegrity(received_at_ms=now, source_timestamp_ms=now - 1000)
        assert meta.quality == DataQuality.SUSPECT
        assert meta.is_tradeable is False

    def test_stale_data_is_bad_quality(self):
        now = _now()
        meta = DataIntegrity(
            received_at_ms=now,
            source_timestamp_ms=now - 300_000,
            checksum_ok=True,
        )
        assert meta.quality == DataQuality.BAD
        assert meta.is_tradeable is False


class TestFailSafeValidation:
    def test_validate_rejects_stale(self):
        now = _now()
        meta = DataIntegrity(received_at_ms=now, source_timestamp_ms=now - 300_000)
        assert validate_data_integrity(meta) is False

    def test_validate_rejects_non_fresh(self):
        now = _now()
        meta = DataIntegrity(received_at_ms=now, source_timestamp_ms=now + 1)
        assert validate_data_integrity(meta) is False

    def test_validate_accepts_good_fresh(self):
        now = _now()
        meta = DataIntegrity(
            received_at_ms=now,
            source_timestamp_ms=now - 10,
            checksum_ok=True,
        )
        assert validate_data_integrity(meta) is True

    def test_never_tradeable_when_stale(self):
        now = _now()
        for age in (65_000, 300_000, 3_600_000):
            meta = DataIntegrity(
                received_at_ms=now,
                source_timestamp_ms=now - age,
                checksum_ok=True,
            )
            assert meta.is_tradeable is False, f"age {age} should not be tradeable"