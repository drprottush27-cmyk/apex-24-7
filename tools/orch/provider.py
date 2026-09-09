"""Provider abstraction for the APEX engineering orchestrator.

The orchestrator must NOT be hard-wired to a single AI provider. Future backends
may include OpenCode, Gemini, Claude, Grok, and local Ollama models. This module:

  - defines an ``AgentProvider`` interface,
  - registers available providers,
  - detects provider availability,
  - records failures and supports a fallback provider,
  - NEVER stores API keys/credentials (it may read a provider name and, if a
    backend requires config at runtime, that config must come from the ambient
    environment that the backend itself already uses — never persisted here).

Provider calls are deterministic adapters; in tests they are replaced by fakes
so no real AI API is ever contacted.
"""

from __future__ import annotations

import os
import shutil
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional

try:
    import httpx
except ImportError:  # pragma: no cover - httpx is a declared dependency
    httpx = None

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5:3b"

PROVIDER_OPCODE = "opencode"
PROVIDER_GEMINI = "gemini"
PROVIDER_CLAUDE = "claude"
PROVIDER_GROK = "grok"
PROVIDER_OLLAMA = "ollama"

FALLBACK_CHAIN = [
    PROVIDER_OPCODE,
    PROVIDER_GEMINI,
    PROVIDER_CLAUDE,
    PROVIDER_GROK,
    PROVIDER_OLLAMA,
]


@dataclass
class ProviderResult:
    ok: bool
    output: str = ""
    error: str = ""
    provider: str = ""
    available: bool = True
    classification: str = "PROVIDER_FAILURE"
    duration_ms: int = 0

    @property
    def success(self) -> bool:
        return self.ok


class ProviderFailure(Exception):
    """Raised when a provider call fails (quota/network/service)."""

    def __init__(self, provider: str, error: str, classification: str = "PROVIDER_FAILURE"):
        super().__init__(f"{provider}: {error}")
        self.provider = provider
        self.error = error
        self.classification = classification


class AgentProvider(ABC):
    """A callable AI backend for an engineering agent."""

    name: str = "abstract"

    @abstractmethod
    def available(self) -> bool:
        """Return True if this provider is reachable and usable."""

    @abstractmethod
    def run(self, prompt: str, context: Dict) -> ProviderResult:
        """Execute a single agent turn deterministically.

        Implementations must never raise for a clean business outcome; they
        return a ProviderResult. Only raise ProviderFailure for hard transport
        failures to trigger fallback handling.
        """

    @abstractmethod
    def rationale(self) -> str:
        """Human-readable availability note (should never include secrets)."""


class OpenCodeProvider(AgentProvider):
    name = PROVIDER_OPCODE

    def available(self) -> bool:
        # opencode CLI may be a local binary; availability is best-effort.
        return shutil.which("opencode") is not None

    def rationale(self) -> str:
        return "opencode CLI detected on PATH" if self.available() else "opencode CLI not on PATH"

    def run(self, prompt: str, context: Dict) -> ProviderResult:
        start = time.monotonic()
        if not self.available():
            raise ProviderFailure(self.name, "opencode CLI not available")
        # In real operation this would spawn the local opencode CLI in a
        # non-trading, controlled subprocess. That execution layer is out of
        # scope for this autonomous-engineering layer and is deliberately NOT
        # wired to any exchange/trading path.
        raise ProviderFailure(self.name, "opencode execution engine not configured (dry adapter)")


