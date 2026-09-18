"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { ACTIVE, KIND_LABELS, type Job, type ParamSpec } from "@/lib/jobs";
import { JobStatusBadge, relativeTime } from "@/components/JobBits";

// Friendlier labels for params; anything not listed falls back to its key.
const PARAM_LABELS: Record<string, string> = {
  jd_text:          "Job description",
  company:          "Company",
  role:             "Role title",
  source_url:       "Source URL",
  tier:             "Tier",
  generation_notes: "Notes for the AI",
  global_comment:   "Direction for the retry",
  research_company: "Research the company first",
  research_role:    "Research the role first",
  draft_input:      "Rough draft to work from",
  key_points:       "Key points to address",
  force:            "Regenerate questions that already have an answer",
  question_ids:     "Questions to answer",
};

const LONG_TEXT = new Set(["jd_text", "generation_notes", "global_comment", "draft_input", "key_points"]);

interface Question { id: string; question_text: string; effective_answer: string | null }

// ── Param inputs ──────────────────────────────────────────────────────────────

function ParamField({
  name, spec, value, onChange, disabled, questions,
}: {
  name:      string;
  spec:      ParamSpec;
  value:     unknown;
  onChange:  (v: unknown) => void;
  disabled:  boolean;
  questions: Question[] | null;
}) {
  const label = PARAM_LABELS[name] ?? name;
  const inputCls = "w-full px-3 py-2 bg-bg-elevated border border-bg-border rounded-lg text-sm text-text-primary placeholder-text-muted focus:outline-none focus:ring-2 focus:ring-accent/40 disabled:opacity-60";

  if (spec.type === "bool") {
    return (
      <label className="flex items-start gap-2 cursor-pointer select-none">
        <input type="checkbox" checked={!!value} disabled={disabled}
          onChange={(e) => onChange(e.target.checked)}
          className="mt-0.5 w-3.5 h-3.5 rounded border-bg-border accent-accent" />
        <span>
          <span className="text-sm text-text-primary">{label}</span>
          {spec.help && <span className="block text-xs text-text-muted">{spec.help}</span>}
        </span>
      </label>
    );
  }

  if (spec.type === "list[str]" && name === "question_ids" && questions) {
    const selected = new Set((value as string[] | null) ?? []);
    const toggle = (id: string) => {
      const next = new Set(selected);
      next.has(id) ? next.delete(id) : next.add(id);
      onChange(next.size ? Array.from(next) : null);
    };
    return (
      <div className="space-y-1.5">
        <p className="text-xs font-medium text-text-secondary uppercase tracking-wide">{label}</p>
        <p className="text-xs text-text-muted">None ticked means every question.</p>
        {questions.length === 0 && <p className="text-sm text-text-muted">This application has no questions.</p>}
        {questions.map((q) => (
          <label key={q.id} className="flex items-start gap-2 cursor-pointer select-none">
            <input type="checkbox" checked={selected.has(q.id)} disabled={disabled}
              onChange={() => toggle(q.id)}
              className="mt-0.5 w-3.5 h-3.5 rounded border-bg-border accent-accent" />
            <span className="text-sm text-text-primary">
              {q.question_text}
              {q.effective_answer && <span className="text-xs text-text-muted"> — answered</span>}
            </span>
          </label>
        ))}
      </div>
    );
  }

  const text = spec.type === "list[str]"
    ? ((value as string[] | null) ?? []).join("\n")
    : value == null ? "" : String(value);

  const set = (raw: string) => {
    if (spec.type === "list[str]") {
      const items = raw.split("\n").map((s) => s.trim()).filter(Boolean);
      onChange(items.length ? items : null);
    } else if (spec.type === "int") {
      onChange(raw === "" ? null : parseInt(raw, 10));
    } else {
      onChange(raw === "" && !spec.required ? null : raw);
    }
  };

  return (
    <div className="space-y-1">
      <label className="text-xs font-medium text-text-secondary uppercase tracking-wide">
        {label}
        {!spec.required && <span className="normal-case font-normal text-text-muted ml-1">optional</span>}
      </label>
      {LONG_TEXT.has(name) || spec.type === "list[str]" ? (
        <textarea value={text} disabled={disabled} onChange={(e) => set(e.target.value)}
          rows={name === "jd_text" ? 12 : 4}
          className={`${inputCls} resize-y leading-relaxed ${name === "jd_text" ? "font-mono text-xs" : ""}`} />
      ) : (
        <input type={spec.type === "int" ? "number" : "text"} value={text} disabled={disabled}
          onChange={(e) => set(e.target.value)} className={inputCls} />
      )}
      {spec.help && <p className="text-xs text-text-muted">{spec.help}</p>}
    </div>
  );
}

