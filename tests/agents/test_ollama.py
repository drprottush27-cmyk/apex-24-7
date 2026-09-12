import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

import apex.advisory.ollama as mod
from apex.advisory.models import AI_STATUS_AVAILABLE, AI_STATUS_DEFAULT
from apex.advisory.ollama import OllamaAdvisor


class _FakeResp:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def advisor():
    return OllamaAdvisor(base_url="http://127.0.0.1:9999", model="qwen3-test", timeout_s=2.0)


class TestSanitize:
    def test_strips_sealed_keys(self, advisor):
        clean = advisor.sanitize({
            "api_key": "should-vanish",
            "passphrase": "also-gone",
            "nested": {"secret": "x", "normal": 1, "ok": True},
            "ok_list": [1, 2],
        })
        assert "api_key" not in clean
        assert "passphrase" not in clean
        assert clean["nested"]["normal"] == 1
        assert clean["ok_list"] == [1, 2]

    def test_rejects_callable(self, advisor):
        with pytest.raises(TypeError):
            advisor.sanitize({"fn": lambda: None})

    def test_rejects_arbitrary_objects(self, advisor):
        class X:
            pass
        with pytest.raises(TypeError):
            advisor.sanitize(X())

    def test_safe_with_to_dict(self):
        class _Serializable:
            def to_dict(self):
                return {"kind": "safe-thing", "value": 1}

        clean = mod._sanitize_node(_Serializable())
        assert clean["value"] == 1

    def test_accepts_decimal_preserving_precision(self, advisor):
        clean = advisor.sanitize({
            "entry_price": Decimal("0.0075000000000000001"),
            "pnl": Decimal("123.4567890123456789"),
        })
        assert clean["entry_price"] == Decimal("0.0075000000000000001")
        assert clean["pnl"] == Decimal("123.4567890123456789")

    def test_accepts_datetime_and_date(self, advisor):
        clean = advisor.sanitize({
            "signaled_at": datetime(2026, 9, 12, 10, 30, 0, tzinfo=timezone.utc),
            "settlement": date(2026, 9, 12),
        })
        assert clean["signaled_at"] == "2026-09-12T10:30:00+00:00"
        assert clean["settlement"] == "2026-09-12"

    def test_decimal_and_timestamp_reach_prompt_rendering(self, advisor):
        prompt = advisor._render_prompt(advisor.sanitize({
            "entry_price": Decimal("0.0075"),
            "pnl": Decimal("123.4567890123456789"),
            "signaled_at": datetime(2026, 9, 12, 10, 30, 0, tzinfo=timezone.utc),
        }))
        assert "0.0075" in prompt
        assert "123.4567890123456789" in prompt
        assert "2026-09-12T10:30:00+00:00" in prompt


class TestIsAvailable:
    def test_disabled_always_false(self):
        assert OllamaAdvisor(enabled=False).is_available() is False

    def test_unavailable_returns_false(self, monkeypatch):
        import urllib.error
        monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **kw: (_ for _ in ()).throw(urllib.error.URLError("no")))
        assert OllamaAdvisor(enabled=True).is_available() is False

    def test_available_returns_true(self, monkeypatch):
        monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **kw: _FakeResp(b'{"models": []}'))
        assert OllamaAdvisor(enabled=True).is_available() is True


class TestAdvise:
    def test_success(self, monkeypatch, advisor):
        monkeypatch.setattr(
            mod.urllib.request,
            "urlopen",
            lambda req, timeout=None: _FakeResp(json.dumps({"response": "hold risk constant"}).encode()),
        )
        result = advisor.advise({"regime": "BULL", "price": 50000})
        assert result.success is True
        assert result.ai_status == AI_STATUS_AVAILABLE
        assert "hold risk constant" in result.advice
        assert result.error is None

    def test_unavailable_returns_failure(self, monkeypatch, advisor):
        monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **kw: (_ for _ in ()).throw(ConnectionError("down")))
        result = advisor.advise({"x": 1})
        assert result.success is False
        assert result.ai_status == AI_STATUS_DEFAULT
        assert "OLLAMA_UNAVAILABLE" in result.error

    def test_malformed_empty_response(self, monkeypatch, advisor):
        monkeypatch.setattr(
            mod.urllib.request,
            "urlopen",
            lambda req, timeout=None: _FakeResp(json.dumps({"response": ""}).encode()),
        )
        result = advisor.advise({"x": 1})
        assert result.success is False
        assert "MALFORMED_RESPONSE" in result.error

    def test_malformed_not_dict(self, monkeypatch, advisor):
        monkeypatch.setattr(
            mod.urllib.request,
            "urlopen",
            lambda req, timeout=None: _FakeResp(json.dumps([1, 2]).encode()),
        )
        result = advisor.advise({"x": 1})
        assert result.success is False

    def test_disabled_returns_disabled(self, advisor):
        advisor.enabled = False
        result = advisor.advise({"x": 1})
        assert result.success is False
        assert result.error == "ADVISORY_DISABLED"

    def test_unsafe_input_yields_failure(self, advisor):
        result = advisor.advise({"bad": lambda: None})
        assert result.success is False
        assert "UNSAFE_INPUT" in result.error

    def test_request_to_generate_endpoint(self, monkeypatch, advisor):
        seen_req = {}

        def fake_urlopen(req, timeout=None):
            seen_req["url"] = req.full_url
            seen_req["method"] = req.method
            return _FakeResp(json.dumps({"response": "ok"}).encode())

        monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
        advisor.advise({"x": 1})
        assert "/api/generate" in seen_req["url"]
        assert seen_req["method"] == "POST"

    def test_no_execution_capabilities(self, advisor):
        for attr in ("place_order", "execute", "sell", "buy", "cancel_order", "set_leverage", "withdraw"):
            assert not hasattr(advisor, attr)

    def test_prompt_has_safety_prefix(self, advisor):
        prompt = advisor._render_prompt({"key": "value"})
        assert "PAPER-ONLY" in prompt
        assert "NO execution authority" in prompt

    def test_large_context_truncates(self, advisor):
        big = {"data": "x" * 20000}
        prompt = advisor._render_prompt(big)
        assert "...(truncated)" in prompt
        assert len(prompt) < 11000