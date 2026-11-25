async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    headers: {
      ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(init?.headers || {}),
    },
    ...init,
  });
  const payload = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(payload?.detail || payload?.error || `HTTP ${resp.status}`);
  }
  return (payload.data ?? payload) as T;
}

export const api = {
  getSystemStatus: () => apiRequest<any>("/api/system/status"),
  listFiles: (params: Record<string, string | number | undefined>) => {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== "") qs.set(k, String(v));
    });
    return apiRequest<any[]>(`/api/files?${qs.toString()}`);
  },
  uploadFile: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return apiRequest<any>("/api/files/upload", { method: "POST", body: form });
  },
  getFile: (id: string) => apiRequest<any>(`/api/files/${encodeURIComponent(id)}`),
  renameFile: (id: string, new_name: string) =>
    apiRequest<any>(`/api/files/${encodeURIComponent(id)}/rename`, {
      method: "POST",
      body: JSON.stringify({ new_name }),
    }),
  batchRename: (items: Array<{ file_id: string; new_name: string }>) =>
    apiRequest<any>("/api/files/batch-rename", {
      method: "POST",
      body: JSON.stringify({ items }),
    }),
  changeExtension: (id: string, new_extension: string, acknowledge_no_reencode: boolean) =>
    apiRequest<any>(`/api/files/${encodeURIComponent(id)}/change-extension`, {
      method: "POST",
      body: JSON.stringify({ new_extension, acknowledge_no_reencode }),
    }),
  deleteFile: (id: string) =>
    apiRequest<any>(`/api/files/${encodeURIComponent(id)}/delete`, {
      method: "POST",
      body: JSON.stringify({ confirmed: true }),
    }),
  overwriteFile: (source_file_id: string, target_file_id: string) =>
    apiRequest<any>("/api/files/overwrite", {
      method: "POST",
      body: JSON.stringify({ source_file_id, target_file_id, confirmed: true }),
    }),
  cleanupTemp: () => apiRequest<any>("/api/files/cleanup-temp", { method: "POST" }),

  probeVideo: (id: string) => apiRequest<any>(`/api/compression/video/probe/${encodeURIComponent(id)}`),
  estimateVideo: (duration_sec: number, preset: string, target_size_bytes?: number, audio_kbps = 96) => {
    const qs = new URLSearchParams({
      duration_sec: String(duration_sec),
      preset,
      audio_kbps: String(audio_kbps),
    });
    if (target_size_bytes) qs.set("target_size_bytes", String(target_size_bytes));
    return apiRequest<any>(`/api/compression/video/estimate?${qs.toString()}`);
  },
  compressVideo: (body: Record<string, unknown>) =>
    apiRequest<any>("/api/compression/video", { method: "POST", body: JSON.stringify(body) }),
  compressPdf: (body: Record<string, unknown>) =>
    apiRequest<any>("/api/compression/pdf", { method: "POST", body: JSON.stringify(body) }),

  listDownloads: () => apiRequest<any[]>("/api/downloads"),
  createDownload: (url: string, filename?: string) =>
    apiRequest<any>("/api/downloads", { method: "POST", body: JSON.stringify({ url, filename: filename || null }) }),
  pauseDownload: (id: string) => apiRequest<any>(`/api/downloads/${encodeURIComponent(id)}/pause`, { method: "POST" }),
  resumeDownload: (id: string) => apiRequest<any>(`/api/downloads/${encodeURIComponent(id)}/resume`, { method: "POST" }),
  cancelDownload: (id: string) => apiRequest<any>(`/api/downloads/${encodeURIComponent(id)}/cancel`, { method: "POST" }),

  listTasks: (status?: string) => apiRequest<any[]>(status ? `/api/tasks?status=${encodeURIComponent(status)}` : "/api/tasks"),
  createTask: (body: Record<string, unknown>) =>
    apiRequest<any>("/api/tasks", { method: "POST", body: JSON.stringify(body) }),
  confirmTask: (id: string) => apiRequest<any>(`/api/tasks/${encodeURIComponent(id)}/confirm`, { method: "POST" }),
  retryTask: (id: string) => apiRequest<any>(`/api/tasks/${encodeURIComponent(id)}/retry`, { method: "POST" }),
  cancelTask: (id: string) => apiRequest<any>(`/api/tasks/${encodeURIComponent(id)}/cancel`, { method: "POST" }),

  planAiCommand: (
    command: string,
    selected_file_ids: string[],
    source: string,
    preferred_provider = "auto",
    preferred_model?: string
  ) =>
    apiRequest<any>("/api/ai/command", {
      method: "POST",
      body: JSON.stringify({
        command,
        selected_file_ids,
        source,
        preferred_provider,
        preferred_model: preferred_model || null,
      }),
    }),
  chatWithAi: (body: {
    provider_id: "openrouter" | "gemini" | "kimi" | "openai";
    model_id?: string;
    message: string;
    history: Array<{ role: "user" | "assistant"; content: string }>;
    include_file_context?: boolean;
  }) =>
    apiRequest<{
      correlation_id: string;
      provider_id: string;
      model_id: string;
      reply: string;
      latency_ms: number;
    }>("/api/ai/chat", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getAiUsage: () => apiRequest<any>("/api/ai/usage"),

  getDriveConfig: () => apiRequest<any>("/api/drive/config"),
  saveDriveConfig: (body: { client_id: string; client_secret: string; scope_mode: string }) =>
    apiRequest<any>("/api/drive/config", { method: "PUT", body: JSON.stringify(body) }),
  startDriveOAuth: () => apiRequest<{ auth_url: string; state: string }>("/api/drive/oauth/start"),
  disconnectDrive: () => apiRequest<any>("/api/drive/disconnect", { method: "POST" }),
  listDriveFiles: (query?: string, page_token?: string, folder_id?: string) => {
    const qs = new URLSearchParams();
    if (query) qs.set("query", query);
    if (page_token) qs.set("page_token", page_token);
    if (folder_id) qs.set("folder_id", folder_id);
    return apiRequest<any>(`/api/drive/files?${qs.toString()}`);
  },
  uploadToDrive: (file_id: string, parent_folder_id?: string) =>
    apiRequest<any>("/api/drive/upload", {
      method: "POST",
      body: JSON.stringify({ file_id, parent_folder_id: parent_folder_id || null, confirmed: true }),
    }),
  downloadFromDrive: (file_id: string) =>
    apiRequest<any>(`/api/drive/download/${encodeURIComponent(file_id)}`, { method: "POST" }),
  renameDriveFile: (file_id: string, new_name: string) =>
    apiRequest<any>(`/api/drive/files/${encodeURIComponent(file_id)}/rename`, {
      method: "POST",
      body: JSON.stringify({ new_name }),
    }),
  changeDriveExtension: (file_id: string, new_extension: string, acknowledge_no_reencode: boolean) =>
    apiRequest<any>(`/api/drive/files/${encodeURIComponent(file_id)}/change-extension`, {
      method: "POST",
      body: JSON.stringify({ new_extension, acknowledge_no_reencode }),
    }),
  deleteDriveFile: (file_id: string) =>
    apiRequest<any>(`/api/drive/files/${encodeURIComponent(file_id)}/delete`, {
      method: "POST",
      body: JSON.stringify({ confirmed: true }),
    }),
  ensureDriveWorkspaceFolder: () =>
    apiRequest<{ folder_id: string; name: string; created: boolean }>("/api/drive/workspace-folder/ensure", {
      method: "POST",
    }),
  runDriveRoundtrip: (drive_file_id: string, action: "COMPRESS_VIDEO" | "COMPRESS_PDF", preset?: string) =>
    apiRequest<any>("/api/drive/roundtrip", {
      method: "POST",
      body: JSON.stringify({ drive_file_id, action, preset: preset || null, cleanup_local_after: true }),
    }),

  listProviders: () => apiRequest<any[]>("/api/settings/providers"),
  updateProvider: (provider_id: string, body: Record<string, unknown>) =>
    apiRequest<any>(`/api/settings/providers/${encodeURIComponent(provider_id)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  testProvider: (provider_id: string) =>
    apiRequest<any>(`/api/settings/providers/${encodeURIComponent(provider_id)}/test`, { method: "POST" }),
  getPrivacy: () => apiRequest<any>("/api/settings/privacy"),
  updatePrivacy: (body: Record<string, unknown>) =>
    apiRequest<any>("/api/settings/privacy", { method: "PUT", body: JSON.stringify(body) }),
};

export function formatBytes(bytes: number): string {
  if (!bytes || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const idx = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** idx).toFixed(idx === 0 ? 0 : 2)} ${units[idx]}`;
}
