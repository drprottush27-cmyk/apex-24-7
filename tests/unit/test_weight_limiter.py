"""Tests for Binance Futures Request WeightLimiter and Transport integration."""
from __future__ import annotations

import threading

from apex.market.rate_limiter import WeightLimiter, estimate_request_weight
from apex.market.transport import ResilientHTTPTransport


class MockClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class MockSleeper:
    def __init__(self, clock: MockClock) -> None:
        self.clock = clock
        self.total_slept = 0.0

    def __call__(self, seconds: float) -> None:
        self.total_slept += seconds
        self.clock.advance(seconds)


def test_weight_limiter_basic_acquire() -> None:
    clock = MockClock(1000.0)
    sleeper = MockSleeper(clock)
    limiter = WeightLimiter(max_weight_per_minute=100, clock=clock, sleeper=sleeper)

    limiter.acquire(20)
    assert limiter.current_used == 20
    limiter.acquire(30)
    assert limiter.current_used == 50
    assert sleeper.total_slept == 0.0


def test_weight_limiter_throttles_when_budget_exceeded() -> None:
    # 1020s is in minute 17 (1020 // 60 = 17)
    clock = MockClock(1020.0)
    sleeper = MockSleeper(clock)
    limiter = WeightLimiter(max_weight_per_minute=50, clock=clock, sleeper=sleeper)

    limiter.acquire(40)
    assert limiter.current_used == 40

    # Acquire 20 more -> 40 + 20 = 60 > 50 -> must throttle until minute 18 (1080.0)
    limiter.acquire(20)
    assert sleeper.total_slept >= 60.0 - (1020.0 % 60.0)
    assert limiter.current_used == 20


def test_weight_limiter_server_weight_sync() -> None:
    clock = MockClock(1000.0)
    sleeper = MockSleeper(clock)
    limiter = WeightLimiter(max_weight_per_minute=100, clock=clock, sleeper=sleeper)

    limiter.acquire(10)
    assert limiter.current_used == 10

    # Server reports higher used weight from header
    limiter.update_server_weight(85)
    assert limiter.current_used == 85

    # Requesting 20 more triggers throttle because 85 + 20 > 100
    limiter.acquire(20)
    assert sleeper.total_slept > 0.0
    assert limiter.current_used == 20


def test_estimate_request_weight() -> None:
    assert estimate_request_weight("https://fapi.binance.com/fapi/v1/ticker/24hr") == 40
    assert estimate_request_weight("https://fapi.binance.com/fapi/v1/ticker/24hr?symbol=BTCUSDT") == 1
    assert estimate_request_weight("https://fapi.binance.com/fapi/v1/depth?symbol=BTCUSDT") == 2
    assert estimate_request_weight("https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT") == 2
    assert estimate_request_weight("https://fapi.binance.com/fapi/v1/exchangeInfo") == 1


def test_weight_limiter_thread_safety() -> None:
    clock = MockClock(1000.0)
    sleeper = MockSleeper(clock)
    limiter = WeightLimiter(max_weight_per_minute=10_000, clock=clock, sleeper=sleeper)

    def worker() -> None:
        for _ in range(50):
            limiter.acquire(2)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert limiter.current_used == 1000


def test_transport_integrates_weight_limiter() -> None:
    clock = MockClock(1000.0)
    sleeper = MockSleeper(clock)
    limiter = WeightLimiter(max_weight_per_minute=100, clock=clock, sleeper=sleeper)

    transport = ResilientHTTPTransport(sleeper=sleeper, weight_limiter=limiter)
    assert transport.weight_limiter is limiter
