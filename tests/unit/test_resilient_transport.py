"""APEX 24/7 — Resilient HTTP Transport Regression Tests.

Verifies:
- Public GET only (POST, PUT, DELETE, PATCH rejected)
- Explicit timeout enforcement
- HTTP 429 rate-limit handling with Retry-After and backoff
- HTTP 418 IP-ban fail-closed behavior without endless retry
- Bounded retry counts (no infinite loops)
- Fail-closed error propagation as RuntimeError
- Zero credentials or authenticated requests
"""

from __future__ import annotations

import io
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from apex.market.transport import HTTPRequest, HTTPResponse, ResilientHTTPTransport


class _MockHTTPResponse:
    """Mock urllib response object."""

    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self._body = body
        self.headers = MagicMock()
        headers_dict = headers or {}
        self.headers.items.return_value = list(headers_dict.items())

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _MockHTTPResponse:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        pass


def _make_http_error(code: int, msg: str, headers: dict[str, str] | None = None) -> urllib.error.HTTPError:
    hdrs = MagicMock()
    hdrs.items.return_value = list((headers or {}).items())
    return urllib.error.HTTPError(
        url="https://fapi.binance.com/test",
        code=code,
        msg=msg,
        hdrs=hdrs,
        fp=io.BytesIO(b"error"),
    )


class TestResilientHTTPTransportSafety:
    def test_non_get_methods_rejected(self) -> None:
        transport = ResilientHTTPTransport()
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            req = HTTPRequest(method=method, url="https://fapi.binance.com/api/v1/order")
            with pytest.raises(AssertionError, match="market-data clients may only GET"):
                transport.request(req)

    def test_successful_get_with_params(self) -> None:
        sleeps: list[float] = []
        transport = ResilientHTTPTransport(timeout_s=5.0, sleeper=sleeps.append)

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _MockHTTPResponse(
                status=200,
                body=b'{"result": "ok"}',
                headers={"content-type": "application/json"},
            )
            req = HTTPRequest(
                method="GET",
                url="https://fapi.binance.com/fapi/v1/klines",
                params={"symbol": "BTCUSDT", "limit": "5"},
            )
            resp = transport.request(req)

            assert isinstance(resp, HTTPResponse)
            assert resp.status == 200
            assert resp.body == b'{"result": "ok"}'
            assert resp.headers.get("content-type") == "application/json"
            assert len(sleeps) == 0

            call_url, kwargs = mock_urlopen.call_args[0][0], mock_urlopen.call_args[1]
            assert "symbol=BTCUSDT" in call_url
            assert "limit=5" in call_url
            assert kwargs["timeout"] == 5.0


class TestResilientHTTPTransportRateLimiting:
    def test_http_429_retries_with_retry_after_and_succeeds(self) -> None:
        sleeps: list[float] = []
        transport = ResilientHTTPTransport(
            timeout_s=5.0,
            max_retries=2,
            base_backoff_s=0.5,
            max_backoff_s=5.0,
            sleeper=sleeps.append,
        )

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = [
                _make_http_error(429, "Too Many Requests", {"retry-after": "1.5"}),
                _MockHTTPResponse(status=200, body=b"[]"),
            ]
            req = HTTPRequest(method="GET", url="https://fapi.binance.com/fapi/v1/klines")
            resp = transport.request(req)

            assert resp.status == 200
            assert sleeps == [1.5]
            assert mock_urlopen.call_count == 2

    def test_http_429_retries_exhausted_raises_runtime_error(self) -> None:
        sleeps: list[float] = []
        transport = ResilientHTTPTransport(
            timeout_s=5.0,
            max_retries=2,
            base_backoff_s=0.2,
            sleeper=sleeps.append,
        )

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = _make_http_error(429, "Too Many Requests")
            req = HTTPRequest(method="GET", url="https://fapi.binance.com/fapi/v1/klines")

            with pytest.raises(RuntimeError, match="HTTP 429 rate limit exceeded"):
                transport.request(req)

            assert mock_urlopen.call_count == 3
            assert len(sleeps) == 2

    def test_http_418_ip_ban_fails_closed_immediately(self) -> None:
        sleeps: list[float] = []
        transport = ResilientHTTPTransport(
            timeout_s=5.0,
            max_retries=3,
            sleeper=sleeps.append,
        )

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = _make_http_error(418, "I'm a teapot (IP Banned)")
            req = HTTPRequest(method="GET", url="https://fapi.binance.com/fapi/v1/klines")

            with pytest.raises(RuntimeError, match="HTTP 418 IP ban received"):
                transport.request(req)

            assert mock_urlopen.call_count == 1
            assert len(sleeps) == 0

    def test_excessive_retry_after_fails_closed(self) -> None:
        sleeps: list[float] = []
        transport = ResilientHTTPTransport(
            timeout_s=5.0,
            max_retries=2,
            max_backoff_s=5.0,
            sleeper=sleeps.append,
        )

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = _make_http_error(418, "Banned", {"retry-after": "120"})
            req = HTTPRequest(method="GET", url="https://fapi.binance.com/fapi/v1/klines")

            with pytest.raises(RuntimeError, match="HTTP 418 IP ban received"):
                transport.request(req)

            assert len(sleeps) == 0


class TestResilientHTTPTransportNetworkErrors:
    def test_client_404_fails_immediately_without_retries(self) -> None:
        sleeps: list[float] = []
        transport = ResilientHTTPTransport(max_retries=3, sleeper=sleeps.append)

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = _make_http_error(404, "Not Found")
            req = HTTPRequest(method="GET", url="https://fapi.binance.com/fapi/v1/unknown")

            with pytest.raises(RuntimeError, match="HTTP 404 client error"):
                transport.request(req)

            assert mock_urlopen.call_count == 1
            assert len(sleeps) == 0

    def test_transient_500_retries_and_succeeds(self) -> None:
        sleeps: list[float] = []
        transport = ResilientHTTPTransport(max_retries=2, base_backoff_s=0.1, sleeper=sleeps.append)

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = [
                _make_http_error(502, "Bad Gateway"),
                _MockHTTPResponse(status=200, body=b"OK"),
            ]
            req = HTTPRequest(method="GET", url="https://fapi.binance.com/fapi/v1/klines")
            resp = transport.request(req)

            assert resp.status == 200
            assert mock_urlopen.call_count == 2
            assert len(sleeps) == 1
            assert sleeps[0] == pytest.approx(0.1)

    def test_transient_network_timeout_retries_and_exhausts(self) -> None:
        sleeps: list[float] = []
        transport = ResilientHTTPTransport(max_retries=2, base_backoff_s=0.1, sleeper=sleeps.append)

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = TimeoutError("Connection timed out")
            req = HTTPRequest(method="GET", url="https://fapi.binance.com/fapi/v1/klines")

            with pytest.raises(RuntimeError, match="Network transport failure"):
                transport.request(req)

            assert mock_urlopen.call_count == 3
            assert len(sleeps) == 2
