"""Persistent engine state for the APEX engineering orchestrator.

The orchestrator MUST be able to determine — purely from durable on-disk
state, never from conversational memory:

  - current task
  - current pipeline stage
  - last successful stage
  - retry count (per loop and overall)
  - last failure (with classification)
  - last reviewer verdict
  - last checkpoint
  - whether a task is safe to resume

All writes are atomic (write-temp-then-rename) so a crash mid-write never
corrupts state, and a new process can always resume from the last safe
checkpoint.

State lives under ``.ai/state/engine_state.json``. Structured run logs go to
``.ai/logs/orchestrator-<date>.log``. Checkpoint markers are emitted under
``.ai/checkpoints/`` for the human-facing protocol.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


ROOT = Path(__file__).resolve().parent.parent
AI_DIR = ROOT / ".ai"
STATE_FILE = AI_DIR / "state" / "engine_state.json"
CHECKPOINTS_DIR = AI_DIR / "checkpoints"
LOGS_DIR = AI_DIR / "logs"

# Pipeline stages in order. The orchestrator advances through these; retries
# may re-enter earlier stages (e.g. TEST -> IMPLEMENT) within bounds.
STAGES = [
    "DISCOVER",
    "PLAN",
    "IMPLEMENT",
    "TEST",
    "SECURITY_REVIEW",
    "REGRESSION",
    "FINAL_REVIEW",
    "CHECKPOINT",
    "COMMIT",
    "STATE_UPDATE",
    "NEXT_QUEUED_TASK",
]

# Stages that permanently complete a task (safe to advance the queue).
TERMINAL_STAGES = {"COMMIT", "STATE_UPDATE"}

# Failure classifications (test gate / retry policy).
FAIL_CLASSIFICATIONS = {
    "IMPLEMENTATION_FAILURE",
    "TEST_FAILURE",
    "REGRESSION",
    "ENVIRONMENT_FAILURE",
    "INFRASTRUCTURE_FAILURE",
    "PROVIDER_FAILURE",
}


def _now_epoch_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class ReviewerVerdict:
    reviewer: str
    verdict: str  # APPROVED | REJECTED
    stage: str = ""
    commit: str = ""
    issues: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=utc_now_iso)


@dataclass
class FailureRecord:
    stage: str
    classification: str
    message: str
    attempt: int
    commit: str = ""
    timestamp: str = field(default_factory=utc_now_iso)


@dataclass
class TaskState:
    task_id: str
    title: str
    status: str = "QUEUED"  # QUEUED | RUNNING | APPROVED | COMMITTED | BLOCKED | FAILED
    stage: str = "DISCOVER"
    last_successful_stage: str = ""
    retry_count: int = 0
    build_attempts: int = 0
    test_attempts: int = 0
    security_cycles: int = 0
    final_review_cycles: int = 0
    provider: str = ""
    fallback_provider: str = ""
    last_failure: Optional[FailureRecord] = None
    last_reviewer_verdict: Optional[ReviewerVerdict] = None
    last_checkpoint: str = ""
    commit: str = ""
    safe_to_resume: bool = True
    blocker: str = ""
    modified_files: List[str] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    paused: bool = False

    # --- deterministic stage-execution evidence --------------------------
    # These are set to True ONLY by the pipeline after a gated stage has
    # genuinely executed (successfully). They are the fail-closed evidence
    # that a stage was NOT skipped, so a task can never reach COMMITTED by
    # sweeping past a required stage without executing it.
    implementation_executed: bool = False
    test_executed: bool = False
    regression_executed: bool = False
    security_review_executed: bool = False
    checkpoint_succeeded: bool = False
    # Non-.ai application files the implementation stage actually produced on
    # disk, and the files that were actually staged into the task's commit.
    implementation_files: List[str] = field(default_factory=list)
    commit_files: List[str] = field(default_factory=list)

    def checkpointed(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EngineState:
    version: int = 1
    current_task_id: str = ""
    pipeline_stage: str = "DISCOVER"
    last_successful_stage: str = ""
    retry_count: int = 0
    last_failure: Optional[FailureRecord] = None
    last_reviewer_verdict: Optional[ReviewerVerdict] = None
    last_checkpoint: str = ""
    active_lock_owner: str = ""
    paused: bool = False
    blocking: bool = False  # hard stop requiring human
    human_required_blocker: str = ""
    tasks: Dict[str, TaskState] = field(default_factory=dict)
    checkpoint_log: List[str] = field(default_factory=list)
    updated_at: str = field(default_factory=utc_now_iso)

    def checkpointed(self) -> Dict[str, Any]:
        return asdict(self)


class EngineStateStore:
    """Atomic, durable store for EngineState."""

    def __init__(self, path: Path = STATE_FILE) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._cache: Optional[EngineState] = None
        self._load_mtime_ns: int = 0

    # -- read -----------------------------------------------------------
    def load(self, force: bool = False) -> EngineState:
        """Load state from disk (or cache). Returns a fresh default if none."""
        try:
            mtime = self.path.stat().st_mtime_ns
        except OSError:
            return EngineState()

        if self._cache is not None and mtime == self._load_mtime_ns and not force:
            # Return a deep copy so mutations don't corrupt the cache.
            return self._from_plain(self._serialize(self._cache))

        if not self.path.exists():
            self._cache = EngineState()
            return EngineState()

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            state = self._from_plain(raw)
        except (json.JSONDecodeError, OSError, KeyError, TypeError, ValueError):
            # An unreadable/partial state file fails closed -> fresh default.
            state = EngineState()
        self._cache = state
        self._load_mtime_ns = mtime
        return self._deserialize(self._serialize(state))

    def _from_plain(self, raw: Dict[str, Any]) -> EngineState:
        tasks = {}
        for tid, t in (raw.get("tasks") or {}).items():
            tasks[tid] = TaskState(
                task_id=t.get("task_id", tid),
                title=t.get("title", ""),
                status=t.get("status", "QUEUED"),
                stage=t.get("stage", "DISCOVER"),
                last_successful_stage=t.get("last_successful_stage", ""),
                retry_count=t.get("retry_count", 0),
                build_attempts=t.get("build_attempts", 0),
                test_attempts=t.get("test_attempts", 0),
                security_cycles=t.get("security_cycles", 0),
                final_review_cycles=t.get("final_review_cycles", 0),
                provider=t.get("provider", ""),
                fallback_provider=t.get("fallback_provider", ""),
                last_failure=self._failure_from(t.get("last_failure")),
                last_reviewer_verdict=self._verdict_from(t.get("last_reviewer_verdict")),
                last_checkpoint=t.get("last_checkpoint", ""),
                commit=t.get("commit", ""),
                safe_to_resume=t.get("safe_to_resume", True),
                blocker=t.get("blocker", ""),
                modified_files=list(t.get("modified_files") or []),
                started_at=t.get("started_at", ""),
                finished_at=t.get("finished_at", ""),
                paused=t.get("paused", False),
                implementation_executed=t.get("implementation_executed", False),
                test_executed=t.get("test_executed", False),
                regression_executed=t.get("regression_executed", False),
                security_review_executed=t.get("security_review_executed", False),
                checkpoint_succeeded=t.get("checkpoint_succeeded", False),
                implementation_files=list(t.get("implementation_files") or []),
                commit_files=list(t.get("commit_files") or []),
            )
        return EngineState(
            version=raw.get("version", 1),
            current_task_id=raw.get("current_task_id", ""),
            pipeline_stage=raw.get("pipeline_stage", "DISCOVER"),
            last_successful_stage=raw.get("last_successful_stage", ""),
            retry_count=raw.get("retry_count", 0),
            last_failure=self._failure_from(raw.get("last_failure")),
            last_reviewer_verdict=self._verdict_from(raw.get("last_reviewer_verdict")),
            last_checkpoint=raw.get("last_checkpoint", ""),
            active_lock_owner=raw.get("active_lock_owner", ""),
            paused=raw.get("paused", False),
            blocking=raw.get("blocking", False),
            human_required_blocker=raw.get("human_required_blocker", ""),
            tasks=tasks,
            checkpoint_log=list(raw.get("checkpoint_log") or []),
            updated_at=raw.get("updated_at", utc_now_iso()),
        )

    @staticmethod
    def _failure_from(raw: Optional[Dict[str, Any]]) -> Optional[FailureRecord]:
        if not raw:
            return None
        return FailureRecord(
            stage=raw.get("stage", ""),
            classification=raw.get("classification", ""),
            message=raw.get("message", ""),
            attempt=raw.get("attempt", 0),
            commit=raw.get("commit", ""),
            timestamp=raw.get("timestamp", ""),
        )

    @staticmethod
    def _verdict_from(raw: Optional[Dict[str, Any]]) -> Optional[ReviewerVerdict]:
        if not raw:
            return None
        return ReviewerVerdict(
            reviewer=raw.get("reviewer", ""),
            verdict=raw.get("verdict", ""),
            stage=raw.get("stage", ""),
            commit=raw.get("commit", ""),
            issues=list(raw.get("issues") or []),
            timestamp=raw.get("timestamp", ""),
        )

    # -- write ----------------------------------------------------------
    def save(self, state: EngineState) -> None:
        """Atomically persist state (temp file + rename on same filesystem)."""
        state.updated_at = utc_now_iso()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = self._serialize(state)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=".engine_state_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, self.path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        self._cache = self._deserialize(data)
        self._load_mtime_ns = self.path.stat().st_mtime_ns

    def _serialize(self, state: EngineState) -> Dict[str, Any]:
        return state.checkpointed()

    def _deserialize(self, data: Dict[str, Any]) -> EngineState:
        return self._from_plain(data)


def new_task_id() -> str:
    return uuid.uuid4().hex[:12]