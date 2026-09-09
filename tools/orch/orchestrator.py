"""Top-level Orchestrator for the APEX engineering orchestrator.

Wires together:
  - EngineStateStore (durable state)
  - Pipeline (stage machine)
  - AgentExecutor + provider abstraction
  - SafetyReviewer (deterministic fail-closed gate)
  - GitOps (commit only; never push/remote)
  - BuilderLock (single builder)
  - SessionDocs (markdown records) and OrchestratorLogger

Command interface:
  Orchestrator.run()     — advance the current task through the pipeline
  Orchestrator.recover() — determine current stage / resume safety
  Orchestrator.pause()   — safe pause (persist, prevent new implementation)
  Orchestrator.resume()  — continue after a pause
  Orchestrator.status()  — report current durable state

Recovery always reads SESSION_STATE/engine_state, inspects Git, inspects the
active lock, determines the current stage, verifies repository consistency, and
resumes only from a safe checkpoint. It never guesses from memory.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from . import gitops as gitops_mod
from .agents import AgentExecutor, make_default_registry
from .committee import AgentCommittee
from .gitops import GitOps
from .lock import BuilderLock, LockError, acquire_builder_lock
from .logging import OrchestratorLogger
from .pipeline import (
    Blocked,
    Pipeline,
    PipelineConfig,
    StepOutcome,
    _non_state_files,
    missing_required_evidence,
)
from .provider import ProviderRegistry
from .safety import SafetyReviewer, SafetyVeto
from .session import SessionDocs
from .state import EngineState, EngineStateStore, TaskState, STAGES, utc_now_iso, ROOT
from . import state as state_mod

# Tasks the orchestrator may NOT start (blocked-until-human).
HUMAN_APPROVAL_REQUIRED = (
    "live trading",
    "production exchange",
    "secrets",
    "api credentials",
    "disabling safety",
    "risk guardian veto",
    "unknown remote",
)


class OrchestratorStatus:
    def __init__(self, state: EngineState, task: Optional[TaskState] = None,
                 provider: str = "", lock_held: bool = False,
                 lock_owner: str = "", safe_to_resume: bool = False,
                 git_clean: bool = True, repo_consistent: bool = True,
                 blockers: Optional[list] = None) -> None:
        self.state = state
        self.task = task
        self.provider = provider
        self.lock_held = lock_held
        self.lock_owner = lock_owner
        self.safe_to_resume = safe_to_resume
        self.git_clean = git_clean
        self.repo_consistent = repo_consistent
        self.blockers = blockers or []

    def as_dict(self) -> dict:
        return {
            "current_task_id": self.state.current_task_id,
            "pipeline_stage": self.state.pipeline_stage,
            "last_successful_stage": self.state.last_successful_stage,
            "retry_count": self.state.retry_count,
            "paused": self.state.paused,
            "blocking": self.state.blocking,
            "human_required_blocker": self.state.human_required_blocker,
            "provider": self.provider,
            "lock_held": self.lock_held,
            "lock_owner": self.lock_owner,
            "safe_to_resume": self.safe_to_resume,
            "git_clean": self.git_clean,
            "repo_consistent": self.repo_consistent,
            "last_failure": self._lazy(state_mod.FailureRecord and self.state.last_failure),
            "last_reviewer_verdict": self._lazy(self.state.last_reviewer_verdict),
            "blockers": self.blockers,
            "task": self.task.checkpointed() if self.task else None,
        }

    @staticmethod
    def _lazy(v):
        return v.checkpointed() if hasattr(v, "checkpointed") else str(v)


class Orchestrator:
    def __init__(
        self,
        store: Optional[EngineStateStore] = None,
        git: Optional[GitOps] = None,
        registry: Optional[ProviderRegistry] = None,
        config: Optional[PipelineConfig] = None,
        logger: Optional[OrchestratorLogger] = None,
        docs: Optional[SessionDocs] = None,
        safety: Optional[SafetyReviewer] = None,
        lock: Optional[BuilderLock] = None,
        committee: Optional[AgentCommittee] = None,
    ) -> None:
        self.store = store or EngineStateStore()
        self.git = git or GitOps()
        self.logger = logger or OrchestratorLogger()
        self.docs = docs or SessionDocs()
        self.safety = safety or SafetyReviewer(self.git)
        self.lock = lock or BuilderLock()
        self.registry = registry or make_default_registry()
        self.config = config or PipelineConfig()
        # Advisory consensus committee (P2-13). When None (default), the
        # committee is NOT consulted and the deterministic gates alone decide,
        # preserving full backward compatibility. When provided, its verdict
        # is advisory ONLY: it can add REJECT issues but can never force an
        # approval past the deterministic SafetyReviewer gate.
        self.committee = committee
        self._preferred_provider: Optional[str] = None

        self.pipeline = Pipeline(self.store, self.config, owner=self)

    # ------------------------------------------------------------------ providers
    def set_provider(self, name: Optional[str]) -> None:
        self._preferred_provider = name

    # -- task queue ---------------------------------------------------------
    def _next_task_from_queue(self) -> Dict:
        """Read .ai/queue/QUEUE.md and return the first *incomplete* task, or {}.

        Eligibility rules (defense-in-depth so a completed task can never be
        re-enrolled):
          1. Entries marked done ('[x]') in QUEUE.md are skipped.
          2. A task already recorded as COMMITTED in durable state
             (engine_state.json) is skipped — even if QUEUE.md is stale.
          3. The first remaining entry is returned with a stable roadmap
             task id (e.g. 'P2-11').
        """
        queue_file = state_mod.AI_DIR / "queue" / "QUEUE.md"
        try:
            text = queue_file.read_text(encoding="utf-8")
        except OSError:
            return {}
        state = self.store.load()
        committed = {
            task.task_id
            for task in state.tasks.values()
            if task.status == "COMMITTED"
        }
        for title, task_id in self._parse_queue_entries(text):
            if task_id and task_id in committed:
                # Already completed && in durable state: never re-enroll.
                continue
            return {"task_id": task_id or title.strip()[:16], "title": title.strip()}
        return {}

    def _parse_queue_entries(self, text: str):
        """Yield ``(title, task_id)`` for each non-done queue entry in order.

        A QUEUE.md line looks like ``1. P2-11 — Add isolated testnet support``.
        Returns a stable roadmap task id parsed from the entry (e.g. 'P2-11').
        """
        import re
        for line in text.splitlines():
            m = re.match(r"^\s*(\d+)\.\s+(.*)$", line)
            if not m:
                continue
            entry = m.group(2).strip()
            # Skip entries explicitly marked done.
            if "[x]" in entry.lower():
                continue
            title = entry.strip("`")
            # Stable roadmap id if present (e.g. 'P2-11'); else fall back to the
            # queue list position ('P<n>') as the task id.
            idm_task = re.match(r"^(P\d+(?:-\d+)?)\b", title)
            if idm_task:
                task_id = idm_task.group(1)
                title = title[idm_task.end():]
                title = title.lstrip("–—-: ")
            else:
                task_id = f"P{m.group(1)}"
            yield title, task_id

    def _parse_queue(self, text: str):
        """Deprecated shim: return (title, task_id) of the first eligible entry."""
        for title, task_id in self._parse_queue_entries(text):
            return title, task_id
        return "", ""

    # -- status ---------------------------------------------------------------
    def status(self) -> OrchestratorStatus:
        state = self.store.load()
        task = state.tasks.get(state.current_task_id)
        prov_available = self.registry.available_providers()
        provider = self._preferred_provider if (self._preferred_provider in prov_available) \
            else (prov_available[0] if prov_available else "none")
        lock_held = self.lock.is_held()
        lock_owner = self.lock.owner_token() or ""
        safe = task.safe_to_resume if task else True
        git_clean = self.git.is_clean()
        repo_consistent = self._repo_consistent()
        blockers = []
        if state.blocking:
            blockers.append(state.human_required_blocker)
        return OrchestratorStatus(
            state=state, task=task, provider=provider, lock_held=lock_held,
            lock_owner=lock_owner, safe_to_resume=safe, git_clean=git_clean,
            repo_consistent=repo_consistent, blockers=blockers,
        )

    def _repo_consistent(self) -> bool:
        # A repo is considered consistent if state file is parseable and we can
        # determine the current task/stage without ambiguity.
        try:
            self.store.load()
            return True
        except Exception:
            return False

    # -- run -------------------------------------------------------------------
    def run(self) -> OrchestratorStatus:
        """Advance the current task through the pipeline until it commits or blocks."""
        state = self.store.load()
        if state.blocking:
            self.logger.log(event="run_blocked_human_required", task=self._cur(state),
                            result="BLOCKED")
            return self.status()

        if state.paused:
            self.logger.log(event="run_paused", task=self._cur(state), result="PAUSED")
            return self.status()

        task = self._ensure_current_task(state)
        if task is None:
            self.logger.log(event="run_no_task_available", task="", result="IDLE")
            return self.status()

        # Guard: do not start tasks requiring human approval.
        for marker in HUMAN_APPROVAL_REQUIRED:
            if marker in (task.title or "").lower():
                self._block(state, f"human_required_blocker task={task.task_id} reason={marker}")
                return self.status()

        try:
            self._advance_loop(state, task)
        except Blocked as b:
            self._block(state, b.reason, classification=b.classification)

        self.logger.log(event="run_iteration_complete", task=self._cur(state, task),
                        stage=task.stage, result=task.status)
        self.store.save(state)
        return self.status()

    def _advance_loop(self, state, task):
        # Advance up to N steps; bounded iteration prevents infinite spins.
        # The loop processes each current stage. It stops once a task has been
        # fully committed (STATE_UPDATE marks it COMMITTED), or the stage stopped
        # changing (terminal/unknown stage), or the task entered a terminal status.
        for _ in range(64):
            if task.status in ("COMMITTED", "FAILED", "BLOCKED"):
                break
            before = task.stage
            self.pipeline.advance(state, task)
            self.store.save(state)
            if task.status in ("COMMITTED", "FAILED", "BLOCKED"):
                break
            if task.stage == before:
                # Stage did not advance; if it's the final STATE_UPDATE stage it
                # was already processed above (status became COMMITTED then).
                break

    def _ensure_current_task(self, state) -> Optional[TaskState]:
        task = state.tasks.get(state.current_task_id)
        if task is not None and task.status not in ("COMMITTED", "FAILED", "BLOCKED"):
            state.pipeline_stage = task.stage
            return task
        # No current task enrolled. The orchestrator does NOT auto-start the
        # next queued task by itself: queue advancement is an explicit, gated
        # operator action (see advance_to_next_queue_task) so that P2-11 is
        # never started unless a human/operator explicitly enrolls it.
        return task if task is not None else None

    def advance_to_next_queue_task(self) -> OrchestratorStatus:
        """Explicitly enroll the next unfinished task from QUEUE.md.

        This is a deliberate, gated operation — it is NOT invoked by run().
        The orchestrator only advances the queue after the current task is
        fully approved and committed. Tasks requiring human approval are
        refused.
        """
        state = self.store.load()
        cur = state.tasks.get(state.current_task_id)
        if cur is not None and cur.status != "COMMITTED":
            self.logger.log(event="queue_advance_blocked_current_not_committed",
                            task=cur.task_id, result="BLOCKED")
            self._block(state, f"current_task_not_committed {cur.task_id}")
            return self.status()

        nxt = self._next_task_from_queue()
        if not nxt:
            self.logger.log(event="queue_empty", result="IDLE")
            return self.status()

        for marker in HUMAN_APPROVAL_REQUIRED:
            if marker in (nxt.get("title") or "").lower():
                title = nxt["title"]
                self._block(state, f"human_required_blocker queue_advance reason={marker} task={title}")
                return self.status()

        task = TaskState(task_id=nxt["task_id"], title=nxt["title"], status="QUEUED")
        state.tasks[task.task_id] = task
        state.current_task_id = task.task_id
        state.pipeline_stage = task.stage
        state.blocking = False
        state.human_required_blocker = ""
        self.store.save(state)
        self.logger.log(event="queue_advance", task=task.task_id, stage=task.stage,
                        result="ENQUEUED")
        return self.status()

    def _cur(self, state, task=None) -> str:
        t = task or state.tasks.get(state.current_task_id)
        return t.task_id if t else state.current_task_id

    # -- pause / resume ----------------------------------------------------------
    def pause(self) -> OrchestratorStatus:
        state = self.store.load()
        state.paused = True
        state.checkpoint_log.append(f"PAUSE@{utc_now_iso()}")
        self.store.save(state)
        if state.current_task_id:
            task = state.tasks.get(state.current_task_id)
            if task:
                task.paused = True
                self.store.save(state)
        self.logger.log(event="orchestrator_paused", result="PAUSED")
        return self.status()

    def resume(self) -> OrchestratorStatus:
        state = self.store.load()
        if not state.paused and not state.blocking:
            return self.status()
        state.paused = False
        state.checkpoint_log.append(f"RESUME@{utc_now_iso()}")
        if state.current_task_id:
            task = state.tasks.get(state.current_task_id)
            if task:
                task.paused = False
        self.store.save(state)
        self.logger.log(event="orchestrator_resumed", result="RUNNING")
        return self.status()

    # -- recovery -----------------------------------------------------------------
    def recover(self) -> OrchestratorStatus:
        """Read durable state, inspect Git + lock, determine stage, verify
        consistency, and mark safe-to-resume from the last safe checkpoint."""
        state = self.store.load()
        git_clean = self.git.is_clean()
        lock_held = self.lock.is_held()
        lock_stale = lock_held and self.lock.is_stale()

        consistent = self._repo_consistent()
        safe = git_clean and consistent

        # If a stale lock lingers, note it but do not auto-delete a valid one.
        stale_note = "stale_lock_detected" if lock_stale else ""

        self.logger.log(event="recovery", task=self._cur(state), result="RECOVERED",
                        checkpoint=state.last_checkpoint, error=stale_note)
        status = self.status()
        return status

    # -- blocking helper ------------------------------------------------------------
    def _block(self, state, reason, classification: str = "BLOCKER") -> None:
        state.blocking = True
        state.human_required_blocker = reason
        state.paused = False
        self.logger.log(event="human_required_blocker", task=self._cur(state),
                        result="BLOCKED", classification=classification, error=reason)
        self.store.save(state)
        # Update SESSION_STATE.md to make the blocker durable and human-visible.
        self.docs.update_blocker(reason)

    # ------------------------------------------------------------------------ actions
    def _act_plan(self, task) -> StepOutcome:
        self.logger.log(event="planner", task=task.task_id, stage="PLAN", agent="PLANNER",
                        provider=self._preferred_provider or "local", result="OK")
        # In the offline/test adapter the plan is deterministic no-op. A real
        # provider integration (future) would produce an implementation plan.
        return StepOutcome(ok=True)

    def _act_build(self, task) -> StepOutcome:
        # Only called while holding the builder lock. Implementation writes to
        # the repo; in offline/test mode the concrete work is injected via the
        # registry's LocalFallbackProvider or a test override. FAIL CLOSED: a
        # build that produced no application files / no real diff is NOT a
        # success — otherwise a no-op build could carry a task to COMMITTED.
        self.logger.log(event="builder", task=task.task_id, stage="IMPLEMENT", agent="BUILDER",
                        result="OK")
        app_files = _non_state_files(task.modified_files or [])
        if not app_files:
            return StepOutcome(ok=False, issues=["no_application_files_changed"],
                               message="build_produced_no_application_files")
        changed = set(self.git.changed_files())
        missing = [f for f in app_files if f not in changed]
        if missing:
            return StepOutcome(ok=False, issues=["implementation_files_not_in_diff"],
                               message=f"build_reported_files_not_in_diff {missing}")
        task.implementation_files = app_files
        return StepOutcome(ok=True, evidence=f"implementation_files {' '.join(app_files)}")

    def _run_test_suite(self, label: str) -> StepOutcome:
        """Run the repository's actual test suite and require genuine evidence.

        Returns ok=True ONLY when the suite ran and reported a passing summary.
        Fail closed (ok=False) on any error, non-zero exit, empty, or skipped-run."""
        import re
        import subprocess
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q"],
                cwd=str(self.git.root),
                capture_output=True,
                text=True,
                timeout=1800,
            )
        except Exception as e:  # noqa: BLE001
            return StepOutcome(ok=False, message=f"{label}_could_not_run {e}")
        combined = (proc.stdout or "") + (proc.stderr or "")
        summary = ""
        m = re.search(r"(\d+ passed[^\n]*)", combined)
        if m:
            summary = m.group(1).strip().splitlines()[0]
        if "no tests ran" in combined:
            summary = summary or "no tests ran"
        if proc.returncode == 0 and summary and "failed" not in summary.lower():
            return StepOutcome(ok=True, evidence=f"{label}: {summary}")
        return StepOutcome(ok=False, issues=[summary or "no_test_summary"],
                           message=f"{label}_failed {summary or combined.strip()[-500:]}")

    def _act_run_tests(self, task) -> StepOutcome:
        out = self._run_test_suite("TEST")
        self.logger.log(event="tester", task=task.task_id, stage="TEST", agent="TESTER",
                        result="PASS" if out.ok else "FAIL", error=out.message)
        return out

    def _act_run_regression(self, task) -> StepOutcome:
        out = self._run_test_suite("REGRESSION")
        self.logger.log(event="regression", task=task.task_id, stage="REGRESSION", agent="TESTER",
                        result="PASS" if out.ok else "FAIL", error=out.message)
        return out

    def _act_security_review(self, task) -> StepOutcome:
        try:
            report = self.safety.inspect()
        except SafetyVeto as e:
            # Absolute safety violation -> hard block immediately.
            raise Blocked(task, f"absolute_safety_veto: {e.message}",
                          classification="SAFETY_VETO")
        self.logger.log(event="security_review", task=task.task_id, stage="SECURITY_REVIEW",
                        agent="SECURITY_REVIEWER", result=report.verdict,
                        error="; ".join(report.issues))
        return StepOutcome(ok=report.approved, issues=report.issues,
                           message="; ".join(report.issues),
                           evidence=f"security_report verdict={report.verdict} issues={len(report.issues)}")

    def _act_final_review(self, task) -> StepOutcome:
        issues = self._final_review_checks(task)
        # Advisory committee consensus (P2-13): when a committee is configured,
        # its independent reviewers weigh in. A committee that fails to reach
        # quorum, rejects, or is inconclusive adds fail-closed issues — it can
        # only block approval, never force one past the deterministic gates.
        committee = self._advisory_committee_issues(task)
        issues.extend(committee)
        ok = not issues
        self.logger.log(event="final_review", task=task.task_id, stage="FINAL_REVIEW",
                        agent="FINAL_REVIEWER", result="APPROVED" if ok else "REJECTED",
                        error="; ".join(issues))
        return StepOutcome(ok=ok, issues=issues, message="; ".join(issues),
                           evidence=f"final_review verdict={'APPROVED' if ok else 'REJECTED'}"
                                    f" committee_issues={len(committee)}")

    def _advisory_committee_issues(self, task) -> List[str]:
        """Run the advisory committee (if configured). Fail closed:
        unusable/absent quorum or a REJECT yields issues; an APPROVE is never
        sufficient on its own and adds no positive authority."""
        committee = getattr(self, "committee", None)
        if committee is None:
            return []
        verdict = committee.run(
            f"VALIDATE task {task.task_id} {task.title} for approval",
            {"agent": "COMMITTEE", "task_id": task.task_id},
        )
        if verdict.approved:
            # Advisory approval carries no extra authority beyond the
            # deterministic gates; record it but assert nothing.
            return []
        return list(verdict.issues)

    def _final_review_checks(self, task) -> list:
        issues = []
        # FINAL_REVIEW cannot approve a task whose required prior stages were
        # skipped or never genuinely executed (fail closed).
        missing = missing_required_evidence(task)
        issues.extend(f"final_review_skipped_stage:{m}" for m in missing)
        state = self.store.load()
        # Security review must have passed for this stage to be reached, but
        # final reviewer independently reconfirms protections.
        try:
            self.safety.inspect()
        except SafetyVeto as e:
            issues.append(str(e))
        if not self.git.is_clean() and task.stage != "COMMIT":
            # During final review we allow the pending build diff to be present,
            # but not unrelated files. For simplicity: unrelated untracked but no
            # requirement here; commit gate enforces cleanliness later.
            pass
        return issues

    def _act_verify_commit_gate(self, task) -> None:
        # diff --check whitespace gate.
        if self.git.diff_check():
            raise Blocked(task, "diff_check_failed",
                          classification="COMMIT_GATE")
        self.git.assert_no_unknown_remote()

        # Integrity: every required stage must have genuinely executed and the
        # counters must reflect a real run. A task that skipped TEST / SECURITY
        # REVIEW / REGRESSION — or that has no real implementation diff — can
        # never pass the commit gate.
        missing = missing_required_evidence(task)
        if missing:
            raise Blocked(task, f"commit_gate_required_stage_missing {missing}",
                          classification="INTEGRITY_FAILURE")
        if task.test_attempts < 1:
            raise Blocked(task, "commit_gate_test_never_executed",
                          classification="INTEGRITY_FAILURE")
        if task.security_cycles < 1:
            raise Blocked(task, "commit_gate_security_never_executed",
                          classification="INTEGRITY_FAILURE")

        # Implementation tasks must carry a real non-.ai implementation diff.
        app_files = _non_state_files(task.modified_files or []) \
            or _non_state_files(task.implementation_files or [])
        if not app_files:
            raise Blocked(task, "commit_gate_no_implementation_diff",
                          classification="INTEGRITY_FAILURE")
        changed = set(self.git.changed_files())
        missing_files = [f for f in app_files if f not in changed]
        if missing_files:
            raise Blocked(task, f"commit_gate_files_not_in_diff {missing_files}",
                          classification="INTEGRITY_FAILURE")

        # Dirty-working-tree rejection: the working tree may contain ONLY the
        # files the Builder reports having modified (task.modified_files). Any
        # unpresented/foreign change is rejected, and a no-op build that reports
        # nothing must leave the tree clean.
        present = set(self.git.changed_files())
        registered = set(task.modified_files or [])
        if not registered:
            if present:
                raise Blocked(task, f"dirty_working_tree_rejected {sorted(present)}",
                              classification="COMMIT_GATE")
            return
        foreign = present - registered
        if foreign:
            raise Blocked(task, f"dirty_working_tree_rejected {sorted(foreign)}",
                          classification="COMMIT_GATE")

    def _act_commit(self, task) -> StepOutcome:
        # Re-run the commit gate so reliance is never on a previous review alone.
        try:
            self._act_verify_commit_gate(task)
        except Blocked as b:
            return StepOutcome(ok=False, classification="COMMIT_GATE", message=b.reason)
        # Write the durable state/session docs BEFORE staging so they are part
        # of the same single-task commit, leaving the tree clean afterward.
        self._write_final_docs(task)
        pre = self.git.changed_files()
        # A state-only commit (only .ai/docs) can never satisfy an implementation
        # task — reject before committing (fail closed).
        app_in_commit = _non_state_files(pre)
        if not app_in_commit:
            return StepOutcome(ok=False, classification="COMMIT_GATE",
                               message=f"state_only_commit_rejected files={sorted(pre)}")
        sha = self.git.stage_and_commit(
            f"{task.task_id} {task.title}")
        task.commit = sha
        task.commit_files = sorted(pre)
        self.logger.log(event="git_commit", task=task.task_id, stage="COMMIT", agent="builder",
                        result="COMMITTED", commit=sha)
        return StepOutcome(ok=True, data=sha,
                           evidence=f"commit={sha} files={len(pre)} app_files={len(app_in_commit)}")

    def _write_final_docs(self, task) -> None:
        if not task.commit:
            # placeholder until the commit SHA is known; docs updated in
            # STATE_UPDATE with the real SHA via a lightweight, already-committed
            # note. To keep the tree clean, the completion record is tied to the
            # task's last_checkpoint, not a separate commit.
            self.docs.append_completed(task, task.last_checkpoint or "(pending)")
            self.docs.write_session(current=task.task_id, status="COMMITTING",
                                    events=[f"{task.task_id} {task.title} approved, committing"])
        else:
            self.docs.append_completed(task, task.commit)

    def _act_update_state_docs(self, task) -> None:
        # Completion record is finalized in engine_state.json (authoritative,
        # gitignored). No additional markdown files are written here so the
        # working tree remains clean after the single-task commit. The markdown
        # record was written in _write_final_docs (before commit) and committed
        # alongside the code.
        self.logger.log(event="state_update_complete", task=task.task_id,
                        stage="STATE_UPDATE", commit=task.commit or "")

    def _act_acquire_builder(self, task) -> Optional[str]:
        if self.lock.is_held():
            raise LockError("builder_lock_held")
        return self.lock.acquire(owner=task.task_id)

    def _act_release_builder(self, task, *args) -> None:
        if self.lock.token is not None:
            self.lock.release()