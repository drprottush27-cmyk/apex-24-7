"""APEX Unified Control Plane - Grok / xAI External Intelligence Connector.

INVIOLABLE SAFETY INVARIANT:
---------------------------
ZERO execution authority. Zero order routing.
Read-only external intelligence, market commentary, and thesis analysis only.
All outputs are strictly labeled: [AI RESEARCH — NOT FINANCIAL ADVICE]
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

logger = logging.getLogger("apex.control.xai")

DISCLAIMER_TEXT = "[AI RESEARCH — NOT FINANCIAL ADVICE] [ZERO EXECUTION AUTHORITY]"


class GrokIntelligenceClient:
    """Read-only client for Grok / xAI external intelligence.
    
    Provides macro sentiment, thesis commentary, and market intelligence.
    Degrades gracefully when API key is missing or service is unreachable.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 10.0,
    ) -> None:
        self.api_key = (
            api_key
            or os.getenv("APEX_XAI_API_KEY")
            or os.getenv("GROK_API_KEY")
            or os.getenv("XAI_API_KEY")
        )
        self.base_url = (
            base_url
            or os.getenv("APEX_XAI_BASE_URL", "https://api.x.ai/v1")
        ).rstrip("/")
        self.model = (
            model
            or os.getenv("APEX_XAI_MODEL", "grok-beta")
        )
        self.timeout = timeout

    @property
    def is_available(self) -> bool:
        """Return True if an API key is configured."""
        return bool(self.api_key and len(self.api_key.strip()) > 5)

    def get_status(self) -> Dict[str, Any]:
        """Return connector status and availability."""
        return {
            "available": self.is_available,
            "provider": "xAI / Grok",
            "model": self.model,
            "base_url": self.base_url,
            "execution_authority": "ZERO (READ-ONLY RESEARCH)",
            "disclaimer": DISCLAIMER_TEXT,
        }

    def _call_chat_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 1000,
    ) -> Optional[str]:
        """Execute a chat completion request to xAI API."""
        if not self.is_available:
            return None

        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "ApexControlPlane-Intelligence/1.0",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                choices = data.get("choices", [])
                if choices and "message" in choices[0]:
                    return str(choices[0]["message"].get("content", ""))
                return None
        except Exception as exc:
            logger.warning("xAI API request failed: %s", exc)
            return None

    def analyze_market_sentiment(
        self,
        symbol: str,
        market_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Generate structured market sentiment and intelligence summary.
        
        Gracefully returns fallback intelligence if external API is unreachable.
        """
        ctx_str = json.dumps(market_context or {}, indent=2)
        sys_prompt = (
            "You are a rigorous quantitative crypto research analyst for the APEX autonomous platform. "
            "You provide objective, risk-focused commentary. You have ZERO execution authority. "
            "Always include macro catalysts, downside risks, and invalidation criteria."
        )
        user_prompt = (
            f"Analyze market conditions for {symbol}.\n\n"
            f"Current Context:\n{ctx_str}\n\n"
            "Provide:\n"
            "1. Macro regime assessment\n"
            "2. Technical & liquidity structure\n"
            "3. Key risk factors\n"
            "4. Suggested focus areas for human operators"
        )

        raw_output = self._call_chat_completion(sys_prompt, user_prompt)
        if raw_output:
            return {
                "status": "success",
                "source": "xAI / Grok",
                "symbol": symbol,
                "analysis": raw_output,
                "disclaimer": DISCLAIMER_TEXT,
                "has_execution_authority": False,
            }

        # Structured fallback response when API key is unconfigured or call fails
        return {
            "status": "fallback_local",
            "source": "APEX Control Plane Local Intelligence Engine",
            "symbol": symbol,
            "analysis": (
                f"Local analysis for {symbol}: Regime tracking active. "
                f"External xAI intelligence is {'unconfigured' if not self.is_available else 'temporarily offline'}. "
                "Operating under strict local risk envelope and automated scanner parameters."
            ),
            "disclaimer": DISCLAIMER_TEXT,
            "has_execution_authority": False,
        }

    def review_thesis(self, thesis: Dict[str, Any]) -> Dict[str, Any]:
        """Review an active investment thesis and generate counter-thesis / risk commentary."""
        thesis_str = json.dumps(thesis, indent=2)
        sys_prompt = (
            "You are a devil's advocate crypto risk officer. Your role is to critically evaluate "
            "investment theses, find flaws, detect blind spots, and identify unexpected liquidation vectors."
        )
        user_prompt = (
            f"Critique and stress-test the following APEX investment thesis:\n\n"
            f"{thesis_str}\n\n"
            "Deliver a concise breakdown of:\n"
            "1. Major invalidation risks not accounted for\n"
            "2. Hidden correlation or macro headwinds\n"
            "3. Liquidity and orderbook slippage vulnerabilities"
        )

        raw_output = self._call_chat_completion(sys_prompt, user_prompt)
        if raw_output:
            return {
                "status": "success",
                "source": "xAI / Grok",
                "asset": thesis.get("asset", "UNKNOWN"),
                "critique": raw_output,
                "disclaimer": DISCLAIMER_TEXT,
                "has_execution_authority": False,
            }

        return {
            "status": "fallback_local",
            "source": "APEX Control Plane Risk Rules",
            "asset": thesis.get("asset", "UNKNOWN"),
            "critique": (
                f"Local thesis safety check for {thesis.get('asset')}: "
                f"Invalidation level defined at {thesis.get('invalidation_level', 'N/A')}. "
                "Hard risk envelope mandates strictly enforced stop-losses and paper isolation."
            ),
            "disclaimer": DISCLAIMER_TEXT,
            "has_execution_authority": False,
        }

    # SAFETY ASSERTION: Execution methods are explicitly rejected
    def execute_order(self, *args: Any, **kwargs: Any) -> None:
        """Inviolable invariant: Grok intelligence client has ZERO execution authority."""
        raise PermissionError(
            "VIOLATION: GrokIntelligenceClient has ZERO execution authority. "
            "All execution must route exclusively through RiskGuardian -> OEM."
        )
