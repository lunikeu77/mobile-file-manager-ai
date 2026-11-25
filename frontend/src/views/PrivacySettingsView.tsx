import React, { useEffect, useState } from "react";
import { ShieldCheck, Trash2 } from "lucide-react";
import { api, formatBytes } from "../api/client";
import { PrivacySettings } from "../types";

export const PrivacySettingsView: React.FC = () => {
  const [prefs, setPrefs] = useState<PrivacySettings | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = async () => {
    const data = await api.getPrivacy();
    setPrefs(data);
  };

  useEffect(() => {
    load().catch((e) => setErr(e.message));
  }, []);

  const updateField = async (patch: Record<string, unknown>) => {
    setErr(null);
    setMsg(null);
    try {
      const updated = await api.updatePrivacy(patch);
      setPrefs(updated);
      setMsg("개인정보 및 데이터 전송 정책이 저장되었습니다.");
    } catch (e: any) {
      setErr(e.message);
    }
  };

  const runCleanup = async () => {
    setErr(null);
    try {
      const res = await api.cleanupTemp();
      setMsg(`임시 파일 정리 완료: ${res.removed_count}개 삭제 (${formatBytes(res.freed_bytes)} 확보)`);
      await load();
    } catch (e: any) {
      setErr(e.message);
    }
  };

  if (!prefs) return <div className="text-sm text-slate-400">설정을 불러오는 중...</div>;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold text-white">개인정보 및 데이터 전송 설정</h2>
        <p className="text-sm text-slate-400">
          AI 공급자 호출 시 파일 본문 전송을 기본 차단하며, 파일명·메타데이터 전송 여부와 임시 파일 정리 주기를 제어합니다.
        </p>
      </div>

      {msg && <div className="rounded-xl border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm text-emerald-300">{msg}</div>}
      {err && <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-300">{err}</div>}

      <div className="space-y-4 rounded-2xl border border-slate-800 bg-slate-900/70 p-5">
        <label className="flex items-start justify-between gap-4">
          <div>
            <div className="text-sm font-semibold text-white">AI에 실제 파일명 전송 허용</div>
            <div className="text-xs text-slate-400">
              비활성화(기본값) 시 AI에게는 `[redacted_file_1]`처럼 마스킹된 ID와 MIME/크기만 전달됩니다.
            </div>
          </div>
          <input
            type="checkbox"
            checked={prefs.ai_send_filenames}
            onChange={(e) => updateField({ ai_send_filenames: e.target.checked })}
            className="mt-1 h-4 w-4"
          />
        </label>

        <label className="flex items-start justify-between gap-4 border-t border-slate-800 pt-4">
          <div>
            <div className="text-sm font-semibold text-white">AI에 파일 메타데이터(크기/MIME) 전달</div>
            <div className="text-xs text-slate-400">파일 본문 전체는 어떤 경우에도 AI 공급자에게 전송되지 않습니다.</div>
          </div>
          <input
            type="checkbox"
            checked={prefs.ai_send_metadata}
            onChange={(e) => updateField({ ai_send_metadata: e.target.checked })}
            className="mt-1 h-4 w-4"
          />
        </label>

        <label className="flex items-start justify-between gap-4 border-t border-slate-800 pt-4">
          <div>
            <div className="text-sm font-semibold text-white">무료 공급자 전용 모드 (OpenAI 유료 호출 차단)</div>
            <div className="text-xs text-slate-400">활성화 시 L0 로컬 규칙, OpenRouter 무료 모델, Gemini 무료 티어만 사용합니다.</div>
          </div>
          <input
            type="checkbox"
            checked={prefs.ai_free_only_mode}
            onChange={(e) => updateField({ ai_free_only_mode: e.target.checked })}
            className="mt-1 h-4 w-4"
          />
        </label>

        <label className="flex items-start justify-between gap-4 border-t border-slate-800 pt-4">
          <div>
            <div className="text-sm font-semibold text-white">429/5xx 장애 시 자동 Failover 활성화</div>
            <div className="text-xs text-slate-400">401/403 인증 오류는 무한 전환하지 않고 즉시 설정 오류로 표시합니다.</div>
          </div>
          <input
            type="checkbox"
            checked={prefs.ai_auto_failover}
            onChange={(e) => updateField({ ai_auto_failover: e.target.checked })}
            className="mt-1 h-4 w-4"
          />
        </label>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-800 pt-4">
          <div>
            <div className="text-sm font-semibold text-white">임시 파일 자동 정리 주기 (시간)</div>
            <div className="text-xs text-slate-400">마지막 정리: {prefs.last_cleanup_at || "기록 없음"}</div>
          </div>
          <div className="flex items-center gap-2">
            <input
              type="number"
              min={1}
              max={720}
              value={prefs.temp_cleanup_hours}
              onChange={(e) => updateField({ temp_cleanup_hours: Number(e.target.value) })}
              className="w-20 rounded-xl border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm text-white"
            />
            <button
              onClick={runCleanup}
              className="inline-flex items-center gap-1.5 rounded-xl border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-700"
            >
              <Trash2 className="h-3.5 w-3.5" /> 지금 정리
            </button>
          </div>
        </div>
      </div>

      {/* Security Disclosure */}
      <div className="rounded-2xl border border-indigo-500/30 bg-indigo-500/5 p-5 space-y-2">
        <div className="flex items-center gap-2 text-sm font-semibold text-indigo-300">
          <ShieldCheck className="h-4 w-4" /> 저장소 보안 및 한계 고지
        </div>
        <p className="text-xs leading-relaxed text-slate-300">{prefs.security_notice}</p>
        {prefs.using_ephemeral_secret && (
          <p className="text-xs font-semibold text-amber-300">
            경고: 현재 서버에 고정 `ENCRYPTION_SECRET` 환경 변수가 설정되지 않아 임시 로컬 키를 사용 중입니다. Render 배포 시 환경 변수에 `ENCRYPTION_SECRET`을 반드시 지정하세요.
          </p>
        )}
      </div>
    </div>
  );
};
