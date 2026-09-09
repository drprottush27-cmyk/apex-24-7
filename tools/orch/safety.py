"""Deterministic safety gate for the APEX engineering orchestrator.

The Security Reviewer inspects the ACTUAL working tree / git diff and verifies
the APEX safety invariants. Critically, the orchestrator MUST NEVER:

  - enable live trading,
  - create production exchange credentials,
  - bypass Risk Guardian,
  - disable deterministic execution vetoes,
  - modify safety controls to make tests pass,
  - connect to an unknown Git remote,
  - push to a remote automatically,
  - place real-money orders.

Any attempt to weaken these protections stops the pipeline immediately and
records a human-required blocker (fail closed).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from . import gitops as gitops_mod
from .gitops import GitOps

ROOT = Path(__file__).resolve().parent.parent

# Substrings that indicate a dangerous safety-control weakening.
_PROHIBITED_PATTERNS = [
    r"LIVE_TRADING_ENABLED\s*=\s*true",
    r"AUTO_EXECUTE\s*=\s*true",
    r"TRADING_MODE\s*=\s*LIVE",
    r"TRADING_MODE\s*=\s*live",
    r"allow_origins\s*=\s*\[\s*['\"]\*['\"]\s*\]",
    r"bypass\s*(risk|guardian|veto|guard)",
    r"disable\s*(risk|guardian|veto|guard)",
    r"execution_bypass",
]

# Prohibited secrets (never write these into source/diff).
_SECRET_PATTERNS = [
    r"api[_-]?key\s*=\s*['\"][^'\"]+['\"]",
    r"api[_-]?secret\s*=\s*['\"][^'\"]+['\"]",
    r"seed\s*phrase",
    r"private\s*key",
    r"sk-[A-Za-z0-9]{20,}",
    r"-----BEGIN (RSA |EC |)PRIVATE KEY",
    r"wallet\s*:\s*['\"][0-9a-fA-F]{40}['\"]",
]


@dataclass
class SafetyReport:
    verdict: str  # APPROVED | REJECTED
    issues: List[str] = field(default_factory=list)

    @property
    def approved(self) -> bool:
        return self.verdict == "APPROVED"


class SafetyVeto(Exception):
    """Raised when an absolute trading-safety violation is detected."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class SafetyReviewer:
    """Deterministic, fail-closed inspection of the working tree + diff."""

    def __init__(self, git: Optional[GitOps] = None) -> None:
        self.git = git or GitOps()

    # -- primary entry --------------------------------------------------
    def inspect(self) -> SafetyReport:
        issues: List[str] = []
        diff = self.git.diff()
        staging = self.git.cached_diff()
        untracked_source = self._collect_untracked_source()
        combined = "\n".join([diff, staging, untracked_source])

        # 1. Absolute trading-safety invariants (fail closed -> Veto).
        self._assert_live_trading_disabled(combined)

        # 2. No prohibited safety weakening.
        for pat in _PROHIBITED_PATTERNS:
            if re.search(pat, combined, re.IGNORECASE):
                issues.append(f"prohibited_safety_weakening {pat}")

        # 3. No secrets.
        for pat in _SECRET_PATTERNS:
            if re.search(pat, combined):
                issues.append(f"potential_secret_detected pattern={pat}")

        # 4. No unknown remotes.
        try:
            self.git.assert_no_unknown_remote()
        except gitops_mod.GitOpsError as e:
            issues.append(str(e))

        # 5. Authentication / CORS fail-closed defaults.
        if re.search(r"ALLOWED_ORIGINS\s*=\s*['\"]\*['\"]", combined):
            issues.append("cors_wildcard_not_allowed")

        verdict = "APPROVED" if not issues else "REJECTED"
        return SafetyReport(verdict=verdict, issues=issues)

    # -- fail-closed live-trading guard ---------------------------------
    def _assert_live_trading_disabled(self, combined: str) -> None:
        """Verify DRY_RUN default, LIVE disabled, AUTO_EXECUTE off.

        Any attempt to turn these on is an absolute blocker -> Veto (raises).
        """
        if re.search(r"LIVE_TRADING_ENABLED\s*=\s*true", combined, re.IGNORECASE):
            raise SafetyVeto("live_trading_enabled=true_attempted")
        if re.search(r"TRADING_MODE\s*=\s*LIVE", combined, re.IGNORECASE):
            raise SafetyVeto("trading_mode=live_attempted")
        if re.search(r"AUTO_EXECUTE\s*=\s*true", combined, re.IGNORECASE):
            raise SafetyVeto("auto_execute=true_attempted")
        # Risk Guardian must remain authoritative: no diff may delete/weaken it.
        if re.search(r"def using_veto|veto\s*=\s*False|veto\s*=\s*false", combined):
            raise SafetyVeto("risk_guardian_veto_weakened")

    def _collect_untracked_source(self) -> str:
        lines: List[str] = []
        root = self.git.root
        for rel in self.git.untracked():
            p = root / rel
            if p.is_file() and (p.suffix in {".py", ".md", ".env.example", ".sh", ".json", ".toml", ".yaml", ".yml"}):
                try:
                    lines.append(p.read_text(encoding="utf-8", errors="ignore"))
                except OSError:
                    continue
        return "\n".join(lines)


def assert_absolute_safety(report: SafetyReport) -> str:
    """Confirm report verdict; raise SafetyVeto on REJECT so the pipeline stops.
    Returns the verdict."""
    if report.approved:
        return "APPROVED"
    raise SafetyVeto("; ".join(report.issues))