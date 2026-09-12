from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

AI_STATUS_DEFAULT = "DATA_UNAVAILABLE"
AI_STATUS_AVAILABLE = "AVAILABLE"


@dataclass(frozen=True)
class AdvisoryResult:
    """Result of an advisory-only LLM consultation.

    The advisor NEVER executes; it only returns text/structured reasoning.
    """

    success: bool
    ai_status: str
    model: str
    advice: Optional[str]
    reasoning: Optional[str]
    error: Optional[str]
    generated_at: str

    @property
    def is_available(self) -> bool:
        return self.success and self.ai_status == AI_STATUS_AVAILABLE