class OllamaProvider(AgentProvider):
    """Advisory-only local LLM backend via the Ollama HTTP API.

    This provider talks ONLY to the local Ollama server configured by
    ``OLLAMA_URL`` / ``OLLAMA_MODEL`` in the ambient environment (never
    persisted, never stored). Its output is advisory text consumed by the
    engineering orchestrator for planning/analysis/review — it has ZERO
    direct execution or trading authority. No order/execution/account path
    reads this provider's output.

    ``available()`` performs a bounded, short-timeout reachability probe of
    the local server and fails closed (returns False) on any error, so an
    unreachable Ollama can never be misreported as available. A caller may
    inject an ``httpx`` transport (e.g. ``httpx.MockTransport``) for hermetic
    tests so no real network is ever contacted.
    """

    name = PROVIDER_OLLAMA

    def __init__(
        self,
        url: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: float = 2.0,
        transport=None,
    ) -> None:
        self.url = (url or os.environ.get("OLLAMA_URL") or DEFAULT_OLLAMA_URL).rstrip("/")
        self.model = model or os.environ.get("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL
        self._timeout = timeout_seconds
        self._transport = transport
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            if httpx is None:  # pragma: no cover - httpx is a declared dependency
                raise ProviderFailure(self.name, "httpx not installed")
            self._client = httpx.Client(
                timeout=self._timeout,
                base_url=self.url,
                transport=self._transport,
            )
        return self._client

    def available(self) -> bool:
        if httpx is None:
            return False
        try:
            resp = self._ensure_client().get("/api/tags", timeout=self._timeout)
            return resp.status_code == 200
        except Exception:  # noqa: BLE001 - fail closed, never raise
            return False

    def rationale(self) -> str:
        if not self.available():
            return f"Ollama not reachable at {self.url}"
        return f"Ollama local server at {self.url} (model {self.model})"

    def run(self, prompt: str, context: Dict) -> ProviderResult:
        start = time.monotonic()
        if httpx is None:
            return ProviderResult(ok=False, provider=self.name,
                                  error="httpx not installed",
                                  classification="PROVIDER_FAILURE")
        if not self.available():
            return ProviderResult(ok=False, provider=self.name,
                                  error=f"Ollama unreachable at {self.url}",
                                  classification="INFRASTRUCTURE_FAILURE")
        try:
            resp = self._ensure_client().post(
                "/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                },
                timeout=self._timeout,
            )
        except Exception as e:  # noqa: BLE001
            return ProviderResult(ok=False, provider=self.name,
                                  error=str(e),
                                  classification="INFRASTRUCTURE_FAILURE")
        if resp.status_code != 200:
            return ProviderResult(ok=False, provider=self.name,
                                  error=f"ollama_http_{resp.status_code}",
                                  classification="PROVIDER_FAILURE")
        try:
            body = resp.json()
        except ValueError:
            return ProviderResult(ok=False, provider=self.name,
                                  error="ollama_invalid_json",
                                  classification="PROVIDER_FAILURE")
        text = body.get("response") or body.get("message", {}).get("content") or ""
        if not text.strip():
            return ProviderResult(ok=False, provider=self.name,
                                  error="ollama_empty_response",
                                  classification="PROVIDER_FAILURE")
        duration = int((time.monotonic() - start) * 1000)
        return ProviderResult(ok=True, output=text.strip(), provider=self.name,
                              classification="ADVISORY_OUTPUT", duration_ms=duration)


class LocalFallbackProvider(AgentProvider):
    """Used in tests/offline mode: returns canned, deterministic output.

    This is the ONLY provider that never touches a network or AI API, keeping
    tests hermetic and safe. It is never selected automatically in production
    unless explicitly enabled via configuration (offline/test mode).
    """

    name = "local-fake"

    def __init__(self, responder=None):
        self.responder = responder

    def available(self) -> bool:
        return True

    def rationale(self) -> str:
        return "deterministic local stub (tests/offline)"

    def run(self, prompt: str, context: Dict) -> ProviderResult:
        if self.responder is None:
            return ProviderResult(ok=True, output="", provider=self.name)
        out = self.responder(prompt=prompt, context=context)
        return ProviderResult(ok=True, output=out, provider=self.name)


class ProviderRegistry:
    """Registry of providers + availability detection + fallback selection."""

    def __init__(self, providers: Optional[List[AgentProvider]] = None) -> None:
        self._providers: Dict[str, AgentProvider] = {}
        for p in (providers or [OpenCodeProvider()]):
            self._providers[p.name] = p

    def register(self, provider: AgentProvider) -> None:
        self._providers[provider.name] = provider

    def available_providers(self) -> List[str]:
        return [name for name, p in self._providers.items() if p.available()]

    def provider(self, name: str) -> Optional[AgentProvider]:
        return self._providers.get(name)

    def select_available(self, preferred: Optional[str] = None) -> Optional[str]:
        """Pick the first available provider, preferring ``preferred``."""
        available = self.available_providers()
        if not available:
            return None
        if preferred and preferred in available:
            return preferred
        for name in FALLBACK_CHAIN:
            if name in available:
                return name
        return available[0]


def make_default_registry(offline_mode: bool = False) -> ProviderRegistry:
    """Build the default provider set.

    In offline/test mode a deterministic stub is used — never any real AI call.
    """
    registry = ProviderRegistry(providers=[OpenCodeProvider()])
    if offline_mode:
        registry.register(LocalFallbackProvider())
        return registry

    # Real-operation backends, each gated by its own availability check and
    # using only ambient env credentials. Ollama is an advisory-only local LLM:
    # its output never carries any direct execution/trading authority. When no
    # real backend is reachable, select_available() returns None and the
    # orchestrator fails closed rather than fabricating a provider.
    registry.register(OllamaProvider())
    return registry