"use client";

import { useState, useEffect, useCallback } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { STATUS_LABELS } from "@/lib/statuses";
import { decodeEntities } from "@/lib/text";
import StatusPicker from "@/components/ui/StatusPicker";
import CoverLetterPanel from "@/components/CoverLetterPanel";
import QuestionsPanel from "@/components/QuestionsPanel";
import { FLAG_LABELS, OUTCOME_HINTS, OUTCOME_STYLES, STAGE_LABELS, type Outcome } from "@/lib/outcomes";

import { api } from "@/lib/api";
const API = api.url;

type ViewMode = "preview" | "raw" | "reasoning" | "jd" | "history" | "cover_letter" | "questions";

const ARRANGEMENT_LABELS: Record<string, string> = {
  remote: "Remote",
  hybrid: "Hybrid",
  office: "On-site",
};

const CURRENCIES = ["GBP", "USD", "EUR", "AUD", "CAD", "SGD"];

const SECTION_LABELS: Record<string, string> = {
  profile:        "Profile",
  experience:     "Experience",
  skills:         "Skills",
  education:      "Education",
  certifications: "Certifications",
};

const EVENT_TYPE_LABELS: Record<string, string> = {
  status_change:  "Status changed",
  cv_edited:      "CV edited",
  pdf_rendered:   "PDF rendered",
  email_received: "Email received",
  note_added:     "Note added",
};

// ── Sections panel ────────────────────────────────────────────────────────────

