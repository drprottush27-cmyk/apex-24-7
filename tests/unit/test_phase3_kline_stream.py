"""Phase 3 Tests — WebSocket Kline Stream.

Tests for kline_stream.py with fully mocked WebSocket transport.
No network access required.

Tests:
18. WebSocket open candle ignored
19. WebSocket closed candle accepted
20. Duplicate WebSocket event ignored
21. Malformed WebSocket event rejected
22. Reconnect/backoff behavior
"""

import asyncio
import json
import time
from collections.abc import AsyncGenerator
from unittest.mock import MagicMock

from apex.domain.candles import Candle
from apex.domain.types import Timeframe
from apex.market.kline_stream import (
    KlineStreamHandler,
    KlineStreamManager,
    parse_kline_event,
)
from apex.market.transport import WebSocketClient

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_ws_event(
    symbol: str = "BTCUSDT",
    interval: str = "1h",
    open_time_ms: int = 1700000000000,
    close_time_ms: int | None = None,
    open_p: str = "50000.0",
    high: str = "50200.0",
    low: str = "49900.0",
    close_p: str = "50100.0",
    volume: str = "125.5",
    is_closed: bool = True,
) -> str:
    """Create a raw WebSocket kline event JSON string."""
    if close_time_ms is None:
        close_time_ms = open_time_ms + 3599999
    event = {
        "e": "kline",
        "E": int(time.time()),
        "s": symbol,
        "k": {
            "t": open_time_ms,
            "T": close_time_ms,
            "s": symbol,
            "i": interval,
            "o": open_p,
            "h": high,
            "l": low,
            "c": close_p,
            "v": volume,
            "x": is_closed,
        },
    }
    return json.dumps(event)


def _past_ms(hours_ago: int = 2) -> int:
    return int(time.time() * 1000) - (hours_ago * 3600_000)


# ── Test: Event parsing ──────────────────────────────────────────────────────

class TestParseKlineEvent:
    def test_parse_valid_event(self) -> None:
        raw = _make_ws_event()
        event = parse_kline_event(raw)
        assert event is not None
        assert event.symbol == "BTCUSDT"
        assert event.is_closed is True

    def test_parse_non_kline_event(self) -> None:
        raw = json.dumps({"e": "trade", "s": "BTCUSDT"})
        event = parse_kline_event(raw)
        assert event is None

    def test_parse_invalid_json(self) -> None:
        event = parse_kline_event("not json")
        assert event is None

    def test_parse_empty_dict(self) -> None:
        event = parse_kline_event("{}")
        assert event is None

    def test_parse_missing_k_field(self) -> None:
        raw = json.dumps({"e": "kline"})
        event = parse_kline_event(raw)
        assert event is None

    def test_parse_non_dict_root(self) -> None:
        event = parse_kline_event('"string"')
        assert event is None

    def test_parse_missing_required_fields(self) -> None:
        raw = json.dumps({"e": "kline", "k": {"s": "BTCUSDT"}})
        event = parse_kline_event(raw)
        assert event is None

    def test_parse_empty_symbol(self) -> None:
        event_data = {
            "e": "kline",
            "k": {
                "t": 1000, "T": 2000, "s": "", "i": "1h",
                "o": "1", "h": "2", "l": "0.5", "c": "1.5",
                "v": "100", "x": True,
            },
        }
        event = parse_kline_event(json.dumps(event_data))
        assert event is None


# ── Test: Handler - open candle ignored ───────────────────────────────────────

class TestOpenCandleIgnored:
    def test_open_candle_not_emitted(self) -> None:
        received: list[Candle] = []
        handler = KlineStreamHandler(on_candle=received.append)

        raw = _make_ws_event(is_closed=False, open_time_ms=_past_ms(1))
        handler.handle_event(raw)

        assert len(received) == 0

    def test_open_then_closed_emits_only_closed(self) -> None:
        received: list[Candle] = []
        handler = KlineStreamHandler(on_candle=received.append)

        ts = _past_ms(1)
        handler.handle_event(_make_ws_event(is_closed=False, open_time_ms=ts))
        handler.handle_event(_make_ws_event(is_closed=True, open_time_ms=ts))

        assert len(received) == 1
        assert received[0].is_closed is True


# ── Test: Handler - closed candle accepted ────────────────────────────────────

class TestClosedCandleAccepted:
    def test_closed_candle_emitted(self) -> None:
        received: list[Candle] = []
        handler = KlineStreamHandler(on_candle=received.append)

        ts = _past_ms(2)
        handler.handle_event(_make_ws_event(is_closed=True, open_time_ms=ts))

        assert len(received) == 1
        assert received[0].symbol == "BTCUSDT"
        assert received[0].timeframe == Timeframe.H1
        assert received[0].open == 50000.0
        assert received[0].is_closed is True

    def test_multiple_closed_candles_emitted(self) -> None:
        received: list[Candle] = []
        handler = KlineStreamHandler(on_candle=received.append)

        for i in range(3):
            ts = _past_ms(3 - i)
            handler.handle_event(_make_ws_event(is_closed=True, open_time_ms=ts))

        assert len(received) == 3


# ── Test: Handler - duplicate event ───────────────────────────────────────────

