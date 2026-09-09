"""Agent committee: consensus aggregation of independent advisory reviewers.

P2-13 — the engineering orchestrator currently relies on a single reviewer
authority per stage. This module introduces a committee: a set of independent
advisory agents, each run through the provider abstraction, whose individual
verdicts (APPROVE / REJECT / ABSTAIN) are aggregated into a single committee
verdict by a deterministic rule.

SAFETY CONTRACT (fail closed, advisory-only):
  - The committee is ADVISORY. Its output informs the engineering review stages
    only; it carries NO direct execution or trading authority. No order,
    execution, or account path reads committee output.
  - The committee can never WEAKEN a decision: an APPROVE requires a genuine
    quorum of participating members to agree; any member that is unavailable,
    errors, abstains, or returns an unusable verdict keeps the committee from
    approving (the verdict defaults to NOT-APPROVED / INCONCLUSIVE).
  - A committee APPROVE is never sufficient on its own — the deterministic
    SafetyReviewer gate remains authoritative and must still pass. A committee
    REJECT adds issues (fail closed); it can only block, never force-approve.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .agents import AgentExecutor
from .provider import ProviderRegistry, make_default_registry


class ReviewVote(str):
    """Strict vocabulary for a single committee member's vote."""

    APPROVE = "APPROVE"
    REJECT = "REJECT"
    ABSTAIN = "ABSTAIN"


@dataclass
class ReviewerVote:
    """One advisory committee member's independent judgment."""

    reviewer: str            # role/member id, e.g. "SECURITY_REVIEWER"
    vote: str                # ReviewVote: APPROVE | REJECT | ABSTAIN
    reasoning: str = ""
    provider: str = ""
    ok: bool = True          # False if the member could not produce a verdict

    def to_dict(self) -> dict:
        return {
            "reviewer": self.reviewer,
            "vote": self.vote,
            "reasoning": self.reasoning,
            "provider": self.provider,
            "ok": self.ok,
        }


@dataclass
class CommitteeVerdict:
    """Aggregated advisory verdict of the whole committee."""

    verdict: str             # APPROVED | REJECTED | INCONCLUSIVE
    required_quorum: int
    votes: List[ReviewerVote] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)

    @property
    def approved(self) -> bool:
        return self.verdict == "APPROVED"

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "required_quorum": self.required_quorum,
            "issues": self.issues,
            "votes": [v.to_dict() for v in self.votes],
        }


