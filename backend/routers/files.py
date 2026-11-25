from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from backend.models import (
    BatchRenameRequest,
    ChangeExtensionRequest,
    ConfirmDeleteRequest,
    OverwriteConfirmRequest,
    RenameFileRequest,
)
from backend.security.validator import SpecValidationError

router = APIRouter(prefix="/api/files", tags=["files"])


@router.get("")
async def list_files(
    request: Request,
    source: str = Query("local"),
    query: str | None = Query(None),
    mime_type: str | None = Query(None),
    extension: str | None = Query(None),
    min_size_bytes: int | None = Query(None),
    max_size_bytes: int | None = Query(None),
    folder: str | None = Query(None),
):
    container = request.app.state.container
    items = await container.local_files.list_files(
        source=source,
        query=query,
        mime_type=mime_type,
        extension=extension,
        min_size_bytes=min_size_bytes,
        max_size_bytes=max_size_bytes,
        folder=folder,
    )
    return {"ok": True, "data": items}


@router.post("/upload")
async def upload_file(request: Request, file: UploadFile = File(...)):
    container = request.app.state.container
    try:
        saved = await container.local_files.save_upload_stream(file)
        return {"ok": True, "data": saved}
    except SpecValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{file_id}")
async def get_file_detail(request: Request, file_id: str):
    container = request.app.state.container
    rec = await container.local_files.get_file(file_id)
    if not rec:
        raise HTTPException(status_code=404, detail="File not found.")
    return {"ok": True, "data": rec}


@router.get("/{file_id}/download")
async def download_local_file(request: Request, file_id: str):
    container = request.app.state.container
    rec = await container.local_files.get_file(file_id)
    if not rec or rec["source"] != "local":
        raise HTTPException(status_code=404, detail="Local file not found.")
    path = Path(rec["local_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="File missing on disk.")
    return FileResponse(path=path, filename=rec["name"], media_type=rec["mime_type"])


@router.post("/{file_id}/rename")
async def rename_file(request: Request, file_id: str, body: RenameFileRequest):
    container = request.app.state.container
    try:
        updated = await container.local_files.rename_local_file(file_id, body.new_name)
        return {"ok": True, "data": updated}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SpecValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/batch-rename")
async def batch_rename_files(request: Request, body: BatchRenameRequest):
    container = request.app.state.container
    results = []
    errors = []
    for item in body.items:
        try:
            updated = await container.local_files.rename_local_file(item.file_id, item.new_name)
            results.append(updated)
        except Exception as exc:
            errors.append({"file_id": item.file_id, "error": str(exc)})
    return {"ok": len(errors) == 0, "data": {"updated": results, "errors": errors}}


@router.post("/{file_id}/change-extension")
async def change_extension(request: Request, file_id: str, body: ChangeExtensionRequest):
    if not body.acknowledge_no_reencode:
        raise HTTPException(
            status_code=400,
            detail="확장자 변경은 실제 파일 내용(바이트)을 재인코딩하지 않음을 확인(acknowledge_no_reencode=true)해야 합니다.",
        )
    container = request.app.state.container
    try:
        updated = await container.local_files.change_extension_local(file_id, body.new_extension)
        return {"ok": True, "data": updated}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SpecValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{file_id}/delete")
async def delete_file(request: Request, file_id: str, body: ConfirmDeleteRequest):
    if not body.confirmed:
        raise HTTPException(status_code=400, detail="파일 삭제는 명시적 사용자 확인(confirmed=true)이 필요합니다.")
    container = request.app.state.container
    try:
        res = await container.local_files.delete_local_file(file_id)
        return {"ok": True, "data": res}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/overwrite")
async def overwrite_file(request: Request, body: OverwriteConfirmRequest):
    if not body.confirmed:
        raise HTTPException(status_code=400, detail="파일 덮어쓰기는 명시적 사용자 확인(confirmed=true)이 필요합니다.")
    container = request.app.state.container
    try:
        res = await container.local_files.overwrite_local_file(body.source_file_id, body.target_file_id)
        return {"ok": True, "data": res}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/cleanup-temp")
async def cleanup_temp(request: Request):
    container = request.app.state.container
    prefs = await container.db.get_preferences()
    hours = int(prefs.get("temp_cleanup_hours", "24"))
    res = await container.local_files.cleanup_temp_files(max_age_hours=hours)
    return {"ok": True, "data": res}