function SectionsPanel({ appId, sections }: { appId: string; sections: any[] }) {
  if (!sections.length) return null;
  return (
    <div className="bg-bg-surface border border-bg-border rounded-xl px-5 py-4">
      <h2 className="text-xs font-semibold text-text-muted uppercase tracking-wide mb-3">
        Section review
      </h2>
      <div className="flex flex-wrap gap-2">
        {sections.map((section: any) => {
          const accepted = !!section.accepted_report_id;
          return (
            <Link
              key={section.id}
              href={`/applications/${appId}/review/${section.section_name}`}
              className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-xs transition-colors hover:border-accent/50 ${
                accepted
                  ? "border-green-300 bg-green-50 text-green-800"
                  : "border-bg-border bg-bg-elevated text-text-secondary hover:text-text-primary"
              }`}
            >
              <span className="font-medium">
                {SECTION_LABELS[section.section_name] ?? section.section_name}
              </span>
              {accepted && <span className="text-green-600 text-[10px]">✓</span>}
              {!accepted && section.latest_report?.escalated && (
                <span className="text-amber-600 text-[10px]">⚠</span>
              )}
            </Link>
          );
        })}
      </div>
    </div>
  );
}

// ── Role details panel ────────────────────────────────────────────────────────

function RoleDetails({ meta, onSave }: { meta: any; onSave: (updates: any) => void }) {
  const [editing, setEditing] = useState(false);
  const [saving, setSaving]   = useState(false);
  const [form, setForm]       = useState({
    location:         meta?.location         ?? "",
    work_arrangement: meta?.work_arrangement ?? "",
    hybrid_days:      meta?.hybrid_days      ?? "",
    salary_min:       meta?.salary_min       ?? "",
    salary_max:       meta?.salary_max       ?? "",
    salary_currency:  meta?.salary_currency  ?? "GBP",
  });

  function fmt(n: number | null | undefined) {
    if (!n) return null;
    return new Intl.NumberFormat("en-GB").format(n);
  }

  function salaryDisplay() {
    const cur = meta?.salary_currency ?? "GBP";
    const sym = cur === "GBP" ? "£" : cur === "USD" ? "$" : cur === "EUR" ? "€" : cur;
    if (meta?.salary_min && meta?.salary_max)
      return `${sym}${fmt(meta.salary_min)} – ${sym}${fmt(meta.salary_max)}`;
    if (meta?.salary_min) return `from ${sym}${fmt(meta.salary_min)}`;
    if (meta?.salary_max) return `up to ${sym}${fmt(meta.salary_max)}`;
    return null;
  }

  function arrangementDisplay() {
    if (!meta?.work_arrangement) return null;
    const base = ARRANGEMENT_LABELS[meta.work_arrangement] ?? meta.work_arrangement;
    if (meta.work_arrangement === "hybrid" && meta.hybrid_days)
      return `${base} (${meta.hybrid_days}d/wk in office)`;
    return base;
  }

  async function save() {
    setSaving(true);
    const payload: Record<string, any> = {
      location:         form.location         || null,
      work_arrangement: form.work_arrangement || null,
      hybrid_days:      form.hybrid_days !== "" ? parseInt(String(form.hybrid_days)) : null,
      salary_min:       form.salary_min  !== "" ? parseInt(String(form.salary_min))  : null,
      salary_max:       form.salary_max  !== "" ? parseInt(String(form.salary_max))  : null,
      salary_currency:  form.salary_currency  || "GBP",
    };
    const cleaned = Object.fromEntries(Object.entries(payload).filter(([, v]) => v !== null));
    try {
      onSave(cleaned);
      setEditing(false);
    } finally {
      setSaving(false);
    }
  }

  const salary      = salaryDisplay();
  const arrangement = arrangementDisplay();
  const hasAny      = meta?.location || salary || arrangement;

  return (
    <div className="bg-bg-surface border border-bg-border rounded-xl px-5 py-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-xs font-semibold text-text-muted uppercase tracking-wide">Role details</h2>
        {!editing ? (
          <button onClick={() => setEditing(true)} className="text-xs text-text-muted hover:text-accent transition-colors">
            {hasAny ? "Edit" : "+ Add details"}
          </button>
        ) : (
          <div className="flex gap-2">
            <button onClick={save} disabled={saving} className="text-xs text-accent hover:underline disabled:opacity-50">
              {saving ? "Saving…" : "Save"}
            </button>
            <button onClick={() => setEditing(false)} className="text-xs text-text-muted hover:text-text-secondary">Cancel</button>
          </div>
        )}
      </div>

      {!editing ? (
        <div className="space-y-1.5">
          {meta?.location   && <Detail label="Location" value={meta.location} />}
          {arrangement      && <Detail label="Working"  value={arrangement} />}
          {salary           && <Detail label="Salary"   value={salary} />}
          {!hasAny && <p className="text-xs text-text-muted italic">No role details recorded yet.</p>}
        </div>
      ) : (
        <div className="space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-1 sm:gap-2 sm:items-center">
            <label className="text-xs text-text-secondary">Location</label>
            <input value={form.location} onChange={(e) => setForm((f) => ({ ...f, location: e.target.value }))}
              placeholder="e.g. London, UK"
              className="sm:col-span-2 min-h-11 sm:min-h-0 px-2 py-1.5 text-base sm:text-sm bg-white border border-bg-border rounded-md focus:outline-none focus:ring-1 focus:ring-accent/50" />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-1 sm:gap-2 sm:items-center">
            <label className="text-xs text-text-secondary">Working</label>
            <div className="sm:col-span-2 flex gap-1.5">
              {(["remote", "hybrid", "office"] as const).map((a) => (
                <button key={a} onClick={() => setForm((f) => ({ ...f, work_arrangement: a }))}
                  className={`px-4 py-2 sm:px-3 sm:py-1 text-xs rounded-full border transition-colors capitalize ${
                    form.work_arrangement === a ? "bg-accent text-white border-accent" : "border-bg-border text-text-secondary hover:border-accent/50"
                  }`}>
                  {ARRANGEMENT_LABELS[a]}
                </button>
              ))}
            </div>
          </div>
          {form.work_arrangement === "hybrid" && (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-1 sm:gap-2 sm:items-center">
              <label className="text-xs text-text-secondary">Days in office</label>
              <div className="sm:col-span-2 flex gap-1.5">
                {[1,2,3,4,5].map((d) => (
                  <button key={d} onClick={() => setForm((f) => ({ ...f, hybrid_days: d }))}
                    className={`w-11 h-11 sm:w-8 sm:h-7 text-xs rounded-md border transition-colors ${
                      Number(form.hybrid_days) === d ? "bg-accent text-white border-accent" : "border-bg-border text-text-secondary hover:border-accent/50"
                    }`}>{d}</button>
                ))}
                <span className="text-xs text-text-muted self-center ml-1">days/wk</span>
              </div>
            </div>
          )}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-1 sm:gap-2 sm:items-center">
            <label className="text-xs text-text-secondary">Salary</label>
            <div className="sm:col-span-2 flex flex-wrap gap-2 items-center">
              <select value={form.salary_currency} onChange={(e) => setForm((f) => ({ ...f, salary_currency: e.target.value }))}
                className="px-2 py-1.5 text-xs bg-white border border-bg-border rounded-md focus:outline-none">
                {CURRENCIES.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
              <input type="number" value={form.salary_min} onChange={(e) => setForm((f) => ({ ...f, salary_min: e.target.value }))}
                placeholder="Min" className="w-24 sm:w-28 min-h-11 sm:min-h-0 px-2 py-1.5 text-base sm:text-sm bg-white border border-bg-border rounded-md focus:outline-none" />
              <span className="text-xs text-text-muted">–</span>
              <input type="number" value={form.salary_max} onChange={(e) => setForm((f) => ({ ...f, salary_max: e.target.value }))}
                placeholder="Max" className="w-24 sm:w-28 min-h-11 sm:min-h-0 px-2 py-1.5 text-base sm:text-sm bg-white border border-bg-border rounded-md focus:outline-none" />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-3">
      <span className="text-xs text-text-muted w-16 flex-shrink-0">{label}</span>
      <span className="text-xs text-text-primary">{value}</span>
    </div>
  );
}

// ── Outcome panel ─────────────────────────────────────────────────────────────
// What actually happened, derived from accepted emails and status history — not the
// status field. Re-read whenever the status or the outside-inbox flag changes.

function OutcomePanel({ appId, status, outsideInbox, onFlagChange }: {
  appId:        string;
  status:       string;
  outsideInbox: boolean;
  onFlagChange: (value: boolean) => Promise<void>;
}) {
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [saving, setSaving]   = useState(false);

  useEffect(() => {
    fetch(`${API}/insights/outcomes/${appId}`)
      .then((r) => (r.ok ? r.json() : null))
      .then(setOutcome)
      .catch(() => setOutcome(null));
  }, [appId, status, outsideInbox]);

  if (!outcome) return null;

  const fmt = (d: string | null) =>
    d ? new Date(d).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" }) : "—";

  async function toggle(value: boolean) {
    setSaving(true);
    try { await onFlagChange(value); } finally { setSaving(false); }
  }

  return (
    <div className="bg-bg-elevated border border-bg-border rounded-xl px-4 sm:px-6 py-4 space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-x-4">
        <h2 className="text-xs font-semibold text-text-muted uppercase tracking-wide">How it's going</h2>
        <label className="flex items-center gap-2 min-h-11 text-xs text-text-secondary cursor-pointer">
          <input
            type="checkbox"
            checked={outsideInbox}
            disabled={saving}
            onChange={(e) => toggle(e.target.checked)}
            className="w-5 h-5 sm:w-3.5 sm:h-3.5 accent-accent"
          />
          Applied outside this inbox
        </label>
      </div>

      {outcome.in_scope && outcome.outcome ? (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className={`px-2 py-0.5 text-xs font-medium border rounded-full ${OUTCOME_STYLES[outcome.outcome]}`}>
              {/* "Ghosted" is also a status, so name the derived outcome differently */}
              {outcome.outcome === "ghosted" ? "Went quiet after contact" : outcome.outcome_label}
            </span>
            <span className="text-xs text-text-secondary">{OUTCOME_HINTS[outcome.outcome]}</span>
          </div>
          {outcome.score && (
            <p className="text-xs text-text-secondary">
              Chance of {outcome.score.waiting_for}:{" "}
              <span className="font-medium text-text-primary">{Math.round(outcome.score.p_next * 100)}%</span>
              {outcome.score.waiting_for === "screen" && (
                <> · of a second round: <span className="text-text-primary">{Math.round(outcome.score.p_second * 100)}%</span></>
              )}
            </p>
          )}
          <details className="group">
            <summary className="min-h-11 flex items-center text-xs text-text-muted cursor-pointer select-none">
              Details
            </summary>
          <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-text-secondary">
            <span>Furthest stage: <span className="text-text-primary">{STAGE_LABELS[outcome.furthest_stage]}</span></span>
            {outcome.rounds > 0 && <span>Interview rounds: <span className="text-text-primary">{outcome.rounds}</span></span>}
            <span>Applied: <span className="text-text-primary">{fmt(outcome.applied_on)}{outcome.applied_estimated ? " (estimated)" : ""}</span></span>
            {outcome.first_engagement && <span>First engagement: <span className="text-text-primary">{fmt(outcome.first_engagement)}</span></span>}
            {outcome.decision_on && <span>Decision: <span className="text-text-primary">{fmt(outcome.decision_on)}</span></span>}
            <span>Emails: <span className="text-text-primary">{outcome.email_count}</span></span>
            {outcome.evidence_source && <span>Evidence: <span className="text-text-primary">{outcome.evidence_source}</span></span>}
          </div>
          </details>
        </div>
      ) : (
        <p className="text-xs text-text-muted">
          Not in outcome reports: {outcome.exclusion_label?.toLowerCase() ?? "excluded"}.
        </p>
      )}

      {outcome.flags.length > 0 && (
        <ul className="space-y-0.5">
          {outcome.flags.map((f) => (
            <li key={f} className="text-xs text-amber-700">{FLAG_LABELS[f] ?? f}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ── Notes panel ───────────────────────────────────────────────────────────────

function NotesPanel({ appId, initialNotes }: { appId: string; initialNotes: string }) {
  const [notes, setNotes]   = useState(initialNotes ?? "");
  const [saved, setSaved]   = useState(true);
  const [saving, setSaving] = useState(false);

  async function save(value: string) {
    setSaving(true);
    try {
      await fetch(`${API}/applications/${appId}`, {
        method:  "PATCH",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ notes: value }),
      });
      setSaved(true);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="bg-bg-surface border border-bg-border rounded-xl px-5 py-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-xs font-semibold text-text-muted uppercase tracking-wide">Notes</h2>
        {saving && <span className="text-[10px] text-text-muted">Saving…</span>}
        {!saving && !saved && <span className="text-[10px] text-text-muted">Unsaved</span>}
      </div>
      <textarea
        value={notes}
        onChange={(e) => { setNotes(e.target.value); setSaved(false); }}
        onBlur={(e) => { if (!saved) save(e.target.value); }}
        placeholder="Add any notes about this application…"
        rows={4}
        className="w-full px-3 py-2 text-sm text-text-primary bg-bg-elevated border border-bg-border rounded-lg resize-y focus:outline-none focus:ring-2 focus:ring-accent/40 placeholder-text-muted leading-relaxed"
      />
    </div>
  );
}

// ── History panel ─────────────────────────────────────────────────────────────

function HistoryPanel({ appId }: { appId: string }) {
  const [events, setEvents]   = useState<any[]>([]);
  const [emails, setEmails]   = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`${API}/applications/${appId}/events`)
      .then((r) => r.json())
      .then((data) => { setEvents(Array.isArray(data) ? data : []); setLoading(false); })
      .catch(() => setLoading(false));
    // Emails linked from the Inbox — accepted ones and any still waiting for review.
    fetch(`${API}/inbox?review_status=all&application_uuid=${appId}`)
      .then((r) => r.json())
      .then((data) => setEmails(Array.isArray(data) ? data.filter((e: any) => e.review_status === "accepted" || e.review_status === "pending") : []))
      .catch(() => {});
  }, [appId]);

  function describeEvent(event: any): string {
    if (event.event_type === "status_change") {
      if (event.from_status) {
        const from = STATUS_LABELS[event.from_status] ?? event.from_status;
        const to   = STATUS_LABELS[event.to_status]   ?? event.to_status;
        return `${from} → ${to}`;
      }
      return `Status set to ${STATUS_LABELS[event.to_status] ?? event.to_status}`;
    }
    return EVENT_TYPE_LABELS[event.event_type] ?? event.event_type.replace(/_/g, " ");
  }

  function formatDate(iso: string): string {
    try {
      return new Date(iso).toLocaleString("en-GB", {
        day: "2-digit", month: "short", year: "numeric",
        hour: "2-digit", minute: "2-digit",
      });
    } catch {
      return iso;
    }
  }

  return (
    <div className="bg-bg-elevated p-8 min-h-[40vh]">
      {emails.length > 0 && (
        <div className="mb-8">
          <h3 className="text-xs font-semibold text-text-muted uppercase tracking-wide mb-2">Emails</h3>
          <ul className="space-y-1.5">
            {emails.map((e) => (
              <li key={e.id} className="flex items-baseline gap-2 text-xs">
                <span className="text-text-muted font-mono shrink-0">{(e.received_at ?? "").slice(0, 10)}</span>
                <span className="text-text-primary truncate">{e.subject || "(no subject)"}</span>
                <span className="text-text-muted shrink-0">{(e.classification ?? "unclassified").replace(/_/g, " ")}</span>
                {e.review_status === "pending" && (
                  <Link href="/inbox" className="text-accent hover:underline shrink-0">review</Link>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {loading && (
        <p className="text-sm text-text-muted">Loading history…</p>
      )}
      {!loading && events.length === 0 && (
        <p className="text-sm text-text-muted">No history recorded yet.</p>
      )}
      {!loading && events.length > 0 && (
        <div className="relative">
          <div className="absolute left-[5px] top-2 bottom-2 w-px bg-bg-border" />
          <div className="space-y-4">
            {events.map((event) => (
              <div key={event.id} className="relative flex items-start gap-4">
                <div className="relative z-10 flex-shrink-0 mt-[3px] w-[11px] h-[11px] rounded-full border-2 border-accent/50 bg-bg-elevated" />
                <div className="min-w-0 pb-1">
                  <p className="text-xs font-medium text-text-primary leading-snug">
                    {describeEvent(event)}
                  </p>
                  {event.detail && (
                    <p className="text-xs text-text-muted mt-0.5 leading-snug">{event.detail}</p>
                  )}
                  <p className="text-[10px] text-text-muted font-mono mt-1">
                    {formatDate(event.occurred_at)}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Reasoning panel ───────────────────────────────────────────────────────────

function ReasoningPanel({ content, hasReasoning }: { content: string; hasReasoning: boolean }) {
  if (!hasReasoning || !content) {
    return (
      <div className="bg-bg-elevated p-8 min-h-[40vh] flex items-center justify-center">
        <div className="text-center max-w-sm space-y-2">
          <p className="text-sm font-medium text-text-secondary">Extended thinking was used</p>
          <p className="text-xs text-text-muted leading-relaxed">
            The model reasoned internally using its extended thinking budget before writing this CV.
            That reasoning is private to the model and not surfaced in the output — which means it worked as intended.
          </p>
        </div>
      </div>
    );
  }
  return (
    <div className="bg-bg-elevated p-8 min-h-[60vh] space-y-4">
      <div className="flex items-center gap-2 pb-3 border-b border-bg-border">
        <span className="text-xs font-semibold text-accent uppercase tracking-wide">Model reasoning</span>
        <span className="text-xs text-text-muted">— captured at generation time</span>
      </div>
      <div className="prose-cv max-w-none">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
      </div>
    </div>
  );
}

// ── JD panel ─────────────────────────────────────────────────────────────────

function JdPanel({ jdText }: { jdText: string }) {
  if (!jdText) {
    return (
      <div className="bg-bg-elevated p-8 min-h-[40vh] flex items-center justify-center">
        <p className="text-sm text-text-muted">No job description stored for this application.</p>
      </div>
    );
  }
  return (
    <div className="bg-bg-elevated p-8 min-h-[40vh]">
      <p className="text-sm text-text-primary whitespace-pre-wrap leading-relaxed">{jdText}</p>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function ApplicationPage() {
  const params = useParams();
  const id     = params.id as string;

  const [meta, setMeta]                 = useState<any>(null);
  const [sections, setSections]         = useState<any[]>([]);
  const [content, setContent]           = useState("");
  const [edited, setEdited]             = useState("");
  const [reasoning, setReasoning]       = useState<string>("");
  const [hasReasoning, setHasReasoning] = useState(false);
  const [view, setView]                 = useState<ViewMode>("preview");
  const [showAdvancedTabs, setShowAdvancedTabs] = useState(false);
  const [dirty, setDirty]               = useState(false);
  const [saving, setSaving]             = useState(false);
  const [rendering, setRendering]       = useState(false);
  const [hasPdf, setHasPdf]             = useState(false);
  const [error, setError]               = useState<string | null>(null);

  useEffect(() => { loadData(); }, [id]);

  async function loadData() {
    const [metaRes, cvRes, reasonRes, sectionsRes] = await Promise.all([
      fetch(`${API}/applications/${id}`),
      fetch(`${API}/applications/${id}/cv`),
      fetch(`${API}/applications/${id}/reasoning`),
      fetch(`${API}/sections/${id}`),
    ]);
    if (metaRes.ok) {
      const m = await metaRes.json();
      setMeta(m);
      setHasPdf(m.has_pdf);
    }
    if (cvRes.ok) {
      const c = await cvRes.json();
      setContent(c.content);
      setEdited(c.content);
    }
    if (reasonRes.ok) {
      const r = await reasonRes.json();
      setHasReasoning(r.has_reasoning);
      if (r.has_reasoning) setReasoning(r.content);
    }
    if (sectionsRes.ok) {
      const s = await sectionsRes.json();
      setSections(s);
    }
  }

  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      await fetch(`${API}/applications/${id}/cv`, {
        method:  "PUT",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ content: edited }),
      });
      setContent(edited);
      setDirty(false);
    } finally {
      setSaving(false);
    }
  }, [id, edited]);

  async function handleRender() {
    setRendering(true);
    setError(null);
    try {
      if (dirty) await handleSave();
      const res = await fetch(`${API}/render/${id}`, { method: "POST" });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || "Render failed");
      }
      setHasPdf(true);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setRendering(false);
    }
  }

  async function handleRoleDetailsSave(updates: Record<string, any>) {
    const res = await fetch(`${API}/applications/${id}`, {
      method:  "PATCH",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(updates),
    });
    if (res.ok) setMeta(await res.json());
    else setError("Couldn't save those details. Please try again.");
  }

  const hasJd = !!meta?.jd_text;

  // The tabs a person needs day to day, then the ones that are mostly for tinkering.
  const views: ViewMode[] = [
    "preview",
    "cover_letter",
    "questions",
    ...(hasJd ? (["jd"] as ViewMode[]) : []),
    "history",
  ];
  const advancedViews: ViewMode[] = ["raw", "reasoning"];
  const showAdvanced = showAdvancedTabs || advancedViews.includes(view);

  const VIEW_LABELS: Record<ViewMode, string> = {
    preview:      "CV",
    raw:          "Edit text",
    jd:           "Job ad",
    reasoning:    "🧠 Reasoning",
    history:      "History",
    cover_letter: "Cover letter",
    questions:    "Questions",
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3 sm:gap-4">
        <div>
          <div className="flex items-center gap-2 text-sm text-text-muted mb-1">
            <Link href="/" className="hover:text-accent transition-colors py-2">← Applications</Link>
          </div>
          <h1 className="text-xl font-semibold text-text-primary">
            {decodeEntities(meta?.company) || "Application"}
            {meta?.role && (
              <span className="block sm:inline font-normal text-text-secondary sm:ml-2">
                <span className="hidden sm:inline">— </span>{decodeEntities(meta.role)}
              </span>
            )}
          </h1>
          {meta && (
            <div className="mt-2">
              <StatusPicker
                uuid={id}
                status={meta.status}
                onUpdate={(newStatus: string) => setMeta((m: any) => m ? { ...m, status: newStatus } : m)}
              />
            </div>
          )}
          {meta?.generated_at && (
            <p className="text-xs text-text-muted mt-1.5">Generated {meta.generated_at}</p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2 sm:flex-shrink-0">
          {dirty && (
            <button
              onClick={handleSave}
              disabled={saving}
              className="min-h-11 sm:min-h-0 px-3 py-1.5 text-xs font-medium border border-accent text-accent rounded-lg hover:bg-accent/10 transition-colors disabled:opacity-50"
            >
              {saving ? "Saving…" : "Save changes"}
            </button>
          )}
          <button
            onClick={handleRender}
            disabled={rendering}
            className="min-h-11 sm:min-h-0 px-3 py-1.5 text-xs font-medium bg-accent text-white rounded-lg hover:bg-accent-dim transition-colors disabled:opacity-50 flex items-center gap-1.5"
          >
            {rendering
              ? <><span className="w-3 h-3 border border-white/30 border-t-white rounded-full animate-spin" />Rendering…</>
              : "Render PDF"}
          </button>
          {hasPdf && (
            <a
              href={`${API}/applications/${id}/pdf`}
              target="_blank"
              rel="noopener noreferrer"
              className="min-h-11 sm:min-h-0 inline-flex items-center px-3 py-1.5 text-xs font-medium bg-bg-elevated border border-bg-border text-text-secondary rounded-lg hover:text-text-primary transition-colors"
            >
              Download PDF
            </a>
          )}
        </div>
      </div>

      {error && (
        <div className="px-4 py-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{error}</div>
      )}

      {/* A reminder, never a block: a draft PDF is useful, but don't send an unreviewed CV */}
      {sections.length > 0 && sections.some((s: any) => !s.accepted_report_id) && (
        <div className="px-4 py-3 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-800">
          {sections.filter((s: any) => !!s.accepted_report_id).length} of {sections.length} sections
          reviewed and accepted. Check the rest before you send this CV.
        </div>
      )}

      {meta && <RoleDetails meta={meta} onSave={handleRoleDetailsSave} />}
      {meta && (
        <OutcomePanel
          appId={id}
          status={meta.status}
          outsideInbox={!!meta.outside_inbox}
          onFlagChange={(value) => handleRoleDetailsSave({ outside_inbox: value })}
        />
      )}
      {meta && <NotesPanel appId={id} initialNotes={meta.notes ?? ""} />}
      {sections.length > 0 && <SectionsPanel appId={id} sections={sections} />}

      {/* View toggle */}
      <div className="max-w-full overflow-x-auto">
        <div className="flex gap-1 bg-bg-surface rounded-lg p-1 w-fit border border-bg-border">
          {[...views, ...(showAdvanced ? advancedViews : [])].map((v) => (
            <button key={v} onClick={() => setView(v)}
              className={`shrink-0 min-h-11 sm:min-h-0 px-3 py-1 text-xs font-medium rounded-md transition-colors ${
                view === v ? "bg-white text-text-primary shadow-sm" : "text-text-secondary hover:text-text-primary"
              }`}>
              {VIEW_LABELS[v]}
            </button>
          ))}
          {!showAdvanced && (
            <button onClick={() => setShowAdvancedTabs(true)}
              className="shrink-0 min-h-11 sm:min-h-0 px-3 py-1 text-xs text-text-muted hover:text-text-primary">
              More…
            </button>
          )}
        </div>
      </div>

      {/* Content area — document views share a bordered container;
           panel views manage their own layout */}
      {(["preview", "raw", "jd", "reasoning", "history"] as ViewMode[]).includes(view) && (
        <div className="rounded-xl border border-bg-border overflow-hidden">
          {view === "preview" && (
            <div className="bg-bg-elevated p-4 sm:p-8 prose-cv max-w-none min-h-[60vh]">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
            </div>
          )}
          {view === "raw" && (
            <textarea
              value={edited}
              onChange={(e) => { setEdited(e.target.value); setDirty(e.target.value !== content); }}
              onBlur={() => { if (dirty) handleSave(); }}
              className="w-full h-[70vh] p-6 font-mono text-xs text-text-primary bg-bg-elevated resize-none focus:outline-none leading-relaxed"
              spellCheck={false}
            />
          )}
          {view === "jd"        && <JdPanel jdText={meta?.jd_text ?? ""} />}
          {view === "reasoning" && <ReasoningPanel content={reasoning} hasReasoning={hasReasoning} />}
          {view === "history"   && <HistoryPanel appId={id} />}
        </div>
      )}

      {view === "cover_letter" && <CoverLetterPanel appId={id} />}
      {view === "questions"    && <QuestionsPanel appId={id} />}
    </div>
  );
}
