from __future__ import annotations

import asyncio
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urljoin, urlparse

import httpx

from backend.config import Settings
from backend.db import Database, utc_now_iso
from backend.security.validator import SpecValidationError, validate_external_url, validate_safe_filename
from backend.services.local_files import LocalFileService


class DownloadQueueService:
    """
    Manages external HTTP downloads (with Range resume support, pause, cancel, retry)
    and Google Drive transfer queue records in SQLite `download_jobs`.
    """

    def __init__(self, db: Database, settings: Settings, local_files: LocalFileService) -> None:
        self.db = db
        self.settings = settings
        self.local_files = local_files
        self._active_tasks: dict[str, asyncio.Task[Any]] = {}
        self._pause_flags: set[str] = set()
        self._cancel_flags: set[str] = set()
        self._event_listeners: list[asyncio.Queue[dict[str, Any]]] = []

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self._event_listeners.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        if q in self._event_listeners:
            self._event_listeners.remove(q)

    async def emit_event(self, event_type: str, payload: dict[str, Any]) -> None:
        msg = {"type": event_type, "data": payload, "ts": utc_now_iso()}
        for q in list(self._event_listeners):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    async def list_jobs(self) -> list[dict[str, Any]]:
        return await self.db.fetch_all("SELECT * FROM download_jobs ORDER BY created_at DESC")

    async def get_job(self, download_id: str) -> dict[str, Any] | None:
        return await self.db.fetch_one("SELECT * FROM download_jobs WHERE download_id = ?", (download_id,))

    async def create_http_download(
        self,
        url: str,
        *,
        filename: str | None = None,
        task_id: str | None = None,
        allow_localhost: bool = False,
        auto_start: bool = True,
    ) -> dict[str, Any]:
        safe_url = validate_external_url(url, allow_localhost=allow_localhost)
        inferred_name = filename or Path(urlparse(safe_url).path).name or f"download_{uuid.uuid4().hex[:8]}.bin"
        safe_name = validate_safe_filename(inferred_name)
        download_id = f"dl_{uuid.uuid4().hex[:12]}"
        self.settings.ensure_directories()
        temp_path = str((self.settings.temp_dir / f"{download_id}.part").resolve())
        now = utc_now_iso()

        await self.db.execute(
            """
            INSERT INTO download_jobs
            (download_id, task_id, source_file_id, source_url, filename, direction,
             local_temp_path, output_path, bytes_received, total_bytes, supports_range, status, error_message, created_at, updated_at)
            VALUES (?, ?, NULL, ?, ?, 'download', ?, NULL, 0, 0, 0, 'PENDING', NULL, ?, ?)
            """,
            (download_id, task_id, safe_url, safe_name, temp_path, now, now),
        )
        job = await self.get_job(download_id)
        assert job is not None
        await self.emit_event("download_created", job)
        if auto_start:
            self.start_download_background(download_id)
        return job

    def start_download_background(self, download_id: str) -> None:
        if download_id in self._active_tasks and not self._active_tasks[download_id].done():
            return
        self._pause_flags.discard(download_id)
        self._cancel_flags.discard(download_id)
        self._active_tasks[download_id] = asyncio.create_task(self.run_http_download(download_id))

    @asynccontextmanager
    async def _validated_stream(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        allow_localhost: bool = False,
        max_redirects: int = 5,
    ) -> AsyncIterator[httpx.Response]:
        current_url = validate_external_url(url, allow_localhost=allow_localhost)
        for _ in range(max_redirects + 1):
            async with client.stream("GET", current_url, headers=headers or {}) as resp:
                if resp.status_code in {301, 302, 303, 307, 308}:
                    location = resp.headers.get("Location")
                    if not location:
                        raise SpecValidationError("Redirect response missing Location header.")
                    next_url = urljoin(current_url, location)
                    current_url = validate_external_url(next_url, allow_localhost=allow_localhost)
                    continue
                yield resp
                return
        raise SpecValidationError("Too many HTTP redirects.")

    async def _finalize_local_record(self, dest_path: Path) -> dict[str, Any] | None:
        resolved = str(dest_path.resolve())
        files = await self.local_files.list_files(source="local")
        return next((f for f in files if f.get("local_path") == resolved), None)

    async def run_http_download(self, download_id: str) -> dict[str, Any]:
        job = await self.get_job(download_id)
        if not job:
            raise FileNotFoundError(f"Download job {download_id} not found.")
        if not job.get("source_url"):
            raise ValueError("HTTP source_url is missing for this transfer job.")

        allow_localhost = self.settings.app_env != "production"
        temp_path = Path(job["local_temp_path"])
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        existing_bytes = temp_path.stat().st_size if temp_path.exists() else 0

        await self.db.execute(
            "UPDATE download_jobs SET status = 'RUNNING', error_message = NULL, updated_at = ? WHERE download_id = ?",
            (utc_now_iso(), download_id),
        )

        headers: dict[str, str] = {}
        if existing_bytes > 0:
            headers["Range"] = f"bytes={existing_bytes}-"

        try:
            async with httpx.AsyncClient(follow_redirects=False, timeout=httpx.Timeout(30.0, read=60.0)) as client:
                async with self._validated_stream(
                    client, job["source_url"], headers=headers, allow_localhost=allow_localhost
                ) as resp:
                    if resp.status_code == 416 and existing_bytes > 0:
                        existing_bytes = 0
                        temp_path.unlink(missing_ok=True)
                        return await self._fresh_download(
                            download_id, client, job, temp_path, allow_localhost=allow_localhost
                        )

                    resp.raise_for_status()
                    is_resumed = resp.status_code == 206
                    supports_range = is_resumed or ("bytes" in resp.headers.get("accept-ranges", "").lower())

                    if existing_bytes > 0 and not is_resumed:
                        # Server ignored Range header; start from byte 0
                        existing_bytes = 0
                        mode = "wb"
                    else:
                        mode = "ab" if is_resumed else "wb"

                    content_len = int(resp.headers.get("content-length") or 0)
                    total_bytes = existing_bytes + content_len if content_len > 0 else 0
                    bytes_received = existing_bytes

                    await self.db.execute(
                        "UPDATE download_jobs SET total_bytes = ?, supports_range = ?, bytes_received = ?, updated_at = ? WHERE download_id = ?",
                        (total_bytes, 1 if supports_range else 0, bytes_received, utc_now_iso(), download_id),
                    )

                    last_emit = 0
                    with temp_path.open(mode) as fh:
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            if download_id in self._cancel_flags:
                                fh.close()
                                temp_path.unlink(missing_ok=True)
                                await self.db.execute(
                                    "UPDATE download_jobs SET status = 'CANCELLED', updated_at = ? WHERE download_id = ?",
                                    (utc_now_iso(), download_id),
                                )
                                cancelled_job = await self.get_job(download_id)
                                if cancelled_job:
                                    await self.emit_event("download_updated", cancelled_job)
                                    return cancelled_job

                            if download_id in self._pause_flags:
                                fh.flush()
                                await self.db.execute(
                                    "UPDATE download_jobs SET status = 'PAUSED', bytes_received = ?, updated_at = ? WHERE download_id = ?",
                                    (bytes_received, utc_now_iso(), download_id),
                                )
                                paused_job = await self.get_job(download_id)
                                if paused_job:
                                    await self.emit_event("download_updated", paused_job)
                                    return paused_job

                            fh.write(chunk)
                            bytes_received += len(chunk)
                            if bytes_received - last_emit >= 262144:
                                last_emit = bytes_received
                                await self.db.execute(
                                    "UPDATE download_jobs SET bytes_received = ?, updated_at = ? WHERE download_id = ?",
                                    (bytes_received, utc_now_iso(), download_id),
                                )
                                updated = await self.get_job(download_id)
                                if updated:
                                    await self.emit_event("download_updated", updated)

            # Finalize into workspace
            dest_path = self.local_files._unique_path(self.settings.workspace_dir, job["filename"])
            shutil.move(str(temp_path), str(dest_path))
            await self.local_files.sync_workspace_cache()
            local_record = await self._finalize_local_record(dest_path)
            final_size = dest_path.stat().st_size
            await self.db.execute(
                """
                UPDATE download_jobs
                SET status = 'COMPLETED', output_path = ?, bytes_received = ?, total_bytes = CASE WHEN total_bytes = 0 THEN ? ELSE total_bytes END, updated_at = ?
                WHERE download_id = ?
                """,
                (str(dest_path.resolve()), final_size, final_size, utc_now_iso(), download_id),
            )
            done_job = await self.get_job(download_id)
            if done_job:
                if local_record:
                    done_job["local_file"] = local_record
                    done_job["file_id"] = local_record["file_id"]
                await self.emit_event("download_updated", done_job)
                return done_job
            return {
                "download_id": download_id,
                "status": "COMPLETED",
                "local_file": local_record,
                "file_id": local_record["file_id"] if local_record else None,
            }

        except Exception as exc:
            await self.db.execute(
                "UPDATE download_jobs SET status = 'FAILED', error_message = ?, updated_at = ? WHERE download_id = ?",
                (str(exc)[:500], utc_now_iso(), download_id),
            )
            failed = await self.get_job(download_id)
            if failed:
                await self.emit_event("download_updated", failed)
            return failed or {"download_id": download_id, "status": "FAILED", "error_message": str(exc)}

    async def _fresh_download(
        self,
        download_id: str,
        client: httpx.AsyncClient,
        job: dict[str, Any],
        temp_path: Path,
        *,
        allow_localhost: bool = False,
    ) -> dict[str, Any]:
        async with self._validated_stream(client, job["source_url"], allow_localhost=allow_localhost) as resp:
            resp.raise_for_status()
            total_bytes = int(resp.headers.get("content-length") or 0)
            bytes_received = 0
            with temp_path.open("wb") as fh:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    fh.write(chunk)
                    bytes_received += len(chunk)
        dest_path = self.local_files._unique_path(self.settings.workspace_dir, job["filename"])
        shutil.move(str(temp_path), str(dest_path))
        await self.local_files.sync_workspace_cache()
        local_record = await self._finalize_local_record(dest_path)
        await self.db.execute(
            "UPDATE download_jobs SET status = 'COMPLETED', output_path = ?, bytes_received = ?, total_bytes = ?, updated_at = ? WHERE download_id = ?",
            (str(dest_path.resolve()), bytes_received, total_bytes or bytes_received, utc_now_iso(), download_id),
        )
        done_job = (await self.get_job(download_id)) or {"download_id": download_id, "status": "COMPLETED"}
        if local_record:
            done_job["local_file"] = local_record
            done_job["file_id"] = local_record["file_id"]
        return done_job

    async def pause_job(self, download_id: str) -> dict[str, Any]:
        self._pause_flags.add(download_id)
        job = await self.get_job(download_id)
        if not job:
            raise FileNotFoundError(f"Download job {download_id} not found.")
        if job["status"] == "PENDING":
            await self.db.execute(
                "UPDATE download_jobs SET status = 'PAUSED', updated_at = ? WHERE download_id = ?",
                (utc_now_iso(), download_id),
            )
        return (await self.get_job(download_id)) or job

    async def resume_job(self, download_id: str) -> dict[str, Any]:
        job = await self.get_job(download_id)
        if not job:
            raise FileNotFoundError(f"Download job {download_id} not found.")
        if not job.get("source_url"):
            raise ValueError("Google Drive 전송 작업은 HTTP URL 이어받기 재개를 지원하지 않습니다.")
        self.start_download_background(download_id)
        return (await self.get_job(download_id)) or job

    async def cancel_job(self, download_id: str) -> dict[str, Any]:
        self._cancel_flags.add(download_id)
        job = await self.get_job(download_id)
        if not job:
            raise FileNotFoundError(f"Download job {download_id} not found.")
        if job["status"] in {"PENDING", "PAUSED", "FAILED"}:
            if job.get("local_temp_path"):
                Path(job["local_temp_path"]).unlink(missing_ok=True)
            await self.db.execute(
                "UPDATE download_jobs SET status = 'CANCELLED', updated_at = ? WHERE download_id = ?",
                (utc_now_iso(), download_id),
            )
        return (await self.get_job(download_id)) or job

    async def register_drive_transfer(
        self,
        *,
        direction: str,
        filename: str,
        source_file_id: str,
        total_bytes: int,
        task_id: str | None = None,
    ) -> str:
        download_id = f"tr_{uuid.uuid4().hex[:12]}"
        now = utc_now_iso()
        await self.db.execute(
            """
            INSERT INTO download_jobs
            (download_id, task_id, source_file_id, source_url, filename, direction,
             local_temp_path, output_path, bytes_received, total_bytes, supports_range, status, error_message, created_at, updated_at)
            VALUES (?, ?, ?, NULL, ?, ?, NULL, NULL, 0, ?, 1, 'RUNNING', NULL, ?, ?)
            """,
            (download_id, task_id, source_file_id, filename, direction, total_bytes, now, now),
        )
        return download_id

    async def update_transfer_progress(
        self,
        download_id: str,
        *,
        bytes_received: int,
        status: str = "RUNNING",
        output_path: str | None = None,
        error_message: str | None = None,
    ) -> None:
        await self.db.execute(
            """
            UPDATE download_jobs
            SET bytes_received = ?, status = ?, output_path = COALESCE(?, output_path), error_message = ?, updated_at = ?
            WHERE download_id = ?
            """,
            (bytes_received, status, output_path, error_message, utc_now_iso(), download_id),
        )
        job = await self.get_job(download_id)
        if job:
            await self.emit_event("download_updated", job)
