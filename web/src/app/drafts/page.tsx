"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { ACTIVE, KIND_LABELS, type JobSummary } from "@/lib/jobs";
import { JobStatusBadge, relativeTime } from "@/components/JobBits";

function JobRow({ job }: { job: JobSummary }) {
  return (
    <Link
      href={`/drafts/${job.job_id}`}
      className="flex items-center gap-4 px-4 py-3 hover:bg-bg-surface transition-colors"
    >
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-text-primary truncate">
          {KIND_LABELS[job.kind] ?? job.kind}
        </p>
        <p className="text-xs text-text-secondary truncate">
          {job.company ? `${job.company} — ${job.role}` : "New application"}
        </p>
        {job.status === "failed" && job.error && (
          <p className="text-xs text-red-700 truncate mt-0.5">{job.error}</p>
        )}
      </div>
      <div className="text-right shrink-0 space-y-1">
        <JobStatusBadge status={job.status} />
        <p className="text-[10px] text-text-muted">
          {job.created_by === "agent" ? "Drafted by agent" : "From web"} · {relativeTime(job.finished_at ?? job.submitted_at ?? job.created_at)}
        </p>
      </div>
    </Link>
  );
}

export default function DraftsPage() {
  const [drafts, setDrafts]     = useState<JobSummary[] | null>(null);
  const [activity, setActivity] = useState<JobSummary[]>([]);
  const [error, setError]       = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [d, all] = await Promise.all([
        api.get("/jobs?status=draft&limit=100"),
        api.get("/jobs?limit=30"),
      ]);
      setDrafts(d);
      setActivity((all as JobSummary[]).filter((j) => j.status !== "draft"));
      setError(null);
    } catch (e: any) {
      setError(e.message);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // Refresh while anything is in flight, so statuses move on their own.
  const inFlight = activity.some((j) => ACTIVE.includes(j.status));
  useEffect(() => {
    if (!inFlight) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [inFlight, load]);

  return (
    <div className="max-w-3xl mx-auto space-y-8">
      <div>
        <h1 className="text-2xl font-semibold text-text-primary">Drafts</h1>
        <p className="text-sm text-text-secondary mt-1">
          Generation requests waiting for your review. Nothing here has been sent to a model yet —
          check the inputs and the prompt, adjust them, then submit.
        </p>
      </div>

      {error && (
        <div className="px-4 py-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{error}</div>
      )}

      <section className="space-y-2">
        <h2 className="text-xs font-semibold text-text-secondary uppercase tracking-wide">
          Waiting for review{drafts && drafts.length > 0 ? ` (${drafts.length})` : ""}
        </h2>
        <div className="bg-bg-elevated border border-bg-border rounded-xl divide-y divide-bg-border overflow-hidden">
          {drafts === null ? (
            <p className="px-4 py-6 text-sm text-text-muted">Loading…</p>
          ) : drafts.length === 0 ? (
            <p className="px-4 py-6 text-sm text-text-muted">
              No drafts. When the agent (or you) saves a request for review, it appears here.
            </p>
          ) : (
            drafts.map((j) => <JobRow key={j.job_id} job={j} />)
          )}
        </div>
      </section>

      <section className="space-y-2">
        <h2 className="text-xs font-semibold text-text-secondary uppercase tracking-wide">Recent activity</h2>
        <div className="bg-bg-elevated border border-bg-border rounded-xl divide-y divide-bg-border overflow-hidden">
          {activity.length === 0 ? (
            <p className="px-4 py-6 text-sm text-text-muted">No generation jobs yet.</p>
          ) : (
            activity.map((j) => <JobRow key={j.job_id} job={j} />)
          )}
        </div>
      </section>
    </div>
  );
}