// ── Prompt preview ────────────────────────────────────────────────────────────

function PromptPreview({ job, title }: { job: Job; title: string }) {
  const [system, setSystem] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const pv = job.prompt_preview;
  if (!pv) return null;

  async function showSystem() {
    setLoading(true);
    try {
      const full: Job = await api.get(`/jobs/${job.job_id}?include_prompt=true`);
      setSystem(full.prompt_preview?.system ?? "");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="space-y-2">
      <h2 className="text-xs font-semibold text-text-secondary uppercase tracking-wide">{title}</h2>
      {pv.error ? (
        <div className="px-4 py-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{pv.error}</div>
      ) : (
        <>
          {pv.notes && pv.notes.length > 0 && (
            <ul className="text-xs text-text-secondary space-y-1 list-disc pl-5">
              {pv.notes.map((n, i) => <li key={i}>{n}</li>)}
            </ul>
          )}
          <p className="text-xs text-text-muted">
            Your unchanged experience library, personas and skills are shown as one-line
            placeholders — the model gets them in full.
          </p>
          <pre className="max-h-[32rem] overflow-auto whitespace-pre-wrap break-words bg-bg-elevated border border-bg-border rounded-lg p-4 text-xs font-mono text-text-primary leading-relaxed">
            {pv.user}
          </pre>
          {system === null ? (
            <button onClick={showSystem} disabled={loading}
              className="text-xs text-text-muted hover:text-text-primary transition-colors">
              {loading ? "Loading…" : `Show system prompt (${(pv.system_chars ?? 0).toLocaleString()} characters)`}
            </button>
          ) : (
            <details open className="space-y-2">
              <summary className="text-xs text-text-muted cursor-pointer">System prompt</summary>
              <pre className="max-h-[32rem] overflow-auto whitespace-pre-wrap break-words bg-bg-surface border border-bg-border rounded-lg p-4 text-xs font-mono text-text-secondary leading-relaxed">
                {system}
              </pre>
            </details>
          )}
        </>
      )}
    </section>
  );
}

// ── Result ────────────────────────────────────────────────────────────────────

function resultLink(job: Job): { href: string; label: string } | null {
  const uuid = job.result?.uuid ?? job.app_uuid;
  if (!uuid) return null;
  if (job.kind === "regenerate_section" && job.result?.section_name) {
    return { href: `/applications/${uuid}/review/${job.result.section_name}`, label: "Review the new version" };
  }
  return { href: `/applications/${uuid}`, label: "Open application" };
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function DraftPage() {
  const params = useParams();
  const router = useRouter();
  const jobId  = params.jobId as string;

  const [job, setJob]             = useState<Job | null>(null);
  const [edits, setEdits]         = useState<Record<string, unknown>>({});
  const [questions, setQuestions] = useState<Question[] | null>(null);
  const [busy, setBusy]           = useState<"save" | "submit" | "cancel" | "retry" | null>(null);
  const [error, setError]         = useState<string | null>(null);
  const [notFound, setNotFound]   = useState(false);

  const load = useCallback(async () => {
    const res = await fetch(`${api.url}/jobs/${jobId}`, { cache: "no-store" });
    if (res.status === 404) { setNotFound(true); return null; }
    if (!res.ok) throw new Error(`Couldn't load job: ${res.status}`);
    const data: Job = await res.json();
    setJob(data);
    return data;
  }, [jobId]);

  useEffect(() => { load().catch((e) => setError(e.message)); }, [load]);

  // Follow a queued/running job until it finishes.
  const active = !!job && ACTIVE.includes(job.status);
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => load().catch(() => {}), 2000);
    return () => clearInterval(t);
  }, [active, load]);

  // generate_answers drafts pick questions from the application's list.
  useEffect(() => {
    if (job?.kind === "generate_answers" && job.app_uuid && questions === null) {
      api.get(`/questions/${job.app_uuid}`).then(setQuestions).catch(() => setQuestions([]));
    }
  }, [job, questions]);

  const params_ = useMemo(() => ({ ...(job?.params ?? {}), ...edits }), [job, edits]);
  const dirty   = Object.keys(edits).length > 0;
  const isDraft = job?.status === "draft";

  async function save(): Promise<Job | null> {
    if (!job || !dirty) return job;
    const updated: Job = await api.patch(`/jobs/${job.job_id}`, { params: edits, expected_version: job.version });
    setJob(updated);
    setEdits({});
    return updated;
  }

  async function act(kind: "save" | "submit" | "cancel" | "retry") {
    if (!job) return;
    setBusy(kind);
    setError(null);
    try {
      if (kind === "save") {
        await save();
      } else if (kind === "submit") {
        const current = await save();
        setJob(await api.post(`/jobs/${job.job_id}/submit`, { expected_version: current!.version }));
      } else if (kind === "cancel") {
        setJob(await api.post(`/jobs/${job.job_id}/cancel`));
        setEdits({});
      } else {
        const next: Job = await api.post(`/jobs/${job.job_id}/retry`, { draft: true });
        router.push(`/drafts/${next.job_id}`);
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }

  if (notFound) {
    return (
      <div className="max-w-3xl mx-auto space-y-4">
        <p className="text-sm text-text-secondary">That job doesn&apos;t exist.</p>
        <Link href="/drafts" className="text-sm text-accent hover:underline">Back to drafts</Link>
      </div>
    );
  }
  if (!job) {
    return <div className="max-w-3xl mx-auto text-sm text-text-muted">{error ?? "Loading…"}</div>;
  }

  const link = job.status === "succeeded" ? resultLink(job) : null;

  return (
    <div className="max-w-3xl mx-auto space-y-6 pb-16">
      <div className="space-y-2">
        <Link href="/drafts" className="text-xs text-text-muted hover:text-text-primary">← Drafts</Link>
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <h1 className="text-2xl font-semibold text-text-primary">{KIND_LABELS[job.kind] ?? job.kind}</h1>
            <p className="text-sm text-text-secondary mt-1">
              {job.company ? (
                <Link href={`/applications/${job.app_uuid}`} className="hover:underline">{job.company} — {job.role}</Link>
              ) : "Creates a new application"}
            </p>
          </div>
          <JobStatusBadge status={job.status} />
        </div>
        <p className="text-xs text-text-muted">
          {job.created_by === "agent" ? "Drafted by the agent" : "Created here"} {relativeTime(job.created_at)}
          {job.submitted_by && ` · submitted by ${job.submitted_by === "agent" ? "the agent" : "you"} ${relativeTime(job.submitted_at)}`}
          {isDraft && ` · version ${job.version}`}
        </p>
      </div>

      {error && (
        <div className="px-4 py-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700 flex items-start justify-between gap-4">
          <span>{error}</span>
          <button onClick={() => { setEdits({}); setError(null); load().catch(() => {}); }}
            className="text-xs font-medium underline shrink-0">Reload</button>
        </div>
      )}

      {/* Status panels for non-drafts */}
      {active && (
        <div className="px-4 py-3 bg-bg-surface border border-bg-border rounded-lg flex items-center justify-between gap-4">
          <p className="text-sm text-text-secondary">
            {job.status === "queued" ? "Queued — starting shortly." : "Generating. You can leave this page; it carries on in the background."}
            {job.cancel_requested && " Cancel requested — the output will be discarded when the model call returns."}
          </p>
          {!job.cancel_requested && (
            <button onClick={() => act("cancel")} disabled={busy !== null}
              className="text-xs font-medium text-text-secondary hover:text-text-primary disabled:opacity-50 shrink-0">
              Cancel
            </button>
          )}
        </div>
      )}
      {job.status === "succeeded" && (
        <div className="px-4 py-3 bg-accent/10 border border-accent/30 rounded-lg flex items-center justify-between gap-4">
          <p className="text-sm text-text-primary">Done {relativeTime(job.finished_at)}.</p>
          {link && <Link href={link.href} className="text-sm font-medium text-accent hover:underline shrink-0">{link.label} →</Link>}
        </div>
      )}
      {(job.status === "failed" || job.status === "cancelled") && (
        <div className="px-4 py-3 bg-bg-surface border border-bg-border rounded-lg space-y-2">
          <p className="text-sm text-text-primary">{job.status === "failed" ? "This job failed." : "This job was cancelled."}</p>
          {job.error && <p className="text-xs text-text-secondary font-mono whitespace-pre-wrap">{job.error}</p>}
          <button onClick={() => act("retry")} disabled={busy !== null}
            className="text-xs font-medium text-accent hover:underline disabled:opacity-50">
            {busy === "retry" ? "Creating draft…" : "Try again as a new draft"}
          </button>
        </div>
      )}

      {/* Inputs */}
      {job.editable_params && Object.keys(job.editable_params).length > 0 ? (
        <section className="space-y-4">
          <h2 className="text-xs font-semibold text-text-secondary uppercase tracking-wide">Inputs</h2>
          <div className="bg-bg-surface border border-bg-border rounded-xl px-5 py-4 space-y-4">
            {Object.entries(job.editable_params).map(([name, spec]) => (
              <ParamField key={name} name={name} spec={spec} value={params_[name]}
                disabled={!isDraft || busy !== null}
                onChange={(v) => setEdits((e) => ({ ...e, [name]: v }))}
                questions={questions} />
            ))}
          </div>
        </section>
      ) : !isDraft && Object.keys(job.params).length > 0 ? (
        <section className="space-y-2">
          <h2 className="text-xs font-semibold text-text-secondary uppercase tracking-wide">Inputs</h2>
          <dl className="bg-bg-surface border border-bg-border rounded-xl px-5 py-4 space-y-2 text-sm">
            {Object.entries(job.params).map(([k, v]) => (
              <div key={k}>
                <dt className="text-xs text-text-muted">{PARAM_LABELS[k] ?? k}</dt>
                <dd className="text-text-primary whitespace-pre-wrap break-words line-clamp-6">
                  {v == null || v === "" ? "—" : Array.isArray(v) ? v.join(", ") : String(v)}
                </dd>
              </div>
            ))}
          </dl>
        </section>
      ) : null}

      {/* Draft actions */}
      {isDraft && (
        <div className="flex flex-col sm:flex-row gap-2 sm:items-center">
          <button onClick={() => act("submit")} disabled={busy !== null}
            className="px-5 py-2.5 bg-accent text-white text-sm font-medium rounded-lg hover:bg-accent-dim transition-colors disabled:opacity-50">
            {busy === "submit" ? "Submitting…" : dirty ? "Save and submit" : "Submit for generation"}
          </button>
          {dirty && (
            <button onClick={() => act("save")} disabled={busy !== null}
              className="px-4 py-2.5 bg-bg-elevated border border-bg-border text-sm font-medium text-text-primary rounded-lg hover:bg-bg-surface transition-colors disabled:opacity-50">
              {busy === "save" ? "Saving…" : "Save and refresh preview"}
            </button>
          )}
          <button onClick={() => act("cancel")} disabled={busy !== null}
            className="px-4 py-2.5 text-sm text-text-muted hover:text-status-rejected transition-colors disabled:opacity-50 sm:ml-auto">
            Discard draft
          </button>
        </div>
      )}
      {isDraft && dirty && (
        <p className="text-xs text-text-muted -mt-4">The preview below reflects the saved version — save to update it.</p>
      )}

      <PromptPreview job={job} title={isDraft ? "What will be sent" : "What was sent"} />

      {job.status === "succeeded" && job.result && (
        <details className="text-xs">
          <summary className="cursor-pointer text-text-muted hover:text-text-primary">Raw result</summary>
          <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap break-words bg-bg-elevated border border-bg-border rounded-lg p-4 font-mono text-text-secondary">
            {JSON.stringify(job.result, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}