class AgentCommittee:
    """Runs independent advisory members through the provider abstraction and
    aggregates their votes deterministically.

    A ``committee_member`` is a callable ``(prompt, context) -> ReviewerVote``.
    The default members are built from ``make_default_committee`` and use the
    provider registry so each member can be served by a distinct provider/role.

    Aggregation rule (fail closed):
        - There must be at least ``quorum`` members that produced a usable
          APPROVE/REJECT vote (``vote.ok is False`` or ABSTAIN do NOT count
          toward quorum).
        - If fewer than ``quorum`` usable votes exist, the verdict is
          INCONCLUSIVE (NOT approved).
        - If a usable REJECT vote exists, the verdict is REJECTED.
        - Otherwise (all usable votes are APPROVE and quorum satisfied), the
          verdict is APPROVED.
    """

    def __init__(
        self,
        members: Optional[List[Callable[[str, Dict], ReviewerVote]]] = None,
        quorum: Optional[int] = None,
        executor: Optional[AgentExecutor] = None,
    ) -> None:
        self.executor = executor or AgentExecutor()  # uses default registry
        self.members: List[Callable[[str, Dict], ReviewerVote]] = \
            list(members) if members is not None else make_default_committee(self.executor)
        self.quorum = quorum if quorum is not None else max(1, len(self.members))

    def run(self, prompt: str, context: Optional[Dict] = None) -> CommitteeVerdict:
        if not self.members:
            return CommitteeVerdict(verdict="INCONCLUSIVE", required_quorum=self.quorum,
                                    issues=["committee_no_members"])
        votes: List[ReviewerVote] = []
        for member in self.members:
            try:
                vote = member(prompt, context or {})
            except Exception as exc:  # noqa: BLE001 - fail closed, never raise
                votes.append(ReviewerVote(reviewer=getattr(member, "__name__", "unknown"),
                                          vote=ReviewVote.ABSTAIN,
                                          reasoning=f"member_error: {exc}",
                                          ok=False))
                continue
            if not isinstance(vote, ReviewerVote):
                votes.append(ReviewerVote(reviewer=getattr(member, "__name__", "unknown"),
                                          vote=ReviewVote.ABSTAIN,
                                          reasoning="member_returned_non_vote",
                                          ok=False))
                continue
            votes.append(vote)
        return self._aggregate(votes)

    def _aggregate(self, votes: List[ReviewerVote]) -> CommitteeVerdict:
        usable = [v for v in votes if v.ok and v.vote in (ReviewVote.APPROVE, ReviewVote.REJECT)]
        issues: List[str] = []
        rejected = [v for v in usable if v.vote == ReviewVote.REJECT]
        unusable = [v for v in votes if not (v.ok and v.vote in (ReviewVote.APPROVE, ReviewVote.REJECT))]

        if len(usable) < self.quorum:
            issues.append(f"committee_quorum_not_met usable={len(usable)} required={self.quorum}")
            for v in unusable:
                issues.append(f"member_unusable:{v.reviewer}")
            return CommitteeVerdict(verdict="INCONCLUSIVE", required_quorum=self.quorum,
                                    votes=votes, issues=issues)
        if rejected:
            for v in rejected:
                issues.append(f"committee_reject:{v.reviewer}:{(v.reasoning or '')[:200]}")
            return CommitteeVerdict(verdict="REJECTED", required_quorum=self.quorum,
                                    votes=votes, issues=issues)
        # All usable votes approve and quorum satisfied.
        return CommitteeVerdict(verdict="APPROVED", required_quorum=self.quorum,
                                votes=votes, issues=issues)


# Default committee member roles (advisory reviewers). Each runs the same prompt
# through the provider abstraction under a distinct role identity.
_DEFAULT_MEMBER_ROLES = [
    ("SECURITY_REVIEWER", "security"),
    ("FINANCIAL_RISK_REVIEWER", "risk"),
    ("DESIGN_REVIEWER", "design"),
]


def _member_for_role(executor: AgentExecutor, role: str, tag: str):
    def member(prompt: str, context: Dict) -> ReviewerVote:
        out = executor.run_agent(role, prompt)
        if not out.ok:
            return ReviewerVote(reviewer=role, vote=ReviewVote.ABSTAIN,
                                reasoning=out.error or "member_failed",
                                provider=out.provider, ok=False)
        text = (out.output or "").strip()
        upper = text.upper()
        if "REJECT" in upper and "APPROVE" not in upper:
            return ReviewerVote(reviewer=role, vote=ReviewVote.REJECT,
                                reasoning=text, provider=out.provider)
        if "APPROVE" in upper:
            return ReviewerVote(reviewer=role, vote=ReviewVote.APPROVE,
                                reasoning=text, provider=out.provider)
        return ReviewerVote(reviewer=role, vote=ReviewVote.ABSTAIN,
                            reasoning=text or "no_verdict_text", provider=out.provider)

    member.__name__ = role
    return member


def make_default_committee(executor: Optional[AgentExecutor] = None) -> List[Callable]:
    """Build the default advisory committee over the given provider executor."""
    executor = executor or AgentExecutor()
    return [_member_for_role(executor, role, tag) for role, tag in _DEFAULT_MEMBER_ROLES]


def make_default_registry_committee(offline_mode: bool = False) -> AgentCommittee:
    """Convenience builder: default committee over the default provider registry."""
    registry = make_default_registry(offline_mode=offline_mode)
    executor = AgentExecutor(registry=registry)
    return AgentCommittee(members=make_default_committee(executor), executor=executor)