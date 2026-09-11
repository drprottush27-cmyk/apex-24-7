from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class SafetyConfig:
    dry_run: bool = True
    auto_execute: bool = False
    live_trading_enabled: bool = False

    @classmethod
    def from_env(cls) -> "SafetyConfig":
        return cls(
            dry_run=os.environ.get("DRY_RUN", "true").lower() not in ("0", "false", "no"),
            auto_execute=os.environ.get("AUTO_EXECUTE", "false").lower() in ("1", "true", "yes"),
            live_trading_enabled=os.environ.get("LIVE_TRADING_ENABLED", "false").lower()
            in ("1", "true", "yes"),
        )

    @property
    def trading_allowed(self) -> bool:
        return (
            not self.dry_run
            and self.auto_execute
            and self.live_trading_enabled
        )


def load_safety_config() -> SafetyConfig:
    return SafetyConfig.from_env()