import React, { useState } from "react";
import { Bot, Play, Send, ShieldAlert, Sparkles } from "lucide-react";
import { api } from "../api/client";
import { ConfirmModal } from "../components/ConfirmModal";
import { AIPlanResponse, FileItem, PrivacySettings } from "../types";

interface AiChatViewProps {
  localFiles: FileItem[];
  privacy: PrivacySettings | null;
  onTaskExecuted: () => void;
}

export const AiChatView: React.FC<AiChatViewProps> = ({ localFiles, privacy, onTaskExecuted }) => {
  const [command, setCommand] = useState("");
  const [source, setSource] = useState<"local" | "google_drive">("local");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [plan, setPlan] = useState<AIPlanResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const examples = [
    "PDF만 찾아줘",
    "10MB 초과 파일 보여줘",
    "선택한 파일에 접미사 _backup 추가",
    "선택한 파일 mp4 압축해줘",
  ];

  const handlePlan = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!command.trim()) return;
    setLoading(true);
    setErr(null);
    setMsg(null);
    setPlan(null);
    try {
      const res = await api.planAiCommand(command, selectedIds, source);
      setPlan(res);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  };

  const executePlan = async (confirmed: boolean) => {
    if (!plan) return;
    setConfirmOpen(false);
    setErr(null);
    try {
      const task = await api.createTask({
        title: plan.spec.summary || `AI 작업 (${plan.spec.intent})`,
        task_type: "ai_workflow",
        spec: plan.spec,
        confirmed,
        idempotency_key: `ai_${plan.correlation_id}`,
      });
      setMsg(`작업이 큐에 등록되었습니다 (${task.task_id}, 상태: ${task.status}).`);
      onTaskExecuted();
    } catch (e: any) {
      setErr(e.message);
    }
  };

  const toggleId = (id: string) => {
    setSelectedIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold text-white">AI 명령 채팅 (작업 명세 생성기)</h2>
        <p className="text-sm text-slate-400">
          L0 정형 명령은 외부 AI 호출 없이 0원 즉시 처리되며, 복합 명령은 검증된 JSON 작업 명세만 생성 후 실행합니다.
        </p>
      </div>

      {/* Privacy & Routing Banner */}
      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-slate-800 bg-slate-900/70 px-4 py-2.5 text-xs text-slate-300">
        <Sparkles className="h-4 w-4 text-indigo-400" />
        <span>
          파일명 AI 전송: <strong>{privacy?.ai_send_filenames ? "허용됨" : "마스킹 차단(안전)"}</strong> · 무료 전용 모드:{" "}
          <strong>{privacy?.ai_free_only_mode ? "ON" : "OFF"}</strong>
        </span>
      </div>

      {/* Optional File Context Selector */}
      {localFiles.length > 0 && (
        <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-4">
          <div className="mb-2 text-xs font-medium text-slate-400">선택 대상 파일 (선택 시 L0/AI가 해당 ID를 참조)</div>
          <div className="flex flex-wrap gap-1.5">
            {localFiles.slice(0, 12).map((f) => {
              const active = selectedIds.includes(f.file_id);
              return (
                <button
                  key={f.file_id}
                  type="button"
                  onClick={() => toggleId(f.file_id)}
                  className={`rounded-lg border px-2.5 py-1 text-xs ${
                    active
                      ? "border-indigo-500 bg-indigo-500/20 text-indigo-200"
                      : "border-slate-700 bg-slate-950 text-slate-400"
                  }`}
                >
                  {f.name}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Command Input */}
      <form onSubmit={handlePlan} className="space-y-3 rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
        <div className="flex flex-wrap gap-1.5">
          {examples.map((ex) => (
            <button
              key={ex}
              type="button"
              onClick={() => setCommand(ex)}
              className="rounded-lg border border-slate-700 bg-slate-800/70 px-2.5 py-1 text-xs text-slate-300 hover:bg-slate-700"
            >
              {ex}
            </button>
          ))}
        </div>

        <div className="flex flex-wrap gap-2">
          <select
            value={source}
            onChange={(e) => setSource(e.target.value as any)}
            className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
          >
            <option value="local">로컬 워크스페이스</option>
            <option value="google_drive">Google Drive</option>
          </select>
          <input
            value={command}
            onChange={(e) => setCommand(e.target.value)}
            placeholder="예: PDF만 찾아줘 / 선택한 파일에 접미사 _backup 추가"
            className="min-w-[240px] flex-1 rounded-xl border border-slate-700 bg-slate-950 px-3.5 py-2 text-sm text-white"
          />
          <button
            type="submit"
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-xl bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            <Send className="h-4 w-4" />
            {loading ? "분석 중..." : "계획 생성"}
          </button>
        </div>
      </form>

      {err && <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-300">{err}</div>}
      {msg && <div className="rounded-xl border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm text-emerald-300">{msg}</div>}

      {/* Validated JSON Plan Preview */}
      {plan && (
        <div className="space-y-4 rounded-2xl border border-indigo-500/40 bg-slate-900/80 p-5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Bot className="h-5 w-5 text-indigo-400" />
              <span className="text-base font-semibold text-white">검증된 작업 명세 미리보기</span>
            </div>
            <div className="flex flex-wrap gap-1.5 text-xs">
              <span className="rounded-full bg-indigo-500/20 px-2.5 py-0.5 text-indigo-300">{plan.complexity}</span>
              <span className="rounded-full bg-emerald-500/20 px-2.5 py-0.5 text-emerald-300">{plan.provider_used}</span>
              <span className="rounded-full bg-slate-800 px-2.5 py-0.5 font-mono text-slate-300">{plan.correlation_id}</span>
            </div>
          </div>

          {plan.spec.warnings.length > 0 && (
            <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-200 space-y-1">
              <div className="flex items-center gap-1.5 font-semibold">
                <ShieldAlert className="h-4 w-4" /> 보안·안전 경고
              </div>
              {plan.spec.warnings.map((w, i) => (
                <div key={i}>• {w}</div>
              ))}
            </div>
          )}

          <pre className="max-h-72 overflow-auto rounded-xl bg-slate-950 p-3 text-xs text-emerald-300">
            {JSON.stringify(plan.spec, null, 2)}
          </pre>

          <button
            onClick={() => (plan.spec.requires_confirmation ? setConfirmOpen(true) : executePlan(true))}
            className="inline-flex items-center gap-2 rounded-xl bg-emerald-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-emerald-500"
          >
            <Play className="h-4 w-4" />
            {plan.spec.requires_confirmation ? "위험 작업 확인 후 실행" : "작업 큐에 등록 및 실행"}
          </button>
        </div>
      )}

      <ConfirmModal
        open={confirmOpen}
        title="고위험 / 변경 작업 승인"
        description={`이 작업 계획은 파일 변경·확장자 변경·업로드·삭제 또는 덮어쓰기를 포함합니다.\n요약: ${plan?.spec.summary || plan?.spec.intent}\n정말로 실행하시겠습니까?`}
        confirmLabel="승인 후 실행"
        onConfirm={() => executePlan(true)}
        onCancel={() => setConfirmOpen(false)}
      />
    </div>
  );
};
