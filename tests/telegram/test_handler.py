import pytest
from src.telegram.models import TelegramConfig
from src.telegram.handler import TelegramHandler
from src.apex.audit.killswitch import KillSwitch

@pytest.fixture
def killswitch():
    return KillSwitch()

@pytest.fixture
def handler(killswitch):
    return TelegramHandler(TelegramConfig(authorized_user_ids={12345}), killswitch)

def test_unauthorized_user(handler):
    res = handler.handle_message(9999, "/status")
    assert res.success is False
    assert "UNAUTHORIZED" in res.message

def test_unknown_command(handler):
    res = handler.handle_message(12345, "/magic")
    assert res.success is False
    assert "UNKNOWN_COMMAND" in res.message

def test_halt_command(handler, killswitch):
    assert not killswitch.is_triggered
    res = handler.handle_message(12345, "/halt")
    assert res.success is True
    assert killswitch.is_triggered
    assert "SYSTEM HALTED" in res.message

def test_status_command(handler):
    res = handler.handle_message(12345, "/status")
    assert res.success is True
    assert "ACTIVE" in res.message
