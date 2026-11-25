from __future__ import annotations

import io
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from backend.container import AppContainer
from backend.db import utc_now_iso
from backend.models import TaskStatus


@pytest.mark.asyncio
async def test_gate4_restart_recovery_and_idempotency(temp_settings):
    """
    [검증 4] 프로세스 재시작 후 미완료 작업 상태 복구 및 중복 실행 방지(idempotency_key)
    """
    c1 = AppContainer.build(temp_settings)
    await c1.db.connect()
    await c1.local_files.save_bytes_stream("doc.txt", io.BytesIO(b"abc"))
    files = await c1.local_files.list_files(source="local")
    fid = files[0]["file_id"]

    # Create an idempotent task
    spec = {
        "schema_version": "1.0",
        "intent": "search",
        "steps": [{"action": "SEARCH_FILES", "source": "local", "filters": {"extension": "txt"}}],
        "requires_confirmation": False,
    }
    t1 = await c1.task_engine.create_task(
        title="중복 방지 테스트",
        task_type="search",
        spec=spec,
        idempotency_key="idem-key-001",
    )
    t2 = await c1.task_engine.create_task(
        title="중복 방지 테스트 재전송",
        task_type="search",
        spec=spec,
        idempotency_key="idem-key-001",
    )
    assert t1["task_id"] == t2["task_id"]

    # Simulate an interrupted RUNNING task left over when process died abruptly
    interrupted_id = f"task_{uuid.uuid4().hex[:8]}"
    now = utc_now_iso()
    await c1.db.execute(
        """
        INSERT INTO tasks (task_id, task_type, title, status, idempotency_key, payload_json, progress, requires_confirmation, created_at, updated_at)
        VALUES (?, 'compress_video', '중단된 인코딩', ?, 'idem-interrupted', ?, 40, 0, ?, ?)
        """,
        (interrupted_id, TaskStatus.RUNNING.value, json.dumps(spec), now, now),
    )
    await c1.db.execute(
        """
        INSERT INTO task_steps (step_id, task_id, step_order, action, status, input_ref, retry_count)
        VALUES ('step_int_1', ?, 1, 'COMPRESS_VIDEO', ?, ?, 0)
        """,
        (interrupted_id, TaskStatus.RUNNING.value, fid),
    )
    await c1.db.close()

    # Simulate fresh process startup
    c2 = AppContainer.build(temp_settings)
    await c2.db.connect()
    recovery = await c2.task_engine.start()
    assert interrupted_id in recovery["recovered_task_ids"]

    recovered_task = await c2.task_engine.get_task_detail(interrupted_id)
    assert recovered_task["status"] == TaskStatus.NEEDS_USER_ACTION.value
    assert recovered_task["error_code"] == "SERVER_RESTART_INTERRUPTED"
    assert recovered_task["steps"][0]["status"] == TaskStatus.FAILED.value

    await c2.task_engine.stop()
    await c2.db.close()


def test_gate5_fastapi_health_spa_and_endpoints(fastapi_app):
    """
    [검증 5] 프론트엔드 빌드 서빙 + FastAPI 기동 후 GET /health 및 주요 API 엔드포인트 정상 응답
    """
    with TestClient(fastapi_app) as client:
        h = client.get("/health")
        assert h.status_code == 200
        h_json = h.json()
        assert h_json["status"] == "ok"
        assert h_json["max_concurrent_jobs"] == 1

        st = client.get("/api/system/status")
        assert st.status_code == 200
        assert st.json()["ok"] is True

        # SPA root serves built React index.html
        root = client.get("/")
        assert root.status_code == 200
        assert "<!doctype html>" in root.text.lower()

        # Upload & list via HTTP API without any AI keys
        up = client.post("/api/files/upload", files={"file": ("api_test.txt", b"hello api", "text/plain")})
        assert up.status_code == 200
        uploaded = up.json()["data"]
        assert uploaded["name"] == "api_test.txt"

        # Extension change MUST require acknowledge_no_reencode=true
        bad_ext = client.post(
            f"/api/files/{uploaded['file_id']}/change-extension",
            json={"new_extension": "md", "acknowledge_no_reencode": False},
        )
        assert bad_ext.status_code == 400

        ok_ext = client.post(
            f"/api/files/{uploaded['file_id']}/change-extension",
            json={"new_extension": "md", "acknowledge_no_reencode": True},
        )
        assert ok_ext.status_code == 200
        assert ok_ext.json()["data"]["name"] == "api_test.md"

        # Delete MUST require confirmed=true
        bad_del = client.post(f"/api/files/{uploaded['file_id']}/delete", json={"confirmed": False})
        assert bad_del.status_code == 400

        ok_del = client.post(f"/api/files/{uploaded['file_id']}/delete", json={"confirmed": True})
        assert ok_del.status_code == 200
