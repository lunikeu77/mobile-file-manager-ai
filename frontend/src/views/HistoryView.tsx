import React, { useState } from "react";
import { ChevronDown, ChevronUp, RotateCcw } from "lucide-react";
import { api } from "../api/client";
import { TaskItem } from "../types";
import { StatusBadge } from "../components/StatusBadge";

interface HistoryViewProps {
  tasks: TaskItem[];
  onRefresh: () => void;
}

export const HistoryView: React.FC<HistoryViewProps> = ({ tasks, onRefresh }) => {
  const [expanded, setExpanded] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const handleTaskAction = async (fn: () => Promise<unknown>) => {
    setErr(null);
    try {
      await fn();
      onRefresh();
    } catch (e: any) {
      setErr(e?.message || "작업 상태 변경 중 오류가 발생했습니다.");
    }
  };

  return (
    <div className="space-y-6">
      {err && <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-300">{err}</div>}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-white">작업 이력 &amp; 단계별 로그</h2>
          <p className="text-sm text-slate-400">
            모든 작업의 `task_steps`, `correlation_id`, `idempotency_key` 및 장애 복구 상태를 확인합니다.
          </p>
        </div>
        <button
          onClick={onRefresh}
          className="rounded-xl border border-slate-700 px-3.5 py-2 text-xs font-medium text-slate-200 hover:bg-slate-800"
        >
          새로고침
        </button>
      </div>

      {tasks.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-slate-800 p-8 text-center text-sm text-slate-400">
          작업 이력이 없습니다.
        </div>
      ) : (
        <div className="space-y-3">
          {tasks.map((t) => {
            const isOpen = expanded === t.task_id;
            return (
              <div key={t.task_id} className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <button
                    onClick={() => setExpanded(isOpen ? null : t.task_id)}
                    className="flex items-center gap-2 text-left"
                  >
                    {isOpen ? <ChevronUp className="h-4 w-4 text-slate-400" /> : <ChevronDown className="h-4 w-4 text-slate-400" />}
                    <div>
                      <div className="text-sm font-semibold text-white">{t.title}</div>
                      <div className="text-xs text-slate-400">
                        {t.task_id} · corr: {t.correlation_id || "-"} · 생성: {new Date(t.created_at).toLocaleString()}
                      </div>
                    </div>
                  </button>
                  <div className="flex items-center gap-2">
                    <StatusBadge status={t.status} />
                    {t.status === "NEEDS_USER_ACTION" && (
                      <button
                        onClick={() => handleTaskAction(() => api.confirmTask(t.task_id))}
                        className="rounded-lg bg-emerald-600 px-2.5 py-1 text-xs font-semibold text-white"
                      >
                        승인 실행
                      </button>
                    )}
                    {t.status === "FAILED" && (
                      <button
                        onClick={() => handleTaskAction(() => api.retryTask(t.task_id))}
                        className="inline-flex items-center gap-1 rounded-lg bg-indigo-600 px-2.5 py-1 text-xs text-white"
                      >
                        <RotateCcw className="h-3 w-3" /> 재시도
                      </button>
                    )}
                  </div>
                </div>

                {t.error_message && (
                  <div className="mt-2 rounded-lg bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
                    [{t.error_code}] {t.error_message}
                  </div>
                )}

                {isOpen && (
                  <div className="mt-3 space-y-2 border-t border-slate-800 pt-3">
                    <div className="text-xs text-slate-400">idempotency_key: {t.idempotency_key || "none"}</div>
                    {t.steps.map((s) => (
                      <div key={s.step_id} className="rounded-xl border border-slate-800 bg-slate-950/70 p-3 text-xs">
                        <div className="flex items-center justify-between">
                          <span className="font-semibold text-slate-200">
                            Step {s.step_order}: {s.action}
                          </span>
                          <StatusBadge status={s.status} />
                        </div>
                        <div className="mt-1 text-slate-400">
                          입력: {s.input_ref || "-"} · 재시도: {s.retry_count}회
                        </div>
                        {s.error_message && <div className="mt-1 text-rose-400">{s.error_message}</div>}
                        {s.result_ref && (
                          <pre className="mt-2 max-h-36 overflow-auto rounded bg-slate-900 p-2 text-[11px] text-emerald-300">
                            {s.result_ref}
                          </pre>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
