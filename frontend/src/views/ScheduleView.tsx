import React, { useState } from "react";
import { AlertCircle, CalendarClock, Plus } from "lucide-react";
import { api, formatBytes } from "../api/client";
import { FileItem, TaskItem } from "../types";
import { StatusBadge } from "../components/StatusBadge";

interface ScheduleViewProps {
  localFiles: FileItem[];
  tasks: TaskItem[];
  onRefresh: () => void;
}

export const ScheduleView: React.FC<ScheduleViewProps> = ({ localFiles, tasks, onRefresh }) => {
  const [title, setTitle] = useState("예약 압축 작업");
  const [fileId, setFileId] = useState("");
  const [actionType, setActionType] = useState<"COMPRESS_VIDEO" | "COMPRESS_PDF">("COMPRESS_VIDEO");
  const [chainUploadDrive, setChainUploadDrive] = useState(false);
  const [delayMinutes, setDelayMinutes] = useState(5);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const scheduledTasks = tasks.filter((t) => t.status === "SCHEDULED");

  const createScheduled = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!fileId) return;
    setErr(null);
    setMsg(null);
    const runAt = new Date(Date.now() + delayMinutes * 60_000).toISOString();
    const steps: any[] = [
      {
        action: actionType,
        source: "local",
        file_id: fileId,
        options: { preset: actionType === "COMPRESS_VIDEO" ? "balanced" : "ebook" },
      },
    ];
    if (chainUploadDrive) {
      steps.push({
        action: "UPLOAD_FILE",
        source: "local",
        input_ref: "$previous.output",
      });
    }

    try {
      const task = await api.createTask({
        title,
        task_type: "scheduled_chain",
        scheduled_at: runAt,
        confirmed: true,
        idempotency_key: `sched_${fileId}_${Date.now()}`,
        spec: {
          schema_version: "1.0",
          intent: "scheduled_compression_chain",
          steps,
          requires_confirmation: chainUploadDrive,
        },
      });
      setMsg(`예약 작업이 등록되었습니다 (${task.task_id}, 희망 시각: ${new Date(runAt).toLocaleString()}).`);
      onRefresh();
    } catch (e: any) {
      setErr(e.message);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold text-white">예약 작업 &amp; 자동화 체인</h2>
        <p className="text-sm text-slate-400">서버 백그라운드 스케줄러를 이용해 지정 시간 후 압축·업로드 체인을 실행합니다.</p>
      </div>

      <div className="rounded-2xl border border-amber-500/30 bg-amber-500/5 p-4 text-xs leading-relaxed text-amber-200">
        <div className="flex items-center gap-2 font-semibold">
          <AlertCircle className="h-4 w-4" /> 예약 시각 실행 보장 범위 안내
        </div>
        <p className="mt-1">
          예약 시간은 <strong>실행 보장 시각이 아닌 실행 희망 시각</strong>입니다. Render 무료 인스턴스는 약 15분간 요청이 없으면
          유휴 수면(Spin-down) 상태로 들어가며, 깨어난 뒤 지연된 예약 작업이 순차 재평가·실행됩니다.
        </p>
      </div>

      <form onSubmit={createScheduled} className="space-y-4 rounded-2xl border border-slate-800 bg-slate-900/70 p-5">
        <div className="flex items-center gap-2 text-base font-semibold text-white">
          <CalendarClock className="h-4 w-4 text-violet-400" />새 예약 작업 생성
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="작업 제목"
            required
            className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
          />
          <select
            value={fileId}
            onChange={(e) => setFileId(e.target.value)}
            required
            className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
          >
            <option value="">대상 파일 선택...</option>
            {localFiles.map((f) => (
              <option key={f.file_id} value={f.file_id}>
                {f.name} ({formatBytes(f.size)})
              </option>
            ))}
          </select>
          <select
            value={actionType}
            onChange={(e) => setActionType(e.target.value as any)}
            className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
          >
            <option value="COMPRESS_VIDEO">MP4 압축 (FFmpeg balanced)</option>
            <option value="COMPRESS_PDF">PDF 압축 (ebook)</option>
          </select>
          <div className="flex items-center gap-2">
            <input
              type="number"
              min="1"
              max="1440"
              value={delayMinutes}
              onChange={(e) => setDelayMinutes(Number(e.target.value))}
              className="w-24 rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
            />
            <span className="text-xs text-slate-300">분 후 실행 희망</span>
          </div>
        </div>

        <label className="flex items-center gap-2 text-xs text-slate-300">
          <input
            type="checkbox"
            checked={chainUploadDrive}
            onChange={(e) => setChainUploadDrive(e.target.checked)}
            className="h-4 w-4"
          />
          체인 연결: 압축 완료 후 결과 파일을 Google Drive로 자동 업로드 ($previous.output)
        </label>

        <button
          type="submit"
          className="inline-flex items-center gap-1.5 rounded-xl bg-violet-600 px-4 py-2 text-sm font-semibold text-white hover:bg-violet-500"
        >
          <Plus className="h-4 w-4" /> 예약 작업 등록
        </button>

        {msg && <div className="text-xs text-emerald-300">{msg}</div>}
        {err && <div className="text-xs text-rose-400">{err}</div>}
      </form>

      <div className="space-y-3">
        <h3 className="text-base font-semibold text-white">대기 중인 예약 작업 ({scheduledTasks.length})</h3>
        {scheduledTasks.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-slate-800 p-6 text-center text-sm text-slate-400">
            예약 대기 중인 작업이 없습니다.
          </div>
        ) : (
          scheduledTasks.map((t) => (
            <div key={t.task_id} className="flex flex-wrap items-center justify-between gap-2 rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
              <div>
                <div className="text-sm font-semibold text-white">{t.title}</div>
                <div className="text-xs text-slate-400">
                  희망 시각: {t.scheduled_at ? new Date(t.scheduled_at).toLocaleString() : "-"} · {t.steps.length}단계
                </div>
              </div>
              <div className="flex items-center gap-2">
                <StatusBadge status={t.status} />
                <button
                  onClick={async () => {
                    setErr(null);
                    try {
                      await api.cancelTask(t.task_id);
                      onRefresh();
                    } catch (e: any) {
                      setErr(e?.message || "예약 취소 중 오류가 발생했습니다.");
                    }
                  }}
                  className="rounded-lg border border-rose-500/40 px-2.5 py-1 text-xs text-rose-300"
                >
                  취소
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
};
