from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import urllib.error
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional

from .models import AI_STATUS_AVAILABLE, AI_STATUS_DEFAULT, AdvisoryResult

DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen3:1.7b"
DEFAULT_TIMEOUT_S = 30.0

# Defense-in-depth: keys that must never reach the LLM.
_SEALED_KEYS = frozenset({
    "api_key", "apikey", "api_secret", "asecret", "apipassphrase",
    "secret", "secrets", "passphrase", "password", "token", "access_token",
    "refresh_token", "private_key", "privatekey", "mnemonic", "seed",
    "seed_phrase", "cookie", "cookies", "authorization", "auth", "webhook_passphrase",
    "key_id", "signing_key", "wallet", "credentials",
})


def _sanitize_node(node: Any) -> Any:
    """Recursively strip sealed keys from dicts; reject callables outright."""
    if hasattr(node, "__call__"):
        raise TypeError("callable objects (e.g. functions) are not allowed")
    if isinstance(node, Mapping):
        out: Dict[str, Any] = {}
        for key, value in node.items():
            if isinstance(key, str) and key.strip().lower() in _SEALED_KEYS:
                continue
            out[key] = _sanitize_node(value)
        return out
    if isinstance(node, (list, tuple)):
        return [_sanitize_node(item) for item in node]
    if isinstance(node, (str, int, float, bool)) or node is None:
        return node
    if isinstance(node, Decimal):
        return node
    if isinstance(node, (datetime, date)):
        return node.isoformat()
    # Any other object (e.g. dataclass, Model instance) is rendered structurally
    # only if it can be safely introspected; otherwise it is refused so no
    # execution-capable object can ever reach the model.
    if hasattr(node, "to_dict"):
        return _sanitize_node(node.to_dict())
    if isinstance(node, dict):
        return _sanitize_node(dict(node))
    raise TypeError(f"unsafe type for LLM context: {type(node).__name__}")


class OllamaAdvisor:
    """ADVISORY-ONLY wrapper around a local Ollama instance.

    Security contract:
      * Receives only sanitized, structured, serializable state.
      * NEVER receives credentials or exchange secrets.
      * Has no tools/functions: it can only ask the model for text reasoning.
      * A failure (unavailable, timeout, malformed response) yields
        AI_STATUS = DATA_UNAVAILABLE and never disrupts the backend.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        enabled: bool = True,
    ) -> None:
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL)).rstrip("/")
        self.model = model or os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
        self.timeout_s = timeout_s
        self.enabled = enabled

    def is_available(self) -> bool:
        if not self.enabled:
            return False
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags", headers={"User-Agent": "apex/0.1"})
            with urllib.request.urlopen(req, timeout=min(self.timeout_s, 10.0)) as resp:
                return resp.status == 200
        except Exception:
            return False

    def sanitize(self, payload: Any) -> Any:
        return _sanitize_node(payload)

    def advise(self, context: Mapping[str, Any]) -> AdvisoryResult:
        generated_at = datetime.now(timezone.utc).isoformat()
        if not self.enabled:
            return self._failure("ADVISORY_DISABLED", generated_at)
        try:
            safe_context = self.sanitize(dict(context))
        except TypeError as exc:
            return self._failure(f"UNSAFE_INPUT: {exc}", generated_at)

        prompt = self._render_prompt(safe_context)
        payload = json.dumps({
            "model": self.model,
            "prompt": prompt,
            "stream": False,
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "apex/0.1"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                try:
                    body = resp.read(2_000_000)
                except TypeError:
                    body = resp.read()
            parsed = json.loads(body)
        except Exception as exc:
            return self._failure(f"OLLAMA_UNAVAILABLE: {exc}", generated_at)

        if not isinstance(parsed, dict):
            return self._failure("MALFORMED_RESPONSE", generated_at)
        advice = parsed.get("response")
        if not isinstance(advice, str) or not advice.strip():
            return self._failure("MALFORMED_RESPONSE: empty response", generated_at)

        return AdvisoryResult(
            success=True,
            ai_status=AI_STATUS_AVAILABLE,
            model=self.model,
            advice=advice.strip(),
            reasoning=advice.strip(),
            error=None,
            generated_at=generated_at,
        )

    def _render_prompt(self, safe_context: Mapping[str, Any]) -> str:
        preface = (
            "You are the ADVISORY intelligence layer for a PAPER-ONLY crypto trading system. "
            "You have NO execution authority. Provide concise advisory reasoning only. "
            "Never recommend bypassing risk controls.\n\nStructured APEX state:\n"
        )
        try:
            body = json.dumps(safe_context, default=str)
        except (TypeError, ValueError):
            body = "{}"
        if len(body) > 8000:
            body = body[:8000] + "...(truncated)"
        return preface + body

    def _failure(self, error: str, generated_at: str) -> AdvisoryResult:
        return AdvisoryResult(
            success=False,
            ai_status=AI_STATUS_DEFAULT,
            model=self.model,
            advice=None,
            reasoning=None,
            error=error,
            generated_at=generated_at,
        )