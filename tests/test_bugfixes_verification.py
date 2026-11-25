from __future__ import annotations

import io
import json
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pikepdf
import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db import utc_now_iso
from backend.models import TaskStatus
from backend.security.validator import SpecValidationError
from backend.services.ai_providers import AIRequest, GeminiAdapter, OpenAIAdapter, OpenRouterAdapter


class _PdfAnd404Handler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/redirect-ssrf":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            self.end_headers()
            return
        if self.path == "/missing.pdf":
            self.send_error(404)
            return
        super().do_GET()


@pytest.mark.asyncio
async def test_bugfix1_url_download_chaining_and_failure_status(container, tmp_path: Path):
    """[수정 1] URL 다운로드 -> COMPRESS_PDF 체이닝 성공 및 다운로드 실패 시 태스크 FAILED 전환"""
    serve_dir = tmp_path / "srv"
    serve_dir.mkdir()
    pdf_path = serve_dir / "chain.pdf"
    with pikepdf.new() as pdf:
        pdf.add_blank_page(page_size=(200, 200))
        pdf.save(pdf_path)

    handler = partial(_PdfAnd404Handler, directory=str(serve_dir))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    try:
        # 1) Chained DOWNLOAD_FILE -> COMPRESS_PDF
        task = await container.task_engine.create_task(
            title="다운로드 후 PDF 압축 체인",
            task_type="workflow",
            spec={
                "schema_version": "1.0",
                "intent": "download_and_compress",
                "steps": [
                    {
                        "action": "DOWNLOAD_FILE",
                        "source": "external_url",
                        "options": {"url": f"http://127.0.0.1:{port}/chain.pdf", "filename": "chain.pdf"},
                    },
                    {
                        "action": "COMPRESS_PDF",
                        "source": "local",
                        "input_ref": "$previous.output",
                        "options": {"preset": "structure_only"},
                    },
                ],
                "requires_confirmation": False,
            },
        )
        res = await container.task_engine.execute_task(task["task_id"])
        assert res["status"] == TaskStatus.COMPLETED.value
        assert len(res["steps"]) == 2
        assert all(s["status"] == TaskStatus.COMPLETED.value for s in res["steps"])

        # 2) Failed download must mark task and step as FAILED
        fail_task = await container.task_engine.create_task(
            title="실패 다운로드 테스트",
            task_type="workflow",
            spec={
                "schema_version": "1.0",
                "intent": "download_fail",
                "steps": [
                    {
                        "action": "DOWNLOAD_FILE",
                        "source": "external_url",
                        "options": {"url": f"http://127.0.0.1:{port}/missing.pdf"},
                    }
                ],
                "requires_confirmation": False,
            },
        )
        fail_res = await container.task_engine.execute_task(fail_task["task_id"])
        assert fail_res["status"] == TaskStatus.FAILED.value
        assert fail_res["steps"][0]["status"] == TaskStatus.FAILED.value
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.asyncio
async def test_bugfix2_redirect_ssrf_blocked(container, tmp_path: Path):
    """[수정 2] HTTP 리다이렉트(302 -> 169.254.169.254) SSRF 우회 차단"""
    serve_dir = tmp_path / "srv_ssrf"
    serve_dir.mkdir()
    handler = partial(_PdfAnd404Handler, directory=str(serve_dir))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    try:
        job = await container.downloads.create_http_download(
            f"http://127.0.0.1:{port}/redirect-ssrf",
            filename="ssrf.bin",
            allow_localhost=True,
            auto_start=False,
        )
        res = await container.downloads.run_http_download(job["download_id"])
        assert res["status"] == "FAILED"
        assert "metadata" in (res.get("error_message") or "").lower() or "blocked" in (res.get("error_message") or "").lower()
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.asyncio
async def test_bugfix3_drive_transfer_resume_rejected(container, fastapi_app):
    """[수정 3] source_url이 없는 Drive 전송 작업의 resume_job은 ValueError / HTTP 400 반환"""
    dl_id = await container.downloads.register_drive_transfer(
        direction="upload_drive",
        filename="drv.mp4",
        source_file_id="local_123",
        total_bytes=100,
    )
    with pytest.raises(ValueError):
        await container.downloads.resume_job(dl_id)

    with TestClient(fastapi_app) as client:
        c = client.app.state.container
        did = client.portal.call(
            c.downloads.register_drive_transfer,
        ) if False else None
    # Verify via direct HTTP endpoint on same container using TestClient
    with TestClient(fastapi_app) as client:
        c = client.app.state.container
        # Insert a drive transfer job directly via sync helper in portal
        import anyio
        async def _reg():
            return await c.downloads.register_drive_transfer(
                direction="upload_drive", filename="x.mp4", source_file_id="f1", total_bytes=10
            )
        job_id = client.portal.call(_reg)
        resp = client.post(f"/api/downloads/{job_id}/resume")
        assert resp.status_code == 400


