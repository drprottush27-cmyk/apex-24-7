from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class ExecutionAuth:
    """Requires valid authorization token to interact with gateway."""
    is_authorized: bool
    token_hash: str

@dataclass(frozen=True)
class ExecutionResult:
    """Immutable result from the execution boundary."""
    success: bool
    exchange_order_id: Optional[str]
    error_message: str
