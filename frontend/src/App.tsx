import React, { useCallback, useEffect, useState } from "react";
import { api } from "./api/client";
import { Layout } from "./components/Layout";
import { DownloadJob, FileItem, PrivacySettings, SystemStatus, TaskItem, ViewKey } from "./types";
import { AiChatView } from "./views/AiChatView";
import { AiSettingsView } from "./views/AiSettingsView";
import { CompressionView } from "./views/CompressionView";
import { ExplorerView } from "./views/ExplorerView";
import { FileDetailView } from "./views/FileDetailView";
import { HistoryView } from "./views/HistoryView";
import { HomeView } from "./views/HomeView";
import { PrivacySettingsView } from "./views/PrivacySettingsView";
import { QueueView } from "./views/QueueView";
import { ScheduleView } from "./views/ScheduleView";

export default function App() {
  const [view, setView] = useState<ViewKey>("home");
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [localFiles, setLocalFiles] = useState<FileItem[]>([]);
  const [selectedFile, setSelectedFile] = useState<FileItem | null>(null);
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [downloads, setDownloads] = useState<DownloadJob[]>([]);
  const [privacy, setPrivacy] = useState<PrivacySettings | null>(null);
  const [driveBanner, setDriveBanner] = useState<{ type: "success" | "error"; text: string } | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const driveState = params.get("drive");
    if (driveState === "connected") {
      setDriveBanner({ type: "success", text: "Google Drive OAuth 계정이 성공적으로 연결되었습니다." });
      window.history.replaceState({}, "", window.location.pathname);
    } else if (driveState === "error") {
      const reason = params.get("reason") || "인증이 취소되었거나 실패했습니다.";
      setDriveBanner({ type: "error", text: `Google Drive 연결 실패: ${reason}` });
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, []);

  const refreshAll = useCallback(async () => {
    try {
      const [st, lf, tk, dl, pv] = await Promise.all([
        api.getSystemStatus(),
        api.listFiles({ source: "local" }),
        api.listTasks(),
        api.listDownloads(),
        api.getPrivacy(),
      ]);
      setStatus(st);
      setLocalFiles(lf);
      setTasks(tk);
      setDownloads(dl);
      setPrivacy(pv);
    } catch {
      // Ignore transient polling errors
    }
  }, []);

  useEffect(() => {
    refreshAll();
    const es = new EventSource("/api/events");
    es.addEventListener("task_updated", () => refreshAll());
    es.addEventListener("download_updated", () => refreshAll());
    es.addEventListener("download_created", () => refreshAll());
    const timer = setInterval(refreshAll, 5000);
    return () => {
      es.close();
      clearInterval(timer);
    };
  }, [refreshAll]);

  return (
    <Layout currentView={view} onNavigate={setView}>
      {driveBanner && (
        <div
          className={`mb-4 flex items-center justify-between rounded-xl border px-4 py-3 text-sm ${
            driveBanner.type === "success"
              ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
              : "border-rose-500/40 bg-rose-500/10 text-rose-300"
          }`}
        >
          <span>{driveBanner.text}</span>
          <button onClick={() => setDriveBanner(null)} className="ml-3 text-xs underline">
            닫기
          </button>
        </div>
      )}
      {view === "home" && (
        <HomeView status={status} tasks={tasks} onNavigate={setView} onRefresh={refreshAll} />
      )}
      {view === "explorer" && (
        <ExplorerView
          onSelectFile={(f) => {
            setSelectedFile(f);
            setView("detail");
          }}
          onOpenCompression={(f) => {
            setSelectedFile(f);
            setView("compression");
          }}
          onRefreshSystem={refreshAll}
        />
      )}
      {view === "detail" && (
        <FileDetailView
          file={selectedFile}
          allLocalFiles={localFiles}
          onUpdated={(updated) => {
            setSelectedFile(updated);
            refreshAll();
            if (!updated) setView("explorer");
          }}
          onOpenCompression={(f) => {
            setSelectedFile(f);
            setView("compression");
          }}
        />
      )}
      {view === "compression" && (
        <CompressionView
          localFiles={localFiles}
          preselectedFile={selectedFile}
          onTaskCreated={() => {
            refreshAll();
            setView("queue");
          }}
        />
      )}
      {view === "queue" && <QueueView downloads={downloads} tasks={tasks} onRefresh={refreshAll} />}
      {view === "ai_chat" && (
        <AiChatView localFiles={localFiles} privacy={privacy} onTaskExecuted={refreshAll} />
      )}
      {view === "schedule" && (
        <ScheduleView localFiles={localFiles} tasks={tasks} onRefresh={refreshAll} />
      )}
      {view === "history" && <HistoryView tasks={tasks} onRefresh={refreshAll} />}
      {view === "ai_settings" && <AiSettingsView onRefreshSystem={refreshAll} />}
      {view === "privacy" && <PrivacySettingsView />}
    </Layout>
  );
}
