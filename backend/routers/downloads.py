from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from backend.models import CreateDownloadRequest
from backend.security.validator import SpecValidationError

router = APIRouter(prefix="/api/downloads", tags=["downloads"])


@router.get("")
async def list_downloads(request: Request):
    container = request.app.state.container
    jobs = await container.downloads.list_jobs()
    return {"ok": True, "data": jobs}


@router.post("")
async def create_download(request: Request, body: CreateDownloadRequest):
    container = request.app.state.container
    try:
        job = await container.downloads.create_http_download(
            body.url,
            filename=body.filename,
            allow_localhost=container.settings.app_env != "production",
        )
        return {"ok": True, "data": job}
    except SpecValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{download_id}/pause")
async def pause_download(request: Request, download_id: str):
    container = request.app.state.container
    try:
        job = await container.downloads.pause_job(download_id)
        return {"ok": True, "data": job}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{download_id}/resume")
async def resume_download(request: Request, download_id: str):
    container = request.app.state.container
    try:
        job = await container.downloads.resume_job(download_id)
        return {"ok": True, "data": job}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{download_id}/cancel")
async def cancel_download(request: Request, download_id: str):
    container = request.app.state.container
    try:
        job = await container.downloads.cancel_job(download_id)
        return {"ok": True, "data": job}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
