from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


class AuditEventType(str, enum.Enum):
    DATA_RECEIVED = "data_received"
    DATA_VALIDATED = "data_validated"
    DATA_REJECTED = "data_rejected"
    DATA_STALE = "data_stale"
    PROVIDER_CONNECTED = "provider_connected"
    PROVIDER_DISCONNECTED = "provider_disconnected"
    PROVIDER_ERROR = "provider_error"
    INTEGRITY_CHECK = "integrity_check"
    SYSTEM_START = "system_start"
    SYSTEM_STOP = "system_stop"


@dataclass(frozen=True)
class AuditEvent:
    event_type: AuditEventType
    timestamp_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    source: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    severity: str = "info"

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "timestamp_ms": self.timestamp_ms,
            "source": self.source,
            "details": self.details,
            "severity": self.severity,
        }