@pytest.mark.asyncio
async def test_bugfix8_free_only_blocks_paid_openrouter_and_empty_ai_response(container, monkeypatch):
    """[수정 8] ai_free_only_mode의 OpenRouter 유료 모델 차단, 월 예산 초과 차단, 빈 AI 응답 방어"""
    await container.ai_router.update_provider_setting(
        "openrouter",
        {"api_key": "sk-or-v1-test123456", "enabled": True, "priority": 1, "model_id": "openai/gpt-4o"},
    )
    await container.db.execute("UPDATE ai_provider_settings SET enabled = 0 WHERE provider_id IN ('gemini', 'openai')")
    await container.db.set_preference("ai_free_only_mode", "true")

    with pytest.raises(RuntimeError):
        await container.ai_router.plan_command("복잡한 작업 계획 만들어줘", source="local")

    # Test monthly budget cap when free_only is OFF
    await container.db.set_preference("ai_free_only_mode", "false")
    await container.db.set_preference("ai_monthly_limit_usd", "5.0")
    await container.db.execute(
        """
        INSERT INTO ai_usage_events (event_id, provider_id, model_id, request_type, estimated_cost_usd, status_code, latency_ms, created_at)
        VALUES ('ev_budget_1', 'openrouter', 'openai/gpt-4o', 'plan_command', 5.5, 200, 100, ?)
        """,
        (utc_now_iso(),),
    )
    with pytest.raises(RuntimeError):
        await container.ai_router.plan_command("복잡한 작업 계획 만들어줘", source="local")

    # Test empty choices / candidates defensive SpecValidationError
    req = AIRequest(command="test", context_files=[], selected_file_ids=[], source="local", correlation_id="c1")

    def empty_resp_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [], "candidates": []})

    transport = httpx.MockTransport(empty_resp_handler)
    orig_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: orig_client(*a, transport=transport, **kw))

    for adapter in (OpenRouterAdapter(), GeminiAdapter(), OpenAIAdapter()):
        with pytest.raises(SpecValidationError):
            await adapter.generate_task_proposal("dummy-key", "dummy-model", req)


@pytest.mark.asyncio
async def test_bugfix8_drive_search_filters(container, monkeypatch):
    """[수정 8] Google Drive SEARCH_FILES가 mime_type, extension, min/max size 필터를 모두 적용"""
    await container.secrets.set_json_secret(
        container.drive.SECRET_TOKENS,
        {"access_token": "ya29.mock", "expires_at": 9999999999},
    )

    def drive_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "files": [
                    {"id": "d1", "name": "small.pdf", "mimeType": "application/pdf", "size": "1000"},
                    {"id": "d2", "name": "big.pdf", "mimeType": "application/pdf", "size": "5000000"},
                    {"id": "d3", "name": "movie.mp4", "mimeType": "video/mp4", "size": "9000000"},
                ]
            },
        )

    transport = httpx.MockTransport(drive_handler)
    orig_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: orig_client(*a, transport=transport, **kw))

    task = await container.task_engine.create_task(
        title="Drive PDF 대용량 검색",
        task_type="search",
        spec={
            "schema_version": "1.0",
            "intent": "search_drive",
            "steps": [
                {
                    "action": "SEARCH_FILES",
                    "source": "google_drive",
                    "filters": {"extension": "pdf", "min_size_bytes": 1000000},
                }
            ],
            "requires_confirmation": False,
        },
    )
    res = await container.task_engine.execute_task(task["task_id"])
    assert res["status"] == TaskStatus.COMPLETED.value
    final = json.loads(res["result_json"])["final_output"]
    assert len(final["files"]) == 1
    assert final["files"][0]["id"] == "d2"


def test_bugfix4_6_9_oauth_redirect_render_url_cancel_404_and_drive_compress_guard(fastapi_app, monkeypatch):
    """[수정 4, 6, 9] RENDER_EXTERNAL_URL 폴백, OAuth access_denied 리다이렉트, cancel 404, Drive 파일 압축 400"""
    monkeypatch.delenv("APP_BASE_URL", raising=False)
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://sample-app.onrender.com/")
    cfg = Settings()
    assert cfg.app_base_url == "https://sample-app.onrender.com"
    assert cfg.effective_google_redirect_uri == "https://sample-app.onrender.com/api/drive/oauth/callback"

    with TestClient(fastapi_app) as client:
        # 1) OAuth user cancellation redirects cleanly instead of 422
        resp = client.get("/api/drive/oauth/callback?error=access_denied", follow_redirects=False)
        assert resp.status_code in {302, 307}
        assert "/?drive=error&reason=access_denied" in resp.headers["location"]

        # 2) cancel_task on unknown ID returns 404
        c_resp = client.post("/api/tasks/nonexistent_task_id/cancel")
        assert c_resp.status_code == 404

        # 3) Compression on a Google Drive cached file returns 400
        c = client.app.state.container
        async def _seed_drive_file():
            await c.db.execute(
                """
                INSERT INTO file_cache (file_id, source, name, mime_type, size, modified_time, cached_at)
                VALUES ('drv_vid_1', 'google_drive', 'cloud.mp4', 'video/mp4', 1024, ?, ?)
                """,
                (utc_now_iso(), utc_now_iso()),
            )
        client.portal.call(_seed_drive_file)
        cmp_resp = client.post("/api/compression/video", json={"file_id": "drv_vid_1", "preset": "balanced"})
        assert cmp_resp.status_code == 400
