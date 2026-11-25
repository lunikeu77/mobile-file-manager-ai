from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.security.crypto import redact_sensitive_text


def test_kimi_key_redaction():
    text = "Using key sk-ms-EkfF0123456789abcdef in header"
    redacted = redact_sensitive_text(text)
    assert "sk-ms-EkfF0123456789abcdef" not in redacted
    assert "[REDACTED_KIMI_KEY]" in redacted


@pytest.mark.asyncio
async def test_kimi_chat_and_preferred_provider(container, monkeypatch):
    await container.ai_router.update_provider_setting(
        "kimi",
        {"api_key": "sk-ms-testkimikey123456", "enabled": True, "priority": 3, "model_id": "moonshot-v1-8k"},
    )

    seen_urls: list[str] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "안녕하세요, Kimi 응답입니다."}}],
                "usage": {"total_tokens": 25},
            },
        )

    transport = httpx.MockTransport(mock_handler)
    orig_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: orig_client(*a, transport=transport, **kw))

    res = await container.ai_router.chat_with_provider(
        provider_id="kimi",
        model_id="moonshot-v1-32k",
        message="테스트 인사",
        history=[],
    )
    assert res["provider_id"] == "kimi"
    assert res["model_id"] == "moonshot-v1-32k"
    assert "Kimi 응답" in res["reply"]
    assert any("api.moonshot.ai/v1/chat/completions" in u for u in seen_urls)


@pytest.mark.asyncio
async def test_drive_ensure_workspace_folder(container, monkeypatch):
    await container.secrets.set_json_secret(
        container.drive.SECRET_TOKENS,
        {"access_token": "ya29.mock", "expires_at": 9999999999},
    )

    def drive_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"files": []})
        if request.method == "POST":
            return httpx.Response(200, json={"id": "folder_ws_001", "name": "MobileFileManager_Workspace"})
        return httpx.Response(404)

    transport = httpx.MockTransport(drive_handler)
    orig_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: orig_client(*a, transport=transport, **kw))

    folder = await container.drive.ensure_workspace_folder()
    assert folder["folder_id"] == "folder_ws_001"
    assert folder["created"] is True
    prefs = await container.db.get_preferences()
    assert prefs["drive_workspace_folder_id"] == "folder_ws_001"


def test_ai_chat_endpoint_lists_four_providers(fastapi_app):
    with TestClient(fastapi_app) as client:
        resp = client.get("/api/settings/providers")
        assert resp.status_code == 200
        ids = [p["provider_id"] for p in resp.json()["data"]]
        assert set(["openrouter", "gemini", "kimi", "openai"]).issubset(set(ids))
