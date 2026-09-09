"""Unit tests for deterministic idempotency guard."""

import pytest

from apex.domain.types import Timeframe
from apex.safety.exceptions import DuplicateEventError
from apex.safety.idempotency import IdempotencyGuard


class TestIdempotencyGuard:
    """Test suite verifying deterministic event deduplication."""

    def test_deterministic_key_generation(self) -> None:
        key1 = IdempotencyGuard.compute_event_key(
            symbol="BTCUSDT",
            timeframe=Timeframe.M5,
            candle_timestamp_ms=1700000000000,
            detector_version="v1.0.0",
        )
        key2 = IdempotencyGuard.compute_event_key(
            symbol="BTCUSDT",
            timeframe=Timeframe.M5,
            candle_timestamp_ms=1700000000000,
            detector_version="v1.0.0",
        )

        assert key1 == key2
        assert len(key1) == 64  # SHA-256 hex string

    def test_different_candle_timestamp_produces_different_key(self) -> None:
        key1 = IdempotencyGuard.compute_event_key(
            symbol="BTCUSDT",
            timeframe=Timeframe.M5,
            candle_timestamp_ms=1700000000000,
            detector_version="v1.0.0",
        )
        key2 = IdempotencyGuard.compute_event_key(
            symbol="BTCUSDT",
            timeframe=Timeframe.M5,
            candle_timestamp_ms=1700000300000,  # next 5m candle
            detector_version="v1.0.0",
        )
        assert key1 != key2

    def test_different_detector_version_produces_different_key(self) -> None:
        key1 = IdempotencyGuard.compute_event_key(
            symbol="BTCUSDT",
            timeframe=Timeframe.M5,
            candle_timestamp_ms=1700000000000,
            detector_version="v1.0.0",
        )
        key2 = IdempotencyGuard.compute_event_key(
            symbol="BTCUSDT",
            timeframe=Timeframe.M5,
            candle_timestamp_ms=1700000000000,
            detector_version="v1.1.0",
        )
        assert key1 != key2

    def test_different_symbol_and_timeframe_produce_different_keys(self) -> None:
        key_btc = IdempotencyGuard.compute_event_key("BTCUSDT", Timeframe.M5, 1700000000000, "v1.0")
        key_eth = IdempotencyGuard.compute_event_key("ETHUSDT", Timeframe.M5, 1700000000000, "v1.0")
        key_m15 = IdempotencyGuard.compute_event_key(
            "BTCUSDT", Timeframe.M15, 1700000000000, "v1.0"
        )

        assert key_btc != key_eth
        assert key_btc != key_m15

    def test_duplicate_registration_and_rejection(self) -> None:
        guard = IdempotencyGuard()
        key = guard.compute_event_key("BTCUSDT", Timeframe.M5, 1700000000000, "v1.0")

        # First recording should succeed
        assert guard.is_duplicate(key) is False
        assert guard.record_event(key) is True
        assert guard.is_duplicate(key) is True

        # Replay should be recognized as duplicate
        assert guard.record_event(key) is False

        # ensure_unique should raise DuplicateEventError
        with pytest.raises(DuplicateEventError, match="Duplicate trade event detected"):
            guard.ensure_unique(key)
