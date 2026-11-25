import React, { useState } from "react";
import { Bot, MessageSquare, Play, Send, ShieldAlert, Sparkles, Terminal } from "lucide-react";
import { api } from "../api/client";
import { ConfirmModal } from "../components/ConfirmModal";
import { AIPlanResponse, FileItem, PrivacySettings } from "../types";

interface AiChatViewProps {
  localFiles: FileItem[];
  privacy: PrivacySettings | null;
  onTaskExecuted: () => void;
}

type ProviderChoice = "openrouter" | "gemini" | "kimi" | "openai";

const PROVIDER_CARDS: Array<{
  id: ProviderChoice;
  label: string;
  defaultModel: string;
  presets: string[];
}> = [
  {
    id: "openrouter",
    label: "1. OpenRouter",
    defaultModel: "openrouter/free",
    presets: ["openrouter/free", "meta-llama/llama-3.3-70b-instruct", "openai/gpt-4o-mini"],
  },
  {
    id: "gemini",
    label: "2. Gemini",
    defaultModel: "gemini-3.8-flash",
    presets: ["gemini-3.8-flash", "gemini-2.5-flash"],
  },
  {
    id: "kimi",
    label: "3. Kimi",
    defaultModel: "moonshot-v1-8k",
    presets: ["moonshot-v1-8k", "moonshot-v1-32k", "kimi-k2-0711-preview"],
  },
  {
    id: "openai",
    label: "4. GPT",
    defaultModel: "gpt-4o-mini",
    presets: ["gpt-4o-mini", "gpt-4o"],
  },
];

