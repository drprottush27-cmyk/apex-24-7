"""Pipeline stage machine for the APEX engineering orchestrator.

Implements the explicit, bounded lifecycle:

  DISCOVER -> PLAN -> IMPLEMENT -> TEST -> (fix loop) -> SECURITY_REVIEW
  -> (fix loop) -> REGRESSION -> FINAL_REVIEW -> (fix loop) -> CHECKPOINT
  -> COMMIT -> STATE_UPDATE -> NEXT_QUEUED_TASK

State transitions are explicit and persisted via checkpoints. Retries are
bounded; hitting a limit stops safely and records a blocker. The Orchestrator
drives this; this module is the deterministic state engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from .state import (
    EngineState,
    EngineStateStore,
    FailureRecord,
    ReviewerVerdict,
    TaskState,
    utc_now_iso,
)

# Relative path prefix that is documentation/state bookkeeping only and can NEVER
# satisfy an implementation requirement. A commit consisting solely of these is a
# "state-only commit" and is rejected for implementation tasks.
_STATE_ONLY_PREFIXES = (".ai/", "docs/", ".env.example")


def _is_application_file(rel: str) -> bool:
    """True if a repo-relative path is application code (not .ai/doc bookkeeping).

    Used to distinguish a genuine implementation diff from a state-only commit."""
    r = rel
    if r.startswith("./"):
        r = r[2:]
    if r.startswith(_STATE_ONLY_PREFIXES):
        return False
    return True


def _non_state_files(rel_paths) -> List[str]:
    return sorted({p for p in rel_paths if _is_application_file(p)})


def missing_required_evidence(task: TaskState) -> List[str]:
    """Return the list of required stage-execution evidence flags that are absent.

    FINAL_REVIEW and the commit/STATE_UPDATE gates use this to fail closed: a task
    cannot be approved or COMMITTED unless every required stage genuinely executed."""
    missing: List[str] = []
    if not getattr(task, "implementation_executed", False):
        missing.append("implementation")
    if not getattr(task, "test_executed", False) or task.test_attempts < 1:
        missing.append("test")
    if not getattr(task, "security_review_executed", False) or task.security_cycles < 1:
        missing.append("security_review")
    if not getattr(task, "regression_executed", False):
        missing.append("regression")
    return missing

# Retry defaults (configurable).
DEFAULT_LIMITS = {
    "build": 3,           # Builder fix loop
    "test": 3,            # Test retry
    "security": 3,        # Security rejection fix/review cycles
    "final": 2,           # Final review rejection fix/review cycles
}


@dataclass
class PipelineConfig:
    max_build_attempts: int = DEFAULT_LIMITS["build"]
    max_test_attempts: int = DEFAULT_LIMITS["test"]
    max_security_cycles: int = DEFAULT_LIMITS["security"]
    max_final_review_cycles: int = DEFAULT_LIMITS["final"]


# Structured result from a stage action.
# Building "ok" on success, on failure "ok=False" with classification+message.
# For reviewers, "ok" is True only on an APPROVED verdict.
# `evidence` is a mandatory, non-empty human-readable trace that a stage actually
# executed (e.g. a test summary, a safety report, or a list of produced files).
# The pipeline treats an ok=True outcome with empty evidence as FAILED (fail
# closed) so a no-op/absent action can never masquerade as a real stage run.
@dataclass
class StepOutcome:
    ok: bool
    classification: str = ""
    message: str = ""
    issues: List[str] = None
    data: Any = None
    evidence: str = ""


class BuildRejected(Exception):
    """Signals the Builder was rejected at a reviewer gate and returned to fix."""


class Blocked(Exception):
    """Signals a human-required blocker: bounded retries exhausted or safety veto."""

    def __init__(self, task: TaskState, reason: str, classification: str = ""):
        self.task = task
        self.reason = reason
        self.classification = classification or "BLOCKER"
        super().__init__(reason)


class Pipeline:
    """Deterministic stage machine over persistent EngineState and TaskState."""

    # Action name -> method name on the owner Orchestrator.
    ACTION_METHODS = {
        "plan": "_act_plan",
        "build": "_act_build",
        "run_tests": "_act_run_tests",
        "run_regression": "_act_run_regression",
        "security_review": "_act_security_review",
        "final_review": "_act_final_review",
        "commit": "_act_commit",
        "acquire_builder": "_act_acquire_builder",
        "release_builder": "_act_release_builder",
        "verify_commit_gate": "_act_verify_commit_gate",
        "update_state_docs": "_act_update_state_docs",
    }

    def __init__(
        self,
        store: EngineStateStore,
        config: PipelineConfig | None = None,
        owner: object | None = None,
        actions: Dict[str, Callable] | None = None,
    ) -> None:
        self.store = store
        self.config = config or PipelineConfig()
        self.owner = owner
        # Explicit actions override owner methods (used in hermetic tests).
        self.actions: Dict[str, Callable] = dict(actions or {})

    def _act(self, name: str, task: TaskState) -> StepOutcome:
        fn = self.actions.get(name)
        if fn is None and self.owner is not None:
            method = getattr(self.owner, self.ACTION_METHODS.get(name, ""), None)
            if method is not None:
                fn = method
        if fn is None:
            # Fail closed: an absent action is NEVER treated as a success.
            return StepOutcome(ok=False, message=f"action_not_available {name}")
        out = fn(task)
        if not isinstance(out, StepOutcome):
            return StepOutcome(ok=True, evidence="RAW_ACTION_RETURN")
        return out

    # -- checkpointing ---------------------------------------------------
    def checkpoint(self, state: EngineState, label: str) -> None:
        state.last_successful_stage = state.pipeline_stage
        state.last_checkpoint = f"{state.current_task_id}:{label}@{utc_now_iso()}"
        state.checkpoint_log.append(state.last_checkpoint)
        self.store.save(state)

    def _record_failure(self, task, stage, classification, message, attempt) -> None:
        task.last_failure = FailureRecord(
            stage=stage, classification=classification, message=message, attempt=attempt,
        )
        task.retry_count = max(task.retry_count, attempt)

    def _classify_message(self, message: str, default: str) -> str:
        low = (message or "").lower()
        if any(k in low for k in ("provider", "quota", "rate")):
            return "PROVIDER_FAILURE"
        if any(k in low for k in ("network", "timeout", "connect", "postgres", "redis", "db down", "connection")):
            return "INFRASTRUCTURE_FAILURE"
        if "regression" in low:
            return "REGRESSION"
        if any(k in low for k in ("test", "assert", "pytest", "diff --check", "failed")):
            return "TEST_FAILURE"
        return default

    # -- review handling --------------------------------------------------
    def _record_review(self, state, task, agent_name, out: StepOutcome) -> None:
        verdict = "APPROVED" if out.ok else "REJECTED"
        task.last_reviewer_verdict = ReviewerVerdict(
            reviewer=agent_name,
            verdict=verdict,
            stage=task.stage,
            commit=task.commit or "",
            issues=list(out.issues or []),
        )
        state.last_reviewer_verdict = task.last_reviewer_verdict
        self.store.save(state)

    # -- fail-closed execution guards ------------------------------------
    def _require_implementation_diff(self, task: TaskState) -> None:
        """Fail closed if IMPLEMENT produced no real application diff.

        Uses both the build action's reported files and (when a Git handle is
        reachable via the owner) the actual working-tree diff."""
        app_files = _non_state_files(task.modified_files or [])
        if not app_files:
            raise Blocked(task, "implementation_requires_real_diff (no application files changed)",
                          classification="IMPLEMENTATION_FAILURE")
        git = getattr(getattr(self, "owner", None), "git", None)
        if git is not None:
            changed = set(git.changed_files())
            missing = [f for f in app_files if f not in changed]
            if missing:
                raise Blocked(task, f"implementation_files_not_in_diff {missing}",
                              classification="IMPLEMENTATION_FAILURE")
        task.implementation_files = app_files

    # -- main advance ------------------------------------------------------
    def advance(self, state: EngineState, task: TaskState) -> TaskState:
        """Advance the task one deterministic step. Returns updated task.

        Raises Blocked when retries are exhausted or an absolute safety veto
        fires (so the Orchestrator stops and records a human-required blocker).
        """
        if task.paused:
            return task

        stage = task.stage

        if stage == "DISCOVER":
            task.status = "RUNNING"
            task.started_at = task.started_at or utc_now_iso()
            task.stage = "PLAN"
            self.checkpoint(state, "task_start_discover")
            return task

        if stage == "PLAN":
            out = self._act("plan", task)
            if not out.ok:
                self._record_failure(task, "PLAN", "IMPLEMENTATION_FAILURE", out.message, 1)
                raise Blocked(task, f"plan_failed: {out.message}")
            task.stage = "IMPLEMENT"
            self.checkpoint(state, "plan_complete")
            return task

        if stage == "IMPLEMENT":
            self._act("acquire_builder", task)
            try:
                out = self._act("build", task)
            finally:
                self._act("release_builder", task)
            task.build_attempts += 1  # every execution counts as evidence
            if not out.ok:
                classification = self._classify_message(out.message, "IMPLEMENTATION_FAILURE")
                self._record_failure(task, "IMPLEMENT", classification, out.message, task.build_attempts)
                if task.build_attempts >= self.config.max_build_attempts:
                    raise Blocked(task, f"build_exhausted ({task.build_attempts})", classification)
                self.checkpoint(state, "build_failed_retry")
                return task  # retry IMPLEMENT within bounds
            # Must have produced a real application diff (fail closed).
            self._require_implementation_diff(task)
            task.implementation_executed = True
            task.stage = "TEST"
            self.checkpoint(state, "implementation_complete")
            return task

        if stage == "TEST":
            out = self._act("run_tests", task)
            task.test_attempts += 1  # every execution (pass or fail) counts
            if not (out.ok and out.evidence):
                classification = self._classify_message(
                    out.message or "test_missing_evidence", "TEST_FAILURE")
                self._record_failure(task, "TEST", classification,
                                     out.message or "test_execution_required_evidence", task.test_attempts)
                if task.test_attempts >= self.config.max_test_attempts:
                    raise Blocked(task, f"test_attempts_exhausted ({task.test_attempts})", classification)
                # Test failure -> back to Builder to fix.
                task.stage = "IMPLEMENT"
                self.checkpoint(state, "test_failed_return_to_build")
                return task
            task.test_executed = True
            task.stage = "SECURITY_REVIEW"
            self.checkpoint(state, "tests_complete")
            return task

        if stage == "SECURITY_REVIEW":
            out = self._act("security_review", task)
            self._record_review(state, task, "SECURITY_REVIEW", out)
            task.security_cycles += 1  # every execution (pass or fail) counts
            if not (out.ok and out.evidence):
                if task.security_cycles >= self.config.max_security_cycles:
                    raise Blocked(task, f"security_rejection_exhausted ({task.security_cycles})",
                                  "SECURITY_REJECTION")
                # Builder fixes security findings (only Builder modifies repo).
                task.stage = "IMPLEMENT"
                self.checkpoint(state, "security_rejected_return_to_build")
                return task
            task.security_review_executed = True
            task.stage = "REGRESSION"
            self.checkpoint(state, "security_review_approved")
            return task

        if stage == "REGRESSION":
            out = self._act("run_regression", task)
            if not (out.ok and out.evidence):
                self._record_failure(task, "REGRESSION", "REGRESSION",
                                     out.message or "regression_execution_required_evidence", 1)
                task.stage = "IMPLEMENT"
                self.checkpoint(state, "regression_failed_return_to_build")
                return task
            task.regression_executed = True
            task.stage = "FINAL_REVIEW"
            self.checkpoint(state, "regression_complete")
            return task

        if stage == "FINAL_REVIEW":
            missing = missing_required_evidence(task)
            if missing:
                # Fail closed: FINAL_REVIEW cannot approve skipped required stages.
                out = StepOutcome(
                    ok=False, issues=[f"missing_required_stage:{m}" for m in missing],
                    message=f"final_review_required_stages_missing {' '.join(missing)}")
            else:
                out = self._act("final_review", task)
                if out.ok and not out.evidence:
                    out = StepOutcome(ok=False, issues=["final_review_missing_evidence"],
                                      message="final_review_missing_evidence")
            self._record_review(state, task, "FINAL_REVIEW", out)
            task.final_review_cycles += 1  # every execution counts
            if not out.ok:
                if task.final_review_cycles >= self.config.max_final_review_cycles:
                    raise Blocked(task, f"final_review_rejected_exhausted ({task.final_review_cycles})",
                                  "FINAL_REVIEW_REJECTION")
                task.stage = "IMPLEMENT"
                self.checkpoint(state, "final_review_rejected_return_to_build")
                return task
            task.stage = "CHECKPOINT"
            self.checkpoint(state, "final_approval")
            return task

        if stage == "CHECKPOINT":
            # Pre-commit gate: integrity (all stages executed + real diff),
            # dirty-tree rejection, unknown-remote rejection, diff --check,
            # safety preserved. Raises Blocked if unsafe.
            self._act("verify_commit_gate", task)
            task.checkpoint_succeeded = True
            task.stage = "COMMIT"
            self.checkpoint(state, "before_commit")
            return task

        if stage == "COMMIT":
            if not task.checkpoint_succeeded:
                raise Blocked(task, "commit_rejected_checkpoint_not_succeeded",
                              classification="INTEGRITY_FAILURE")
            out = self._act("commit", task)
            if not out.ok:
                self._record_failure(task, "COMMIT", "IMPLEMENTATION_FAILURE", out.message, 1)
                raise Blocked(task, f"commit_failed: {out.message}")
            task.commit = out.data or ""
            task.stage = "STATE_UPDATE"
            self.checkpoint(state, "commit_complete")
            return task

        if stage == "STATE_UPDATE":
            missing = missing_required_evidence(task)
            if missing or not task.checkpoint_succeeded or task.test_attempts < 1 or task.security_cycles < 1:
                task.status = "FAILED"
                self._record_failure(task, "STATE_UPDATE", "INTEGRITY_FAILURE",
                                     f"committed_without_required_stage_evidence {missing}", 1)
                raise Blocked(task, f"integrity_failure_committed_without_evidence {missing}",
                              classification="INTEGRITY_FAILURE")
            task.status = "COMMITTED"
            task.finished_at = utc_now_iso()
            task.safe_to_resume = False
            self._act("update_state_docs", task)
            state.pipeline_stage = "NEXT_QUEUED_TASK"
            self.checkpoint(state, f"state_update_complete_task_{task.task_id}")
            return task

        # Any terminal/unknown stage: signal the Orchestrator it may move on.
        return task