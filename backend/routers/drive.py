from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from backend.models import (
    ChangeExtensionRequest,
    ConfirmDeleteRequest,
    CreateDriveUploadRequest,
    GoogleOAuthConfigUpdate,
    RenameFileRequest,
)
from backend.security.validator import SpecValidationError
from backend.services.google_drive import GoogleDriveAuthError

router = APIRouter(prefix="/api/drive", tags=["drive"])


@router.get("/config")
async def get_drive_config(request: Request):
    container = request.app.state.container
    return {"ok": True, "data": await container.drive.get_oauth_config()}


@router.put("/config")
async def update_drive_config(request: Request, body: GoogleOAuthConfigUpdate):
    container = request.app.state.container
    cfg = await container.drive.save_oauth_config(body.client_id, body.client_secret, body.scope_mode)
    return {"ok": True, "data": cfg}


@router.get("/oauth/start")
async def start_oauth(request: Request):
    container = request.app.state.container
    try:
        data = await container.drive.create_authorization_url()
        return {"ok": True, "data": data}
    except GoogleDriveAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/oauth/callback")
async def oauth_callback(
    request: Request,
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
):
    if error or not code or not state:
        reason = quote(error or "missing_code_or_state")
        return RedirectResponse(url=f"/?drive=error&reason={reason}")
    container = request.app.state.container
    try:
        await container.drive.handle_oauth_callback(code, state)
        return RedirectResponse(url="/?drive=connected")
    except GoogleDriveAuthError as exc:
        return RedirectResponse(url=f"/?drive=error&reason={quote(str(exc))}")


@router.post("/disconnect")
async def disconnect_drive(request: Request):
    container = request.app.state.container
    await container.drive.disconnect()
    return {"ok": True, "data": {"connected": False}}


@router.get("/files")
async def list_drive_files(
    request: Request,
    query: str | None = Query(None),
    page_token: str | None = Query(None),
    page_size: int = Query(50, ge=1, le=100),
):
    container = request.app.state.container
    try:
        res = await container.drive.list_files(query=query, page_token=page_token, page_size=page_size)
        return {"ok": True, "data": res}
    except GoogleDriveAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/files/{file_id}")
async def get_drive_file(request: Request, file_id: str):
    container = request.app.state.container
    try:
        meta = await container.drive.get_file_metadata(file_id)
        return {"ok": True, "data": meta}
    except GoogleDriveAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.post("/files/{file_id}/rename")
async def rename_drive_file(request: Request, file_id: str, body: RenameFileRequest):
    container = request.app.state.container
    try:
        updated = await container.drive.rename_file(file_id, body.new_name)
        return {"ok": True, "data": updated}
    except GoogleDriveAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except SpecValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/files/{file_id}/change-extension")
async def change_drive_extension(request: Request, file_id: str, body: ChangeExtensionRequest):
    if not body.acknowledge_no_reencode:
        raise HTTPException(status_code=400, detail="확장자 변경 시 파일 내용 미변환 확인이 필요합니다.")
    container = request.app.state.container
    try:
        updated = await container.drive.change_extension(file_id, body.new_extension)
        return {"ok": True, "data": updated}
    except GoogleDriveAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except SpecValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/files/{file_id}/delete")
async def delete_drive_file(request: Request, file_id: str, body: ConfirmDeleteRequest):
    if not body.confirmed:
        raise HTTPException(status_code=400, detail="Google Drive 파일 삭제는 명시적 사용자 확인이 필요합니다.")
    container = request.app.state.container
    try:
        res = await container.drive.delete_file(file_id)
        return {"ok": True, "data": res}
    except GoogleDriveAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except SpecValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/upload")
async def upload_to_drive(request: Request, body: CreateDriveUploadRequest):
    if not body.confirmed:
        raise HTTPException(status_code=400, detail="외부 Google Drive 업로드는 대상 위치 확인(confirmed=true)이 필요합니다.")
    container = request.app.state.container
    try:
        res = await container.drive.resumable_upload_local_file(
            body.file_id,
            parent_folder_id=body.parent_folder_id,
        )
        return {"ok": True, "data": res}
    except GoogleDriveAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SpecValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/download/{file_id}")
async def download_from_drive(request: Request, file_id: str):
    container = request.app.state.container
    try:
        res = await container.drive.download_file_to_local(file_id)
        return {"ok": True, "data": res}
    except GoogleDriveAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except SpecValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
