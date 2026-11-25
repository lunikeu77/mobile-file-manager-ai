from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import pytest_asyncio

from backend.config import Settings
from backend.container import AppContainer
from backend.main import create_app


@pytest.fixture
def temp_settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    static_dir = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    cfg = Settings(
        app_env="development",
        port=8000,
        app_base_url="http://localhost:8000",
        data_dir=data_dir,
        static_dir=static_dir,
        max_concurrent_jobs=1,
        encryption_secret="test-secret-for-unit-integration-tests-32b",
        google_client_id="",
        google_client_secret="",
        openrouter_api_key="",
        gemini_api_key="",
        openai_api_key="",
    )
    cfg.ensure_directories()
    return cfg


@pytest_asyncio.fixture
async def container(temp_settings: Settings):
    c = AppContainer.build(temp_settings)
    await c.db.connect()
    await c.local_files.sync_workspace_cache()
    yield c
    await c.task_engine.stop()
    await c.db.close()


@pytest.fixture
def fastapi_app(temp_settings: Settings):
    return create_app(temp_settings)


def has_ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
