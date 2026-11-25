from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path


def _find_binary(names: list[str]) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _default_static_dir() -> Path:
    env_val = os.getenv("STATIC_DIR")
    if env_val:
        return Path(env_val).resolve()
    static_candidate = Path("./static").resolve()
    if static_candidate.exists():
        return static_candidate
    return Path("./frontend/dist").resolve()


def _default_base_url() -> str:
    val = os.getenv("APP_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "http://localhost:8000"
    return val.rstrip("/")


@dataclass
class Settings:
    app_env: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    port: int = field(default_factory=lambda: int(os.getenv("PORT", "8000")))
    app_base_url: str = field(default_factory=_default_base_url)
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", "./data")).resolve())
    static_dir: Path = field(default_factory=_default_static_dir)
    max_concurrent_jobs: int = field(default_factory=lambda: max(1, int(os.getenv("MAX_CONCURRENT_JOBS", "1"))))
    max_upload_mb: int = field(default_factory=lambda: int(os.getenv("MAX_UPLOAD_MB", "500")))
    encryption_secret: str = field(default_factory=lambda: os.getenv("ENCRYPTION_SECRET", ""))

    # Optional env-injected Google OAuth credentials
    google_client_id: str = field(default_factory=lambda: os.getenv("GOOGLE_CLIENT_ID", ""))
    google_client_secret: str = field(default_factory=lambda: os.getenv("GOOGLE_CLIENT_SECRET", ""))
    google_redirect_uri: str = field(default_factory=lambda: os.getenv("GOOGLE_REDIRECT_URI", ""))

    # Optional env-injected AI keys (never logged or returned in plaintext)
    openrouter_api_key: str = field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY", ""))
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def workspace_dir(self) -> Path:
        return self.data_dir / "workspace"

    @property
    def outputs_dir(self) -> Path:
        return self.data_dir / "outputs"

    @property
    def temp_dir(self) -> Path:
        return self.data_dir / "temp"

    @property
    def effective_google_redirect_uri(self) -> str:
        if self.google_redirect_uri:
            return self.google_redirect_uri
        return f"{self.app_base_url}/api/drive/oauth/callback"

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def detect_binaries(self) -> dict[str, str | None]:
        return {
            "ffmpeg": _find_binary(["ffmpeg"]),
            "ffprobe": _find_binary(["ffprobe"]),
            "ghostscript": _find_binary(["gs", "gswin64c", "gswin32c"]),
        }


settings = Settings()
