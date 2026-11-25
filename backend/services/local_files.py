from __future__ import annotations

import json
import mimetypes
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO

from fastapi import UploadFile

from backend.config import Settings
from backend.db import Database, utc_now_iso
from backend.security.validator import (
    SpecValidationError,
    ensure_within_directory,
    validate_safe_extension,
    validate_safe_filename,
)


class LocalFileService:
    """Manages local workspace and output files with strict path safety and original-file preservation."""

    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    @staticmethod
    def _guess_mime(path: Path) -> str:
        mime, _ = mimetypes.guess_type(path.name)
        return mime or "application/octet-stream"

    async def sync_workspace_cache(self) -> list[dict[str, Any]]:
        """Scans workspace and outputs directories and synchronizes SQLite file_cache for source='local'."""
        self.settings.ensure_directories()
        found_ids: set[str] = set()
        now = utc_now_iso()

        existing_rows = await self.db.fetch_all(
            "SELECT file_id, local_path, metadata_json FROM file_cache WHERE source = 'local'"
        )
        path_to_id = {row["local_path"]: row["file_id"] for row in existing_rows if row.get("local_path")}

        entries: list[dict[str, Any]] = []
        for folder_label, base_dir in (
            ("workspace", self.settings.workspace_dir),
            ("outputs", self.settings.outputs_dir),
        ):
            for p in sorted(base_dir.iterdir()):
                if not p.is_file() or p.name.startswith("."):
                    continue
                resolved = str(p.resolve())
                file_id = path_to_id.get(resolved) or f"local_{uuid.uuid5(uuid.NAMESPACE_URL, resolved).hex[:16]}"
                found_ids.add(file_id)
                stat = p.stat()
                modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
                mime = self._guess_mime(p)
                meta = {
                    "folder": folder_label,
                    "extension": p.suffix.lstrip(".").lower(),
                    "is_output": folder_label == "outputs",
                }
                await self.db.execute(
                    """
                    INSERT INTO file_cache
                    (file_id, source, name, mime_type, size, modified_time, cached_at, parent_id, local_path, metadata_json)
                    VALUES (?, 'local', ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(file_id) DO UPDATE SET
                        name = excluded.name,
                        mime_type = excluded.mime_type,
                        size = excluded.size,
                        modified_time = excluded.modified_time,
                        cached_at = excluded.cached_at,
                        parent_id = excluded.parent_id,
                        local_path = excluded.local_path,
                        metadata_json = excluded.metadata_json
                    """,
                    (
                        file_id,
                        p.name,
                        mime,
                        stat.st_size,
                        modified,
                        now,
                        folder_label,
                        resolved,
                        json.dumps(meta),
                    ),
                )
                entries.append(
                    {
                        "file_id": file_id,
                        "source": "local",
                        "name": p.name,
                        "mime_type": mime,
                        "size": stat.st_size,
                        "modified_time": modified,
                        "cached_at": now,
                        "parent_id": folder_label,
                        "local_path": resolved,
                        "metadata": meta,
                    }
                )

        # Remove stale local entries no longer on disk
        for row in existing_rows:
            if row["file_id"] not in found_ids:
                await self.db.execute("DELETE FROM file_cache WHERE file_id = ?", (row["file_id"],))

        return entries

    async def list_files(
        self,
        *,
        source: str = "local",
        query: str | None = None,
        mime_type: str | None = None,
        extension: str | None = None,
        min_size_bytes: int | None = None,
        max_size_bytes: int | None = None,
        folder: str | None = None,
    ) -> list[dict[str, Any]]:
        if source == "local":
            await self.sync_workspace_cache()

        sql = "SELECT * FROM file_cache WHERE 1=1"
        params: list[Any] = []
        if source != "all":
            sql += " AND source = ?"
            params.append(source)
        if query:
            sql += " AND LOWER(name) LIKE ?"
            params.append(f"%{query.lower()}%")
        if mime_type:
            sql += " AND LOWER(mime_type) LIKE ?"
            params.append(f"%{mime_type.lower()}%")
        if extension:
            ext_clean = extension.lower().lstrip(".")
            sql += " AND LOWER(name) LIKE ?"
            params.append(f"%.{ext_clean}")
        if min_size_bytes is not None:
            sql += " AND size >= ?"
            params.append(min_size_bytes)
        if max_size_bytes is not None:
            sql += " AND size <= ?"
            params.append(max_size_bytes)
        if folder:
            sql += " AND parent_id = ?"
            params.append(folder)
        sql += " ORDER BY modified_time DESC"

        rows = await self.db.fetch_all(sql, tuple(params))
        for row in rows:
            row["metadata"] = json.loads(row.get("metadata_json") or "{}")
        return rows

    async def get_file(self, file_id: str) -> dict[str, Any] | None:
        row = await self.db.fetch_one("SELECT * FROM file_cache WHERE file_id = ?", (file_id,))
        if not row:
            await self.sync_workspace_cache()
            row = await self.db.fetch_one("SELECT * FROM file_cache WHERE file_id = ?", (file_id,))
        if not row:
            return None
        row["metadata"] = json.loads(row.get("metadata_json") or "{}")
        return row

    async def get_all_known_file_ids(self) -> set[str]:
        await self.sync_workspace_cache()
        rows = await self.db.fetch_all("SELECT file_id FROM file_cache")
        return {r["file_id"] for r in rows}

    def _unique_path(self, directory: Path, filename: str) -> Path:
        candidate = ensure_within_directory(directory, directory / filename)
        if not candidate.exists():
            return candidate
        stem = candidate.stem
        suffix = candidate.suffix
        counter = 1
        while True:
            next_candidate = ensure_within_directory(directory, directory / f"{stem}_{counter}{suffix}")
            if not next_candidate.exists():
                return next_candidate
            counter += 1

    async def save_upload_stream(self, upload: UploadFile) -> dict[str, Any]:
        """Streams uploaded file to disk in 1MB chunks to prevent OOM on 512MB Render free instances."""
        self.settings.ensure_directories()
        safe_name = validate_safe_filename(upload.filename or f"upload_{uuid.uuid4().hex[:8]}.bin")
        dest_path = self._unique_path(self.settings.workspace_dir, safe_name)

        max_bytes = self.settings.max_upload_mb * 1024 * 1024
        written = 0
        try:
            with dest_path.open("wb") as out_f:
                while True:
                    chunk = await upload.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        raise SpecValidationError(f"Upload exceeds limit of {self.settings.max_upload_mb} MB.")
                    out_f.write(chunk)
        except Exception:
            if dest_path.exists():
                dest_path.unlink(missing_ok=True)
            raise
        finally:
            await upload.close()

        await self.sync_workspace_cache()
        files = await self.list_files(source="local")
        for f in files:
            if f.get("local_path") == str(dest_path.resolve()):
                return f
        raise RuntimeError("Uploaded file failed to index.")

    async def save_bytes_stream(self, filename: str, stream_reader: BinaryIO, folder: str = "workspace") -> dict[str, Any]:
        self.settings.ensure_directories()
        safe_name = validate_safe_filename(filename)
        base = self.settings.outputs_dir if folder == "outputs" else self.settings.workspace_dir
        dest_path = self._unique_path(base, safe_name)
        with dest_path.open("wb") as out_f:
            shutil.copyfileobj(stream_reader, out_f, length=1024 * 1024)
        await self.sync_workspace_cache()
        files = await self.list_files(source="local")
        for f in files:
            if f.get("local_path") == str(dest_path.resolve()):
                return f
        raise RuntimeError("Saved file failed to index.")

    async def rename_local_file(self, file_id: str, new_name: str) -> dict[str, Any]:
        record = await self.get_file(file_id)
        if not record or record["source"] != "local":
            raise FileNotFoundError(f"Local file '{file_id}' not found.")
        safe_new = validate_safe_filename(new_name)
        old_path = Path(record["local_path"])
        if not old_path.exists():
            raise FileNotFoundError("Source file missing on disk.")
        base_dir = old_path.parent
        target_path = ensure_within_directory(base_dir, base_dir / safe_new)
        if target_path.exists() and target_path != old_path:
            raise FileExistsError(f"Target filename '{safe_new}' already exists. Overwrite requires explicit confirmation.")
        old_path.rename(target_path)
        new_resolved = str(target_path.resolve())
        await self.db.execute(
            "UPDATE file_cache SET name = ?, local_path = ?, mime_type = ?, cached_at = ? WHERE file_id = ?",
            (safe_new, new_resolved, self._guess_mime(target_path), utc_now_iso(), file_id),
        )
        await self.sync_workspace_cache()
        updated = await self.get_file(file_id)
        if updated:
            return updated
        raise RuntimeError("Renamed file could not be re-indexed.")

    async def change_extension_local(self, file_id: str, new_extension: str) -> dict[str, Any]:
        """Changes only the filename extension string; file bytes are NOT re-encoded."""
        record = await self.get_file(file_id)
        if not record or record["source"] != "local":
            raise FileNotFoundError(f"Local file '{file_id}' not found.")
        clean_ext = validate_safe_extension(new_extension)
        old_path = Path(record["local_path"])
        new_name = f"{old_path.stem}.{clean_ext}"
        renamed = await self.rename_local_file(file_id, new_name)
        renamed["warning"] = (
            "확장자 문자열만 변경되었습니다. 파일 내용(바이트)은 재인코딩/변환되지 않았으므로 일부 앱에서 형식이 오인될 수 있습니다."
        )
        renamed["is_format_conversion"] = False
        return renamed

    async def delete_local_file(self, file_id: str) -> dict[str, Any]:
        record = await self.get_file(file_id)
        if not record or record["source"] != "local":
            raise FileNotFoundError(f"Local file '{file_id}' not found.")
        path = Path(record["local_path"])
        if path.exists():
            path.unlink()
        await self.db.execute("DELETE FROM file_cache WHERE file_id = ?", (file_id,))
        return {"deleted_file_id": file_id, "name": record["name"]}

    async def overwrite_local_file(self, source_file_id: str, target_file_id: str) -> dict[str, Any]:
        src = await self.get_file(source_file_id)
        dst = await self.get_file(target_file_id)
        if not src or src["source"] != "local" or not dst or dst["source"] != "local":
            raise FileNotFoundError("Both source and target local files must exist.")
        src_path = Path(src["local_path"])
        dst_path = Path(dst["local_path"])
        shutil.copy2(src_path, dst_path)
        await self.sync_workspace_cache()
        updated = await self.get_file(target_file_id)
        return updated or {"file_id": target_file_id, "overwritten_by": source_file_id}

    async def cleanup_temp_files(self, max_age_hours: int = 24) -> dict[str, Any]:
        self.settings.ensure_directories()
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        removed: list[str] = []
        freed_bytes = 0
        for p in self.settings.temp_dir.iterdir():
            if not p.is_file():
                continue
            mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                size = p.stat().st_size
                p.unlink(missing_ok=True)
                removed.append(p.name)
                freed_bytes += size
        await self.db.set_preference("last_cleanup_at", utc_now_iso())
        return {"removed_count": len(removed), "freed_bytes": freed_bytes, "removed_files": removed}
