from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.config import Settings, settings as default_settings
from backend.container import AppContainer
from backend.routers import ai, compression, downloads, drive, files, settings as settings_router, system, tasks
from backend.security.crypto import SecretRedactionFilter


def configure_logging() -> None:
    root_logger = logging.getLogger()
    if not any(isinstance(f, SecretRedactionFilter) for f in root_logger.filters):
        root_logger.addFilter(SecretRedactionFilter())
    for handler in root_logger.handlers:
        if not any(isinstance(f, SecretRedactionFilter) for f in handler.filters):
            handler.addFilter(SecretRedactionFilter())


def create_app(custom_settings: Settings | None = None) -> FastAPI:
    configure_logging()
    cfg = custom_settings or default_settings

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        container = AppContainer.build(cfg)
        app.state.container = container
        await container.db.connect()
        await container.local_files.sync_workspace_cache()
        recovery = await container.task_engine.start()
        app.state.last_recovery = recovery
        try:
            yield
        finally:
            await container.task_engine.stop()
            await container.db.close()

    app = FastAPI(
        title="Mobile File Manager & Multi-AI Automation API",
        version="2.0.0",
        lifespan=lifespan,
    )

    app.include_router(system.router)
    app.include_router(files.router)
    app.include_router(compression.router)
    app.include_router(downloads.router)
    app.include_router(tasks.router)
    app.include_router(ai.router)
    app.include_router(drive.router)
    app.include_router(settings_router.router)

    static_dir = cfg.static_dir
    if static_dir.exists() and (static_dir / "index.html").exists():
        assets_dir = static_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(request: Request, full_path: str):
            if full_path.startswith("api/") or full_path == "health":
                raise HTTPException(status_code=404, detail="Not Found")
            candidate = (static_dir / full_path).resolve()
            if full_path and candidate.is_file() and candidate.is_relative_to(static_dir.resolve()):
                return FileResponse(candidate)
            return FileResponse(static_dir / "index.html")

    return app


app = create_app()
