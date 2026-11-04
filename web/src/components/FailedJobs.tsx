"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { KIND_LABELS } from "@/lib/jobs";

// Work that started and then failed (a CV that never came back, an answer that errored). It used
// to vanish: the person saw nothing and had no way to try again. Shown here with what went wrong
// in plain words, the technical detail one tap away, and a Try again button.

interface Failed {
  job_id:      string;
  kind:        string;
  app_uuid:    string | null;
  company:     string | null;
  role:        string | null;
  error:       string;
  finished_at: string | null;
}

const DISMISSED_KEY = "atat-dismissed-jobs";

function dismissedIds(): string[] {
  try { return JSON.parse(localStorage.getItem(DISMISSED_KEY) || "[]"); } catch { return []; }
}

/** One line a person can act on, whatever the technical error was. */
function plainReason(error: string): string {
  const e = error.toLowerCase();
  if (e.includes("invalid json") || e.includes("no text content") || e.includes("expecting value") || e.includes("empty"))
    return "The AI's reply came back unreadable. This usually works on a second try.";
  if (e.includes("overloaded") || e.includes("rate") || e.includes("529") || e.includes("timeout") || e.includes("timed out"))
    return "The AI service was busy. Trying again in a minute usually works.";
  if (e.includes("api key") || e.includes("authentication") || e.includes("401"))
    return "The AI key isn't working. Ask whoever set this up to check it.";
  return "Something went wrong while it was working.";
}

export default function FailedJobs() {
  const [rows, setRows] = useState<Failed[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data: Failed[] = await api.get("/jobs/attention");
      const hidden = dismissedIds();
      setRows(data.filter((r) => !hidden.includes(r.job_id)));
    } catch { setRows([]); }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function retry(id: string) {
    setBusy(id);
    setNote(null);
    try {
      await api.post(`/jobs/${id}/retry`);
      setNote("Trying again. It can take a few minutes; this will disappear if it works.");
      await load();
    } catch (e: any) {
      setNote(e?.message || "Couldn't start it again.");
    } finally {
      setBusy(null);
    }
  }

  function dismiss(id: string) {
    try { localStorage.setItem(DISMISSED_KEY, JSON.stringify([...dismissedIds(), id])); } catch { /* private mode */ }
    setRows((r) => r.filter((x) => x.job_id !== id));
  }

  if (rows.length === 0 && !note) return null;

  return (
    <section aria-label="Things that didn't finish" className="space-y-2">
      {rows.map((r) => {
        const what = KIND_LABELS[r.kind] ?? r.kind;
        const who = [r.company, r.role].filter(Boolean).join(" · ");
        return (
          <div key={r.job_id} role="alert" className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 space-y-2">
            <p className="text-sm font-medium text-red-900">
              {what} didn't finish{who ? `: ${who}` : ""}
            </p>
            <p className="text-sm text-red-800">{plainReason(r.error)}</p>
            <div className="flex flex-wrap items-center gap-2">
              <button
                onClick={() => retry(r.job_id)}
                disabled={busy === r.job_id}
                className="min-h-11 sm:min-h-0 px-4 py-2 text-sm sm:text-xs font-medium bg-accent text-white rounded-lg hover:bg-accent-dim transition-colors disabled:opacity-50">
                {busy === r.job_id ? "Starting…" : "Try again"}
              </button>
              {r.app_uuid && (
                <Link href={`/applications/${r.app_uuid}`}
                  className="min-h-11 sm:min-h-0 inline-flex items-center px-3 py-2 text-sm sm:text-xs text-red-900 border border-red-300 rounded-lg hover:bg-red-100">
                  Open
                </Link>
              )}
              <button onClick={() => setOpen(open === r.job_id ? null : r.job_id)}
                className="min-h-11 sm:min-h-0 px-3 py-2 text-sm sm:text-xs text-red-900 hover:underline">
                {open === r.job_id ? "Hide details" : "Details"}
              </button>
              <button onClick={() => dismiss(r.job_id)} aria-label="Dismiss"
                className="min-h-11 sm:min-h-0 px-3 py-2 text-sm sm:text-xs text-red-900 hover:underline sm:ml-auto">
                Dismiss
              </button>
            </div>
            {open === r.job_id && (
              <pre className="text-xs text-red-900 bg-white/60 border border-red-200 rounded-lg p-2 whitespace-pre-wrap break-words">{r.error}</pre>
            )}
          </div>
        );
      })}
      {note && <p className="text-xs text-text-secondary">{note}</p>}
    </section>
  );
}