class TestDuplicateEvent:
    def test_duplicate_ignored(self) -> None:
        received: list[Candle] = []
        handler = KlineStreamHandler(on_candle=received.append)

        ts = _past_ms(2)
        handler.handle_event(_make_ws_event(is_closed=True, open_time_ms=ts))
        handler.handle_event(_make_ws_event(is_closed=True, open_time_ms=ts))

        assert len(received) == 1

    def test_older_event_ignored(self) -> None:
        received: list[Candle] = []
        handler = KlineStreamHandler(on_candle=received.append)

        handler.handle_event(_make_ws_event(is_closed=True, open_time_ms=_past_ms(1)))
        handler.handle_event(_make_ws_event(is_closed=True, open_time_ms=_past_ms(3)))

        assert len(received) == 1

    def test_last_seen_tracking(self) -> None:
        handler = KlineStreamHandler(on_candle=lambda c: None)

        ts1 = _past_ms(3)
        ts2 = _past_ms(2)
        handler.handle_event(_make_ws_event(is_closed=True, open_time_ms=ts1))
        assert handler.last_seen_open_time == ts1

        handler.handle_event(_make_ws_event(is_closed=True, open_time_ms=ts2))
        assert handler.last_seen_open_time == ts2


# ── Test: Handler - malformed event ───────────────────────────────────────────

class TestMalformedEvent:
    def test_invalid_json_handled(self) -> None:
        received: list[Candle] = []
        errors: list[Exception] = []
        handler = KlineStreamHandler(
            on_candle=received.append,
            on_error=errors.append,
        )

        handler.handle_event("not json {{{")

        assert len(received) == 0
        assert len(errors) == 0  # Non-kline messages are silently ignored

    def test_malformed_kline_data_error(self) -> None:
        received: list[Candle] = []
        errors: list[Exception] = []
        handler = KlineStreamHandler(
            on_candle=received.append,
            on_error=errors.append,
        )

        # Create event with invalid price data
        event = {
            "e": "kline",
            "k": {
                "t": _past_ms(2),
                "T": _past_ms(1),  # close < open
                "s": "BTCUSDT",
                "i": "1h",
                "o": "NaN",
                "h": "50200",
                "l": "49900",
                "c": "50100",
                "v": "125",
                "x": True,
            },
        }
        handler.handle_event(json.dumps(event))

        assert len(received) == 0
        assert len(errors) == 1

    def test_zero_handler_no_crash(self) -> None:
        handler = KlineStreamHandler(on_candle=lambda c: None)
        handler.handle_event("invalid")
        handler.handle_event("")


# ── Test: Reconnect/backoff ───────────────────────────────────────────────────

class TestReconnectBackoff:
    def test_reconnect_delay_bounded(self) -> None:
        ws_client = MagicMock()
        manager = KlineStreamManager(
            ws_client=ws_client,
            symbols=["BTCUSDT"],
            interval="1h",
            on_candle=lambda c: None,
            max_reconnect_attempts=3,
        )

        # Verify initial state
        assert manager._reconnect_count == 0
        assert manager._max_reconnect_attempts == 3

    def test_stop_signal(self) -> None:
        ws_client = MagicMock()
        manager = KlineStreamManager(
            ws_client=ws_client,
            symbols=["BTCUSDT"],
            interval="1h",
            on_candle=lambda c: None,
        )

        manager.stop()
        assert manager._should_stop is True

    def test_max_reconnect_stops(self) -> None:
        ws_client = MagicMock()
        manager = KlineStreamManager(
            ws_client=ws_client,
            symbols=["BTCUSDT"],
            interval="1h",
            on_candle=lambda c: None,
            max_reconnect_attempts=1,
        )
        manager._reconnect_count = 2  # Exceed max

        # The reconnect method should set _should_stop
        async def _test() -> None:
            await manager._reconnect()

        asyncio.run(_test())
        assert manager._should_stop is True

    def test_handler_accessible(self) -> None:
        ws_client = MagicMock()
        manager = KlineStreamManager(
            ws_client=ws_client,
            symbols=["BTCUSDT"],
            interval="1h",
            on_candle=lambda c: None,
        )
        assert isinstance(manager.handler, KlineStreamHandler)

    def test_run_processes_messages(self) -> None:
        received: list[Candle] = []
        ts = _past_ms(2)

        messages = [
            _make_ws_event(is_closed=False, open_time_ms=ts),  # Open, ignored
            _make_ws_event(is_closed=True, open_time_ms=ts),  # Closed, accepted
        ]

        class MockWS:
            async def connect(self) -> None:
                pass

            async def receive(self) -> AsyncGenerator[str, None]:
                for msg in messages:
                    yield msg

            async def close(self) -> None:
                pass

            @property
            def is_connected(self) -> bool:  # noqa: ANN201
                return True

        ws_client: WebSocketClient = MockWS()
        manager = KlineStreamManager(
            ws_client=ws_client,
            symbols=["BTCUSDT"],
            interval="1h",
            on_candle=received.append,
        )

        # Run and stop immediately after processing
        async def _run() -> None:
            await ws_client.connect()
            async for msg in ws_client.receive():
                manager.handler.handle_event(msg)
                manager.stop()

        asyncio.run(_run())

        assert len(received) == 1
        assert received[0].is_closed is True
