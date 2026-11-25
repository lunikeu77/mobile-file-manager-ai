import React from "react";

const COLORS: Record<string, string> = {
  COMPLETED: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  ready: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  RUNNING: "bg-sky-500/15 text-sky-300 border-sky-500/30 animate-pulse",
  PENDING: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  SCHEDULED: "bg-violet-500/15 text-violet-300 border-violet-500/30",
  PAUSED: "bg-slate-500/20 text-slate-300 border-slate-500/30",
  NEEDS_USER_ACTION: "bg-orange-500/20 text-orange-300 border-orange-500/40",
  FAILED: "bg-rose-500/15 text-rose-300 border-rose-500/30",
  auth_error: "bg-rose-500/15 text-rose-300 border-rose-500/30",
  rate_limited: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  CANCELLED: "bg-slate-700/40 text-slate-400 border-slate-600/40",
  unconfigured: "bg-slate-700/40 text-slate-400 border-slate-600/40",
};

export const StatusBadge: React.FC<{ status: string }> = ({ status }) => {
  const cls = COLORS[status] || "bg-slate-700/50 text-slate-300 border-slate-600/40";
  return (
    <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium ${cls}`}>
      {status}
    </span>
  );
};
