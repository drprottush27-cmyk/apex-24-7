import importlib

import pytest
from httpx import AsyncClient, ASGITransport

BASE_URL = "http://test"


def _reload_app_with_origins(monkeypatch, origins):
    import api.app as app_module

    monkeypatch.setenv("ALLOWED_ORIGINS", ",".join(origins) if origins else "")
    importlib.reload(app_module)
    return app_module.app


def _cleanup(monkeypatch):
    monkeypatch.undo()


@pytest.mark.asyncio
async def test_fails_closed_no_cross_origin_when_allowlist_empty(monkeypatch):
    app = _reload_app_with_origins(monkeypatch, [])
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as client:
            response = await client.get(
                "/health", headers={"Origin": "https://evil.example.com"}
            )
            assert response.status_code == 200
            assert "access-control-allow-origin" not in response.headers
    finally:
        _cleanup(monkeypatch)


@pytest.mark.asyncio
async def test_allowlisted_origin_receives_matching_allow_origin(monkeypatch):
    app = _reload_app_with_origins(monkeypatch, ["https://app.example.com"])
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as client:
            response = await client.get(
                "/health", headers={"Origin": "https://app.example.com"}
            )
            assert response.status_code == 200
            assert response.headers.get("access-control-allow-origin") == "https://app.example.com"
    finally:
        _cleanup(monkeypatch)


@pytest.mark.asyncio
async def test_non_allowlisted_origin_rejected(monkeypatch):
    app = _reload_app_with_origins(monkeypatch, ["https://app.example.com"])
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as client:
            response = await client.get(
                "/health", headers={"Origin": "https://evil.example.com"}
            )
            assert response.status_code == 200
            assert "access-control-allow-origin" not in response.headers
    finally:
        _cleanup(monkeypatch)


@pytest.mark.asyncio
async def test_multiple_allowlisted_origins_all_matched(monkeypatch):
    app = _reload_app_with_origins(
        monkeypatch, ["https://one.example.com", "https://two.example.com"]
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as client:
            ok = await client.get(
                "/health", headers={"Origin": "https://two.example.com"}
            )
            assert ok.headers.get("access-control-allow-origin") == "https://two.example.com"

            rejected = await client.get(
                "/health", headers={"Origin": "https://three.example.com"}
            )
            assert "access-control-allow-origin" not in rejected.headers
    finally:
        _cleanup(monkeypatch)


@pytest.mark.asyncio
async def test_wildcard_never_emitted_even_when_requested(monkeypatch):
    app = _reload_app_with_origins(monkeypatch, ["https://app.example.com"])
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as client:
            response = await client.get(
                "/health", headers={"Origin": "https://app.example.com"}
            )
            acao = response.headers.get("access-control-allow-origin")
            assert acao is not None
            assert acao != "*"
    finally:
        _cleanup(monkeypatch)
