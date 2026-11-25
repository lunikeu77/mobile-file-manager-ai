from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    SCHEDULED = "SCHEDULED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    NEEDS_USER_ACTION = "NEEDS_USER_ACTION"


class AllowedAction(str, Enum):
    SEARCH_FILES = "SEARCH_FILES"
    RENAME_FILE = "RENAME_FILE"
    CHANGE_EXTENSION = "CHANGE_EXTENSION"
    DOWNLOAD_FILE = "DOWNLOAD_FILE"
    COMPRESS_VIDEO = "COMPRESS_VIDEO"
    COMPRESS_PDF = "COMPRESS_PDF"
    UPLOAD_FILE = "UPLOAD_FILE"
    DELETE_FILE = "DELETE_FILE"
    OVERWRITE_FILE = "OVERWRITE_FILE"


HIGH_RISK_ACTIONS = {
    AllowedAction.DELETE_FILE,
    AllowedAction.OVERWRITE_FILE,
    AllowedAction.UPLOAD_FILE,
    AllowedAction.CHANGE_EXTENSION,
}

ALWAYS_CONFIRM_ACTIONS = {
    AllowedAction.DELETE_FILE,
    AllowedAction.OVERWRITE_FILE,
}


class VideoPreset(str, Enum):
    FAST_COMPAT = "fast_compat"
    BALANCED = "balanced"
    HIGH_COMPRESSION = "high_compression"


class PdfPreset(str, Enum):
    SCREEN = "screen"       # ~72 dpi, smallest size
    EBOOK = "ebook"         # ~150 dpi, balanced
    PRINTER = "printer"     # ~300 dpi, high quality
    STRUCTURE_ONLY = "structure_only"  # pikepdf lossless structure & stream optimization (preserves forms/links better)


class TaskFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str | None = Field(default=None, max_length=200)
    mime_type: str | None = Field(default=None, max_length=120)
    extension: str | None = Field(default=None, max_length=20)
    min_size_bytes: int | None = Field(default=None, ge=0)
    max_size_bytes: int | None = Field(default=None, ge=0)


class StepOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_name: str | None = Field(default=None, max_length=255)
    new_extension: str | None = Field(default=None, max_length=20)
    suffix: str | None = Field(default=None, max_length=60)
    prefix: str | None = Field(default=None, max_length=60)
    preset: str | None = Field(default=None, max_length=40)
    target_size_bytes: int | None = Field(default=None, ge=1024)
    video_codec: Literal["h264", "hevc"] | None = None
    preserve_interactive: bool | None = None
    destination_folder_id: str | None = Field(default=None, max_length=128)
    url: str | None = Field(default=None, max_length=2048)
    filename: str | None = Field(default=None, max_length=255)


class TaskStepSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: AllowedAction
    source: Literal["local", "google_drive", "external_url"] = "local"
    file_id: str | None = Field(default=None, max_length=256)
    input_ref: str | None = Field(default=None, max_length=128)
    filters: TaskFilters | None = None
    options: StepOptions | None = None


class AITaskProposalSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    intent: str = Field(min_length=1, max_length=100)
    summary: str | None = Field(default=None, max_length=500)
    steps: list[TaskStepSpec] = Field(min_length=1, max_length=10)
    requires_confirmation: bool = False
    warnings: list[str] = Field(default_factory=list, max_length=20)


class RenameFileRequest(BaseModel):
    new_name: str = Field(min_length=1, max_length=255)


class BatchRenameItem(BaseModel):
    file_id: str
    new_name: str = Field(min_length=1, max_length=255)


class BatchRenameRequest(BaseModel):
    items: list[BatchRenameItem] = Field(min_length=1, max_length=100)


class ChangeExtensionRequest(BaseModel):
    new_extension: str = Field(min_length=1, max_length=20)
    acknowledge_no_reencode: bool = False


class CompressVideoRequest(BaseModel):
    file_id: str
    preset: VideoPreset = VideoPreset.BALANCED
    target_size_bytes: int | None = Field(default=None, ge=10240)
    video_codec: Literal["h264", "hevc"] = "h264"
    scheduled_at: str | None = None


class CompressPdfRequest(BaseModel):
    file_id: str
    preset: PdfPreset = PdfPreset.EBOOK
    preserve_interactive: bool = False
    scheduled_at: str | None = None


class CreateDownloadRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    filename: str | None = Field(default=None, max_length=255)


class CreateDriveUploadRequest(BaseModel):
    file_id: str
    parent_folder_id: str | None = None
    confirmed: bool = False


class CreateTaskRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    task_type: str = Field(default="workflow", max_length=60)
    spec: AITaskProposalSpec
    scheduled_at: str | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)
    confirmed: bool = False


class AICommandRequest(BaseModel):
    command: str = Field(min_length=1, max_length=2000)
    selected_file_ids: list[str] = Field(default_factory=list, max_length=50)
    source: Literal["local", "google_drive", "all"] = "local"


class ProviderSettingUpdate(BaseModel):
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=1, le=10)
    model_id: str | None = Field(default=None, min_length=1, max_length=150)
    api_key: str | None = Field(default=None, max_length=512)
    free_only: bool | None = None
    monthly_budget_usd: float | None = Field(default=None, ge=0.0, le=10000.0)


class PrivacySettingsUpdate(BaseModel):
    ai_send_filenames: bool | None = None
    ai_send_metadata: bool | None = None
    ai_free_only_mode: bool | None = None
    ai_auto_failover: bool | None = None
    ai_monthly_limit_usd: float | None = Field(default=None, ge=0.0, le=10000.0)
    temp_cleanup_hours: int | None = Field(default=None, ge=1, le=720)


class GoogleOAuthConfigUpdate(BaseModel):
    client_id: str = Field(min_length=5, max_length=256)
    client_secret: str = Field(min_length=5, max_length=256)
    scope_mode: Literal["drive.file", "drive.readonly", "drive"] = "drive.file"


class ConfirmDeleteRequest(BaseModel):
    confirmed: bool = False


class OverwriteConfirmRequest(BaseModel):
    source_file_id: str
    target_file_id: str
    confirmed: bool = False


class ApiResponse(BaseModel):
    ok: bool = True
    data: Any = None
    warnings: list[str] = Field(default_factory=list)
