import pytest
from httpx import AsyncClient, ASGITransport

from api.app import app

VALID_KEY = "test-api-key-valid"
MALFORMED_HEADERS = ["", "Basic abcdef", "Bearer ", "Bearer   ", "not-bearer token"]


def set_api_key(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("API_KEY", raising=False)
    else:
        monkeypatch.setenv("API_KEY", value)


@pytest.mark.asyncio
async def test_health_check_endpoint_is_public():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_miniapp_is_public():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/miniapp")
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_fails_closed_when_api_key_not_configured(monkeypatch):
    set_api_key(monkeypatch, None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/mode")
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_protected_route_rejected_without_auth(monkeypatch):
    set_api_key(monkeypatch, VALID_KEY)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/mode")
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_protected_route_rejects_invalid_auth(monkeypatch):
    set_api_key(monkeypatch, VALID_KEY)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/mode", headers={"Authorization": "Bearer wrong-key"}
        )
        assert response.status_code == 401
        assert "Invalid API key" in response.json().get("detail", "")


@pytest.mark.asyncio
async def test_protected_route_rejects_wrong_scheme(monkeypatch):
    set_api_key(monkeypatch, VALID_KEY)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/mode", headers={"Authorization": "Basic dXNlcjpwYXNz"}
        )
        assert response.status_code == 401
        assert "Invalid authentication scheme" in response.json().get("detail", "")


@pytest.mark.asyncio
@pytest.mark.parametrize("header_value", MALFORMED_HEADERS)
async def test_protected_route_rejects_malformed_auth(monkeypatch, header_value):
    set_api_key(monkeypatch, VALID_KEY)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/mode", headers={"Authorization": header_value}
        )
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_auth_failures_do_not_leak_secret(monkeypatch):
    set_api_key(monkeypatch, VALID_KEY)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/mode", headers={"Authorization": "Bearer wrong-key"}
        )
        body = response.text
        assert "test-api-key-valid" not in body
        assert "wrong-key" not in body


@pytest.mark.asyncio
async def test_valid_auth_reaches_protected_route(monkeypatch):
    set_api_key(monkeypatch, VALID_KEY)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/mode", headers={"Authorization": f"Bearer {VALID_KEY}"}
        )
        assert response.status_code != 401
        assert response.status_code in (200, 503)
