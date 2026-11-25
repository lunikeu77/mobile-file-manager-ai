from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    task_type TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    idempotency_key TEXT UNIQUE,
    correlation_id TEXT,
    payload_json TEXT NOT NULL,
    result_json TEXT,
    progress INTEGER NOT NULL DEFAULT 0,
    requires_confirmation INTEGER NOT NULL DEFAULT 0,
    confirmed_at TEXT,
    created_at TEXT NOT NULL,
    scheduled_at TEXT,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL,
    error_code TEXT,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_scheduled_at ON tasks(scheduled_at);

CREATE TABLE IF NOT EXISTS task_steps (
    step_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    step_order INTEGER NOT NULL,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    input_ref TEXT,
    result_ref TEXT,
    options_json TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    started_at TEXT,
    completed_at TEXT,
    FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_task_steps_task_id ON task_steps(task_id, step_order);

CREATE TABLE IF NOT EXISTS file_cache (
    file_id TEXT PRIMARY KEY,
    source TEXT NOT NULL DEFAULT 'local',
    name TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    modified_time TEXT NOT NULL,
    cached_at TEXT NOT NULL,
    parent_id TEXT,
    local_path TEXT,
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_file_cache_source ON file_cache(source);
CREATE INDEX IF NOT EXISTS idx_file_cache_name ON file_cache(name);

CREATE TABLE IF NOT EXISTS ai_provider_settings (
    provider_id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    priority INTEGER NOT NULL,
    model_id TEXT NOT NULL,
    key_reference TEXT,
    quota_state TEXT NOT NULL DEFAULT 'unconfigured',
    last_error_at TEXT,
    last_error_code TEXT,
    last_error_message TEXT,
    circuit_breaker_until TEXT,
    free_only INTEGER NOT NULL DEFAULT 1,
    monthly_budget_usd REAL NOT NULL DEFAULT 0.0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_usage_events (
    event_id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    request_type TEXT NOT NULL,
    token_usage_if_available TEXT,
    estimated_cost_usd REAL,
    status_code INTEGER,
    latency_ms INTEGER,
    correlation_id TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_usage_created ON ai_usage_events(created_at);

CREATE TABLE IF NOT EXISTS download_jobs (
    download_id TEXT PRIMARY KEY,
    task_id TEXT,
    source_file_id TEXT,
    source_url TEXT,
    filename TEXT NOT NULL,
    direction TEXT NOT NULL DEFAULT 'download',
    local_temp_path TEXT,
    output_path TEXT,
    bytes_received INTEGER NOT NULL DEFAULT 0,
    total_bytes INTEGER NOT NULL DEFAULT 0,
    supports_range INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_download_jobs_status ON download_jobs(status);

CREATE TABLE IF NOT EXISTS encrypted_secrets (
    key_reference TEXT PRIMARY KEY,
    ciphertext TEXT NOT NULL,
    masked_preview TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_preferences (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

DEFAULT_PROVIDERS = [
    ("openrouter", 1, 1, "meta-llama/llama-3.3-70b-instruct:free", "unconfigured", 1, 0.0),
    ("gemini", 1, 2, "gemini-2.5-flash", "unconfigured", 1, 0.0),
    ("kimi", 1, 3, "moonshot-v1-8k", "unconfigured", 0, 5.0),
    ("openai", 0, 4, "gpt-4o-mini", "unconfigured", 0, 5.0),
]

DEFAULT_PREFERENCES = {
    "ai_send_filenames": "false",
    "ai_send_metadata": "true",
    "ai_free_only_mode": "false",
    "ai_auto_failover": "true",
    "ai_monthly_limit_usd": "0.0",
    "temp_cleanup_hours": "24",
    "google_scope_mode": "drive.file",
    "drive_workspace_folder_name": "MobileFileManager_Workspace",
}


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> aiosqlite.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = await aiosqlite.connect(self.db_path)
            self._conn.row_factory = aiosqlite.Row
            await self._conn.executescript(SCHEMA_SQL)
            await self._seed_defaults()
            await self._conn.commit()
        return self._conn

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _seed_defaults(self) -> None:
        assert self._conn is not None
        now = utc_now_iso()
        for pid, enabled, priority, model_id, quota_state, free_only, budget in DEFAULT_PROVIDERS:
            await self._conn.execute(
                """
                INSERT OR IGNORE INTO ai_provider_settings
                (provider_id, enabled, priority, model_id, key_reference, quota_state, free_only, monthly_budget_usd, updated_at)
                VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?)
                """,
                (pid, enabled, priority, model_id, quota_state, free_only, budget, now),
            )
        for key, value in DEFAULT_PREFERENCES.items():
            await self._conn.execute(
                "INSERT OR IGNORE INTO app_preferences (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, now),
            )

    async def fetch_all(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        conn = await self.connect()
        async with conn.execute(query, params) as cur:
            rows = await cur.fetchall()
            return [dict(row) for row in rows]

    async def fetch_one(self, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        conn = await self.connect()
        async with conn.execute(query, params) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        conn = await self.connect()
        await conn.execute(query, params)
        await conn.commit()

    async def get_preferences(self) -> dict[str, str]:
        rows = await self.fetch_all("SELECT key, value FROM app_preferences")
        return {row["key"]: row["value"] for row in rows}

    async def set_preference(self, key: str, value: str) -> None:
        await self.execute(
            """
            INSERT INTO app_preferences (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, utc_now_iso()),
        )
