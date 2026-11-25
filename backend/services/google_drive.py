from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import shutil
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from backend.config import Settings
from backend.db import Database, utc_now_iso
from backend.security.validator import validate_safe_extension, validate_safe_filename
from backend.services.downloads import DownloadQueueService
from backend.services.local_files import LocalFileService
from backend.services.secrets_store import EncryptedSecretStore

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_DRIVE_API = "https://www.googleapis.com/drive/v3"
GOOGLE_DRIVE_UPLOAD_API = "https://www.googleapis.com/upload/drive/v3/files"

SCOPE_MAP = {
    "drive.file": "https://www.googleapis.com/auth/drive.file",
    "drive.readonly": "https://www.googleapis.com/auth/drive.readonly",
    "drive": "https://www.googleapis.com/auth/drive",
}


class GoogleDriveAuthError(RuntimeError):
    """Raised when Google OAuth credentials or tokens are missing/invalid."""


class GoogleDriveService:
    """
    Google Drive API v3 REST integration:
    - OAuth 2.0 Authorization Code + PKCE
    - Minimal scope (`drive.file` default) with clear upgrade path
    - Paginated `files.list` cached in SQLite `file_cache`
    - Resumable upload (`uploadType=resumable`) & streaming download
    - Encrypted token storage (never plaintext in SQLite or logs)
    """

    SECRET_CLIENT_ID = "google_oauth:client_id"
    SECRET_CLIENT_SECRET = "google_oauth:client_secret"
    SECRET_TOKENS = "google_oauth:tokens"

    def __init__(
        self,
        db: Database,
        settings: Settings,
        secrets_store: EncryptedSecretStore,
        local_files: LocalFileService,
        downloads: DownloadQueueService,
    ) -> None:
        self.db = db
        self.settings = settings
        self.secrets = secrets_store
        self.local_files = local_files
        self.downloads = downloads
        # In-memory PKCE state cache (short-lived during OAuth handshake)
        self._pending_states: dict[str, dict[str, Any]] = {}

    async def get_oauth_config(self) -> dict[str, Any]:
        prefs = await self.db.get_preferences()
        scope_mode = prefs.get("google_scope_mode", "drive.file")
        stored_cid = await self.secrets.get_secret(self.SECRET_CLIENT_ID)
        stored_csec = await self.secrets.get_secret(self.SECRET_CLIENT_SECRET)

        client_id = stored_cid or self.settings.google_client_id
        client_secret = stored_csec or self.settings.google_client_secret
        tokens = await self.secrets.get_json_secret(self.SECRET_TOKENS)

        return {
            "configured": bool(client_id and client_secret),
            "connected": bool(tokens and (tokens.get("access_token") or tokens.get("refresh_token"))),
            "client_id_masked": f"{client_id[:8]}••••{client_id[-6:]}" if client_id and len(client_id) > 16 else ("configured" if client_id else ""),
            "redirect_uri": self.settings.effective_google_redirect_uri,
            "scope_mode": scope_mode,
            "scope_url": SCOPE_MAP.get(scope_mode, SCOPE_MAP["drive.file"]),
            "scope_notice": (
                "기본 Scope인 'drive.file'은 본 앱이 생성하거나 사용자가 명시적으로 연 파일만 접근합니다. "
                "전체 Drive 탐색이 필요한 경우 'drive.readonly' 또는 'drive'를 선택할 수 있으며, "
                "프로덕션 공개 배포 시 Google OAuth 앱 검수(Verification)가 요구될 수 있습니다."
            ),
        }

    async def save_oauth_config(self, client_id: str, client_secret: str, scope_mode: str) -> dict[str, Any]:
        await self.secrets.set_secret(self.SECRET_CLIENT_ID, client_id.strip())
        await self.secrets.set_secret(self.SECRET_CLIENT_SECRET, client_secret.strip())
        await self.db.set_preference("google_scope_mode", scope_mode)
        return await self.get_oauth_config()

    async def _resolve_client_creds(self) -> tuple[str, str]:
        cid = (await self.secrets.get_secret(self.SECRET_CLIENT_ID)) or self.settings.google_client_id
        csec = (await self.secrets.get_secret(self.SECRET_CLIENT_SECRET)) or self.settings.google_client_secret
        if not cid or not csec:
            raise GoogleDriveAuthError(
                "Google OAuth Client ID/Secret이 설정되지 않았습니다. 설정 화면 또는 .env에서 등록하세요."
            )
        return cid, csec

    async def create_authorization_url(self) -> dict[str, str]:
        cid, _ = await self._resolve_client_creds()
        prefs = await self.db.get_preferences()
        scope_mode = prefs.get("google_scope_mode", "drive.file")
        scope = SCOPE_MAP.get(scope_mode, SCOPE_MAP["drive.file"])

        state = secrets.token_urlsafe(24)
        code_verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

        self._pending_states[state] = {
            "code_verifier": code_verifier,
            "created_at": time.time(),
        }

        params = {
            "client_id": cid,
            "redirect_uri": self.settings.effective_google_redirect_uri,
            "response_type": "code",
            "scope": scope,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return {"auth_url": f"{GOOGLE_AUTH_URL}?{urlencode(params)}", "state": state}

    async def handle_oauth_callback(self, code: str, state: str) -> dict[str, Any]:
        pending = self._pending_states.pop(state, None)
        if not pending or time.time() - pending["created_at"] > 900:
            raise GoogleDriveAuthError("Invalid or expired OAuth state parameter.")

        cid, csec = await self._resolve_client_creds()
        payload = {
            "code": code,
            "client_id": cid,
            "client_secret": csec,
            "redirect_uri": self.settings.effective_google_redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": pending["code_verifier"],
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(GOOGLE_TOKEN_URL, data=payload)
        if resp.status_code != 200:
            raise GoogleDriveAuthError(f"OAuth token exchange failed ({resp.status_code}).")
        token_data = resp.json()
        token_data["expires_at"] = time.time() + int(token_data.get("expires_in", 3600))
        await self.secrets.set_json_secret(self.SECRET_TOKENS, token_data)
        return {"connected": True, "scope": token_data.get("scope")}

    async def disconnect(self) -> None:
        await self.secrets.delete_secret(self.SECRET_TOKENS)
        await self.db.execute("DELETE FROM file_cache WHERE source = 'google_drive'")

    async def _get_valid_access_token(self) -> str:
        tokens = await self.secrets.get_json_secret(self.SECRET_TOKENS)
        if not tokens or not tokens.get("access_token"):
            raise GoogleDriveAuthError("Google Drive에 로그인되어 있지 않습니다.")

        expires_at = float(tokens.get("expires_at") or 0)
        if time.time() < expires_at - 60:
            return str(tokens["access_token"])

        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            return str(tokens["access_token"])

        cid, csec = await self._resolve_client_creds()
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": cid,
                    "client_secret": csec,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
        if resp.status_code != 200:
            raise GoogleDriveAuthError("Google OAuth token refresh failed; please sign in again.")
        refreshed = resp.json()
        tokens["access_token"] = refreshed["access_token"]
        tokens["expires_at"] = time.time() + int(refreshed.get("expires_in", 3600))
        await self.secrets.set_json_secret(self.SECRET_TOKENS, tokens)
        return str(tokens["access_token"])

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        token = await self._get_valid_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.request(method, url, headers=headers, params=params, json=json_body)
                if resp.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                    await asyncio.sleep(0.5 * (2**attempt))
                    continue
                if resp.status_code in {401, 403}:
                    raise GoogleDriveAuthError(f"Google Drive authentication/permission error ({resp.status_code}).")
                resp.raise_for_status()
                return resp
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < 2:
                    await asyncio.sleep(0.5 * (2**attempt))
                    continue
                raise RuntimeError(f"Google Drive network error: {exc}") from exc
        raise RuntimeError(f"Google Drive request failed: {last_exc}")

    async def list_files(
        self,
        *,
        query: str | None = None,
        mime_type: str | None = None,
        extension: str | None = None,
        min_size_bytes: int | None = None,
        max_size_bytes: int | None = None,
        page_token: str | None = None,
        page_size: int = 50,
    ) -> dict[str, Any]:
        q_parts = ["trashed = false"]
        if query:
            escaped = query.replace("'", "\\'")
            q_parts.append(f"name contains '{escaped}'")
        if mime_type:
            escaped_mime = mime_type.replace("'", "\\'")
            q_parts.append(f"mimeType contains '{escaped_mime}'")
        if extension:
            ext_clean = extension.lstrip(".").replace("'", "\\'")
            q_parts.append(f"name contains '.{ext_clean}'")

        params: dict[str, Any] = {
            "q": " and ".join(q_parts),
            "pageSize": min(max(1, page_size), 100),
            "fields": "nextPageToken, files(id, name, mimeType, size, modifiedTime, parents, webViewLink)",
            "orderBy": "modifiedTime desc",
        }
        if page_token:
            params["pageToken"] = page_token

        resp = await self._request_with_retry("GET", f"{GOOGLE_DRIVE_API}/files", params=params)
        data = resp.json()
        raw_files = data.get("files", [])
        now = utc_now_iso()

        for item in raw_files:
            meta = {"webViewLink": item.get("webViewLink"), "parents": item.get("parents", [])}
            await self.db.execute(
                """
                INSERT INTO file_cache
                (file_id, source, name, mime_type, size, modified_time, cached_at, parent_id, local_path, metadata_json)
                VALUES (?, 'google_drive', ?, ?, ?, ?, ?, ?, NULL, ?)
                ON CONFLICT(file_id) DO UPDATE SET
                    name = excluded.name,
                    mime_type = excluded.mime_type,
                    size = excluded.size,
                    modified_time = excluded.modified_time,
                    cached_at = excluded.cached_at,
                    parent_id = excluded.parent_id,
                    metadata_json = excluded.metadata_json
                """,
                (
                    item["id"],
                    item["name"],
                    item.get("mimeType", "application/octet-stream"),
                    int(item.get("size") or 0),
                    item.get("modifiedTime") or now,
                    now,
                    (item.get("parents") or [None])[0],
                    json.dumps(meta),
                ),
            )

        filtered: list[dict[str, Any]] = []
        for item in raw_files:
            size_val = int(item.get("size") or 0)
            name_val = str(item.get("name") or "").lower()
            mime_val = str(item.get("mimeType") or "").lower()
            if query and query.lower() not in name_val:
                continue
            if mime_type and mime_type.lower() not in mime_val:
                continue
            if extension and not name_val.endswith(f".{extension.lower().lstrip('.')}"):
                continue
            if min_size_bytes is not None and size_val < min_size_bytes:
                continue
            if max_size_bytes is not None and size_val > max_size_bytes:
                continue
            filtered.append(item)

        return {
            "files": filtered,
            "next_page_token": data.get("nextPageToken"),
        }

    async def get_file_metadata(self, file_id: str) -> dict[str, Any]:
        resp = await self._request_with_retry(
            "GET",
            f"{GOOGLE_DRIVE_API}/files/{file_id}",
            params={"fields": "id, name, mimeType, size, modifiedTime, parents, webViewLink"},
        )
        return resp.json()

    async def rename_file(self, file_id: str, new_name: str) -> dict[str, Any]:
        safe_name = validate_safe_filename(new_name)
        resp = await self._request_with_retry(
            "PATCH",
            f"{GOOGLE_DRIVE_API}/files/{file_id}",
            params={"fields": "id, name, mimeType, size, modifiedTime"},
            json_body={"name": safe_name},
        )
        updated = resp.json()
        await self.db.execute(
            "UPDATE file_cache SET name = ?, modified_time = ?, cached_at = ? WHERE file_id = ?",
            (updated["name"], updated.get("modifiedTime") or utc_now_iso(), utc_now_iso(), file_id),
        )
        return updated

    async def change_extension(self, file_id: str, new_extension: str) -> dict[str, Any]:
        meta = await self.get_file_metadata(file_id)
        clean_ext = validate_safe_extension(new_extension)
        stem = Path(meta["name"]).stem
        updated = await self.rename_file(file_id, f"{stem}.{clean_ext}")
        updated["is_format_conversion"] = False
        updated["warning"] = (
            "확장자 문자열만 변경되었습니다. Google Drive 내 파일 바이트 내용은 변환되지 않으며 형식이 오인될 수 있습니다."
        )
        return updated

    async def delete_file(self, file_id: str) -> dict[str, Any]:
        await self._request_with_retry("DELETE", f"{GOOGLE_DRIVE_API}/files/{file_id}")
        await self.db.execute("DELETE FROM file_cache WHERE file_id = ?", (file_id,))
        return {"deleted_file_id": file_id}

    async def download_file_to_local(self, file_id: str, task_id: str | None = None) -> dict[str, Any]:
        meta = await self.get_file_metadata(file_id)
        safe_name = validate_safe_filename(meta["name"])
        total_bytes = int(meta.get("size") or 0)
        token = await self._get_valid_access_token()
        dl_id = await self.downloads.register_drive_transfer(
            direction="download",
            filename=safe_name,
            source_file_id=file_id,
            total_bytes=total_bytes,
            task_id=task_id,
        )

        self.settings.ensure_directories()
        temp_path = self.settings.temp_dir / f"drive_{uuid.uuid4().hex[:10]}.tmp"
        dest_path = self.local_files._unique_path(self.settings.workspace_dir, safe_name)

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=120.0)) as client:
                async with client.stream(
                    "GET",
                    f"{GOOGLE_DRIVE_API}/files/{file_id}",
                    params={"alt": "media"},
                    headers={"Authorization": f"Bearer {token}"},
                ) as resp:
                    resp.raise_for_status()
                    bytes_received = 0
                    with temp_path.open("wb") as fh:
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            fh.write(chunk)
                            bytes_received += len(chunk)
                            if bytes_received % (512 * 1024) < 65536:
                                await self.downloads.update_transfer_progress(dl_id, bytes_received=bytes_received)

            shutil.move(str(temp_path), str(dest_path))
            await self.local_files.sync_workspace_cache()
            await self.downloads.update_transfer_progress(
                dl_id,
                bytes_received=dest_path.stat().st_size,
                status="COMPLETED",
                output_path=str(dest_path.resolve()),
            )
            local_record = next(
                (
                    f
                    for f in await self.local_files.list_files(source="local")
                    if f.get("local_path") == str(dest_path.resolve())
                ),
                None,
            )
            return {"download_id": dl_id, "local_file": local_record}
        except Exception as exc:
            temp_path.unlink(missing_ok=True)
            await self.downloads.update_transfer_progress(dl_id, bytes_received=0, status="FAILED", error_message=str(exc))
            raise

    async def resumable_upload_local_file(
        self,
        local_file_id: str,
        *,
        parent_folder_id: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Uploads a local file to Google Drive via streaming Resumable Upload protocol."""
        record = await self.local_files.get_file(local_file_id)
        if not record or record["source"] != "local":
            raise FileNotFoundError(f"Local file '{local_file_id}' not found.")
        local_path = Path(record["local_path"])
        total_bytes = local_path.stat().st_size
        mime_type = record["mime_type"] or "application/octet-stream"
        token = await self._get_valid_access_token()

        dl_id = await self.downloads.register_drive_transfer(
            direction="upload_drive",
            filename=record["name"],
            source_file_id=local_file_id,
            total_bytes=total_bytes,
            task_id=task_id,
        )

        metadata: dict[str, Any] = {"name": record["name"]}
        if parent_folder_id:
            metadata["parents"] = [parent_folder_id]

        chunk_size = 1024 * 1024 * 2  # 2MB chunks (safe for 512MB RAM)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=120.0)) as client:
                init_resp = await client.post(
                    GOOGLE_DRIVE_UPLOAD_API,
                    params={"uploadType": "resumable", "fields": "id, name, mimeType, size, webViewLink"},
                    headers={
                        "Authorization": f"Bearer {token}",
                        "X-Upload-Content-Type": mime_type,
                        "X-Upload-Content-Length": str(total_bytes),
                    },
                    json=metadata,
                )
                init_resp.raise_for_status()
                session_uri = init_resp.headers["Location"]

                uploaded = 0
                drive_result: dict[str, Any] = {}
                with local_path.open("rb") as fh:
                    if total_bytes == 0:
                        put_resp = await client.put(
                            session_uri,
                            headers={"Content-Length": "0", "Content-Type": mime_type},
                            content=b"",
                        )
                        put_resp.raise_for_status()
                        drive_result = put_resp.json()
                    else:
                        while uploaded < total_bytes:
                            chunk = fh.read(chunk_size)
                            if not chunk:
                                break
                            end = uploaded + len(chunk) - 1
                            headers = {
                                "Content-Length": str(len(chunk)),
                                "Content-Range": f"bytes {uploaded}-{end}/{total_bytes}",
                                "Content-Type": mime_type,
                            }
                            put_resp = await client.put(session_uri, headers=headers, content=chunk)
                            if put_resp.status_code == 308:
                                uploaded += len(chunk)
                                await self.downloads.update_transfer_progress(dl_id, bytes_received=uploaded)
                            elif put_resp.status_code in {200, 201}:
                                uploaded += len(chunk)
                                drive_result = put_resp.json()
                                break
                            else:
                                put_resp.raise_for_status()

            await self.downloads.update_transfer_progress(
                dl_id, bytes_received=total_bytes, status="COMPLETED", output_path=drive_result.get("id")
            )
            return {"download_id": dl_id, "drive_file": drive_result}
        except Exception as exc:
            await self.downloads.update_transfer_progress(dl_id, bytes_received=0, status="FAILED", error_message=str(exc))
            raise
