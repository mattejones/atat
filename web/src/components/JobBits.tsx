// Small shared pieces for showing generation jobs.

import type { JobStatus } from "@/lib/jobs";

const STATUS_STYLE: Record<JobStatus, { label: string; cls: string }> = {
  draft:     { label: "Draft",     cls: "bg-status-reviewing/10 text-status-reviewing border-status-reviewing/30" },
  queued:    { label: "Queued",    cls: "bg-bg-surface text-text-secondary border-bg-border" },
  running:   { label: "Running",   cls: "bg-accent-glow text-accent border-accent/30" },
  succeeded: { label: "Done",      cls: "bg-accent/10 text-accent border-accent/30" },
  failed:    { label: "Failed",    cls: "bg-status-rejected/10 text-status-rejected border-status-rejected/30" },
  cancelled: { label: "Cancelled", cls: "bg-bg-surface text-text-muted border-bg-border" },
};

export function JobStatusBadge({ status }: { status: JobStatus }) {
  const s = STATUS_STYLE[status];
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 text-[11px] font-medium border rounded-full ${s.cls}`}>
      {status === "running" && <span className="w-2 h-2 border border-accent/30 border-t-accent rounded-full animate-spin" />}
      {s.label}
    </span>
  );
}

export function relativeTime(iso: string | null): string {
  if (!iso) return "";
  const secs = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60)    return "just now";
  if (secs < 3600)  return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return new Date(iso).toLocaleDateString("en-GB", { day: "2-digit", month: "short" });
}
