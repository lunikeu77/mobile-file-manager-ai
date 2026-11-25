import React, { useEffect, useState } from "react";
import { CheckCircle2, Cloud, KeyRound } from "lucide-react";
import { api } from "../api/client";
import { DriveConfig, ProviderSetting } from "../types";
import { StatusBadge } from "../components/StatusBadge";

export const AiSettingsView: React.FC<{ onRefreshSystem: () => void }> = ({ onRefreshSystem }) => {
  const [providers, setProviders] = useState<ProviderSetting[]>([]);
  const [keyInputs, setKeyInputs] = useState<Record<string, string>>({});
  const [driveCfg, setDriveCfg] = useState<DriveConfig | null>(null);
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [scopeMode, setScopeMode] = useState<"drive.file" | "drive.readonly" | "drive">("drive.file");
  const [usageEvents, setUsageEvents] = useState<any[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const loadAll = async () => {
    const [p, d, u] = await Promise.all([api.listProviders(), api.getDriveConfig(), api.getAiUsage()]);
    setProviders(p);
    setDriveCfg(d);
    setScopeMode(d.scope_mode);
    setUsageEvents(u.events || []);
  };

  useEffect(() => {
    loadAll().catch((e) => setErr(e.message));
  }, []);

  const saveProvider = async (p: ProviderSetting) => {
    setErr(null);
    setMsg(null);
    try {
      const payload: Record<string, unknown> = {
        enabled: p.enabled,
        priority: p.priority,
        model_id: p.model_id,
        free_only: p.free_only,
        monthly_budget_usd: p.monthly_budget_usd,
      };
      if (keyInputs[p.provider_id] !== undefined && keyInputs[p.provider_id].trim() !== "") {
        payload.api_key = keyInputs[p.provider_id].trim();
      }
      await api.updateProvider(p.provider_id, payload);
      setKeyInputs((prev) => ({ ...prev, [p.provider_id]: "" }));
      setMsg(`${p.provider_id} 설정이 암호화 저장되었습니다.`);
      await loadAll();
      onRefreshSystem();
    } catch (e: any) {
      setErr(e.message);
    }
  };

  const testProvider = async (providerId: string) => {
    setErr(null);
    setMsg(null);
    try {
      const res = await api.testProvider(providerId);
      setMsg(`${providerId} 연결 확인 성공: ${JSON.stringify(res)}`);
      await loadAll();
    } catch (e: any) {
      setErr(`${providerId} 연결 테스트 실패: ${e.message}`);
      await loadAll();
    }
  };

  const saveDriveOAuth = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    setMsg(null);
    try {
      await api.saveDriveConfig({ client_id: clientId, client_secret: clientSecret, scope_mode: scopeMode });
      setClientId("");
      setClientSecret("");
      setMsg("Google OAuth Client 설정이 암호화 저장되었습니다.");
      await loadAll();
    } catch (e: any) {
      setErr(e.message);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold text-white">AI 공급자 및 Google Drive 설정</h2>
        <p className="text-sm text-slate-400">
          API 키와 OAuth 비밀값은 Fernet 암호화 후 저장되며 UI에는 마스킹 상태만 표시됩니다.
        </p>
      </div>

      {msg && <div className="rounded-xl border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm text-emerald-300">{msg}</div>}
      {err && <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-300">{err}</div>}

      {/* AI Providers */}
      <div className="space-y-4">
        <h3 className="flex items-center gap-2 text-lg font-semibold text-white">
          <KeyRound className="h-5 w-5 text-indigo-400" /> 다중 AI 공급자 라우팅 &amp; API 키
        </h3>
        {providers.map((p) => (
          <div key={p.provider_id} className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5 space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="text-base font-bold uppercase text-white">{p.provider_id}</span>
                <StatusBadge status={p.quota_state} />
                {p.key_configured && (
                  <span className="font-mono text-xs text-emerald-300">등록됨 ({p.key_masked})</span>
                )}
              </div>
              <label className="flex items-center gap-2 text-xs text-slate-300">
                <input
                  type="checkbox"
                  checked={p.enabled}
                  onChange={(e) =>
                    setProviders((prev) =>
                      prev.map((x) => (x.provider_id === p.provider_id ? { ...x, enabled: e.target.checked } : x))
                    )
                  }
                />
                활성화
              </label>
            </div>

            {p.provider_id === "openai" && (
              <div className="text-xs text-amber-300">
                안내: OpenAI API 과금은 ChatGPT Free/Plus 구독과 별개이며, 입력한 API 키의 조직 크레딧 기준으로 동작합니다.
              </div>
            )}

            <div className="grid gap-2 sm:grid-cols-4">
              <div>
                <label className="text-[11px] text-slate-400">우선순위</label>
                <input
                  type="number"
                  min={1}
                  max={10}
                  value={p.priority}
                  onChange={(e) =>
                    setProviders((prev) =>
                      prev.map((x) => (x.provider_id === p.provider_id ? { ...x, priority: Number(e.target.value) } : x))
                    )
                  }
                  className="mt-1 w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-white"
                />
              </div>
              <div className="sm:col-span-2">
                <label className="text-[11px] text-slate-400">모델 ID</label>
                <input
                  value={p.model_id}
                  onChange={(e) =>
                    setProviders((prev) =>
                      prev.map((x) => (x.provider_id === p.provider_id ? { ...x, model_id: e.target.value } : x))
                    )
                  }
                  className="mt-1 w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-white"
                />
              </div>
              <div>
                <label className="text-[11px] text-slate-400">월 예산 상한 ($)</label>
                <input
                  type="number"
                  step="0.5"
                  min={0}
                  value={p.monthly_budget_usd}
                  onChange={(e) =>
                    setProviders((prev) =>
                      prev.map((x) =>
                        x.provider_id === p.provider_id ? { ...x, monthly_budget_usd: Number(e.target.value) } : x
                      )
                    )
                  }
                  className="mt-1 w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-white"
                />
              </div>
            </div>

            <div className="flex flex-wrap gap-2">
              <input
                type="password"
                value={keyInputs[p.provider_id] || ""}
                onChange={(e) => setKeyInputs((prev) => ({ ...prev, [p.provider_id]: e.target.value }))}
                placeholder="새 API 키 입력 (비워두면 기존 키 유지)"
                className="min-w-[220px] flex-1 rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-white"
              />
              <button
                onClick={() => saveProvider(p)}
                className="rounded-xl bg-indigo-600 px-4 py-2 text-xs font-semibold text-white"
              >
                저장
              </button>
              <button
                onClick={() => testProvider(p.provider_id)}
                className="rounded-xl border border-slate-700 bg-slate-800 px-4 py-2 text-xs text-slate-200"
              >
                키 검증 테스트
              </button>
            </div>
            {p.last_error_message && (
              <div className="text-xs text-rose-400">
                최근 오류 ({p.last_error_code}): {p.last_error_message}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Google Drive OAuth */}
      <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-lg font-semibold text-white">
            <Cloud className="h-5 w-5 text-emerald-400" /> Google Drive OAuth 2.0 (PKCE)
          </div>
          {driveCfg?.connected ? (
            <span className="inline-flex items-center gap-1 text-xs font-semibold text-emerald-300">
              <CheckCircle2 className="h-4 w-4" /> 연결됨
            </span>
          ) : (
            <span className="text-xs text-slate-400">미연동</span>
          )}
        </div>

        <p className="text-xs text-slate-300">{driveCfg?.scope_notice}</p>
        <div className="text-xs text-slate-400">
          Redirect URI (Google Cloud Console에 등록):{" "}
          <code className="rounded bg-slate-950 px-2 py-1 text-sky-300">{driveCfg?.redirect_uri}</code>
        </div>

        <form onSubmit={saveDriveOAuth} className="grid gap-2 sm:grid-cols-3">
          <input
            value={clientId}
            onChange={(e) => setClientId(e.target.value)}
            placeholder="Google Client ID"
            required
            className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-white"
          />
          <input
            type="password"
            value={clientSecret}
            onChange={(e) => setClientSecret(e.target.value)}
            placeholder="Google Client Secret"
            required
            className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-white"
          />
          <select
            value={scopeMode}
            onChange={(e) => setScopeMode(e.target.value as any)}
            className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-white"
          >
            <option value="drive.file">drive.file (최소 권한 권장)</option>
            <option value="drive.readonly">drive.readonly (전체 읽기)</option>
            <option value="drive">drive (전체 읽기/쓰기 - 검수 필요)</option>
          </select>
          <button type="submit" className="rounded-xl bg-emerald-600 px-4 py-2 text-xs font-semibold text-white">
            OAuth 자격증명 저장
          </button>
        </form>

        <div className="flex flex-wrap gap-2">
          <button
            onClick={async () => {
              try {
                const { auth_url } = await api.startDriveOAuth();
                window.location.href = auth_url;
              } catch (e: any) {
                setErr(e.message);
              }
            }}
            className="rounded-xl bg-sky-600 px-4 py-2 text-xs font-semibold text-white"
          >
            Google 계정 연결하기
          </button>
          {driveCfg?.connected && (
            <button
              onClick={() => api.disconnectDrive().then(loadAll)}
              className="rounded-xl border border-rose-500/40 px-4 py-2 text-xs text-rose-300"
            >
              Drive 연결 해제
            </button>
          )}
        </div>
      </div>

      {/* Recent Usage Events */}
      <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5">
        <h3 className="mb-3 text-base font-semibold text-white">최근 AI 호출 이벤트</h3>
        {usageEvents.length === 0 ? (
          <p className="text-xs text-slate-400">기록된 외부 AI 호출이 없습니다.</p>
        ) : (
          <div className="space-y-1.5 text-xs">
            {usageEvents.slice(0, 10).map((ev) => (
              <div key={ev.event_id} className="flex flex-wrap justify-between rounded-lg bg-slate-950 px-3 py-2 text-slate-300">
                <span>
                  {ev.provider_id} ({ev.model_id}) · HTTP {ev.status_code} · {ev.latency_ms}ms
                </span>
                <span className="font-mono text-slate-500">{ev.correlation_id}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
