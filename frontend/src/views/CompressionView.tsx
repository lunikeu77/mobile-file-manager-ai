import React, { useEffect, useState } from "react";
import { AlertCircle, Film, FileText, Play } from "lucide-react";
import { api, formatBytes } from "../api/client";
import { FileItem } from "../types";

interface CompressionViewProps {
  localFiles: FileItem[];
  preselectedFile: FileItem | null;
  onTaskCreated: () => void;
}

export const CompressionView: React.FC<CompressionViewProps> = ({
  localFiles,
  preselectedFile,
  onTaskCreated,
}) => {
  const [tab, setTab] = useState<"mp4" | "pdf">(
    preselectedFile?.name.toLowerCase().endsWith(".pdf") ? "pdf" : "mp4"
  );
  const [selectedFileId, setSelectedFileId] = useState<string>(preselectedFile?.file_id || "");
  const [videoPreset, setVideoPreset] = useState<"fast_compat" | "balanced" | "high_compression">("balanced");
  const [videoCodec, setVideoCodec] = useState<"h264" | "hevc">("h264");
  const [targetMb, setTargetMb] = useState("");
  const [probeInfo, setProbeInfo] = useState<any>(null);
  const [estimate, setEstimate] = useState<any>(null);

  const [pdfPreset, setPdfPreset] = useState<"screen" | "ebook" | "printer" | "structure_only">("ebook");
  const [preserveInteractive, setPreserveInteractive] = useState(false);

  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const mp4Files = localFiles.filter((f) => /\.(mp4|mov|mkv)$/i.test(f.name));
  const pdfFiles = localFiles.filter((f) => /\.pdf$/i.test(f.name));

  useEffect(() => {
    if (preselectedFile) {
      setSelectedFileId(preselectedFile.file_id);
      if (preselectedFile.name.toLowerCase().endsWith(".pdf")) setTab("pdf");
      else if (/\.(mp4|mov|mkv)$/i.test(preselectedFile.name)) setTab("mp4");
    }
  }, [preselectedFile]);

  const handleTabSwitch = (nextTab: "mp4" | "pdf") => {
    setTab(nextTab);
    setProbeInfo(null);
    setEstimate(null);
    setErr(null);
    const validList = nextTab === "mp4" ? mp4Files : pdfFiles;
    if (!validList.some((f) => f.file_id === selectedFileId)) {
      setSelectedFileId("");
    }
  };

  const runProbe = async () => {
    if (!selectedFileId) return;
    setErr(null);
    try {
      const info = await api.probeVideo(selectedFileId);
      setProbeInfo(info);
      const targetBytes = targetMb ? Math.round(Number(targetMb) * 1024 * 1024) : undefined;
      const est = await api.estimateVideo(Math.max(info.duration_sec, 1), videoPreset, targetBytes);
      setEstimate(est);
    } catch (e: any) {
      setErr(e.message);
    }
  };

  const startMp4Job = async () => {
    if (!selectedFileId) return;
    setErr(null);
    setMsg(null);
    try {
      const targetBytes = targetMb ? Math.round(Number(targetMb) * 1024 * 1024) : null;
      const task = await api.compressVideo({
        file_id: selectedFileId,
        preset: videoPreset,
        video_codec: videoCodec,
        target_size_bytes: targetBytes,
      });
      setMsg(`MP4 백그라운드 압축 작업이 등록되었습니다 (${task.task_id}). 브라우저 탭을 닫아도 서버에서 계속 실행됩니다.`);
      onTaskCreated();
    } catch (e: any) {
      setErr(e.message);
    }
  };

  const startPdfJob = async () => {
    if (!selectedFileId) return;
    setErr(null);
    setMsg(null);
    try {
      const task = await api.compressPdf({
        file_id: selectedFileId,
        preset: pdfPreset,
        preserve_interactive: preserveInteractive,
      });
      setMsg(`PDF 백그라운드 압축 작업이 등록되었습니다 (${task.task_id}). 원본 파일은 그대로 보존됩니다.`);
      onTaskCreated();
    } catch (e: any) {
      setErr(e.message);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold text-white">서버 사이드 압축 설정</h2>
        <p className="text-sm text-slate-400">
          브라우저 탭을 닫아도 서버 백그라운드 워커(동시 1개 제한, 스트리밍 파일 처리)가 끝까지 실행하며 원본을 보존합니다.
        </p>
      </div>

      <div className="flex gap-2">
        <button
          onClick={() => handleTabSwitch("mp4")}
          className={`inline-flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-semibold ${
            tab === "mp4" ? "bg-indigo-600 text-white" : "bg-slate-900 text-slate-400"
          }`}
        >
          <Film className="h-4 w-4" />
          MP4 압축 (FFmpeg)
        </button>
        <button
          onClick={() => handleTabSwitch("pdf")}
          className={`inline-flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-semibold ${
            tab === "pdf" ? "bg-emerald-600 text-white" : "bg-slate-900 text-slate-400"
          }`}
        >
          <FileText className="h-4 w-4" />
          PDF 압축 (pikepdf / Ghostscript)
        </button>
      </div>

      {msg && <div className="rounded-xl border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm text-emerald-300">{msg}</div>}
      {err && <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-300">{err}</div>}

      {tab === "mp4" ? (
        <div className="space-y-4 rounded-2xl border border-slate-800 bg-slate-900/70 p-5">
          <div>
            <label className="block text-xs font-medium text-slate-400">대상 MP4 파일</label>
            <select
              value={selectedFileId}
              onChange={(e) => setSelectedFileId(e.target.value)}
              className="mt-1 w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2.5 text-sm text-white"
            >
              <option value="">로컬 MP4 파일을 선택하세요...</option>
              {mp4Files.map((f) => (
                <option key={f.file_id} value={f.file_id}>
                  {f.name} ({formatBytes(f.size)})
                </option>
              ))}
            </select>
          </div>

          <div className="grid gap-3 sm:grid-cols-3">
            <div>
              <label className="block text-xs font-medium text-slate-400">압축 프리셋</label>
              <select
                value={videoPreset}
                onChange={(e) => setVideoPreset(e.target.value as any)}
                className="mt-1 w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
              >
                <option value="fast_compat">fast_compat (고속·호환성 우선)</option>
                <option value="balanced">balanced (균형)</option>
                <option value="high_compression">high_compression (고압축·720p 상한)</option>
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400">비디오 코덱</label>
              <select
                value={videoCodec}
                onChange={(e) => setVideoCodec(e.target.value as any)}
                className="mt-1 w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
              >
                <option value="h264">H.264 (기본 호환성)</option>
                <option value="hevc">HEVC / H.265 (고효율)</option>
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400">목표 용량 MB (선택)</label>
              <input
                type="number"
                step="0.5"
                min="0.1"
                value={targetMb}
                onChange={(e) => setTargetMb(e.target.value)}
                placeholder="예: 10 (비우면 CRF)"
                className="mt-1 w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
              />
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            <button
              onClick={runProbe}
              disabled={!selectedFileId}
              className="rounded-xl border border-slate-700 bg-slate-800 px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
            >
              ffprobe 분석 &amp; 용량 추정
            </button>
            <button
              onClick={startMp4Job}
              disabled={!selectedFileId}
              className="inline-flex items-center gap-2 rounded-xl bg-indigo-600 px-5 py-2 text-sm font-semibold text-white hover:bg-indigo-500 disabled:opacity-40"
            >
              <Play className="h-4 w-4" />
              백그라운드 MP4 압축 시작
            </button>
          </div>

          {probeInfo && (
            <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-4 text-xs text-slate-300 space-y-1">
              <div>
                해상도: {probeInfo.width}x{probeInfo.height} · 재생시간: {probeInfo.duration_sec.toFixed(2)}초 · 코덱: {probeInfo.video_codec}
              </div>
              {estimate && (
                <>
                  <div>
                    추정 오디오 비트레이트: {estimate.audio_kbps} kbps · 추정 비디오 비트레이트:{" "}
                    {estimate.estimated_video_kbps ? `${estimate.estimated_video_kbps} kbps` : `CRF ${estimate.crf}`}
                  </div>
                  <div className="text-amber-300">{estimate.disclaimer}</div>
                </>
              )}
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-4 rounded-2xl border border-slate-800 bg-slate-900/70 p-5">
          <div>
            <label className="block text-xs font-medium text-slate-400">대상 PDF 파일</label>
            <select
              value={selectedFileId}
              onChange={(e) => setSelectedFileId(e.target.value)}
              className="mt-1 w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2.5 text-sm text-white"
            >
              <option value="">로컬 PDF 파일을 선택하세요...</option>
              {pdfFiles.map((f) => (
                <option key={f.file_id} value={f.file_id}>
                  {f.name} ({formatBytes(f.size)})
                </option>
              ))}
            </select>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label className="block text-xs font-medium text-slate-400">PDF 최적화 프리셋</label>
              <select
                value={pdfPreset}
                onChange={(e) => setPdfPreset(e.target.value as any)}
                className="mt-1 w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
              >
                <option value="screen">screen (72dpi 최소 용량)</option>
                <option value="ebook">ebook (150dpi 균형)</option>
                <option value="printer">printer (300dpi 고화질)</option>
                <option value="structure_only">structure_only (pikepdf 구조/스트림 무손실 최적화)</option>
              </select>
            </div>
            <label className="flex items-center gap-2 pt-5 text-xs text-slate-300">
              <input
                type="checkbox"
                checked={preserveInteractive}
                onChange={(e) => setPreserveInteractive(e.target.checked)}
                className="h-4 w-4"
              />
              전자서명·폼·링크 보존 우선 (Ghostscript 평탄화 건너뛰고 pikepdf만 사용)
            </label>
          </div>

          <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-amber-200 space-y-1">
            <div className="flex items-center gap-1.5 font-semibold">
              <AlertCircle className="h-3.5 w-3.5" /> PDF 압축 주의사항 및 오픈소스 라이선스
            </div>
            <p>• 암호화된 PDF는 비밀번호 해제 전까지 압축이 거부되며 원본이 유지됩니다.</p>
            <p>• 텍스트 중심 PDF는 압축률이 낮을 수 있으며, Ghostscript(/screen, /ebook) 사용 시 전자서명·폼·투명도가 평탄화될 수 있습니다.</p>
            <p>• 엔진 라이선스: pikepdf (MPL-2.0), pypdf (BSD-3-Clause), Ghostscript (AGPL-3.0, 상용 재배포 시 라이선스 검토 필요).</p>
          </div>

          <button
            onClick={startPdfJob}
            disabled={!selectedFileId}
            className="inline-flex items-center gap-2 rounded-xl bg-emerald-600 px-5 py-2 text-sm font-semibold text-white hover:bg-emerald-500 disabled:opacity-40"
          >
            <Play className="h-4 w-4" />
            백그라운드 PDF 압축 시작
          </button>
        </div>
      )}
    </div>
  );
};
