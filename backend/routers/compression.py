from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request

from backend.models import AllowedAction, CompressPdfRequest, CompressVideoRequest, VideoPreset

router = APIRouter(prefix="/api/compression", tags=["compression"])


@router.get("/video/probe/{file_id}")
async def probe_video(request: Request, file_id: str):
    container = request.app.state.container
    rec = await container.local_files.get_file(file_id)
    if not rec or rec["source"] != "local":
        raise HTTPException(status_code=404, detail="Local video file not found.")
    try:
        info = await container.compression.probe_video(Path(rec["local_path"]))
        return {"ok": True, "data": info}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/video/estimate")
async def estimate_video(
    request: Request,
    duration_sec: float = Query(..., gt=0),
    preset: VideoPreset = Query(VideoPreset.BALANCED),
    target_size_bytes: int | None = Query(None, ge=1024),
    audio_kbps: int = Query(96, ge=32, le=320),
):
    container = request.app.state.container
    est = container.compression.estimate_video_target(
        duration_sec=duration_sec,
        preset=preset,
        target_size_bytes=target_size_bytes,
        audio_kbps=audio_kbps,
    )
    return {"ok": True, "data": est}


@router.post("/video")
async def enqueue_video_compression(request: Request, body: CompressVideoRequest):
    container = request.app.state.container
    rec = await container.local_files.get_file(body.file_id)
    if not rec:
        raise HTTPException(status_code=404, detail="File not found.")
    if rec["source"] != "local":
        raise HTTPException(
            status_code=400,
            detail="Google Drive 파일은 먼저 로컬 워크스페이스로 다운로드한 뒤 압축하세요.",
        )
    task = await container.task_engine.create_task(
        title=f"MP4 압축: {rec['name']} ({body.preset.value})",
        task_type="compress_video",
        scheduled_at=body.scheduled_at,
        idempotency_key=f"cmp_vid_{body.file_id}_{uuid.uuid4().hex[:8]}",
        spec={
            "schema_version": "1.0",
            "intent": "compress_video",
            "steps": [
                {
                    "action": AllowedAction.COMPRESS_VIDEO.value,
                    "source": "local",
                    "file_id": body.file_id,
                    "options": {
                        "preset": body.preset.value,
                        "target_size_bytes": body.target_size_bytes,
                        "video_codec": body.video_codec,
                    },
                }
            ],
            "requires_confirmation": False,
        },
    )
    return {"ok": True, "data": task}


@router.post("/pdf")
async def enqueue_pdf_compression(request: Request, body: CompressPdfRequest):
    container = request.app.state.container
    rec = await container.local_files.get_file(body.file_id)
    if not rec:
        raise HTTPException(status_code=404, detail="File not found.")
    if rec["source"] != "local":
        raise HTTPException(
            status_code=400,
            detail="Google Drive 파일은 먼저 로컬 워크스페이스로 다운로드한 뒤 압축하세요.",
        )
    task = await container.task_engine.create_task(
        title=f"PDF 압축: {rec['name']} ({body.preset.value})",
        task_type="compress_pdf",
        scheduled_at=body.scheduled_at,
        idempotency_key=f"cmp_pdf_{body.file_id}_{uuid.uuid4().hex[:8]}",
        spec={
            "schema_version": "1.0",
            "intent": "compress_pdf",
            "steps": [
                {
                    "action": AllowedAction.COMPRESS_PDF.value,
                    "source": "local",
                    "file_id": body.file_id,
                    "options": {
                        "preset": body.preset.value,
                        "preserve_interactive": body.preserve_interactive,
                    },
                }
            ],
            "requires_confirmation": False,
        },
    )
    return {"ok": True, "data": task}
