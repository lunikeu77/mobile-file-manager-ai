from __future__ import annotations

from dataclasses import dataclass

from backend.config import Settings
from backend.db import Database
from backend.security.crypto import SecretManager
from backend.services.ai_providers import AIRouter
from backend.services.compression import CompressionService
from backend.services.downloads import DownloadQueueService
from backend.services.google_drive import GoogleDriveService
from backend.services.local_files import LocalFileService
from backend.services.secrets_store import EncryptedSecretStore
from backend.services.task_engine import TaskExecutionEngine


@dataclass
class AppContainer:
    settings: Settings
    db: Database
    crypto: SecretManager
    secrets: EncryptedSecretStore
    local_files: LocalFileService
    compression: CompressionService
    downloads: DownloadQueueService
    drive: GoogleDriveService
    ai_router: AIRouter
    task_engine: TaskExecutionEngine

    @classmethod
    def build(cls, settings: Settings) -> AppContainer:
        settings.ensure_directories()
        db = Database(settings.db_path)
        crypto = SecretManager(settings.encryption_secret, settings.data_dir)
        secrets = EncryptedSecretStore(db, crypto)
        local_files = LocalFileService(db, settings)
        compression = CompressionService(settings, local_files)
        downloads = DownloadQueueService(db, settings, local_files)
        drive = GoogleDriveService(db, settings, secrets, local_files, downloads)
        ai_router = AIRouter(db, settings, secrets, local_files)
        task_engine = TaskExecutionEngine(db, settings, local_files, compression, downloads, drive)
        return cls(
            settings=settings,
            db=db,
            crypto=crypto,
            secrets=secrets,
            local_files=local_files,
            compression=compression,
            downloads=downloads,
            drive=drive,
            ai_router=ai_router,
            task_engine=task_engine,
        )
