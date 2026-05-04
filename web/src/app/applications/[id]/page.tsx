"use client";

import { useState, useEffect } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const API = process.env.NEXT_PUBLIC_API_URL;

type ViewMode = "preview" | "raw" | "pdf" | "reasoning";

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
          const flagCount = section.latest_report?.active_flags ?? null;
          const hasEval   = section.latest_report?.status !== "pending" || section.latest_report?.escalated;

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
          <div className="grid grid-cols-3 gap-2 items-center">
            <label className="text-xs text-text-secondary">Location</label>
            <input value={form.location} onChange={(e) => setForm((f) => ({ ...f, location: e.target.value }))}
              placeholder="e.g. London, UK"
              className="col-span-2 px-2 py-1.5 text-sm bg-white border border-bg-border rounded-md focus:outline-none focus:ring-1 focus:ring-accent/50" />
          </div>
          <div className="grid grid-cols-3 gap-2 items-center">
            <label className="text-xs text-text-secondary">Working</label>
            <div className="col-span-2 flex gap-1.5">
              {(["remote", "hybrid", "office"] as const).map((a) => (
                <button key={a} onClick={() => setForm((f) => ({ ...f, work_arrangement: a }))}
                  className={`px-3 py-1 text-xs rounded-full border transition-colors capitalize ${
                    form.work_arrangement === a ? "bg-accent text-white border-accent" : "border-bg-border text-text-secondary hover:border-accent/50"
                  }`}>
                  {ARRANGEMENT_LABELS[a]}
                </button>
              ))}
            </div>
          </div>
          {form.work_arrangement === "hybrid" && (
            <div className="grid grid-cols-3 gap-2 items-center">
              <label className="text-xs text-text-secondary">Days in office</label>
              <div className="col-span-2 flex gap-1.5">
                {[1,2,3,4,5].map((d) => (
                  <button key={d} onClick={() => setForm((f) => ({ ...f, hybrid_days: d }))}
                    className={`w-8 h-7 text-xs rounded-md border transition-colors ${
                      Number(form.hybrid_days) === d ? "bg-accent text-white border-accent" : "border-bg-border text-text-secondary hover:border-accent/50"
                    }`}>{d}</button>
                ))}
                <span className="text-xs text-text-muted self-center ml-1">days/wk</span>
              </div>
            </div>
          )}
          <div className="grid grid-cols-3 gap-2 items-center">
            <label className="text-xs text-text-secondary">Salary</label>
            <div className="col-span-2 flex gap-2 items-center">
              <select value={form.salary_currency} onChange={(e) => setForm((f) => ({ ...f, salary_currency: e.target.value }))}
                className="px-2 py-1.5 text-xs bg-white border border-bg-border rounded-md focus:outline-none">
                {CURRENCIES.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
              <input type="number" value={form.salary_min} onChange={(e) => setForm((f) => ({ ...f, salary_min: e.target.value }))}
                placeholder="Min" className="w-28 px-2 py-1.5 text-sm bg-white border border-bg-border rounded-md focus:outline-none" />
              <span className="text-xs text-text-muted">–</span>
              <input type="number" value={form.salary_max} onChange={(e) => setForm((f) => ({ ...f, salary_max: e.target.value }))}
                placeholder="Max" className="w-28 px-2 py-1.5 text-sm bg-white border border-bg-border rounded-md focus:outline-none" />
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
          <p className="text-xs text-text-muted mt-3">
            Reasoning only appears here when the model outputs it as text, which happens
            when extended thinking is disabled or the model uses the sentinel to separate its chain-of-thought.
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

// ── Main page ─────────────────────────────────────────────────────────────────

export default function ApplicationPage() {
  const params = useParams();
  const id     = params.id as string;

  const [meta, setMeta]               = useState<any>(null);
  const [sections, setSections]       = useState<any[]>([]);
  const [content, setContent]         = useState("");
  const [edited, setEdited]           = useState("");
  const [reasoning, setReasoning]     = useState<string>("");
  const [hasReasoning, setHasReasoning] = useState(false);
  const [view, setView]               = useState<ViewMode>("preview");
  const [dirty, setDirty]             = useState(false);
  const [saving, setSaving]           = useState(false);
  const [rendering, setRendering]     = useState(false);
  const [hasPdf, setHasPdf]           = useState(false);
  const [error, setError]             = useState<string | null>(null);

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

  async function handleSave() {
    setSaving(true);
    try {
      await fetch(`${API}/applications/${id}/cv`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: edited }),
      });
      setContent(edited);
      setDirty(false);
    } finally {
      setSaving(false);
    }
  }

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
      setView("pdf");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setRendering(false);
    }
  }

  async function handleRoleDetailsSave(updates: Record<string, any>) {
    const res = await fetch(`${API}/applications/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(updates),
    });
    if (res.ok) setMeta(await res.json());
  }

  const views: ViewMode[] = [
    "preview",
    "raw",
    ...(hasPdf ? (["pdf"] as ViewMode[]) : []),
    "reasoning",
  ];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 text-sm text-text-muted mb-1">
            <Link href="/" className="hover:text-accent transition-colors">Applications</Link>
            <span>/</span>
            <span className="text-text-secondary">{meta?.company || id}</span>
          </div>
          <h1 className="text-xl font-semibold text-text-primary">
            {meta?.company || "Application"}
            {meta?.role && <span className="font-normal text-text-secondary ml-2">— {meta.role}</span>}
          </h1>
          {meta?.generated_at && (
            <p className="text-xs text-text-muted mt-0.5">Generated {meta.generated_at}</p>
          )}
        </div>

        <div className="flex items-center gap-2 flex-shrink-0">
          {dirty && (
            <button onClick={handleSave} disabled={saving}
              className="px-3 py-1.5 text-xs font-medium border border-accent text-accent rounded-lg hover:bg-accent/10 transition-colors disabled:opacity-50">
              {saving ? "Saving…" : "Save changes"}
            </button>
          )}
          <button onClick={handleRender} disabled={rendering}
            className="px-3 py-1.5 text-xs font-medium bg-accent text-white rounded-lg hover:bg-accent-dim transition-colors disabled:opacity-50 flex items-center gap-1.5">
            {rendering
              ? <><span className="w-3 h-3 border border-white/30 border-t-white rounded-full animate-spin" />Rendering…</>
              : "Render PDF"}
          </button>
          {hasPdf && (
            <a href={`${API}/applications/${id}/pdf`} target="_blank" rel="noopener noreferrer"
              className="px-3 py-1.5 text-xs font-medium bg-bg-elevated border border-bg-border text-text-secondary rounded-lg hover:text-text-primary transition-colors">
              Download PDF
            </a>
          )}
        </div>
      </div>

      {error && (
        <div className="px-4 py-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{error}</div>
      )}

      {meta && <RoleDetails meta={meta} onSave={handleRoleDetailsSave} />}

      {/* Section review entry points */}
      {sections.length > 0 && <SectionsPanel appId={id} sections={sections} />}

      {/* View toggle */}
      <div className="flex gap-1 bg-bg-surface rounded-lg p-1 w-fit border border-bg-border">
        {views.map((v) => (
          <button key={v} onClick={() => setView(v)}
            className={`px-3 py-1 text-xs font-medium rounded-md capitalize transition-colors ${
              view === v ? "bg-white text-text-primary shadow-sm" : "text-text-secondary hover:text-text-primary"
            }`}>
            {v === "reasoning" ? "🧠 Reasoning" : v}
          </button>
        ))}
      </div>

      {/* Content area */}
      <div className="rounded-xl border border-bg-border overflow-hidden">
        {view === "preview" && (
          <div className="bg-bg-elevated p-8 prose-cv max-w-none min-h-[60vh]">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
          </div>
        )}
        {view === "raw" && (
          <textarea value={edited}
            onChange={(e) => { setEdited(e.target.value); setDirty(e.target.value !== content); }}
            className="w-full h-[70vh] p-6 font-mono text-xs text-text-primary bg-bg-elevated resize-none focus:outline-none leading-relaxed"
            spellCheck={false} />
        )}
        {view === "pdf" && hasPdf && (
          <iframe src={`${API}/applications/${id}/pdf`} className="w-full h-[85vh]" title="CV PDF" />
        )}
        {view === "reasoning" && (
          <ReasoningPanel content={reasoning} hasReasoning={hasReasoning} />
        )}
      </div>
    </div>
  );
}
