"""Unit tests for production endpoint isolation guard."""

import pytest

from apex.domain.types import TradingMode
from apex.safety.endpoint_guard import EndpointGuard
from apex.safety.exceptions import EndpointViolationError, ProductionEndpointBlockedError


class TestEndpointGuard:
    """Test suite for EndpointGuard isolation invariants."""

    def test_safe_endpoints_accepted_per_mode(self) -> None:
        guard = EndpointGuard()

        # DRY_RUN
        guard.validate_endpoint("local://mock-execution", TradingMode.DRY_RUN)
        guard.validate_endpoint("dry-run://internal", TradingMode.DRY_RUN)

        # SHADOW
        guard.validate_endpoint("shadow://local-simulator", TradingMode.SHADOW)

        # PAPER (Binance Futures Sandbox/Testnet)
        guard.validate_endpoint("https://testnet.binancefuture.com", TradingMode.PAPER)
        guard.validate_endpoint("wss://fstream.binancefuture.com", TradingMode.PAPER)

    def test_production_endpoints_strictly_rejected(self) -> None:
        guard = EndpointGuard()

        prohibited_urls = [
            "https://fapi.binance.com",
            "https://fapi.binance.com/fapi/v1/order",
            "wss://fstream.binance.com/ws",
            "https://api.binance.com",
            "https://dapi.binance.com",
            "https://live.binance.com",
            "https://production.binance.com",
        ]

        for url in prohibited_urls:
            with pytest.raises(ProductionEndpointBlockedError, match="CRITICAL SAFETY VIOLATION"):
                guard.validate_endpoint(url, TradingMode.PAPER)

            with pytest.raises(ProductionEndpointBlockedError, match="CRITICAL SAFETY VIOLATION"):
                guard.validate_endpoint(url, TradingMode.DRY_RUN)

    def test_unknown_endpoints_rejected(self) -> None:
        guard = EndpointGuard()

        unknown_urls = [
            "https://custom-broker.com",
            "https://some-exchange.io",
            "http://localhost:8080",
        ]

        for url in unknown_urls:
            with pytest.raises(EndpointViolationError, match="not in the approved allowlist"):
                guard.validate_endpoint(url, TradingMode.PAPER)

    def test_empty_or_invalid_endpoint_fails_closed(self) -> None:
        guard = EndpointGuard()

        with pytest.raises(EndpointViolationError, match="cannot be empty"):
            guard.validate_endpoint("", TradingMode.PAPER)

        with pytest.raises(EndpointViolationError, match="cannot be empty"):
            guard.validate_endpoint("   ", TradingMode.PAPER)

    def test_mode_mismatch_rejected(self) -> None:
        guard = EndpointGuard()

        # Testnet URL is for PAPER, not DRY_RUN
        with pytest.raises(EndpointViolationError, match="not in the approved allowlist"):
            guard.validate_endpoint("https://testnet.binancefuture.com", TradingMode.DRY_RUN)

    def test_validate_order_routing(self) -> None:
        guard = EndpointGuard()

        # Valid PAPER destination
        guard.validate_order_routing(
            TradingMode.PAPER, destination_url="https://testnet.binancefuture.com"
        )

        # Prohibited live destination in routing
        with pytest.raises(ProductionEndpointBlockedError):
            guard.validate_order_routing(
                TradingMode.PAPER, destination_url="https://fapi.binance.com"
            )
