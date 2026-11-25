import React, { useState } from "react";
import { DownloadCloud, Pause, Play, RotateCcw, X } from "lucide-react";
import { api, formatBytes } from "../api/client";
import { DownloadJob, TaskItem } from "../types";
import { StatusBadge } from "../components/StatusBadge";

interface QueueViewProps {
  downloads: DownloadJob[];
  tasks: TaskItem[];
  onRefresh: () => void;
}

export const QueueView: React.FC<QueueViewProps> = ({ downloads, tasks, onRefresh }) => {
  const [url, setUrl] = useState("");
  const [filename, setFilename] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const enqueueDownload = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    try {
      await api.createDownload(url, filename || undefined);
      setUrl("");
      setFilename("");
      onRefresh();
    } catch (e: any) {
      setErr(e.message);
    }
  };

  const handleAction = async (fn: () => Promise<unknown>) => {
    setErr(null);
    try {
      await fn();
      onRefresh();
    } catch (e: any) {
      setErr(e?.message || "작업 처리 중 오류가 발생했습니다.");
    }
  };

  const activeTasks = tasks.filter((t) =>
    ["PENDING", "RUNNING", "NEEDS_USER_ACTION", "PAUSED"].includes(t.status)
  );

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold text-white">다운로드 및 작업 대기열</h2>
        <p className="text-sm text-slate-400">
          HTTP Range 이어받기(서버 지원 시), Drive 전송 진행률, 일시정지·재개·취소·재시도를 실시간 관리합니다.
        </p>
      </div>

      <form onSubmit={enqueueDownload} className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5 space-y-3">
        <div className="flex items-center gap-2 text-sm font-semibold text-white">
          <DownloadCloud className="h-4 w-4 text-sky-400" />
          외부 URL 다운로드 추가
        </div>
        <div className="grid gap-2 sm:grid-cols-3">
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://example.com/sample.mp4"
            required
            className="sm:col-span-2 rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
          />
          <input
            value={filename}
            onChange={(e) => setFilename(e.target.value)}
            placeholder="저장 파일명 (선택)"
            className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
          />
        </div>
        <button type="submit" className="rounded-xl bg-sky-600 px-4 py-2 text-sm font-semibold text-white hover:bg-sky-500">
          다운로드 대기열 등록
        </button>
        {err && <div className="text-xs text-rose-400">{err}</div>}
      </form>

      {/* Download & Transfer Jobs */}
      <div className="space-y-3">
        <h3 className="text-base font-semibold text-white">전송 큐 ({downloads.length})</h3>
        {downloads.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-slate-800 p-6 text-center text-sm text-slate-400">
            진행 중이거나 기록된 다운로드가 없습니다.
          </div>
        ) : (
          downloads.map((d) => {
            const pct = d.total_bytes > 0 ? Math.min(100, Math.round((d.bytes_received / d.total_bytes) * 100)) : 0;
            return (
              <div key={d.download_id} className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4 space-y-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <div className="text-sm font-semibold text-white">{d.filename}</div>
                    <div className="text-xs text-slate-400">
                      {d.direction === "upload_drive" ? "Drive 업로드" : "다운로드"} · {formatBytes(d.bytes_received)} /{" "}
                      {d.total_bytes > 0 ? formatBytes(d.total_bytes) : "알 수 없음"}
                      {d.supports_range ? " · Range 이어받기 지원" : ""}
                    </div>
                  </div>
                  <StatusBadge status={d.status} />
                </div>

                <div className="h-2 w-full overflow-hidden rounded-full bg-slate-800">
                  <div className="h-full bg-sky-500 transition-all" style={{ width: `${pct}%` }} />
                </div>

                {d.error_message && <div className="text-xs text-rose-400">{d.error_message}</div>}

                {d.source_url && (
                  <div className="flex flex-wrap gap-2">
                    {d.status === "RUNNING" && (
                      <button
                        onClick={() => handleAction(() => api.pauseDownload(d.download_id))}
                        className="inline-flex items-center gap-1 rounded-lg border border-slate-700 px-2.5 py-1 text-xs text-slate-200"
                      >
                        <Pause className="h-3 w-3" /> 일시정지
                      </button>
                    )}
                    {(d.status === "PAUSED" || d.status === "FAILED") && (
                      <button
                        onClick={() => handleAction(() => api.resumeDownload(d.download_id))}
                        className="inline-flex items-center gap-1 rounded-lg bg-indigo-600 px-2.5 py-1 text-xs text-white"
                      >
                        <Play className="h-3 w-3" /> 재개/재시도
                      </button>
                    )}
                    {["PENDING", "RUNNING", "PAUSED", "FAILED"].includes(d.status) && (
                      <button
                        onClick={() => handleAction(() => api.cancelDownload(d.download_id))}
                        className="inline-flex items-center gap-1 rounded-lg border border-rose-500/40 px-2.5 py-1 text-xs text-rose-300"
                      >
                        <X className="h-3 w-3" /> 취소
                      </button>
                    )}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>

      {/* Active Background Tasks */}
      <div className="space-y-3">
        <h3 className="text-base font-semibold text-white">대기/실행 중 백그라운드 작업 ({activeTasks.length})</h3>
        {activeTasks.map((t) => (
          <div key={t.task_id} className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4 space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="text-sm font-semibold text-white">{t.title}</div>
                <div className="text-xs text-slate-400">진행률 {t.progress}% · {t.error_message || t.task_id}</div>
              </div>
              <StatusBadge status={t.status} />
            </div>
            <div className="flex gap-2">
              {t.status === "NEEDS_USER_ACTION" && (
                <button
                  onClick={() => handleAction(() => api.confirmTask(t.task_id))}
                  className="rounded-lg bg-emerald-600 px-3 py-1 text-xs font-semibold text-white"
                >
                  승인 후 실행
                </button>
              )}
              {t.status === "FAILED" && (
                <button
                  onClick={() => handleAction(() => api.retryTask(t.task_id))}
                  className="inline-flex items-center gap-1 rounded-lg bg-indigo-600 px-3 py-1 text-xs text-white"
                >
                  <RotateCcw className="h-3 w-3" /> 재시도
                </button>
              )}
              {["PENDING", "NEEDS_USER_ACTION"].includes(t.status) && (
                <button
                  onClick={() => handleAction(() => api.cancelTask(t.task_id))}
                  className="rounded-lg border border-rose-500/40 px-3 py-1 text-xs text-rose-300"
                >
                  취소
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};
