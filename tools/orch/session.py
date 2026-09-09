"""Session document manager for the APEX engineering orchestrator.

Updates and reads the human-readable .ai markdown records in addition to the
machine-readable engine_state.json. These documents are the source of truth
between AI sessions per the AGENT_PROTOCOL:
  - .ai/SESSION_STATE.md
  - .ai/TODO.md
  - .ai/TEST_STATUS.md
  - .ai/COMPLETED.md
  - .ai/PROJECT_STATE.md
  - .ai/DECISIONS.md
  - .ai/SECURITY.md

This layer keeps docs in sync with EngineState so both are plausible for
humans and deterministic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .state import TaskState, ROOT, utc_now_iso

AI_DIR = ROOT / ".ai"
SESSION_FILE = AI_DIR / "SESSION_STATE.md"
TODO_FILE = AI_DIR / "TODO.md"
TEST_STATUS_FILE = AI_DIR / "TEST_STATUS.md"
COMPLETED_FILE = AI_DIR / "COMPLETED.md"
PROJECT_STATE_FILE = AI_DIR / "PROJECT_STATE.md"


class SessionDocs:
    """Read/write helpers for the .ai markdown records."""

    _session_template = (
        "# APEX Session State\n\n"
        "Current task: {current}\n"
        "Status: {status}\n\n"
        "Recent events:\n{events}\n"
    )

    def __init__(self, ai_dir: Path = AI_DIR) -> None:
        self.ai_dir = Path(ai_dir)
        self.session_file = self.ai_dir / "SESSION_STATE.md"
        self.completed_file = self.ai_dir / "COMPLETED.md"
        self.test_status_file = self.ai_dir / "TEST_STATUS.md"
        self.project_state_file = self.ai_dir / "PROJECT_STATE.md"
        self.todo_file = self.ai_dir / "TODO.md"

    def write_session(self, current: str, status: str, events: list[str]) -> None:
        body = "\n".join(f"- {e}" for e in events)
        self._atomic_write(self.session_file, self._session_template.format(
            current=current, status=status, events=body))

    def write_todo(self, items: list[tuple[str, str]]) -> None:
        # items: list of (marker "- [ ]"/"- [x]", text)
        body = "\n".join(f"{m} {t}" for m, t in items)
        self._atomic_write(self.todo_file, "# APEX TODO\n\n## Backlog\n" + body + "\n")

    def mark_todo_done(self, tag: str, task_title: str) -> None:
        """Under TEST_STATUS/TODO we simply don't rewrite full TODO here to avoid
        clobbering the curated backlog. This is a no-op placeholder; the human
        task completion record is governed by the orchestrator's own commit."""

    def append_completed(self, task: TaskState, git_commit: str) -> None:
        body = (
            "\n\n## " + task.task_id + " — " + task.title + " (COMPLETE)\n\n"
            "Commit: " + git_commit + "\n"
            "Status: committed\n"
        )
        try:
            existing = self.completed_file.read_text(encoding="utf-8")
        except OSError:
            existing = "# Completed Work\n"
        self._atomic_write(self.completed_file, existing + body)

    def update_test_status(self, summary: str) -> None:
        existing = ""
        try:
            existing = self.test_status_file.read_text(encoding="utf-8")
        except OSError:
            existing = "# APEX Test Status\n"
        # Append a fresh marker line (kept minimal to avoid clobbering curated text).
        self._atomic_write(self.test_status_file, existing + "\n" + summary + "\n")

    def write_project_state(self, headline: str) -> None:
        existing = ""
        try:
            existing = self.project_state_file.read_text(encoding="utf-8")
        except OSError:
            existing = "# APEX Project State\n"
        # We only append a timestamped log line, not rewrite the holistic doc.
        self._atomic_write(self.project_state_file, existing + "\n" + headline + "\n")

    def update_blocker(self, reason: str) -> None:
        """Persist a human-required blocker into SESSION_STATE so it survives
        process death and is visible to the next session."""
        note = f"HUMAN-REQUIRED BLOCKER: {reason}"
        try:
            existing = self.session_file.read_text(encoding="utf-8")
        except OSError:
            existing = "# APEX Session State\n"
        timestamped = f"[{utc_now_iso()}] {note}\n"
        self._atomic_write(self.session_file, existing + timestamped)

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)