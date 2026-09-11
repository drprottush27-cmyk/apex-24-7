class KillSwitch:
    """Central safety mechanism to halt all system operations."""
    def __init__(self):
        self._is_triggered = False
        self._reason = ""

    @property
    def is_triggered(self) -> bool:
        return self._is_triggered

    @property
    def reason(self) -> str:
        return self._reason

    def trigger(self, reason: str):
        self._is_triggered = True
        self._reason = reason
