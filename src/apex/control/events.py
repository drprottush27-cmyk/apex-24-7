"""APEX UNIFIED CONTROL PLANE — Central Event Bus & Immutable Audit Trail.

Requirements:
- Centralized event model for all system components, interfaces, and agents.
- Topics: system.*, market.*, trade.*, risk.*, killswitch.*, alert.*, agent.*, report.*
- Correlation ID tracking across user requests, decisions, and execution.
- Append-only persistent audit log file (var/audit_events.jsonl).
- Read-only queries for Mini App Audit View, Telegram /events, and debugging.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ControlEvent:
    """Immutable audit & operational event."""

    event_id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")
    timestamp_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    topic: str = "system.info"
    actor: str = "system"
    source: str = "control_plane"
    status: str = "SUCCESS"
    correlation_id: str | None = None
    affected_component: str | None = None
    summary: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ControlPlaneEventBus:
    """Thread-safe centralized Event Bus and Audit Log Manager."""

    def __init__(self, log_path: Path | str | None = None, max_in_memory: int = 500) -> None:
        self.log_path = Path(log_path) if log_path else Path("var/audit_events.jsonl")
        self._lock = threading.RLock()
        self._subscribers: dict[str, list[Callable[[ControlEvent], None]]] = {}
        self._history: deque[ControlEvent] = deque(maxlen=max_in_memory)
        self._init_storage()

    def _init_storage(self) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            if not self.log_path.exists():
                self.log_path.touch()
            else:
                # Preload recent events
                lines = self.log_path.read_text(encoding="utf-8").splitlines()
                for line in lines[-200:]:
                    if line.strip():
                        try:
                            d = json.loads(line)
                            evt = ControlEvent(
                                event_id=d.get("event_id", ""),
                                timestamp_ms=int(d.get("timestamp_ms", 0)),
                                topic=d.get("topic", "system.info"),
                                actor=d.get("actor", "system"),
                                source=d.get("source", "control_plane"),
                                status=d.get("status", "SUCCESS"),
                                correlation_id=d.get("correlation_id"),
                                affected_component=d.get("affected_component"),
                                summary=d.get("summary", ""),
                                payload=d.get("payload") or {},
                                error=d.get("error"),
                            )
                            self._history.append(evt)
                        except Exception:
                            pass
        except Exception as exc:
            logger.warning("Failed initializing audit event log at %s: %s", self.log_path, exc)

    def subscribe(self, topic_prefix: str, callback: Callable[[ControlEvent], None]) -> None:
        """Subscribe a callback to a topic or wildcard prefix (e.g. 'system.*' or '*')."""
        with self._lock:
            self._subscribers.setdefault(topic_prefix, []).append(callback)

    def emit(
        self,
        topic: str,
        summary: str,
        actor: str = "system",
        source: str = "control_plane",
        status: str = "SUCCESS",
        correlation_id: str | None = None,
        affected_component: str | None = None,
        payload: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> ControlEvent:
        """Publish an event to subscribers and persist it to the audit trail."""
        evt = ControlEvent(
            topic=topic,
            summary=summary,
            actor=actor,
            source=source,
            status=status,
            correlation_id=correlation_id,
            affected_component=affected_component,
            payload=payload or {},
            error=error,
        )

        with self._lock:
            self._history.append(evt)
            self._append_to_disk(evt)
            callbacks = self._get_matching_callbacks(topic)

        # Dispatch callbacks outside lock
        for cb in callbacks:
            try:
                cb(evt)
            except Exception as exc:
                logger.exception("Error in event callback for topic '%s': %s", topic, exc)

        return evt

    def get_recent_events(
        self,
        limit: int = 100,
        topic_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve recent events sorted latest-first."""
        with self._lock:
            events = list(self._history)

        if topic_filter:
            events = [e for e in events if e.topic.startswith(topic_filter)]

        events.reverse()
        return [e.to_dict() for e in events[:limit]]

    def _get_matching_callbacks(self, topic: str) -> list[Callable[[ControlEvent], None]]:
        matched: list[Callable[[ControlEvent], None]] = []
        for prefix, cbs in self._subscribers.items():
            if prefix in ("*", "") or topic == prefix or (prefix.endswith("*") and topic.startswith(prefix[:-1])):
                matched.extend(cbs)
            elif topic.startswith(f"{prefix}."):
                matched.extend(cbs)
        return matched

    def _append_to_disk(self, evt: ControlEvent) -> None:
        try:
            line = json.dumps(evt.to_dict()) + "\n"
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception as exc:
            logger.error("Failed appending event to audit log: %s", exc)
