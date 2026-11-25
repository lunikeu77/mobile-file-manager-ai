export type ViewKey =
  | "home"
  | "explorer"
  | "detail"
  | "compression"
  | "queue"
  | "ai_chat"
  | "schedule"
  | "history"
  | "ai_settings"
  | "privacy";

export interface FileItem {
  file_id: string;
  source: "local" | "google_drive";
  name: string;
  mime_type: string;
  size: number;
  modified_time: string;
  cached_at?: string;
  parent_id?: string | null;
  local_path?: string | null;
  metadata?: Record<string, unknown>;
}

export interface TaskStep {
  step_id: string;
  task_id: string;
  step_order: number;
  action: string;
  status: string;
  input_ref: string | null;
  result_ref: string | null;
  options_json: string | null;
  retry_count: number;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface TaskItem {
  task_id: string;
  task_type: string;
  title: string;
  status:
    | "PENDING"
    | "SCHEDULED"
    | "RUNNING"
    | "PAUSED"
    | "COMPLETED"
    | "FAILED"
    | "CANCELLED"
    | "NEEDS_USER_ACTION";
  idempotency_key: string | null;
  correlation_id: string | null;
  payload_json: string;
  result_json: string | null;
  progress: number;
  requires_confirmation: number;
  confirmed_at: string | null;
  created_at: string;
  scheduled_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  updated_at: string;
  error_code: string | null;
  error_message: string | null;
  steps: TaskStep[];
}

export interface DownloadJob {
  download_id: string;
  task_id: string | null;
  source_file_id: string | null;
  source_url: string | null;
  filename: string;
  direction: "download" | "upload_drive";
  bytes_received: number;
  total_bytes: number;
  supports_range: number;
  status: "PENDING" | "RUNNING" | "PAUSED" | "COMPLETED" | "FAILED" | "CANCELLED";
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProviderSetting {
  provider_id: "openrouter" | "gemini" | "kimi" | "openai";
  enabled: boolean;
  priority: number;
  model_id: string;
  key_configured: boolean;
  key_masked: string;
  key_reference: string | null;
  quota_state: string;
  last_error_at: string | null;
  last_error_code: string | null;
  last_error_message: string | null;
  circuit_breaker_until: string | null;
  free_only: boolean;
  monthly_budget_usd: number;
  updated_at: string;
}

export interface PrivacySettings {
  ai_send_filenames: boolean;
  ai_send_metadata: boolean;
  ai_free_only_mode: boolean;
  ai_auto_failover: boolean;
  ai_monthly_limit_usd: number;
  temp_cleanup_hours: number;
  last_cleanup_at: string | null;
  using_ephemeral_secret: boolean;
  security_notice: string;
}

export interface DriveConfig {
  configured: boolean;
  connected: boolean;
  client_id_masked: string;
  redirect_uri: string;
  scope_mode: "drive.file" | "drive.readonly" | "drive";
  scope_url: string;
  workspace_folder_id?: string;
  workspace_folder_name?: string;
  scope_notice: string;
}

export interface SystemStatus {
  app_env: string;
  max_concurrent_jobs: number;
  using_ephemeral_secret: boolean;
  binaries: Record<string, string | null>;
  local_file_count: number;
  local_total_bytes: number;
  active_task_count: number;
  active_download_count: number;
  drive_connected: boolean;
  ai_configured_count: number;
  render_notices: string[];
  licenses: Record<string, string>;
}

export interface AIPlanResponse {
  correlation_id: string;
  complexity: string;
  provider_used: string;
  model_id: string;
  latency_ms?: number;
  failover_chain: Array<Record<string, unknown>>;
  spec: {
    schema_version: "1.0";
    intent: string;
    summary?: string;
    steps: Array<Record<string, unknown>>;
    requires_confirmation: boolean;
    warnings: string[];
  };
}
