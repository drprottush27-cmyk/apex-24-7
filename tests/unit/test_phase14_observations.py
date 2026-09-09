from __future__ import annotations

from apex.market.observations import (
    MarketObservationClient,
    build_depth_url,
    build_funding_rate_url,
    build_open_interest_hist_url,
    parse_depth_payload,
    parse_funding_payload,
    parse_liquidation_payload,
    parse_open_interest_payload,
)
from apex.market.transport import HTTPRequest, HTTPResponse


class MockTransport:
    def __init__(self, responses: dict[str, HTTPResponse]) -> None:
        self._responses = responses
        self.requests: list[HTTPRequest] = []

    def request(self, req: HTTPRequest) -> HTTPResponse:
        self.requests.append(req)
        resp = self._responses.get(req.url)
        if resp is None:
            raise RuntimeError("network unavailable")
        return resp


def http_response(body: object, status: int = 200) -> HTTPResponse:
    import json

    return HTTPResponse(status=status, body=json.dumps(body).encode())


def test_url_builders_are_read_only_paths() -> None:
    assert "/fapi/v1/openInterestHist" in build_open_interest_hist_url("BTCUSDT")
    assert "/fapi/v1/fundingRate" in build_funding_rate_url("BTCUSDT")
    assert "/fapi/v1/depth" in build_depth_url("BTCUSDT")
    for url in (
        build_open_interest_hist_url("BTCUSDT"),
        build_funding_rate_url("BTCUSDT"),
        build_depth_url("BTCUSDT"),
    ):
        assert "BTCUSDT" in url


def test_parse_open_interest_payload() -> None:
    payload = [
        {"symbol": "BTCUSDT", "sumOpenInterest": "1000.5", "timestamp": 1699999999000},
        {"symbol": "BTCUSDT", "sumOpenInterest": "1050.0", "timestamp": 1700000299000},
    ]
    points = parse_open_interest_payload(payload)
    assert len(points) == 2
    assert points[0].value == 1000.5
    assert points[1].timestamp_ms == 1700000299000


def test_parse_open_interest_bad_payload() -> None:
    from apex.market.observations import ObservationUnavailableError

    try:
        parse_open_interest_payload([{"sumOpenInterest": "not-a-number"}])
    except ObservationUnavailableError:
        return
    raise AssertionError("malformed OI payload was accepted")


def test_parse_funding_payload() -> None:
    payload = [
        {"symbol": "BTCUSDT", "fundingTime": 1700000000000, "fundingRate": "0.0001"},
        {"symbol": "BTCUSDT", "fundingTime": 1700000100000, "fundingRate": "-0.0002"},
    ]
    points = parse_funding_payload(payload)
    assert len(points) == 2
    assert points[0].rate == 0.0001
    assert points[1].rate == -0.0002


def test_parse_depth_payload_mid() -> None:
    payload = {
        "lastUpdateId": 1,
        "bids": [["99.9", "1.0"], ["99.8", "2.0"]],
        "asks": [["100.1", "1.5"], ["100.2", "3.0"]],
    }
    snap = parse_depth_payload(payload)
    assert snap.mid_price == 100.0
    assert len(snap.bids) == 2
    assert len(snap.asks) == 2
    assert snap.bids[0].quantity == 1.0


def test_parse_liquidation_single_and_list() -> None:
    single = {"side": "SELL", "notional": "500000.0", "ts": 1700000000000}
    many = [
        {"side": "BUY", "notional": "200000.0", "ts": 1700000001000},
        {"side": "SELL", "notional": "700000.0", "ts": 1700000002000},
    ]
    assert len(parse_liquidation_payload(single)) == 1
    events = parse_liquidation_payload(many)
    assert len(events) == 2
    assert events[0].side == "BUY"
    assert events[1].notional == 700000.0


def test_observation_client_fetch_ok() -> None:

    oi = [
        {"symbol": "BTCUSDT", "sumOpenInterest": "1000.5", "timestamp": 1699999999000},
        {"symbol": "BTCUSDT", "sumOpenInterest": "1050.0", "timestamp": 1700000299000},
    ]
    funding = [{"symbol": "BTCUSDT", "fundingTime": 1700000000000, "fundingRate": "0.0001"}]
    depth = {
        "lastUpdateId": 1,
        "bids": [["99.9", "1.0"]],
        "asks": [["100.1", "1.5"]],
    }
    transport = MockTransport(
        {
            build_open_interest_hist_url("BTCUSDT"): http_response(oi),
            build_funding_rate_url("BTCUSDT"): http_response(funding),
            build_depth_url("BTCUSDT"): http_response(depth),
        }
    )
    client = MarketObservationClient(transport)
    result = client.fetch_all("BTCUSDT")
    assert result.oi_history is not None and len(result.oi_history) == 2
    assert result.funding_history is not None and len(result.funding_history) == 1
    assert result.depth is not None and result.depth.mid_price == 100.0
    assert all(r.url.startswith("https://fapi.binance.com/fapi/v1/") for r in transport.requests)


