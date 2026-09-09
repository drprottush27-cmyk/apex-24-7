"""APEX 24/7 — WebSocket Kline Stream Foundation.

Read-only Binance kline stream with:
- Closed-candle-only processing
- Deduplication
- Rejects malformed events
- Reconnection with bounded backoff
- Never emits unsafe candles
- Never generates trading orders
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

from apex.domain.candles import Candle
from apex.market.normalize import normalize_kline
from apex.market.transport import WebSocketClient
from apex.safety.exceptions import (
    InvalidNumericalDataError,
    UnclosedCandleError,
)

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_MAX_RECONNECT_DELAY_S: float = 60.0
_INITIAL_RECONNECT_DELAY_S: float = 1.0
_BACKOFF_MULTIPLIER: float = 2.0
_MAX_RECONNECT_ATTEMPTS: int = 10


# ── Kline Event Data ──────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class KlineEvent:
    """Parsed WebSocket kline event."""

    symbol: str
    interval: str
    open_time_ms: int
    close_time_ms: int
    open: str
    high: str
    low: str
    close: str
    volume: str
    is_closed: bool


# ── Raw Event Parsing ─────────────────────────────────────────────────────────

def parse_kline_event(raw: str) -> KlineEvent | None:
    """Parse a raw WebSocket text message into a KlineEvent.

    Returns None if the message is not a valid kline event.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(data, dict):
        return None

    event = data.get("e")
    if event != "kline":
        return None

    k = data.get("k")
    if not isinstance(k, dict):
        return None

    try:
        symbol = str(k.get("s", ""))
        interval = str(k.get("i", ""))
        open_time = int(k["t"])
        close_time = int(k["T"])
        open_p = str(k["o"])
        high_p = str(k["h"])
        low_p = str(k["l"])
        close_p = str(k["c"])
        vol = str(k["v"])
        is_closed = bool(k["x"])
    except (KeyError, TypeError, ValueError):
        return None

    if not symbol or not interval:
        return None

    return KlineEvent(
        symbol=symbol,
        interval=interval,
        open_time_ms=open_time,
        close_time_ms=close_time,
        open=open_p,
        high=high_p,
        low=low_p,
        close=close_p,
        volume=vol,
        is_closed=is_closed,
    )


# ── Kline Stream Handler ─────────────────────────────────────────────────────

class KlineStreamHandler:
    """Processes WebSocket kline events into validated domain Candles.

    Only emits closed candles. Rejects malformed, open, or duplicate events.
    """

    def __init__(
        self,
        on_candle: Callable[[Candle], None],
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self._on_candle = on_candle
        self._on_error = on_error
        self._last_seen_open_time: int = -1
        self._seen_symbols: set[str] = set()

    @property
    def last_seen_open_time(self) -> int:
        return self._last_seen_open_time

    def handle_event(self, raw: str) -> None:
        """Process a raw WebSocket message.

        Only closed candles are processed and emitted.
        Open candles are silently ignored.
        Malformed events are rejected with on_error callback.

        Args:
            raw: Raw text message from WebSocket.
        """
        event = parse_kline_event(raw)
        if event is None:
            return

        # Skip open candles — closed-candle-only invariant
        if not event.is_closed:
            return

        # Deduplicate — reject if we've already seen this candle
        if event.open_time_ms <= self._last_seen_open_time:
            return

        # Normalize to domain Candle (fail-closed)
        try:
            candle = normalize_kline(
                raw_kline=[
                    event.open_time_ms,
                    event.open,
                    event.high,
                    event.low,
                    event.close,
                    event.volume,
                    event.close_time_ms,
                    "0",  # quote_volume (not needed for domain)
                    0,    # trades (not needed for domain)
                    "0",  # taker_buy_volume (not needed)
                    "0",  # taker_buy_quote_volume (not needed)
                ],
                symbol=event.symbol,
                interval=event.interval,
                reject_open=True,
            )
        except (InvalidNumericalDataError, UnclosedCandleError) as exc:
            if self._on_error is not None:
                self._on_error(exc)
            return

        # Update tracking state
        self._last_seen_open_time = event.open_time_ms
        self._seen_symbols.add(event.symbol)

        # Emit validated candle
        self._on_candle(candle)


# ── WebSocket Stream Manager ──────────────────────────────────────────────────

class KlineStreamManager:
    """Manages a WebSocket kline stream with reconnection and backoff.

    Handles:
    - Connection lifecycle
    - Reconnection after disconnect
    - Bounded exponential backoff
    - Error propagation to handler
    """

    def __init__(
        self,
        ws_client: WebSocketClient,
        symbols: list[str],
        interval: str,
        on_candle: Callable[[Candle], None],
        on_error: Callable[[Exception], None] | None = None,
        max_reconnect_attempts: int = _MAX_RECONNECT_ATTEMPTS,
    ) -> None:
        self._ws_client = ws_client
        self._symbols = [s.upper() for s in symbols]
        self._interval = interval
        self._handler = KlineStreamHandler(
            on_candle=on_candle,
            on_error=on_error,
        )
        self._max_reconnect_attempts = max_reconnect_attempts
        self._should_stop = False
        self._reconnect_count = 0

    @property
    def handler(self) -> KlineStreamHandler:
        return self._handler

    def stop(self) -> None:
        """Signal the stream to stop."""
        self._should_stop = True

    async def run(self) -> None:
        """Run the WebSocket stream with reconnection logic.

        Connects to Binance WebSocket, processes kline events,
        and reconnects on disconnect with bounded exponential backoff.
        """
        self._should_stop = False
        self._reconnect_count = 0

        while not self._should_stop:
            try:
                await self._ws_client.connect()
                self._reconnect_count = 0

                async for message in self._ws_client.receive():
                    if self._should_stop:
                        break
                    self._handler.handle_event(message)

                if self._should_stop:
                    break

                # Connection closed normally, attempt reconnect
                await self._reconnect()

            except Exception as exc:
                if self._handler._on_error is not None:
                    self._handler._on_error(exc)
                if self._should_stop:
                    break
                await self._reconnect()

        await self._safe_close()

    async def _reconnect(self) -> None:
        """Attempt reconnection with bounded exponential backoff."""
        self._reconnect_count += 1

        if self._reconnect_count > self._max_reconnect_attempts:
            logger.error(
                "Max reconnect attempts (%d) reached. Stopping.",
                self._max_reconnect_attempts,
            )
            self._should_stop = True
            return

        delay = min(
            _INITIAL_RECONNECT_DELAY_S * (_BACKOFF_MULTIPLIER ** (self._reconnect_count - 1)),
            _MAX_RECONNECT_DELAY_S,
        )
        logger.info(
            "Reconnecting in %.1fs (attempt %d/%d)",
            delay,
            self._reconnect_count,
            self._max_reconnect_attempts,
        )
        await self._async_sleep(delay)

    async def _safe_close(self) -> None:
        """Safely close the WebSocket connection."""
        import contextlib
        with contextlib.suppress(Exception):
            await self._ws_client.close()

    @staticmethod
    async def _async_sleep(seconds: float) -> None:
        """Async sleep for backoff delay."""
        import asyncio
        await asyncio.sleep(seconds)
