from abc import ABC, abstractmethod
from apex.audit.events import AuditEvent


class AuditLog(ABC):
    @abstractmethod
    async def append(self, event: AuditEvent) -> None: ...

    @abstractmethod
    async def get_events(
        self,
        event_type: str | None = None,
        since_ms: int | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]: ...

    @abstractmethod
    async def count(self, event_type: str | None = None) -> int: ...
