"""Agent executor layer for the APEX engineering orchestrator.

Maps the five agent contracts (PLANNER, BUILDER, TESTER, SECURITY_REVIEWER,
FINAL_REVIEWER) onto the provider abstraction. Planning research, test, and
review agents may operate independently. Only the BUILDER may modify the
repository, and it must hold the builder lock.

The TESTER and REVIEWERS are independent and must inspect the ACTUAL
repository/diff — never rely on the Builder's claims.

This layer is deterministic and testable; tests inject fakes instead of real
AI APIs.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .provider import (
    ProviderFailure,
    ProviderRegistry,
    ProviderResult,
    make_default_registry,
)


@dataclass
class AgentOutcome:
    agent: str
    ok: bool
    provider: str = ""
    output: str = ""
    error: str = ""
    verdict: str = ""  # for reviewers: APPROVED | REJECTED
    issues: List[str] = field(default_factory=list)
    classification: str = ""


class AgentResult:
    """Typed result normalized from a provider response for a given agent."""

    def __init__(self, outcome: AgentOutcome):
        self.outcome = outcome
        self.ok = outcome.ok
        self.output = outcome.output
        self.verdict = outcome.verdict
        self.issues = outcome.issues


class AgentExecutor:
    """Runs a named agent via the provider abstraction with fallback."""

    def __init__(self, registry: Optional[ProviderRegistry] = None) -> None:
        self.registry = registry or make_default_registry()
        self._provider_failures: List[str] = []

    def _classify(self, err: str) -> str:
        low = err.lower()
        if any(k in low for k in ("quota", "rate", "limit")):
            return "PROVIDER_FAILURE"
        if any(k in low for k in ("network", "timeout", "connect", "unreachable")):
            return "INFRASTRUCTURE_FAILURE"
        return "PROVIDER_FAILURE"

    def run_agent(self, agent: str, prompt: str) -> AgentOutcome:
        """Run one agent turn. Prefers configured provider then fallback."""
        preferred = self._preferred_provider()
        providers_to_try = self._ordered_providers(preferred)
        last_err = ""
        last_class = "PROVIDER_FAILURE"
        used_provider = ""
        fallback_used = ""

        for name in providers_to_try:
            prov = self.registry.provider(name)
            if prov is None or not prov.available():
                self._provider_failures.append(name)
                continue
            try:
                result = prov.run(prompt=prompt, context={"agent": agent})
            except ProviderFailure as exc:
                last_err = exc.error
                last_class = exc.classification
                self._provider_failures.append(name)
                continue

            used_provider = name
            if result.ok:
                return AgentOutcome(agent=agent, ok=True, provider=used_provider,
                                    output=result.output)
            last_err = result.error or "agent returned failure"
            last_class = result.classification
            self._provider_failures.append(name)
        else:
            fallback_used = self._pick_fallback()

        if last_err:
            return AgentOutcome(agent=agent, ok=False, error=last_err,
                                classification=last_class, provider=used_provider)
        return AgentOutcome(agent=agent, ok=False, error="no_provider_available",
                            provider=used_provider)

    # -- provider selection helpers -------------------------------------
    def _preferred_provider(self) -> Optional[str]:
        return self._preferred

    def _ordered_providers(self, preferred: Optional[str]) -> List[str]:
        chosen = self.registry.select_available(preferred=preferred)
        if not chosen:
            return []
        return [chosen]

    def _pick_fallback(self) -> str:
        avail = self.registry.available_providers()
        if not avail:
            return ""
        for n in avail:
            if n != self._preferred:
                return n
        return avail[0]

    # Configured preferred provider for a run.
    _preferred: Optional[str] = None

    def set_preferred_provider(self, name: Optional[str]) -> None:
        self._preferred = name


# Convenience wrappers with the right agent identity for prompting.
def planner_prompt(task: Dict) -> str:
    return f"PLANNER: produce implementation plan for task {task.get('task_id')} {task.get('title')}"


def builder_prompt(task: Dict, plan: str = "") -> str:
    return f"BUILDER: implement task {task.get('task_id')} {task.get('title')}.\nPlan: {plan}"


def tester_prompt(task: Dict) -> str:
    return f"TESTER: run tests for task {task.get('task_id')} {task.get('title')}"


def security_review_prompt(task: Dict) -> str:
    return f"SECURITY_REVIEWER: review diff for task {task.get('task_id')} {task.get('title')}"


def final_review_prompt(task: Dict) -> str:
    return f"FINAL_REVIEWER: final release gate for task {task.get('task_id')} {task.get('title')}"