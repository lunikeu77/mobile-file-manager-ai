import React, { useEffect, useState } from "react";
import { Cloud, CloudUpload, HardDrive, Search, Trash2, Upload, FileEdit, Minimize2 } from "lucide-react";
import { api, formatBytes } from "../api/client";
import { ConfirmModal } from "../components/ConfirmModal";
import { FileItem } from "../types";

interface ExplorerViewProps {
  onSelectFile: (file: FileItem) => void;
  onOpenCompression: (file: FileItem) => void;
  onRefreshSystem: () => void;
}

export const ExplorerView: React.FC<ExplorerViewProps> = ({
  onSelectFile,
  onOpenCompression,
  onRefreshSystem,
}) => {
  const [source, setSource] = useState<"local" | "google_drive">("local");
  const [files, setFiles] = useState<FileItem[]>([]);
  const [query, setQuery] = useState("");
  const [extFilter, setExtFilter] = useState("");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [batchSuffix, setBatchSuffix] = useState("_renamed");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [nextPageToken, setNextPageToken] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<FileItem | null>(null);

  const loadFiles = async (pageToken?: string) => {
    setLoading(true);
    setError(null);
    try {
      if (source === "local") {
        const items = await api.listFiles({
          source: "local",
          query: query || undefined,
          extension: extFilter || undefined,
        });
        setFiles(items);
        setNextPageToken(null);
      } else {
        const res = await api.listDriveFiles(query || undefined, pageToken);
        const mapped: FileItem[] = (res.files || []).map((f: any) => ({
          file_id: f.id,
          source: "google_drive",
          name: f.name,
          mime_type: f.mimeType || "application/octet-stream",
          size: Number(f.size || 0),
          modified_time: f.modifiedTime || "",
        }));
        setFiles(pageToken ? [...files, ...mapped] : mapped);
        setNextPageToken(res.next_page_token || null);
      }
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    setSelectedIds([]);
    loadFiles();
  }, [source]);

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setLoading(true);
    setError(null);
    try {
      const uploaded = await api.uploadFile(file);
      if (source === "google_drive") {
        const res = await api.uploadToDrive(uploaded.file_id);
        setInfo(`Google Drive 업로드 완료: ${file.name} (ID: ${res.drive_file?.id})`);
      } else {
        setInfo(`로컬 워크스페이스 업로드 완료: ${file.name}`);
      }
      await loadFiles();
      onRefreshSystem();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
      e.target.value = "";
    }
  };

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  const handleBatchSuffix = async () => {
    if (selectedIds.length === 0 || !batchSuffix.trim()) return;
    setError(null);
    try {
      const items = selectedIds
        .map((fid) => {
          const f = files.find((x) => x.file_id === fid);
          if (!f) return null;
          const dot = f.name.lastIndexOf(".");
          const newName =
            dot > 0
              ? `${f.name.slice(0, dot)}${batchSuffix.trim()}${f.name.slice(dot)}`
              : `${f.name}${batchSuffix.trim()}`;
          return { file_id: fid, new_name: newName };
        })
        .filter((x): x is { file_id: string; new_name: string } => x !== null);
      if (items.length === 0) return;
      await api.batchRename(items);
      setInfo(`${items.length}개 파일 일괄 이름 변경 완료`);
      setSelectedIds([]);
      await loadFiles();
    } catch (err: any) {
      setError(err.message);
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    try {
      if (deleteTarget.source === "google_drive") {
        await api.deleteDriveFile(deleteTarget.file_id);
      } else {
        await api.deleteFile(deleteTarget.file_id);
      }
      setInfo(`삭제 완료: ${deleteTarget.name}`);
      setDeleteTarget(null);
      await loadFiles();
      onRefreshSystem();
    } catch (err: any) {
      setError(err.message);
      setDeleteTarget(null);
    }
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-2xl font-bold text-white">파일 탐색기</h2>
          <p className="text-sm text-slate-400">로컬 워크스페이스(원본/결과물) 및 Google Drive 통합 탐색</p>
        </div>

        <label className="inline-flex cursor-pointer items-center gap-2 rounded-xl bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-indigo-500">
          <Upload className="h-4 w-4" />
          {source === "google_drive" ? "PC → Google Drive 바로 업로드" : "파일 업로드 (로컬)"}
          <input type="file" className="hidden" onChange={handleUpload} />
        </label>
      </div>

      {/* Source Tabs */}
      <div className="flex gap-2">
        <button
          onClick={() => setSource("local")}
          className={`inline-flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-medium ${
            source === "local" ? "bg-indigo-600/25 text-indigo-300 ring-1 ring-indigo-500/40" : "bg-slate-900 text-slate-400"
          }`}
        >
          <HardDrive className="h-4 w-4" />
          로컬 워크스페이스
        </button>
        <button
          onClick={() => setSource("google_drive")}
          className={`inline-flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-medium ${
            source === "google_drive"
              ? "bg-emerald-600/25 text-emerald-300 ring-1 ring-emerald-500/40"
              : "bg-slate-900 text-slate-400"
          }`}
        >
          <Cloud className="h-4 w-4" />
          Google Drive
        </button>
      </div>

      {/* Search & Filter */}
      <div className="flex flex-wrap gap-2">
        <div className="relative min-w-[200px] flex-1">
          <Search className="absolute top-2.5 left-3 h-4 w-4 text-slate-400" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="파일명 검색..."
            className="w-full rounded-xl border border-slate-800 bg-slate-900 py-2 pr-3 pl-9 text-sm text-white placeholder-slate-500 focus:border-indigo-500 focus:outline-none"
          />
        </div>
        {source === "local" && (
          <input
            value={extFilter}
            onChange={(e) => setExtFilter(e.target.value)}
            placeholder="확장자 (예: mp4, pdf)"
            className="w-40 rounded-xl border border-slate-800 bg-slate-900 px-3 py-2 text-sm text-white placeholder-slate-500"
          />
        )}
        <button
          onClick={() => loadFiles()}
          className="rounded-xl border border-slate-700 bg-slate-800 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700"
        >
          조회
        </button>
      </div>

      {selectedIds.length > 0 && source === "local" && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-indigo-500/30 bg-indigo-500/10 p-3">
          <span className="text-xs font-semibold text-indigo-300">{selectedIds.length}개 선택됨</span>
          <input
            value={batchSuffix}
            onChange={(e) => setBatchSuffix(e.target.value)}
            className="rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1 text-xs text-white"
            placeholder="접미사 (예: _v2)"
          />
          <button
            onClick={handleBatchSuffix}
            className="rounded-lg bg-indigo-600 px-3 py-1 text-xs font-semibold text-white hover:bg-indigo-500"
          >
            일괄 접미사 추가
          </button>
        </div>
      )}

      {error && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-300">
          <span>{error}</span>
          {source === "google_drive" && (
            <button
              onClick={async () => {
                try {
                  const { auth_url } = await api.startDriveOAuth();
                  window.location.href = auth_url;
                } catch (e: any) {
                  setError(e.message);
                }
              }}
              className="rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-sky-500"
            >
              Google 계정으로 바로 연결하기
            </button>
          )}
        </div>
      )}
      {info && <div className="rounded-xl border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm text-emerald-300">{info}</div>}

      {/* File List */}
      <div className="space-y-2">
        {loading && <div className="text-sm text-slate-400">불러오는 중...</div>}
        {!loading && files.length === 0 && (
          <div className="rounded-2xl border border-dashed border-slate-800 p-8 text-center text-sm text-slate-400">
            표시할 파일이 없습니다. 상단 '파일 업로드' 버튼으로 파일을 추가하거나 URL 다운로드를 사용하세요.
          </div>
        )}
        {files.map((f) => (
          <div
            key={f.file_id}
            className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-800 bg-slate-900/70 p-3.5 hover:border-slate-700"
          >
            <div className="flex min-w-0 items-center gap-3">
              {source === "local" && (
                <input
                  type="checkbox"
                  checked={selectedIds.includes(f.file_id)}
                  onChange={() => toggleSelect(f.file_id)}
                  className="h-4 w-4 rounded border-slate-600 bg-slate-800"
                />
              )}
              <div className="min-w-0">
                <button
                  onClick={() => onSelectFile(f)}
                  className="truncate text-left text-sm font-semibold text-white hover:text-indigo-300"
                >
                  {f.name}
                </button>
                <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
                  <span>{formatBytes(f.size)}</span>
                  <span>·</span>
                  <span>{f.mime_type}</span>
                  {f.parent_id && (
                    <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-300">{f.parent_id}</span>
                  )}
                </div>
              </div>
            </div>

            <div className="flex items-center gap-1.5">
              <button
                onClick={() => onSelectFile(f)}
                className="rounded-lg border border-slate-700 px-2.5 py-1.5 text-xs text-slate-200 hover:bg-slate-800"
                title="상세 / 이름·확장자 변경"
              >
                <FileEdit className="h-3.5 w-3.5" />
              </button>
              {source === "local" && (
                <button
                  onClick={async () => {
                    setError(null);
                    try {
                      const res = await api.uploadToDrive(f.file_id);
                      setInfo(`Google Drive 업로드 완료: ${f.name} (ID: ${res.drive_file?.id})`);
                    } catch (e: any) {
                      setError(e.message);
                    }
                  }}
                  className="inline-flex items-center gap-1 rounded-lg border border-sky-500/40 bg-sky-500/10 px-2.5 py-1.5 text-xs text-sky-300 hover:bg-sky-500/20"
                  title="Google Drive 전용 폴더로 업로드"
                >
                  <CloudUpload className="h-3.5 w-3.5" />
                  Drive 업로드
                </button>
              )}
              {source === "local" && (f.name.toLowerCase().endsWith(".mp4") || f.name.toLowerCase().endsWith(".pdf")) && (
                <button
                  onClick={() => onOpenCompression(f)}
                  className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-2.5 py-1.5 text-xs text-emerald-300 hover:bg-emerald-500/20"
                  title="압축 설정"
                >
                  <Minimize2 className="h-3.5 w-3.5" />
                </button>
              )}
              {source === "google_drive" && (
                <>
                  {(f.name.toLowerCase().endsWith(".mp4") || f.name.toLowerCase().endsWith(".pdf")) && (
                    <button
                      onClick={async () => {
                        try {
                          const action = f.name.toLowerCase().endsWith(".pdf") ? "COMPRESS_PDF" : "COMPRESS_VIDEO";
                          const res = await api.runDriveRoundtrip(f.file_id, action);
                          setInfo(
                            `Drive 왕복 작업 시작 (${res.task?.task_id}): 임시 다운로드 → 압축 → 전용 폴더(${res.workspace_folder?.name}) 재업로드`
                          );
                          onRefreshSystem();
                        } catch (e: any) {
                          setError(e.message);
                        }
                      }}
                      className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-2.5 py-1.5 text-xs text-emerald-300"
                      title="Drive에서 임시 다운로드 → 압축 → 전용 폴더 재업로드"
                    >
                      압축 후 Drive 저장
                    </button>
                  )}
                  <button
                    onClick={async () => {
                      try {
                        await api.downloadFromDrive(f.file_id);
                        setInfo(`Drive에서 로컬 워크스페이스로 다운로드 완료: ${f.name}`);
                        onRefreshSystem();
                      } catch (e: any) {
                        setError(e.message);
                      }
                    }}
                    className="rounded-lg border border-sky-500/40 bg-sky-500/10 px-2.5 py-1.5 text-xs text-sky-300"
                  >
                    로컬로 받기
                  </button>
                </>
              )}
              <button
                onClick={() => setDeleteTarget(f)}
                className="rounded-lg border border-rose-500/30 px-2.5 py-1.5 text-xs text-rose-300 hover:bg-rose-500/15"
                title="삭제"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        ))}
      </div>

      {nextPageToken && (
        <button
          onClick={() => loadFiles(nextPageToken)}
          className="w-full rounded-xl border border-slate-700 py-2 text-sm text-slate-300 hover:bg-slate-800"
        >
          다음 페이지 불러오기
        </button>
      )}

      <ConfirmModal
        open={Boolean(deleteTarget)}
        title="파일 삭제 확인"
        description={`정말로 '${deleteTarget?.name}' 파일을 삭제하시겠습니까?\n삭제 작업은 되돌릴 수 없으며 명시적 승인 후에만 실행됩니다.`}
        confirmLabel="영구 삭제"
        onConfirm={confirmDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
};