export const AiChatView: React.FC<AiChatViewProps> = ({ localFiles, privacy, onTaskExecuted }) => {
  const [mode, setMode] = useState<"chat" | "task">("chat");
  const [selectedProvider, setSelectedProvider] = useState<ProviderChoice>("openrouter");
  const [useAutoRouting, setUseAutoRouting] = useState(false);
  const [customModel, setCustomModel] = useState("");

  // Direct Chat state
  const [chatHistory, setChatHistory] = useState<Array<{ role: "user" | "assistant"; content: string; meta?: string }>>([]);
  const [includeFiles, setIncludeFiles] = useState(false);

  // Task Spec state
  const [command, setCommand] = useState("");
  const [source, setSource] = useState<"local" | "google_drive">("local");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [plan, setPlan] = useState<AIPlanResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const activeProviderMeta = PROVIDER_CARDS.find((p) => p.id === selectedProvider)!;
  const examples = [
    "PDF만 찾아줘",
    "10MB 초과 파일 보여줘",
    "선택한 파일에 접미사 _backup 추가",
    "선택한 파일 mp4 압축해줘",
  ];

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!command.trim()) return;
    setLoading(true);
    setErr(null);
    setMsg(null);

    if (mode === "chat") {
      const userMsg = command.trim();
      setCommand("");
      const nextHistory = [...chatHistory, { role: "user" as const, content: userMsg }];
      setChatHistory(nextHistory);
      try {
        const res = await api.chatWithAi({
          provider_id: selectedProvider,
          model_id: customModel.trim() || undefined,
          message: userMsg,
          history: nextHistory.map((m) => ({ role: m.role, content: m.content })),
          include_file_context: includeFiles,
        });
        setChatHistory([
          ...nextHistory,
          {
            role: "assistant",
            content: res.reply,
            meta: `${res.provider_id} · ${res.model_id} · ${res.latency_ms}ms`,
          },
        ]);
      } catch (e: any) {
        setErr(e.message);
      } finally {
        setLoading(false);
      }
      return;
    }

    // Task planning mode
    setPlan(null);
    try {
      const res = await api.planAiCommand(
        command,
        selectedIds,
        source,
        useAutoRouting ? "auto" : selectedProvider,
        useAutoRouting ? undefined : customModel.trim() || undefined
      );
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
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-2xl font-bold text-white">AI 모델별 채팅 &amp; 자동화 명령</h2>
          <p className="text-sm text-slate-400">
            OpenRouter · Gemini · Kimi · GPT 중 원하는 모델을 직접 선택해 대화하거나 파일 작업 계획을 생성합니다.
          </p>
        </div>

        <div className="flex rounded-xl border border-slate-800 bg-slate-900 p-1">
          <button
            type="button"
            onClick={() => setMode("chat")}
            className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold ${
              mode === "chat" ? "bg-indigo-600 text-white" : "text-slate-400"
            }`}
          >
            <MessageSquare className="h-3.5 w-3.5" /> 일반 AI 채팅
          </button>
          <button
            type="button"
            onClick={() => setMode("task")}
            className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold ${
              mode === "task" ? "bg-emerald-600 text-white" : "text-slate-400"
            }`}
          >
            <Terminal className="h-3.5 w-3.5" /> 파일 작업 계획
          </button>
        </div>
      </div>

      {/* Model Selector Bar */}
      <div className="space-y-3 rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-xs font-semibold text-slate-300">사용할 AI 모델 선택</span>
          {mode === "task" && (
            <label className="flex items-center gap-1.5 text-xs text-slate-300">
              <input
                type="checkbox"
                checked={useAutoRouting}
                onChange={(e) => setUseAutoRouting(e.target.checked)}
              />
              L0 규칙 우선 + 자동 Failover
            </label>
          )}
        </div>

        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {PROVIDER_CARDS.map((p) => {
            const active = !useAutoRouting && selectedProvider === p.id;
            return (
              <button
                key={p.id}
                type="button"
                onClick={() => {
                  setUseAutoRouting(false);
                  setSelectedProvider(p.id);
                  setCustomModel("");
                }}
                className={`rounded-xl border px-3 py-2 text-left transition ${
                  active
                    ? "border-indigo-500 bg-indigo-500/20 text-white"
                    : "border-slate-800 bg-slate-950/70 text-slate-400 hover:border-slate-700"
                }`}
              >
                <div className="text-xs font-bold">{p.label}</div>
                <div className="truncate text-[11px] text-slate-400">{p.defaultModel}</div>
              </button>
            );
          })}
        </div>

        {!useAutoRouting && (
          <div className="flex flex-wrap items-center gap-2">
            <input
              value={customModel}
              onChange={(e) => setCustomModel(e.target.value)}
              placeholder={`모델 ID 직접 입력 (기본: ${activeProviderMeta.defaultModel})`}
              className="min-w-[220px] flex-1 rounded-xl border border-slate-700 bg-slate-950 px-3 py-1.5 text-xs text-white"
            />
            {activeProviderMeta.presets.map((preset) => (
              <button
                key={preset}
                type="button"
                onClick={() => setCustomModel(preset)}
                className="rounded-lg border border-slate-700 bg-slate-800 px-2.5 py-1 text-[11px] text-slate-300 hover:bg-slate-700"
              >
                {preset}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Privacy Banner */}
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-800 bg-slate-900/50 px-4 py-2 text-xs text-slate-300">
        <div className="flex items-center gap-2">
          <Sparkles className="h-3.5 w-3.5 text-indigo-400" />
          <span>
            파일명 AI 전송: <strong>{privacy?.ai_send_filenames ? "허용됨" : "마스킹 차단(안전)"}</strong>
          </span>
        </div>
        {mode === "chat" && (
          <label className="flex items-center gap-1.5 text-xs text-slate-300">
            <input
              type="checkbox"
              checked={includeFiles}
              onChange={(e) => setIncludeFiles(e.target.checked)}
            />
            워크스페이스 파일 메타데이터 컨텍스트 포함
          </label>
        )}
      </div>

      {mode === "chat" ? (
        <div className="space-y-3 rounded-2xl border border-slate-800 bg-slate-900/60 p-4">
          <div className="max-h-96 space-y-3 overflow-y-auto pr-1">
            {chatHistory.length === 0 && (
              <div className="py-8 text-center text-xs text-slate-400">
                위에서 모델(OpenRouter / Gemini / Kimi / GPT)을 선택하고 대화를 시작하세요.
              </div>
            )}
            {chatHistory.map((m, idx) => (
              <div
                key={idx}
                className={`rounded-xl p-3 text-sm ${
                  m.role === "user"
                    ? "ml-8 bg-indigo-600/20 text-indigo-100"
                    : "mr-8 border border-slate-800 bg-slate-950/80 text-slate-200"
                }`}
              >
                <div className="whitespace-pre-wrap leading-relaxed">{m.content}</div>
                {m.meta && <div className="mt-1.5 text-[11px] text-slate-500">{m.meta}</div>}
              </div>
            ))}
          </div>
        </div>
      ) : (
        localFiles.length > 0 && (
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
        )
      )}

      {/* Prompt Input */}
      <form onSubmit={handleSubmit} className="space-y-3 rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
        {mode === "task" && (
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
        )}

        <div className="flex flex-wrap gap-2">
          {mode === "task" && (
            <select
              value={source}
              onChange={(e) => setSource(e.target.value as any)}
              className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
            >
              <option value="local">로컬 워크스페이스</option>
              <option value="google_drive">Google Drive</option>
            </select>
          )}
          <input
            value={command}
            onChange={(e) => setCommand(e.target.value)}
            placeholder={
              mode === "chat"
                ? `${activeProviderMeta.label}에게 메시지 보내기...`
                : "예: PDF만 찾아줘 / 선택한 파일에 접미사 _backup 추가"
            }
            className="min-w-[240px] flex-1 rounded-xl border border-slate-700 bg-slate-950 px-3.5 py-2 text-sm text-white"
          />
          <button
            type="submit"
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-xl bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            <Send className="h-4 w-4" />
            {loading ? "전송 중..." : mode === "chat" ? "보내기" : "계획 생성"}
          </button>
        </div>
      </form>

      {err && <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-300">{err}</div>}
      {msg && <div className="rounded-xl border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm text-emerald-300">{msg}</div>}

      {/* Validated JSON Plan Preview */}
      {mode === "task" && plan && (
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
