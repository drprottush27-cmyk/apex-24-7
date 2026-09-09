"""Structured logging for the APEX engineering orchestrator.

Writes JSON lines (one per record), never secrets. Each record carries:
  timestamp, task, stage, agent, provider, attempt, result, error classification,
  git commit, checkpoint.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .state import LOGS_DIR

_log_lock = threading.Lock()


class OrchestratorLogger:
    def __init__(self, directory: Path = LOGS_DIR) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def _emit(self, record: Dict[str, Any]) -> None:
        record.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        path = self.directory / ("orchestrator-%s.log" % _today())
        line = json.dumps(record, default=str, sort_keys=True)
        with _log_lock:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def log(
        self,
        *,
        event: str,
        task: str = "",
        stage: str = "",
        agent: str = "",
        provider: str = "",
        attempt: int = 0,
        result: str = "",
        classification: str = "",
        commit: str = "",
        checkpoint: str = "",
        error: str = "",
        **extra: Any,
    ) -> None:
        record = {
            "event": event,
            "task": task,
            "stage": stage,
            "agent": agent,
            "provider": provider,
            "attempt": attempt,
            "result": result,
            "classification": classification,
            "commit": commit,
            "checkpoint": checkpoint,
            "error": error[:500] if error else "",
        }
        record.update(extra)
        self._emit(record)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")