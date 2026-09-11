from dataclasses import dataclass
from enum import Enum
from typing import Set

class CommandType(Enum):
    STATUS = "/status"
    HALT = "/halt"

@dataclass(frozen=True)
class TelegramConfig:
    authorized_user_ids: Set[int]

@dataclass(frozen=True)
class CommandResult:
    success: bool
    message: str
