import React, { useEffect, useState } from "react";
import { AlertTriangle, CloudUpload, Download, Minimize2, Trash2 } from "lucide-react";
import { api, formatBytes } from "../api/client";
import { ConfirmModal } from "../components/ConfirmModal";
import { FileItem } from "../types";

interface FileDetailViewProps {
  file: FileItem | null;
  allLocalFiles: FileItem[];
  onUpdated: (file: FileItem | null) => void;
  onOpenCompression: (file: FileItem) => void;
}

export const FileDetailView: React.FC<FileDetailViewProps> = ({
  file,
  allLocalFiles,
  onUpdated,
  onOpenCompression,
}) => {
  const [newName, setNewName] = useState(file?.name || "");
  const [newExt, setNewExt] = useState("");
  const [ackNoReencode, setAckNoReencode] = useState(false);
  const [overwriteTargetId, setOverwriteTargetId] = useState("");
  const [modalMode, setModalMode] = useState<null | "delete" | "upload_drive" | "overwrite">(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setNewName(file?.name || "");
    setNewExt("");
    setAckNoReencode(false);
    setMsg(null);
    setErr(null);
  }, [file?.file_id, file?.name]);

  if (!file) {
    return (
      <div className="rounded-2xl border border-dashed border-slate-800 p-8 text-center text-sm text-slate-400">
        파일 탐색기에서 파일을 선택하면 메타데이터, 이름 변경, 확장자 문자열 변경 및 압축을 수행할 수 있습니다.
      </div>
    );
  }

  const handleRename = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    setMsg(null);
    try {
      const updated =
        file.source === "google_drive"
          ? await api.renameDriveFile(file.file_id, newName)
          : await api.renameFile(file.file_id, newName);
      setMsg("파일명 메타데이터 변경 완료 (원본 바이트 유지)");
      onUpdated({ ...file, ...updated, name: updated.name });
    } catch (e: any) {
      setErr(e.message);
    }
  };

  const handleChangeExt = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    setMsg(null);
    try {
      const updated =
        file.source === "google_drive"
          ? await api.changeDriveExtension(file.file_id, newExt, ackNoReencode)
          : await api.changeExtension(file.file_id, newExt, ackNoReencode);
      setMsg(updated.warning || "확장자 문자열만 변경되었습니다.");
      onUpdated({ ...file, ...updated, name: updated.name });
      setAckNoReencode(false);
    } catch (e: any) {
      setErr(e.message);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold text-white">파일 상세 정보</h2>
        <p className="text-sm text-slate-400">파일명·확장자 문자열 변경과 실제 포맷 재인코딩은 엄격히 분리되어 있습니다.</p>
      </div>

      {msg && <div className="rounded-xl border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm text-emerald-300">{msg}</div>}
      {err && <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-300">{err}</div>}

      <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5">
        <div className="grid gap-3 text-sm sm:grid-cols-2">
          <div>
            <span className="text-xs text-slate-400">파일명</span>
            <div className="font-semibold text-white">{file.name}</div>
          </div>
          <div>
            <span className="text-xs text-slate-400">소스 / ID</span>
            <div className="font-mono text-xs text-slate-300">
              {file.source} · {file.file_id}
            </div>
          </div>
          <div>
            <span className="text-xs text-slate-400">MIME Type</span>
            <div className="text-slate-200">{file.mime_type}</div>
          </div>
          <div>
            <span className="text-xs text-slate-400">크기 / 수정 시각</span>
            <div className="text-slate-200">
              {formatBytes(file.size)} · {file.modified_time}
            </div>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap gap-2">
          {file.source === "local" && (
            <>
              <a
                href={`/api/files/${encodeURIComponent(file.file_id)}/download`}
                className="inline-flex items-center gap-1.5 rounded-xl bg-slate-800 px-3.5 py-2 text-xs font-medium text-white hover:bg-slate-700"
              >
                <Download className="h-3.5 w-3.5" />
                다운로드
              </a>
              <button
                onClick={() => onOpenCompression(file)}
                className="inline-flex items-center gap-1.5 rounded-xl bg-emerald-600/20 px-3.5 py-2 text-xs font-medium text-emerald-300 ring-1 ring-emerald-500/30"
              >
                <Minimize2 className="h-3.5 w-3.5" />
                압축 설정으로 이동
              </button>
              <button
                onClick={() => setModalMode("upload_drive")}
                className="inline-flex items-center gap-1.5 rounded-xl bg-sky-600/20 px-3.5 py-2 text-xs font-medium text-sky-300 ring-1 ring-sky-500/30"
              >
                <CloudUpload className="h-3.5 w-3.5" />
                Google Drive 업로드
              </button>
            </>
          )}
          <button
            onClick={() => setModalMode("delete")}
            className="inline-flex items-center gap-1.5 rounded-xl bg-rose-600/20 px-3.5 py-2 text-xs font-medium text-rose-300 ring-1 ring-rose-500/30"
          >
            <Trash2 className="h-3.5 w-3.5" />
            삭제
          </button>
        </div>
      </div>

      {/* Rename */}
      <form onSubmit={handleRename} className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5 space-y-3">
        <h3 className="text-base font-semibold text-white">1. 파일명 변경 (메타데이터/파일명만 변경)</h3>
        <div className="flex flex-wrap gap-2">
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            className="min-w-[220px] flex-1 rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
          />
          <button type="submit" className="rounded-xl bg-indigo-600 px-4 py-2 text-sm font-semibold text-white">
            이름 변경
          </button>
        </div>
      </form>

      {/* Change Extension String (Local & Google Drive) */}
      <form onSubmit={handleChangeExt} className="rounded-2xl border border-amber-500/30 bg-slate-900/70 p-5 space-y-3">
        <div className="flex items-center gap-2 text-base font-semibold text-amber-300">
          <AlertTriangle className="h-4 w-4" />
          2. 확장자 문자열 변경 (실제 포맷 변환 아님)
        </div>
        <p className="text-xs leading-relaxed text-slate-300">
          주의: 확장자 변경은 파일명의 마지막 문자열만 바꾸며, <strong>파일 내용(바이트)은 변환되지 않습니다.</strong>{" "}
          잘못된 확장자로 변경하면 플레이어나 뷰어에서 형식이 오인되어 열리지 않을 수 있습니다. 실제 동영상/문서 변환·압축은 '압축 설정'을 사용하세요.
        </p>
        <div className="flex flex-wrap gap-2">
          <input
            value={newExt}
            onChange={(e) => setNewExt(e.target.value)}
            placeholder="새 확장자 (예: mkv, dat)"
            className="w-48 rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
          />
          <button type="submit" className="rounded-xl bg-amber-600 px-4 py-2 text-sm font-semibold text-white">
            확장자 문자열 변경
          </button>
        </div>
        <label className="flex items-center gap-2 text-xs text-slate-300">
          <input
            type="checkbox"
            checked={ackNoReencode}
            onChange={(e) => setAckNoReencode(e.target.checked)}
            className="h-4 w-4"
          />
          파일 바이트 내용이 변환되지 않으며 형식이 오인될 수 있음을 이해했습니다.
        </label>
      </form>

      {/* Overwrite existing file */}
      {file.source === "local" && allLocalFiles.length > 1 && (
        <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5 space-y-3">
          <h3 className="text-base font-semibold text-white">3. 기존 파일 덮어쓰기 (고위험)</h3>
          <div className="flex flex-wrap gap-2">
            <select
              value={overwriteTargetId}
              onChange={(e) => setOverwriteTargetId(e.target.value)}
              className="min-w-[220px] flex-1 rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
            >
              <option value="">덮어쓸 대상 파일 선택...</option>
              {allLocalFiles
                .filter((x) => x.file_id !== file.file_id)
                .map((x) => (
                  <option key={x.file_id} value={x.file_id}>
                    {x.name} ({formatBytes(x.size)})
                  </option>
                ))}
            </select>
            <button
              disabled={!overwriteTargetId}
              onClick={() => setModalMode("overwrite")}
              className="rounded-xl bg-rose-600/80 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40"
            >
              대상 덮어쓰기
            </button>
          </div>
        </div>
      )}

      <ConfirmModal
        open={modalMode === "delete"}
        title="파일 삭제 확인"
        description={`'${file.name}' 파일을 영구 삭제합니다. 계속하시겠습니까?`}
        confirmLabel="삭제 실행"
        onCancel={() => setModalMode(null)}
        onConfirm={async () => {
          setModalMode(null);
          try {
            if (file.source === "google_drive") await api.deleteDriveFile(file.file_id);
            else await api.deleteFile(file.file_id);
            onUpdated(null);
          } catch (e: any) {
            setErr(e.message);
          }
        }}
      />

      <ConfirmModal
        open={modalMode === "upload_drive"}
        title="Google Drive 업로드 확인"
        description={`로컬 파일 '${file.name}'을 연결된 Google Drive 내 드라이브(최상위)로 Resumable 업로드합니다. 계속하시겠습니까?`}
        danger={false}
        confirmLabel="업로드 시작"
        onCancel={() => setModalMode(null)}
        onConfirm={async () => {
          setModalMode(null);
          try {
            const res = await api.uploadToDrive(file.file_id);
            setMsg(`Google Drive 업로드 완료 (ID: ${res.drive_file?.id})`);
          } catch (e: any) {
            setErr(e.message);
          }
        }}
      />

      <ConfirmModal
        open={modalMode === "overwrite"}
        title="기존 파일 덮어쓰기 확인"
        description={`선택한 대상 파일의 내용이 '${file.name}'으로 덮어써집니다. 대상 파일의 기존 내용은 복구할 수 없습니다.`}
        confirmLabel="덮어쓰기 실행"
        onCancel={() => setModalMode(null)}
        onConfirm={async () => {
          setModalMode(null);
          try {
            await api.overwriteFile(file.file_id, overwriteTargetId);
            setMsg("대상 파일 덮어쓰기가 완료되었습니다.");
          } catch (e: any) {
            setErr(e.message);
          }
        }}
      />
    </div>
  );
};
