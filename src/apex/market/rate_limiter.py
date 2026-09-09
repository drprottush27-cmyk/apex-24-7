"""APEX 24/7 — Binance Futures Request Weight Limiter.

Tracks and paces request weight against the Binance USD\u24e2-M Futures IP rate limit
(2,400 weight per minute ceiling). Ensures the system never exceeds rate limits
regardless of universe size or scan frequency.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable


class WeightLimiter:
    """Thread-safe request weight limiter for Binance USD\u24e2-M Futures.

    Tracks both client-side estimated weight and server-reported weight
    from the \x27x-mbx-used-weight-1m\x27 header.
    """

    def __init__(
        self,
        max_weight_per_minute: int = 2000,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_weight_per_minute <= 0:
            raise ValueError("max_weight_per_minute must be positive")
        self._max_weight = max_weight_per_minute
        self._clock = clock
        self._sleep = sleeper
        self._lock = threading.Lock()
        self._minute_bucket = int(self._clock()) // 60
        self._client_used = 0
        self._server_used = 0

    @property
    def max_weight(self) -> int:
        return self._max_weight

    def _roll_bucket(self, now: float) -> None:
        bucket = int(now) // 60
        if bucket != self._minute_bucket:
            self._minute_bucket = bucket
            self._client_used = 0
            self._server_used = 0

    @property
    def current_used(self) -> int:
        """Current used weight within the active 1-minute window."""
        with self._lock:
            self._roll_bucket(self._clock())
            return max(self._client_used, self._server_used)

    def update_server_weight(self, server_used: int) -> None:
        """Update limiter with exchange-reported \x27x-mbx-used-weight-1m\x27 header."""
        if server_used < 0:
            return
        with self._lock:
            self._roll_bucket(self._clock())
            self._server_used = max(self._server_used, server_used)

    def acquire(self, weight: int = 1) -> None:
        """Block until the requested weight can be spent in the current minute."""
        if weight <= 0:
            return
        while True:
            with self._lock:
                now = self._clock()
                self._roll_bucket(now)
                effective_used = max(self._client_used, self._server_used)
                if effective_used + weight <= self._max_weight:
                    self._client_used += weight
                    return
                # Must wait until the next minute boundary
                remaining = 60.0 - (now % 60.0) + 0.05
            self._sleep(max(0.01, min(remaining, 1.0)))


def estimate_request_weight(url: str) -> int:
    """Estimate Binance Futures weight cost from endpoint path."""
    if "/fapi/v1/ticker/24hr" in url and "symbol=" not in url:
        return 40
    if "/fapi/v1/depth" in url:
        return 2
    if "/fapi/v1/klines" in url:
        return 2
    return 1
