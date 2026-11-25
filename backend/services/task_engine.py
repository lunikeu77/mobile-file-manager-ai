from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.config import Settings
from backend.db import Database, utc_now_iso
from backend.models import AITaskProposalSpec, AllowedAction, PdfPreset, TaskStatus, VideoPreset
from backend.security.validator import validate_ai_task_spec
from backend.services.compression import CompressionService
from backend.services.downloads import DownloadQueueService
from backend.services.google_drive import GoogleDriveService
from backend.services.local_files import LocalFileService


class TaskExecutionEngine:
    """
    SQLite-persisted background task queue & scheduler engine.
    - Ensures idempotency via `idempotency_key` and per-step checkpoints in `task_steps`.
    - Survives browser tab closure; runs inside server background worker loop.
    - On startup, safely recovers interrupted `RUNNING` tasks (never auto-re-runs destructive steps).
    - Periodically checks `SCHEDULED` tasks (best-effort; Render free 15-min idle spin-down caveat documented).
    """

    def __init__(
        self,
        db: Database,
        settings: Settings,
        local_files: LocalFileService,
        compression: CompressionService,
        downloads: DownloadQueueService,
        drive: GoogleDriveService,
    ) -> None:
        self.db = db
        self.settings = settings
        self.local_files = local_files
        self.compression = compression
        self.downloads = downloads
        self.drive = drive
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker_task: asyncio.Task[Any] | None = None
        self._scheduler_task: asyncio.Task[Any] | None = None
        self._running = False

    async def start(self) -> dict[str, Any]:
        recovery = await self.recover_interrupted_tasks()
        self._running = True
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop())
        if self._scheduler_task is None or self._scheduler_task.done():
            self._scheduler_task = asyncio.create_task(self._scheduler_loop())

        # Enqueue any PENDING tasks already in DB
        pending = await self.db.fetch_all(
            "SELECT task_id FROM tasks WHERE status = ? ORDER BY created_at ASC",
            (TaskStatus.PENDING.value,),
        )
        for row in pending:
            await self._queue.put(row["task_id"])
        return recovery

    async def stop(self) -> None:
        self._running = False
        for t in (self._worker_task, self._scheduler_task):
            if t and not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

    async def recover_interrupted_tasks(self) -> dict[str, Any]:
        """
        Detects tasks left in RUNNING when the server restarted or crashed.
        Transitions them safely to NEEDS_USER_ACTION (avoiding blind duplicate execution)
        and marks any incomplete RUNNING step as FAILED with recovery note.
        """
        now = utc_now_iso()
        interrupted = await self.db.fetch_all(
            "SELECT task_id, title FROM tasks WHERE status = ?",
            (TaskStatus.RUNNING.value,),
        )
        recovered_ids: list[str] = []
        for t in interrupted:
            tid = t["task_id"]
            await self.db.execute(
                """
                UPDATE task_steps
                SET status = ?, error_message = '서버 재시작으로 인해 실행이 중단되었습니다. 중복 실행 방지를 위해 상태를 점검하세요.'
                WHERE task_id = ? AND status = ?
                """,
                (TaskStatus.FAILED.value, tid, TaskStatus.RUNNING.value),
            )
            await self.db.execute(
                """
                UPDATE tasks
                SET status = ?, error_code = 'SERVER_RESTART_INTERRUPTED',
                    error_message = '서버 재시작으로 작업이 중단되어 안전하게 복구 대기(NEEDS_USER_ACTION) 상태로 전환되었습니다.',
                    updated_at = ?
                WHERE task_id = ?
                """,
                (TaskStatus.NEEDS_USER_ACTION.value, now, tid),
            )
            recovered_ids.append(tid)
        return {"recovered_count": len(recovered_ids), "recovered_task_ids": recovered_ids}

    async def _scheduler_loop(self) -> None:
        while self._running:
            try:
                now = datetime.now(timezone.utc)
                scheduled = await self.db.fetch_all(
                    "SELECT task_id, scheduled_at, requires_confirmation, confirmed_at FROM tasks WHERE status = ?",
                    (TaskStatus.SCHEDULED.value,),
                )
                for row in scheduled:
                    if row["requires_confirmation"] and not row["confirmed_at"]:
                        continue
                    sched_str = row.get("scheduled_at")
                    if not sched_str:
                        continue
                    sched_dt = datetime.fromisoformat(sched_str.replace("Z", "+00:00"))
                    if sched_dt.tzinfo is None:
                        sched_dt = sched_dt.replace(tzinfo=timezone.utc)
                    if sched_dt <= now:
                        await self.db.execute(
                            "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ? AND status = ?",
                            (TaskStatus.PENDING.value, utc_now_iso(), row["task_id"], TaskStatus.SCHEDULED.value),
                        )
                        await self._queue.put(row["task_id"])
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await asyncio.sleep(2.0)

    async def _worker_loop(self) -> None:
        while self._running:
            try:
                task_id = await self._queue.get()
                try:
                    await self.execute_task(task_id)
                finally:
                    self._queue.task_done()
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(0.5)

    async def create_task(
        self,
        *,
        title: str,
        task_type: str,
        spec: AITaskProposalSpec | dict[str, Any],
        scheduled_at: str | None = None,
        idempotency_key: str | None = None,
        confirmed: bool = False,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        # Check idempotency key first to guarantee zero duplicate task creation
        if idempotency_key:
            existing = await self.db.fetch_one(
                "SELECT * FROM tasks WHERE idempotency_key = ?",
                (idempotency_key,),
            )
            if existing:
                return await self.get_task_detail(existing["task_id"])

        known_ids = await self.local_files.get_all_known_file_ids()
        raw_spec = spec.model_dump(mode="json") if isinstance(spec, AITaskProposalSpec) else spec
        validated = validate_ai_task_spec(
            raw_spec,
            known_ids,
            allow_localhost_urls=self.settings.app_env != "production",
        )

        needs_confirm = validated.requires_confirmation
        if needs_confirm and not confirmed:
            initial_status = TaskStatus.NEEDS_USER_ACTION.value
        elif scheduled_at:
            initial_status = TaskStatus.SCHEDULED.value
        else:
            initial_status = TaskStatus.PENDING.value

        task_id = f"task_{uuid.uuid4().hex[:12]}"
        now = utc_now_iso()
        corr = correlation_id or f"corr_{uuid.uuid4().hex[:12]}"

        await self.db.execute(
            """
            INSERT INTO tasks
            (task_id, task_type, title, status, idempotency_key, correlation_id, payload_json,
             result_json, progress, requires_confirmation, confirmed_at, created_at, scheduled_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 0, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                task_type,
                title,
                initial_status,
                idempotency_key,
                corr,
                json.dumps(validated.model_dump(mode="json"), ensure_ascii=False),
                1 if needs_confirm else 0,
                now if (needs_confirm and confirmed) else None,
                now,
                scheduled_at,
                now,
            ),
        )

        for idx, step in enumerate(validated.steps, start=1):
            step_id = f"step_{uuid.uuid4().hex[:12]}"
            in_ref = step.input_ref or step.file_id or (step.options.url if step.options else None)
            await self.db.execute(
                """
                INSERT INTO task_steps
                (step_id, task_id, step_order, action, status, input_ref, result_ref, options_json, retry_count)
                VALUES (?, ?, ?, ?, ?, ?, NULL, ?, 0)
                """,
                (
                    step_id,
                    task_id,
                    idx,
                    step.action.value,
                    TaskStatus.PENDING.value,
                    in_ref,
                    json.dumps(step.model_dump(mode="json"), ensure_ascii=False),
                ),
            )

        if initial_status == TaskStatus.PENDING.value:
            await self._queue.put(task_id)

        task_detail = await self.get_task_detail(task_id)
        await self.downloads.emit_event("task_updated", task_detail)
        return task_detail

    async def confirm_task(self, task_id: str) -> dict[str, Any]:
        task = await self.db.fetch_one("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
        if not task:
            raise FileNotFoundError(f"Task {task_id} not found.")
        now = utc_now_iso()
        next_status = TaskStatus.SCHEDULED.value if task.get("scheduled_at") else TaskStatus.PENDING.value
        await self.db.execute(
            "UPDATE tasks SET confirmed_at = ?, status = ?, error_code = NULL, error_message = NULL, updated_at = ? WHERE task_id = ?",
            (now, next_status, now, task_id),
        )
        if next_status == TaskStatus.PENDING.value:
            await self._queue.put(task_id)
        return await self.get_task_detail(task_id)

    async def retry_task(self, task_id: str) -> dict[str, Any]:
        task = await self.db.fetch_one("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
        if not task:
            raise FileNotFoundError(f"Task {task_id} not found.")
        if task["requires_confirmation"] and not task["confirmed_at"]:
            raise PermissionError("Task requires explicit user confirmation before retry.")
        now = utc_now_iso()
        await self.db.execute(
            "UPDATE task_steps SET status = ?, error_message = NULL, retry_count = retry_count + 1 WHERE task_id = ? AND status != ?",
            (TaskStatus.PENDING.value, task_id, TaskStatus.COMPLETED.value),
        )
        await self.db.execute(
            "UPDATE tasks SET status = ?, error_code = NULL, error_message = NULL, updated_at = ? WHERE task_id = ?",
            (TaskStatus.PENDING.value, now, task_id),
        )
        await self._queue.put(task_id)
        return await self.get_task_detail(task_id)

    async def cancel_task(self, task_id: str) -> dict[str, Any]:
        await self.db.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ? AND status IN (?, ?, ?)",
            (
                TaskStatus.CANCELLED.value,
                utc_now_iso(),
                task_id,
                TaskStatus.PENDING.value,
                TaskStatus.SCHEDULED.value,
                TaskStatus.NEEDS_USER_ACTION.value,
            ),
        )
        return await self.get_task_detail(task_id)

    async def list_tasks(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = await self.db.fetch_all(
                "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC",
                (status,),
            )
        else:
            rows = await self.db.fetch_all("SELECT * FROM tasks ORDER BY created_at DESC")
        result = []
        for row in rows:
            steps = await self.db.fetch_all(
                "SELECT * FROM task_steps WHERE task_id = ? ORDER BY step_order ASC",
                (row["task_id"],),
            )
            row["steps"] = steps
            result.append(row)
        return result

    async def get_task_detail(self, task_id: str) -> dict[str, Any]:
        row = await self.db.fetch_one("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
        if not row:
            raise FileNotFoundError(f"Task {task_id} not found.")
        row["steps"] = await self.db.fetch_all(
            "SELECT * FROM task_steps WHERE task_id = ? ORDER BY step_order ASC",
            (task_id,),
        )
        return row

    async def execute_task(self, task_id: str) -> dict[str, Any]:
        task = await self.db.fetch_one("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
        if not task:
            return {"task_id": task_id, "status": "NOT_FOUND"}
        if task["status"] in {TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value}:
            return await self.get_task_detail(task_id)
        if task["requires_confirmation"] and not task["confirmed_at"]:
            await self.db.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                (TaskStatus.NEEDS_USER_ACTION.value, utc_now_iso(), task_id),
            )
            return await self.get_task_detail(task_id)

        now = utc_now_iso()
        await self.db.execute(
            "UPDATE tasks SET status = ?, started_at = COALESCE(started_at, ?), updated_at = ? WHERE task_id = ?",
            (TaskStatus.RUNNING.value, now, now, task_id),
        )
        await self.downloads.emit_event("task_updated", await self.get_task_detail(task_id))

        steps = await self.db.fetch_all(
            "SELECT * FROM task_steps WHERE task_id = ? ORDER BY step_order ASC",
            (task_id,),
        )
        step_outputs: dict[int, Any] = {}
        previous_output: Any = None
        total = max(len(steps), 1)

        try:
            for idx, step in enumerate(steps, start=1):
                if step["status"] == TaskStatus.COMPLETED.value and step.get("result_ref"):
                    # Checkpoint already completed: skip re-execution!
                    previous_output = json.loads(step["result_ref"])
                    step_outputs[idx] = previous_output
                    continue

                await self.db.execute(
                    "UPDATE task_steps SET status = ?, started_at = ? WHERE step_id = ?",
                    (TaskStatus.RUNNING.value, utc_now_iso(), step["step_id"]),
                )

                step_spec = json.loads(step["options_json"] or "{}")
                out = await self._execute_single_step(task_id, step_spec, previous_output, step_outputs)
                previous_output = out
                step_outputs[idx] = out

                await self.db.execute(
                    "UPDATE task_steps SET status = ?, result_ref = ?, completed_at = ? WHERE step_id = ?",
                    (TaskStatus.COMPLETED.value, json.dumps(out, ensure_ascii=False), utc_now_iso(), step["step_id"]),
                )
                progress = int((idx / total) * 100)
                await self.db.execute(
                    "UPDATE tasks SET progress = ?, updated_at = ? WHERE task_id = ?",
                    (progress, utc_now_iso(), task_id),
                )
                await self.downloads.emit_event("task_updated", await self.get_task_detail(task_id))

            await self.db.execute(
                "UPDATE tasks SET status = ?, progress = 100, result_json = ?, completed_at = ?, updated_at = ? WHERE task_id = ?",
                (
                    TaskStatus.COMPLETED.value,
                    json.dumps({"final_output": previous_output}, ensure_ascii=False),
                    utc_now_iso(),
                    utc_now_iso(),
                    task_id,
                ),
            )
        except Exception as exc:
            await self.db.execute(
                "UPDATE task_steps SET status = ?, error_message = ? WHERE task_id = ? AND status = ?",
                (TaskStatus.FAILED.value, str(exc)[:500], task_id, TaskStatus.RUNNING.value),
            )
            await self.db.execute(
                "UPDATE tasks SET status = ?, error_code = ?, error_message = ?, updated_at = ? WHERE task_id = ?",
                (TaskStatus.FAILED.value, type(exc).__name__, str(exc)[:500], utc_now_iso(), task_id),
            )

        final_detail = await self.get_task_detail(task_id)
        await self.downloads.emit_event("task_updated", final_detail)
        return final_detail

    def _resolve_file_id(self, step_spec: dict[str, Any], previous_output: Any, step_outputs: dict[int, Any]) -> str:
        if step_spec.get("file_id"):
            return str(step_spec["file_id"])
        ref = step_spec.get("input_ref") or ""
        target = previous_output
        if ref.startswith("$step_"):
            step_num = int(ref.split("_")[1].split(".")[0])
            target = step_outputs.get(step_num)
        if isinstance(target, dict):
            if target.get("file_id"):
                return str(target["file_id"])
            if isinstance(target.get("output_file"), dict) and target["output_file"].get("file_id"):
                return str(target["output_file"]["file_id"])
            if isinstance(target.get("local_file"), dict) and target["local_file"].get("file_id"):
                return str(target["local_file"]["file_id"])
            if isinstance(target.get("files"), list) and target["files"]:
                return str(target["files"][0]["file_id"])
        raise ValueError(f"Could not resolve input file_id from '{ref}'.")

    async def _execute_single_step(
        self,
        task_id: str,
        step_spec: dict[str, Any],
        previous_output: Any,
        step_outputs: dict[int, Any],
    ) -> Any:
        action = step_spec["action"]
        source = step_spec.get("source", "local")
        options = step_spec.get("options") or {}
        filters = step_spec.get("filters") or {}

        if action == AllowedAction.SEARCH_FILES.value:
            if source == "google_drive":
                res = await self.drive.list_files(
                    query=filters.get("query"),
                    mime_type=filters.get("mime_type"),
                    extension=filters.get("extension"),
                    min_size_bytes=filters.get("min_size_bytes"),
                    max_size_bytes=filters.get("max_size_bytes"),
                )
                return {"files": [{"file_id": f["id"], **f} for f in res["files"]]}
            files = await self.local_files.list_files(
                source="local",
                query=filters.get("query"),
                mime_type=filters.get("mime_type"),
                extension=filters.get("extension"),
                min_size_bytes=filters.get("min_size_bytes"),
                max_size_bytes=filters.get("max_size_bytes"),
            )
            return {"files": files}

        if action == AllowedAction.RENAME_FILE.value:
            fid = self._resolve_file_id(step_spec, previous_output, step_outputs)
            rec = await self.local_files.get_file(fid)
            if not rec:
                raise FileNotFoundError(f"File '{fid}' not found.")
            if options.get("new_name"):
                new_name = options["new_name"]
            else:
                p = Path(rec["name"])
                new_name = f"{options.get('prefix', '')}{p.stem}{options.get('suffix', '')}{p.suffix}"
            if rec["source"] == "google_drive":
                return await self.drive.rename_file(fid, new_name)
            return await self.local_files.rename_local_file(fid, new_name)

        if action == AllowedAction.CHANGE_EXTENSION.value:
            fid = self._resolve_file_id(step_spec, previous_output, step_outputs)
            new_ext = options.get("new_extension")
            if not new_ext:
                raise ValueError("CHANGE_EXTENSION requires options.new_extension.")
            rec = await self.local_files.get_file(fid)
            if rec and rec["source"] == "google_drive":
                return await self.drive.change_extension(fid, new_ext)
            return await self.local_files.change_extension_local(fid, new_ext)

        if action == AllowedAction.DOWNLOAD_FILE.value:
            if options.get("url"):
                job = await self.downloads.create_http_download(
                    options["url"],
                    filename=options.get("filename"),
                    task_id=task_id,
                    allow_localhost=self.settings.app_env != "production",
                    auto_start=False,
                )
                res = await self.downloads.run_http_download(job["download_id"])
                if res.get("status") != "COMPLETED":
                    raise RuntimeError(
                        res.get("error_message") or f"HTTP download failed with status={res.get('status')}"
                    )
                return res
            fid = self._resolve_file_id(step_spec, previous_output, step_outputs)
            return await self.drive.download_file_to_local(fid, task_id=task_id)

        if action == AllowedAction.COMPRESS_VIDEO.value:
            fid = self._resolve_file_id(step_spec, previous_output, step_outputs)
            preset_str = options.get("preset") or VideoPreset.BALANCED.value
            return await self.compression.compress_mp4(
                fid,
                preset=VideoPreset(preset_str),
                target_size_bytes=options.get("target_size_bytes"),
                video_codec=options.get("video_codec") or "h264",
            )

        if action == AllowedAction.COMPRESS_PDF.value:
            fid = self._resolve_file_id(step_spec, previous_output, step_outputs)
            preset_str = options.get("preset") or PdfPreset.EBOOK.value
            return await self.compression.compress_pdf(
                fid,
                preset=PdfPreset(preset_str),
                preserve_interactive=bool(options.get("preserve_interactive", False)),
            )

        if action == AllowedAction.UPLOAD_FILE.value:
            fid = self._resolve_file_id(step_spec, previous_output, step_outputs)
            return await self.drive.resumable_upload_local_file(
                fid,
                parent_folder_id=options.get("destination_folder_id"),
                task_id=task_id,
            )

        if action == AllowedAction.DELETE_FILE.value:
            fid = self._resolve_file_id(step_spec, previous_output, step_outputs)
            rec = await self.local_files.get_file(fid)
            if rec and rec["source"] == "google_drive":
                return await self.drive.delete_file(fid)
            return await self.local_files.delete_local_file(fid)

        if action == AllowedAction.OVERWRITE_FILE.value:
            if not isinstance(previous_output, dict):
                raise ValueError("OVERWRITE_FILE requires source file from previous step or file_id.")
            target_id = step_spec.get("file_id")
            if not target_id:
                raise ValueError("OVERWRITE_FILE requires target file_id.")
            src_id = self._resolve_file_id({}, previous_output, step_outputs)
            return await self.local_files.overwrite_local_file(src_id, target_id)

        raise ValueError(f"Unsupported action: {action}")
