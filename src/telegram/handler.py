from .models import TelegramConfig, CommandType, CommandResult
from src.apex.audit.killswitch import KillSwitch

class TelegramHandler:
    def __init__(self, config: TelegramConfig, killswitch: KillSwitch):
        self.config = config
        self.killswitch = killswitch

    def handle_message(self, user_id: int, text: str) -> CommandResult:
        if user_id not in self.config.authorized_user_ids:
            return CommandResult(False, "UNAUTHORIZED: User not permitted.")
        
        cmd_str = text.strip().split()[0].lower() if text else ""
        try:
            cmd_type = CommandType(cmd_str)
        except ValueError:
            return CommandResult(False, f"UNKNOWN_COMMAND: {cmd_str}")

        if cmd_type == CommandType.HALT:
            self.killswitch.trigger(f"Telegram Emergency Halt initiated by user {user_id}")
            return CommandResult(True, "SYSTEM HALTED. Manual intervention required.")
        
        elif cmd_type == CommandType.STATUS:
            state = "HALTED" if self.killswitch.is_triggered else "ACTIVE"
            return CommandResult(True, f"System Status: {state}")
        
        return CommandResult(False, "UNHANDLED_COMMAND")
