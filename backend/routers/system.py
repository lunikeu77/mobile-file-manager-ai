from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from backend.db import utc_now_iso

router = APIRouter(tags=["system"])


@router.get("/health")
async def health_check(request: Request):
    container = request.app.state.container
    binaries = container.settings.detect_binaries()
    return {
        "status": "ok",
        "timestamp": utc_now_iso(),
        "app_env": container.settings.app_env,
        "max_concurrent_jobs": container.settings.max_concurrent_jobs,
        "binaries": {k: bool(v) for k, v in binaries.items()},
    }


@router.get("/api/system/status")
async def system_status(request: Request):
    container = request.app.state.container
    binaries = container.settings.detect_binaries()
    files = await container.local_files.list_files(source="local")
    tasks = await container.task_engine.list_tasks()
    downloads = await container.downloads.list_jobs()
    drive_cfg = await container.drive.get_oauth_config()
    providers = await container.ai_router.list_provider_settings()

    active_tasks = [t for t in tasks if t["status"] in {"PENDING", "RUNNING", "SCHEDULED", "NEEDS_USER_ACTION"}]
    active_downloads = [d for d in downloads if d["status"] in {"PENDING", "RUNNING", "PAUSED"}]
    total_local_bytes = sum(f["size"] for f in files)

    return {
        "ok": True,
        "data": {
            "app_env": container.settings.app_env,
            "max_concurrent_jobs": container.settings.max_concurrent_jobs,
            "using_ephemeral_secret": container.crypto.using_ephemeral_secret,
            "binaries": binaries,
            "local_file_count": len(files),
            "local_total_bytes": total_local_bytes,
            "active_task_count": len(active_tasks),
            "active_download_count": len(active_downloads),
            "drive_connected": drive_cfg["connected"],
            "ai_configured_count": sum(1 for p in providers if p["key_configured"]),
            "render_notices": [
                "Render 무료 플랜은 약 15분간 외부 HTTP 요청이 없으면 유휴 수면(Spin-down) 상태가 되며, 재기동 시 임시 디스크(/app/data)는 초기화될 수 있습니다.",
                "브라우저 탭을 닫아도 서버가 깨어 있는 동안 백그라운드 FFmpeg/PDF 압축 및 예약 작업은 계속 실행됩니다.",
                "예약 실행은 서버 시계 기반 best-effort 방식이며, 유휴 수면을 피하려면 /health 엔드포인트를 외부 Keep-Alive 모니터에 등록할 수 있습니다.",
                "사용자가 UI에 입력한 API 키/토큰은 Fernet(ENCRYPTION_SECRET)으로 암호화 저장되지만, 서버 운영 환경 메모리/런타임 접근까지 완전히 차단하는 HSM은 아닙니다.",
            ],
            "licenses": {
                "ffmpeg": "LGPL/GPL (system binary)",
                "pikepdf": "MPL-2.0",
                "pypdf": "BSD-3-Clause",
                "ghostscript": "AGPL-3.0 (optional system binary; commercial redistribution requires license review)",
            },
        },
    }


@router.get("/api/events")
async def sse_events(request: Request):
    container = request.app.state.container
    queue = container.downloads.subscribe()

    async def event_generator():
        try:
            yield {"event": "connected", "data": json.dumps({"ts": utc_now_iso()})}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield {"event": msg["type"], "data": json.dumps(msg["data"], ensure_ascii=False)}
                except asyncio.TimeoutError:
                    yield {"event": "heartbeat", "data": json.dumps({"ts": utc_now_iso()})}
        finally:
            container.downloads.unsubscribe(queue)

    return EventSourceResponse(event_generator())
