import React, { useState } from "react";
import {
  Bot,
  Clock,
  Cpu,
  DownloadCloud,
  FileText,
  FolderOpen,
  History,
  Home,
  Menu,
  Minimize2,
  ShieldCheck,
  X,
} from "lucide-react";
import { ViewKey } from "../types";

export const NAV_ITEMS: Array<{ key: ViewKey; label: string; short: string; icon: React.ReactNode }> = [
  { key: "home", label: "홈 대시보드", short: "홈", icon: <Home className="h-4 w-4" /> },
  { key: "explorer", label: "파일 탐색기", short: "탐색기", icon: <FolderOpen className="h-4 w-4" /> },
  { key: "detail", label: "파일 상세", short: "상세", icon: <FileText className="h-4 w-4" /> },
  { key: "compression", label: "압축 설정", short: "압축", icon: <Minimize2 className="h-4 w-4" /> },
  { key: "queue", label: "다운로드/대기열", short: "대기열", icon: <DownloadCloud className="h-4 w-4" /> },
  { key: "ai_chat", label: "AI 명령 채팅", short: "AI", icon: <Bot className="h-4 w-4" /> },
  { key: "schedule", label: "예약 작업", short: "예약", icon: <Clock className="h-4 w-4" /> },
  { key: "history", label: "작업 이력", short: "이력", icon: <History className="h-4 w-4" /> },
  { key: "ai_settings", label: "AI·Drive 설정", short: "공급자", icon: <Cpu className="h-4 w-4" /> },
  { key: "privacy", label: "개인정보·보안", short: "보안", icon: <ShieldCheck className="h-4 w-4" /> },
];

interface LayoutProps {
  currentView: ViewKey;
  onNavigate: (view: ViewKey) => void;
  children: React.ReactNode;
}

export const Layout: React.FC<LayoutProps> = ({ currentView, onNavigate, children }) => {
  const [mobileDrawer, setMobileDrawer] = useState(false);

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 md:flex">
      {/* Desktop Sidebar */}
      <aside className="hidden w-64 shrink-0 border-r border-slate-800/80 bg-slate-900/60 md:flex md:flex-col">
        <div className="border-b border-slate-800/80 p-5">
          <div className="text-xs font-semibold tracking-wider text-indigo-400 uppercase">Render Full-Stack</div>
          <h1 className="mt-1 text-lg font-bold text-white">파일 관리 &amp; AI 자동화</h1>
          <p className="mt-1 text-xs text-slate-400">FFmpeg · PDF · Drive · Multi-AI</p>
        </div>
        <nav className="flex-1 space-y-1 p-3">
          {NAV_ITEMS.map((item) => {
            const active = currentView === item.key;
            return (
              <button
                key={item.key}
                onClick={() => onNavigate(item.key)}
                className={`flex w-full items-center gap-3 rounded-xl px-3.5 py-2.5 text-sm font-medium transition ${
                  active
                    ? "bg-indigo-600/20 text-indigo-300 ring-1 ring-indigo-500/40"
                    : "text-slate-300 hover:bg-slate-800/70 hover:text-white"
                }`}
              >
                {item.icon}
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>
      </aside>

      {/* Mobile Header */}
      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-slate-800 bg-slate-900/90 px-4 py-3 backdrop-blur md:hidden">
        <div>
          <span className="text-sm font-bold text-white">파일 관리 &amp; AI 자동화</span>
          <span className="ml-2 rounded bg-indigo-500/20 px-1.5 py-0.5 text-[10px] text-indigo-300">
            {NAV_ITEMS.find((n) => n.key === currentView)?.short}
          </span>
        </div>
        <button
          onClick={() => setMobileDrawer(!mobileDrawer)}
          className="rounded-lg border border-slate-700 p-2 text-slate-300"
          aria-label="메뉴 열기"
        >
          {mobileDrawer ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
        </button>
      </header>

      {mobileDrawer && (
        <div className="fixed inset-0 z-40 bg-black/70 md:hidden" onClick={() => setMobileDrawer(false)}>
          <div
            className="h-full w-72 bg-slate-900 p-4 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-4 flex items-center justify-between">
              <span className="font-bold text-white">전체 10개 화면</span>
              <button onClick={() => setMobileDrawer(false)} className="p-1 text-slate-400">
                <X className="h-5 w-5" />
              </button>
            </div>
            <div className="space-y-1">
              {NAV_ITEMS.map((item) => (
                <button
                  key={item.key}
                  onClick={() => {
                    onNavigate(item.key);
                    setMobileDrawer(false);
                  }}
                  className={`flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm ${
                    currentView === item.key ? "bg-indigo-600/25 text-indigo-300" : "text-slate-300 hover:bg-slate-800"
                  }`}
                >
                  {item.icon}
                  <span>{item.label}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Main Content */}
      <main className="min-w-0 flex-1 pb-24 md:pb-8">
        <div className="mx-auto max-w-6xl p-4 md:p-7">{children}</div>
      </main>

      {/* Mobile Quick Bottom Nav */}
      <nav className="fixed right-0 bottom-0 left-0 z-30 grid grid-cols-5 border-t border-slate-800 bg-slate-900/95 py-1.5 backdrop-blur md:hidden">
        {(["home", "explorer", "compression", "queue", "ai_chat"] as ViewKey[]).map((key) => {
          const item = NAV_ITEMS.find((n) => n.key === key)!;
          const active = currentView === key;
          return (
            <button
              key={key}
              onClick={() => onNavigate(key)}
              className={`flex flex-col items-center gap-0.5 py-1 text-[11px] ${
                active ? "text-indigo-400" : "text-slate-400"
              }`}
            >
              {item.icon}
              <span>{item.short}</span>
            </button>
          );
        })}
      </nav>
    </div>
  );
};
