"""APEX 24/7 — Market Data Transport Protocols.

Abstract transport interfaces for HTTP and WebSocket market data.
Network code is isolated behind these protocols to enable full test mocking.
"""

from __future__ import annotations

import contextlib
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from apex.market.rate_limiter import WeightLimiter, estimate_request_weight


@dataclass(frozen=True, slots=True)
class HTTPRequest:
    """Immutable HTTP request descriptor."""

    method: str
    url: str
    params: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HTTPResponse:
    """Immutable HTTP response descriptor."""

    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class HTTPTransport(Protocol):
    """Protocol for synchronous HTTP request execution.

    Implementations must be deterministic and stateless.
    """

    def request(self, req: HTTPRequest) -> HTTPResponse:
        """Execute an HTTP request and return the response.

        Raises RuntimeError on network failure.
        """
        ...


class ResilientHTTPTransport(HTTPTransport):
    """Synchronous read-only HTTP transport with rate-limit resilience.

    Safety invariants:
    - Public GET only. Any state-mutating method (POST, PUT, DELETE, PATCH) is refused.
    - No credentials or authentication material handled or transmitted.
    - Explicit request timeout.
    - Synchronous bounded retry / backoff on rate limits (HTTP 429) and transient errors.
    - Respects 'Retry-After' header when supplied, bounded by max_backoff_s.
    - Handles HTTP 418 (IP ban) fail-closed without aggressive retry loops.
    - Enforces maximum retry count; never retries indefinitely.
    - Surfaces fatal failures as RuntimeError so callers trigger degraded health.
    - No background threads, no asyncio, no second scheduler.
    """

    def __init__(
        self,
        timeout_s: float = 10.0,
        max_retries: int = 2,
        base_backoff_s: float = 0.5,
        max_backoff_s: float = 5.0,
        sleeper: Callable[[float], None] = time.sleep,
        weight_limiter: WeightLimiter | None = None,
    ) -> None:
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._base_backoff_s = base_backoff_s
        self._max_backoff_s = max_backoff_s
        self._sleep = sleeper
        self._limiter = weight_limiter if weight_limiter is not None else WeightLimiter()

    @property
    def timeout_s(self) -> float:
        return self._timeout_s

    @property
    def max_retries(self) -> int:
        return self._max_retries

    @property
    def weight_limiter(self) -> WeightLimiter | None:
        return self._limiter

    def _parse_retry_after(self, headers: dict[str, str]) -> float | None:
        raw = headers.get("retry-after")
        if raw is None:
            return None
        try:
            val = float(raw.strip())
            return val if val >= 0.0 else None
        except (ValueError, TypeError):
            return None

    def request(self, req: HTTPRequest) -> HTTPResponse:
        if req.method.upper() != "GET":
            raise AssertionError(
                f"Read-only transport refuses method {req.method!r}: "
                "market-data clients may only GET public endpoints."
            )

        url = req.url
        if req.params:
            url = f"{url}?{urllib.parse.urlencode(req.params)}"

        if self._limiter is not None:
            cost = estimate_request_weight(url)
            self._limiter.acquire(cost)

        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                with urllib.request.urlopen(url, timeout=self._timeout_s) as resp:  # noqa: S310
                    status = int(resp.status)
                    body = bytes(resp.read())
                    headers = {str(k).lower(): str(v) for k, v in resp.headers.items()}
                    if self._limiter is not None and "x-mbx-used-weight-1m" in headers:
                        with contextlib.suppress(ValueError, TypeError):
                            self._limiter.update_server_weight(int(headers["x-mbx-used-weight-1m"]))
                    return HTTPResponse(status=status, body=body, headers=headers)
            except urllib.error.HTTPError as exc:
                last_error = exc
                status = int(exc.code)
                headers = {str(k).lower(): str(v) for k, v in exc.headers.items()} if exc.headers else {}
                if self._limiter is not None and "x-mbx-used-weight-1m" in headers:
                    with contextlib.suppress(ValueError, TypeError):
                        self._limiter.update_server_weight(int(headers["x-mbx-used-weight-1m"]))

                # HTTP 418: Exchange IP ban. Do not loop aggressively.
                # Fail closed immediately unless a bounded Retry-After is explicitly given.
                if status == 418:
                    retry_after = self._parse_retry_after(headers)
                    if (
                        retry_after is not None
                        and retry_after <= self._max_backoff_s
                        and attempt < self._max_retries
                    ):
                        self._sleep(retry_after)
                        continue
                    raise RuntimeError(
                        f"HTTP 418 IP ban received from exchange ({url}): {exc}"
                    ) from exc

                # HTTP 429: Rate limit exceeded.
                if status == 429:
                    if attempt >= self._max_retries:
                        raise RuntimeError(
                            f"HTTP 429 rate limit exceeded on {url} (retries exhausted): {exc}"
                        ) from exc
                    retry_after = self._parse_retry_after(headers)
                    backoff = (
                        retry_after
                        if retry_after is not None
                        else (self._base_backoff_s * (2**attempt))
                    )
                    backoff = min(backoff, self._max_backoff_s)
                    self._sleep(backoff)
                    continue

                # Non-retryable 4xx client errors (400, 403, 404, etc.)
                if 400 <= status < 500:
                    raise RuntimeError(
                        f"HTTP {status} client error from {url}: {exc}"
                    ) from exc

                # 5xx server errors: retry if attempts remain
                if attempt < self._max_retries:
                    backoff = min(self._base_backoff_s * (2**attempt), self._max_backoff_s)
                    self._sleep(backoff)
                    continue
                raise RuntimeError(
                    f"HTTP {status} server error from {url} after {attempt + 1} attempts: {exc}"
                ) from exc

            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt < self._max_retries:
                    backoff = min(self._base_backoff_s * (2**attempt), self._max_backoff_s)
                    self._sleep(backoff)
                    continue
                raise RuntimeError(
                    f"Network transport failure for {url} after {attempt + 1} attempts: {exc}"
                ) from exc

        raise RuntimeError(
            f"Request failed for {url} after {self._max_retries + 1} attempts: {last_error}"
        )


@runtime_checkable
class WebSocketClient(Protocol):
    """Protocol for WebSocket client transport.

    Implementations must provide an async generator of text messages.
    The receive() method should yield messages until disconnected,
    then return normally.
    """

    async def connect(self) -> None:
        """Establish a WebSocket connection."""
        ...

    def receive(self) -> AsyncGenerator[str, None]:
        """Receive messages from the connection as an async generator.

        Yields text messages until the connection is closed.
        """
        ...

    async def close(self) -> None:
        """Close the current connection."""
        ...

    @property
    def is_connected(self) -> bool:
        """Whether the client currently holds an open connection."""
        ...
