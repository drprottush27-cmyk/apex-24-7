"""APEX 24/7 — Market Observation Client (Phase 14).

Read-only PUBLIC market-data observations that feed the advisory tactical
analytics (Phase 12/13): open-interest history, funding history, depth
snapshot, and liquidation events.

SAFETY CONTRACT:
- Public, unauthenticated, read-only endpoints ONLY. No credentials, no
  signing, no order placement, no account data.
- Every fetch is fail-safe: any transport, HTTP, or parse failure produces a
  None / empty observation — never an exception that can block signal
  evaluation, and never partial-bogus data.
- The EndpointGuard governs ORDER ROUTING; this module makes no order routing
  calls. It reads market data only.
- Observations are advisory-only inputs. They cannot authorize or veto orders.

Consumption pattern:
    client = MarketObservationClient(transport)
    oi = client.fetch_open_interest_history("BTCUSDT")      # list | None
    funding = client.fetch_funding_history("BTCUSDT")       # list | None
    depth = client.fetch_depth("BTCUSDT")                   # DepthSnapshot | None
    LiquidationPoint.from_payload({"side": "SELL", ...})    # pure parse
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from apex.engines.tactical.model import (
    DepthLevel,
    DepthSnapshot,
    FundingPoint,
    LiquidationPoint,
    OpenInterestPoint,
)
from apex.market.binance_http import FAPI_REST_URL
from apex.market.transport import HTTPRequest, HTTPResponse, HTTPTransport
from apex.safety.exceptions import ApexError as _ApexError
from apex.safety.exceptions import InvalidNumericalDataError

# ── Endpoint Paths ─────────────────────────────────────────────────────────────

OPEN_INTEREST_HIST_PATH: str = "/fapi/v1/openInterestHist"
FUNDING_RATE_PATH: str = "/fapi/v1/fundingRate"
DEPTH_PATH: str = "/fapi/v1/depth"


class ObservationUnavailableError(_ApexError):
    """Raised when an observation cannot be fetched or parsed.

    Clients are expected to catch this at the boundary and degrade to None.
    """


# ── URL Construction ───────────────────────────────────────────────────────────

def build_open_interest_hist_url(
    symbol: str,
    period: str = "5m",
    limit: int = 30,
    base_url: str = FAPI_REST_URL,
) -> str:
    """Construct the public open-interest history URL (read-only)."""
    params: dict[str, str] = {
        "symbol": symbol.upper(),
        "period": period,
        "limit": str(limit),
    }
    return f"{base_url}{OPEN_INTEREST_HIST_PATH}?{urlencode(params)}"


def build_funding_rate_url(
    symbol: str,
    limit: int = 30,
    base_url: str = FAPI_REST_URL,
) -> str:
    """Construct the public funding-rate history URL (read-only)."""
    params: dict[str, str] = {
        "symbol": symbol.upper(),
        "limit": str(limit),
    }
    return f"{base_url}{FUNDING_RATE_PATH}?{urlencode(params)}"


def build_depth_url(
    symbol: str,
    limit: int = 20,
    base_url: str = FAPI_REST_URL,
) -> str:
    """Construct the public order-book depth URL (read-only)."""
    params: dict[str, str] = {
        "symbol": symbol.upper(),
        "limit": str(limit),
    }
    return f"{base_url}{DEPTH_PATH}?{urlencode(params)}"


# ── Transport Helpers ──────────────────────────────────────────────────────────

def _parse_json_object(body: bytes) -> Any:
    try:
        return json.loads(body)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ObservationUnavailableError(f"invalid JSON payload: {exc}") from exc


def _assert_http_ok(status: int, url: str) -> None:
    if status != 200:
        raise ObservationUnavailableError(f"HTTP {status} from {url}")


def _to_float(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ObservationUnavailableError(
            f"field '{field}' cannot be boolean, got {value!r}"
        )
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ObservationUnavailableError(
            f"field '{field}' is not numeric, got {value!r}"
        ) from exc
    if result != result or result in (float("inf"), float("-inf")):
        raise ObservationUnavailableError(
            f"field '{field}' is not finite, got {value!r}"
        )
    return result


def _to_timestamp(value: Any, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ObservationUnavailableError(
            f"field '{field}' is not an integer timestamp, got {value!r}"
        ) from exc


# ── Payload Parsers (pure, deterministic) ─────────────────────────────────────

def parse_open_interest_payload(payload: Any) -> tuple[OpenInterestPoint, ...]:
    """Parse a Binance openInterestHist payload into OI points.

    Raises ObservationUnavailableError on malformed payloads (fail-closed).
    """
    if not isinstance(payload, list):
        raise ObservationUnavailableError(
            f"OI payload must be a list, got {type(payload).__name__}"
        )
    points: list[OpenInterestPoint] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ObservationUnavailableError("OI payload item must be a dict")
        try:
            points.append(
                OpenInterestPoint(
                    timestamp_ms=_to_timestamp(item.get("timestamp"), "timestamp"),
                    value=_to_float(item.get("sumOpenInterest"), "sumOpenInterest"),
                )
            )
        except InvalidNumericalDataError as exc:
            raise ObservationUnavailableError(f"invalid OI point: {exc}") from exc
    return tuple(points)


def parse_funding_payload(payload: Any) -> tuple[FundingPoint, ...]:
    """Parse a Binance fundingRate payload into funding points.

    Raises ObservationUnavailableError on malformed payloads (fail-closed).
    """
    if not isinstance(payload, list):
        raise ObservationUnavailableError(
            f"funding payload must be a list, got {type(payload).__name__}"
        )
    points: list[FundingPoint] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ObservationUnavailableError("funding payload item must be a dict")
        try:
            points.append(
                FundingPoint(
                    timestamp_ms=_to_timestamp(item.get("fundingTime"), "fundingTime"),
                    rate=_to_float(item.get("fundingRate"), "fundingRate"),
                )
            )
        except InvalidNumericalDataError as exc:
            raise ObservationUnavailableError(f"invalid funding point: {exc}") from exc
    return tuple(points)


def parse_depth_payload(payload: Any) -> DepthSnapshot:
    """Parse a Binance depth payload into a DepthSnapshot.

    Raises ObservationUnavailableError on malformed payloads (fail-closed).
    """
    if not isinstance(payload, dict):
        raise ObservationUnavailableError(
            f"depth payload must be a dict, got {type(payload).__name__}"
        )
    bids_raw = payload.get("bids")
    asks_raw = payload.get("asks")
    if not isinstance(bids_raw, list) or not isinstance(asks_raw, list):
        raise ObservationUnavailableError("depth payload missing bids/asks lists")
    if not bids_raw or not asks_raw:
        raise ObservationUnavailableError("depth payload has empty book")

    def _levels(rows: list[Any]) -> tuple[DepthLevel, ...]:
        levels: list[DepthLevel] = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 2:
                raise ObservationUnavailableError("depth level must be [price, qty]")
            try:
                levels.append(
                    DepthLevel(
                        price=_to_float(row[0], "price"),
                        quantity=_to_float(row[1], "quantity"),
                    )
                )
            except InvalidNumericalDataError as exc:
                raise ObservationUnavailableError(f"invalid depth level: {exc}") from exc
        return tuple(levels)

    bids = _levels(bids_raw)
    asks = _levels(asks_raw)
    best_bid = bids[0].price
    best_ask = asks[0].price
    mid = (best_bid + best_ask) / 2.0
    try:
        return DepthSnapshot(mid_price=mid, bids=bids, asks=asks)
    except InvalidNumericalDataError as exc:
        raise ObservationUnavailableError(f"invalid depth snapshot: {exc}") from exc


def parse_liquidation_payload(
    payload: Any,
    *,
    notional_field: str = "notional",
    side_field: str = "side",
    time_field: str = "ts",
) -> tuple[LiquidationPoint, ...]:
    """Parse a liquidation-event payload into LiquidationPoint records.

    Both a single event dict and a list of event dicts are accepted. The
    payload shape is intentionally configurable because liquidation feeds are
    less standardized than OI/funding/depth; parsers must treat unknown shapes
    as unavailable (fail-closed).
    """
    if isinstance(payload, list):
        events: list[Any] = payload
    elif isinstance(payload, dict):
        events = [payload]
    else:
        raise ObservationUnavailableError(
            f"liquidation payload must be a dict or list, got {type(payload).__name__}"
        )

    points: list[LiquidationPoint] = []
    for item in events:
        if not isinstance(item, dict):
            raise ObservationUnavailableError("liquidation event must be a dict")
        try:
            points.append(
                LiquidationPoint(
                    timestamp_ms=_to_timestamp(item.get(time_field), time_field),
                    side=str(item.get(side_field, "")),
                    notional=_to_float(item.get(notional_field), notional_field),
                )
            )
        except InvalidNumericalDataError as exc:
            raise ObservationUnavailableError(
                f"invalid liquidation event: {exc}"
            ) from exc
    return tuple(points)


# ── Fail-Safe Observation Client ──────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class ObservationResult:
    """Fail-safe observation fetch bundle.

    Either observations for a given feed, or None (feed unavailable). Access
    to raw payloads is intentionally NOT exposed: only validated domain models
    are consumed downstream.
    """

    oi_history: tuple[OpenInterestPoint, ...] | None = None
    funding_history: tuple[FundingPoint, ...] | None = None
    depth: DepthSnapshot | None = None
    liquidations: tuple[LiquidationPoint, ...] | None = None


class MarketObservationClient:
    """Read-only public market-data observation client.

    Every method is fail-safe: transport errors, HTTP errors, and parse
    errors all degrade to None. No exception escapes.
    """

    def __init__(
        self,
        http_transport: HTTPTransport,
        base_url: str = FAPI_REST_URL,
    ) -> None:
        self._http = http_transport
        self._base_url = base_url

    def fetch_open_interest_history(
        self,
        symbol: str,
        period: str = "5m",
        limit: int = 30,
    ) -> tuple[OpenInterestPoint, ...] | None:
        """Fetch open-interest history. Returns None if unavailable."""
        url = build_open_interest_hist_url(
            symbol, period=period, limit=limit, base_url=self._base_url
        )
        payload = self._request(url)
        if payload is None:
            # Fallback to alternative Binance Futures data path
            alt_params = {
                "symbol": symbol.upper(),
                "period": period,
                "limit": str(limit),
            }
            alt_url = f"{self._base_url}/futures/data/openInterestHist?{urlencode(alt_params)}"
            payload = self._request(alt_url)

        if payload is None:
            return None
        try:
            return parse_open_interest_payload(payload)
        except ObservationUnavailableError:
            return None

    def fetch_funding_history(
        self,
        symbol: str,
        limit: int = 30,
    ) -> tuple[FundingPoint, ...] | None:
        """Fetch funding-rate history. Returns None if unavailable."""
        url = build_funding_rate_url(symbol, limit=limit, base_url=self._base_url)
        payload = self._request(url)
        if payload is None:
            return None
        try:
            return parse_funding_payload(payload)
        except ObservationUnavailableError:
            return None

    def fetch_depth(
        self,
        symbol: str,
        limit: int = 20,
    ) -> DepthSnapshot | None:
        """Fetch current order-book depth. Returns None if unavailable."""
        url = build_depth_url(symbol, limit=limit, base_url=self._base_url)
        payload = self._request(url)
        if payload is None:
            return None
        try:
            return parse_depth_payload(payload)
        except ObservationUnavailableError:
            return None

    def fetch_all(
        self,
        symbol: str,
        *,
        oi_period: str = "5m",
        oi_limit: int = 30,
        funding_limit: int = 30,
        depth_limit: int = 20,
    ) -> ObservationResult:
        """Fetch all available observation feeds for a symbol.

        Each feed independently degrades to None on any failure.
        """
        return ObservationResult(
            oi_history=self.fetch_open_interest_history(
                symbol, period=oi_period, limit=oi_limit
            ),
            funding_history=self.fetch_funding_history(symbol, limit=funding_limit),
            depth=self.fetch_depth(symbol, limit=depth_limit),
        )

    def _request(self, url: str) -> Any:
        try:
            resp: HTTPResponse = self._http.request(HTTPRequest("GET", url))
        except Exception:
            return None  # transport failure -> unavailable (fail-safe)
        try:
            _assert_http_ok(resp.status, url)
        except ObservationUnavailableError:
            return None
        try:
            return _parse_json_object(resp.body)
        except ObservationUnavailableError:
            return None