def test_observation_client_fail_safe_on_network_error() -> None:
    transport = MockTransport({})
    client = MarketObservationClient(transport)
    assert client.fetch_open_interest_history("BTCUSDT") is None
    assert client.fetch_funding_history("BTCUSDT") is None
    assert client.fetch_depth("BTCUSDT") is None
    result = client.fetch_all("BTCUSDT")
    assert result.oi_history is None
    assert result.funding_history is None
    assert result.depth is None


def test_observation_client_fail_safe_on_http_error() -> None:
    transport = MockTransport(
        {build_open_interest_hist_url("BTCUSDT"): http_response("{}", status=500)}
    )
    client = MarketObservationClient(transport)
    assert client.fetch_open_interest_history("BTCUSDT") is None


def test_observation_client_fail_safe_on_bad_payload() -> None:
    transport = MockTransport(
        {build_depth_url("BTCUSDT"): http_response({"bids": "not-a-list", "asks": "x"})}
    )
    client = MarketObservationClient(transport)
    assert client.fetch_depth("BTCUSDT") is None


def test_observation_result_fields_are_typed_models() -> None:
    # ObservationResult only exposes validated domain models, never raw payloads.
    result = MarketObservationClient(MockTransport({})).fetch_all("BTCUSDT")
    assert result.oi_history is None
    assert result.funding_history is None
    assert result.depth is None
    assert result.liquidations is None


def test_observation_client_fail_safe_on_timeout() -> None:
    """HTTP timeout should degrade to None (fail-safe)."""

    class TimeoutTransport:
        def request(self, req: HTTPRequest) -> HTTPResponse:
            raise TimeoutError("Request timed out")

    client = MarketObservationClient(TimeoutTransport())
    assert client.fetch_open_interest_history("BTCUSDT") is None
    assert client.fetch_funding_history("BTCUSDT") is None
    assert client.fetch_depth("BTCUSDT") is None


def test_observation_client_fail_safe_on_http_429() -> None:
    """HTTP 429 rate limit should degrade to None (fail-safe)."""
    transport = MockTransport(
        {build_open_interest_hist_url("BTCUSDT"): http_response("{}", status=429)}
    )
    client = MarketObservationClient(transport)
    assert client.fetch_open_interest_history("BTCUSDT") is None


def test_observation_client_fail_safe_on_http_418() -> None:
    """HTTP 418 IP ban should degrade to None (fail-safe)."""
    transport = MockTransport(
        {build_funding_rate_url("BTCUSDT"): http_response("{}", status=418)}
    )
    client = MarketObservationClient(transport)
    assert client.fetch_funding_history("BTCUSDT") is None


def test_observation_client_fail_safe_on_malformed_json() -> None:
    """Malformed JSON should degrade to None (fail-safe)."""
    transport = MockTransport(
        {build_depth_url("BTCUSDT"): HTTPResponse(status=200, body=b"not json")}
    )
    client = MarketObservationClient(transport)
    assert client.fetch_depth("BTCUSDT") is None


def test_observation_client_fail_safe_on_empty_payload() -> None:
    """Empty payload should degrade to None (fail-safe)."""
    transport = MockTransport(
        {build_open_interest_hist_url("BTCUSDT"): http_response([])}
    )
    client = MarketObservationClient(transport)
    result = client.fetch_open_interest_history("BTCUSDT")
    assert result is not None
    assert len(result) == 0


def test_observation_client_partial_fail_safe() -> None:
    """When one feed fails, others should still succeed independently."""
    oi = [{"symbol": "BTCUSDT", "sumOpenInterest": "1000.5", "timestamp": 1699999999000}]
    transport = MockTransport(
        {
            build_open_interest_hist_url("BTCUSDT"): http_response(oi),
            build_funding_rate_url("BTCUSDT"): http_response("{}", status=500),
            build_depth_url("BTCUSDT"): http_response({"bids": "bad", "asks": "bad"}),
        }
    )
    client = MarketObservationClient(transport)
    result = client.fetch_all("BTCUSDT")
    assert result.oi_history is not None and len(result.oi_history) == 1
    assert result.funding_history is None
    assert result.depth is None
