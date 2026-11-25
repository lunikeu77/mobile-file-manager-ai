import React from "react";
import {
  AlertCircle,
  Bot,
  Clock,
  Cloud,
  Copy,
  Cpu,
  DownloadCloud,
  FolderOpen,
  HardDrive,
  Minimize2,
} from "lucide-react";
import { formatBytes } from "../api/client";
import { SystemStatus, TaskItem, ViewKey } from "../types";
import { StatusBadge } from "../components/StatusBadge";

interface HomeViewProps {
  status: SystemStatus | null;
  tasks: TaskItem[];
  onNavigate: (view: ViewKey) => void;
  onRefresh: () => void;
}

export const HomeView: React.FC<HomeViewProps> = ({ status, tasks, onNavigate, onRefresh }) => {
  const healthUrl = `${window.location.origin}/health`;

  const copyHealth = () => {
    navigator.clipboard?.writeText(healthUrl);
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-2xl font-bold text-white">통합 대시보드</h2>
          <p className="text-sm text-slate-400">
            AI 키 없이도 로컬 탐색·이름 변경·MP4/PDF 압축·다운로드·예약 작업이 독립 실행됩니다.
          </p>
        </div>
        <button
          onClick={onRefresh}
          className="rounded-xl border border-slate-700 bg-slate-900 px-4 py-2 text-sm font-medium text-slate-200 hover:bg-slate-800"
        >
          상태 새로고침
        </button>
      </div>

      {/* Stat Cards */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
          <div className="flex items-center gap-2 text-xs text-slate-400">
            <HardDrive className="h-4 w-4 text-indigo-400" />
            로컬 워크스페이스
          </div>
          <div className="mt-2 text-2xl font-bold text-white">{status?.local_file_count ?? 0}개</div>
          <div className="text-xs text-slate-400">{formatBytes(status?.local_total_bytes ?? 0)}</div>
        </div>
        <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
          <div className="flex items-center gap-2 text-xs text-slate-400">
            <Clock className="h-4 w-4 text-sky-400" />
            활성/예약 작업
          </div>
          <div className="mt-2 text-2xl font-bold text-white">{status?.active_task_count ?? 0}건</div>
          <div className="text-xs text-slate-400">동시 압축 워커: {status?.max_concurrent_jobs ?? 1}개 제한</div>
        </div>
        <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
          <div className="flex items-center gap-2 text-xs text-slate-400">
            <Cloud className="h-4 w-4 text-emerald-400" />
            Google Drive
          </div>
          <div className="mt-2 text-lg font-bold text-white">
            {status?.drive_connected ? "연결됨" : "미연동 (로컬 사용 가능)"}
          </div>
          <button onClick={() => onNavigate("ai_settings")} className="text-xs text-indigo-400 hover:underline">
            OAuth 설정 열기
          </button>
        </div>
        <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
          <div className="flex items-center gap-2 text-xs text-slate-400">
            <Cpu className="h-4 w-4 text-amber-400" />
            엔진 &amp; AI 공급자
          </div>
          <div className="mt-2 text-sm font-semibold text-white">
            FFmpeg {status?.binaries?.ffmpeg ? "✓" : "✗"} · GS {status?.binaries?.ghostscript ? "✓" : "pikepdf"}
          </div>
          <div className="mt-1 text-xs text-slate-400">
            AI 키 등록: {status?.ai_configured_count ?? 0}개 (L0 규칙은 항상 무료)
          </div>
        </div>
      </div>

      {/* Quick Actions */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <button
          onClick={() => onNavigate("explorer")}
          className="flex items-center gap-2.5 rounded-2xl border border-slate-800 bg-slate-900 p-4 text-left hover:border-indigo-500/50"
        >
          <FolderOpen className="h-5 w-5 text-indigo-400" />
          <div>
            <div className="text-sm font-semibold text-white">파일 탐색·업로드</div>
            <div className="text-xs text-slate-400">로컬 / Drive</div>
          </div>
        </button>
        <button
          onClick={() => onNavigate("compression")}
          className="flex items-center gap-2.5 rounded-2xl border border-slate-800 bg-slate-900 p-4 text-left hover:border-indigo-500/50"
        >
          <Minimize2 className="h-5 w-5 text-emerald-400" />
          <div>
            <div className="text-sm font-semibold text-white">MP4·PDF 압축</div>
            <div className="text-xs text-slate-400">백그라운드 실행</div>
          </div>
        </button>
        <button
          onClick={() => onNavigate("ai_chat")}
          className="flex items-center gap-2.5 rounded-2xl border border-slate-800 bg-slate-900 p-4 text-left hover:border-indigo-500/50"
        >
          <Bot className="h-5 w-5 text-violet-400" />
          <div>
            <div className="text-sm font-semibold text-white">AI 명령 채팅</div>
            <div className="text-xs text-slate-400">L0 규칙 + 다중 AI</div>
          </div>
        </button>
        <button
          onClick={() => onNavigate("queue")}
          className="flex items-center gap-2.5 rounded-2xl border border-slate-800 bg-slate-900 p-4 text-left hover:border-indigo-500/50"
        >
          <DownloadCloud className="h-5 w-5 text-sky-400" />
          <div>
            <div className="text-sm font-semibold text-white">전송 대기열</div>
            <div className="text-xs text-slate-400">Range 이어받기</div>
          </div>
        </button>
      </div>

      {/* Recent Tasks */}
      <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-base font-semibold text-white">최근 백그라운드 작업</h3>
          <button onClick={() => onNavigate("history")} className="text-xs text-indigo-400 hover:underline">
            전체 이력 보기
          </button>
        </div>
        {tasks.length === 0 ? (
          <p className="text-sm text-slate-400">아직 등록된 작업이 없습니다.</p>
        ) : (
          <div className="space-y-2">
            {tasks.slice(0, 5).map((t) => (
              <div
                key={t.task_id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-800 bg-slate-950/60 px-4 py-3"
              >
                <div>
                  <div className="text-sm font-medium text-white">{t.title}</div>
                  <div className="text-xs text-slate-400">
                    {t.task_id} · 진행률 {t.progress}%
                  </div>
                </div>
                <StatusBadge status={t.status} />
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Render & Ephemeral Disk Honest Notice */}
      <div className="rounded-2xl border border-amber-500/30 bg-amber-500/5 p-5">
        <div className="flex items-center gap-2 text-sm font-semibold text-amber-300">
          <AlertCircle className="h-4 w-4" />
          Render 무료 인스턴스 운영 안내 &amp; Keep-Alive 헬스체크
        </div>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-xs leading-relaxed text-slate-300">
          {(status?.render_notices || []).map((n, idx) => (
            <li key={idx}>{n}</li>
          ))}
        </ul>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <code className="rounded-lg bg-slate-900 px-3 py-1.5 text-xs text-emerald-300">{healthUrl}</code>
          <button
            onClick={copyHealth}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-700"
          >
            <Copy className="h-3.5 w-3.5" />
            헬스체크 URL 복사
          </button>
        </div>
      </div>
    </div>
  );
};
