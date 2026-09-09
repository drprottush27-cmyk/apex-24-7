"""APEX 24/7 — Production Endpoint Isolation Guard.

Structurally enforces endpoint isolation.
Production/live endpoints are permanently blocked and rejected.
Only explicitly allowlisted sandbox/paper/shadow endpoints are permissible.
"""

from urllib.parse import urlparse

from apex.config.constants import APPROVED_ENDPOINTS, PROHIBITED_PATTERNS
from apex.domain.types import TradingMode
from apex.safety.exceptions import (
    EndpointViolationError,
    ProductionEndpointBlockedError,
)


class EndpointGuard:
    """Foundational boundary guard enforcing endpoint isolation."""

    def __init__(
        self,
        approved_endpoints: dict[str, set[str]] | None = None,
        prohibited_patterns: tuple[str, ...] | None = None,
    ) -> None:
        self._approved = approved_endpoints or APPROVED_ENDPOINTS
        self._prohibited = prohibited_patterns or PROHIBITED_PATTERNS

    def validate_mode(self, mode: TradingMode) -> None:
        """Ensure trading mode is strictly non-production."""
        if not isinstance(mode, TradingMode):
            raise EndpointViolationError(
                f"Invalid trading mode '{mode}'. Must be a valid TradingMode enum."
            )
        mode_key = mode.value
        if mode_key not in self._approved:
            raise EndpointViolationError(
                f"Trading mode '{mode_key}' has no approved execution endpoints."
            )

    def validate_endpoint(self, endpoint: str, mode: TradingMode) -> None:
        """Strictly validate an outbound endpoint URL or URI scheme.

        Rejects all production URLs, unrecognized schemes, and unallowlisted routes.
        Fail-closed: any parsing error or violation aborts immediately.
        """
        if not endpoint or not isinstance(endpoint, str):
            raise EndpointViolationError("Endpoint URL cannot be empty or non-string.")

        cleaned_endpoint = endpoint.strip()
        if not cleaned_endpoint:
            raise EndpointViolationError("Endpoint URL cannot be empty or non-string.")

        lower_endpoint = cleaned_endpoint.lower()

        # Step 1: Validate mode
        self.validate_mode(mode)

        # Step 2: Prohibited pattern check (hard production block)
        for pattern in self._prohibited:
            if pattern in lower_endpoint:
                raise ProductionEndpointBlockedError(
                    f"CRITICAL SAFETY VIOLATION: Endpoint '{cleaned_endpoint}' matches "
                    f"prohibited production pattern '{pattern}'. Execution blocked."
                )

        # Step 3: Exact or scheme allowlist verification
        mode_key = mode.value
        allowed_for_mode = self._approved.get(mode_key, set())
        if cleaned_endpoint not in allowed_for_mode:
            # Check parsed base URL without query params or paths
            parsed = urlparse(cleaned_endpoint)
            base_url = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else cleaned_endpoint
            if base_url not in allowed_for_mode:
                raise EndpointViolationError(
                    f"Endpoint '{cleaned_endpoint}' is not in the approved allowlist "
                    f"for mode '{mode_key}'. Allowed: {sorted(allowed_for_mode)}"
                )

    def validate_order_routing(self, mode: TradingMode, destination_url: str | None = None) -> None:
        """Verify routing safety prior to dispatching any order or query."""
        self.validate_mode(mode)
        if destination_url is not None:
            self.validate_endpoint(destination_url, mode)
