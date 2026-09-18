"use client";

import { useState, useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useRouter } from "next/navigation";
import { createDraft, runJob } from "@/lib/jobs";

const API = process.env.NEXT_PUBLIC_API_URL;

// ── Types ─────────────────────────────────────────────────────────────────────

type CoverLetterStatus = "draft" | "generated" | "edited";
type CoverLetterView   = "preview" | "raw" | "research";

interface CoverLetter {
  id:               string;
  application_id:   string;
  markdown:         string | null;
  status:           CoverLetterStatus;
  research_company: number;
  research_role:    number;
  draft_input:      string | null;
  key_points:       string | null;
  research_brief:   string | null;
  model:            string | null;
  generated_at:     string | null;
  has_pdf:          boolean;
}

// ── Generate form ─────────────────────────────────────────────────────────────

function GenerateForm({
  researchCompany,
  setResearchCompany,
  researchRole,
  setResearchRole,
  draftInput,
  setDraftInput,
  keyPoints,
  setKeyPoints,
  generating,
  onGenerate,
  onDraft,
  error,
  compact = false,
}: {
  researchCompany:    boolean;
  setResearchCompany: (v: boolean) => void;
  researchRole:       boolean;
  setResearchRole:    (v: boolean) => void;
  draftInput:         string;
  setDraftInput:      (v: string) => void;
  keyPoints:          string;
  setKeyPoints:       (v: string) => void;
  generating:         boolean;
  onGenerate:         () => void;
  onDraft:            () => void;
  error:              string | null;
  compact?:           boolean;
}) {
  const researchActive = researchCompany || researchRole;

  return (
    <div className="bg-bg-surface border border-bg-border rounded-xl px-5 py-4 space-y-4">
      {!compact && (
        <div>
          <h2 className="text-sm font-semibold text-text-primary">Generate cover letter</h2>
          <p className="text-xs text-text-muted mt-0.5 leading-relaxed">
            Uses your CV, experience library, and job description. Enable research to pull
            current company and role context via web search before writing.
          </p>
        </div>
      )}

      {/* Research toggles */}
      <div className="space-y-2">
        {!compact && (
          <p className="text-xs font-medium text-text-secondary">Phase 1 — Research</p>
        )}
        <div className="flex gap-5">
          <label className="flex items-center gap-2 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={researchCompany}
              onChange={(e) => setResearchCompany(e.target.checked)}
              className="w-3.5 h-3.5 rounded border-bg-border accent-accent"
            />
            <span className="text-xs text-text-secondary">Research company</span>
          </label>
          <label className="flex items-center gap-2 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={researchRole}
              onChange={(e) => setResearchRole(e.target.checked)}
              className="w-3.5 h-3.5 rounded border-bg-border accent-accent"
            />
            <span className="text-xs text-text-secondary">Research role</span>
          </label>
        </div>
      </div>

      {/* Optional inputs */}
      {!compact && (
        <div>
          <p className="text-xs font-medium text-text-secondary mb-2">Phase 2 — Generation inputs (optional)</p>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <label className="text-xs text-text-secondary">
                Rough draft <span className="text-text-muted font-normal">— paste notes or a first attempt</span>
              </label>
              <textarea
                value={draftInput}
                onChange={(e) => setDraftInput(e.target.value)}
                placeholder="A rough draft or key thoughts you want woven in…"
                rows={4}
                className="w-full px-3 py-2 text-sm bg-bg-elevated border border-bg-border rounded-lg resize-y focus:outline-none focus:ring-2 focus:ring-accent/40 placeholder-text-muted leading-relaxed text-text-primary"
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs text-text-secondary">
                Key points <span className="text-text-muted font-normal">— specific things to address</span>
              </label>
              <textarea
                value={keyPoints}
                onChange={(e) => setKeyPoints(e.target.value)}
                placeholder="Bullet points or specific requirements you want the letter to address…"
                rows={4}
                className="w-full px-3 py-2 text-sm bg-bg-elevated border border-bg-border rounded-lg resize-y focus:outline-none focus:ring-2 focus:ring-accent/40 placeholder-text-muted leading-relaxed text-text-primary"
              />
            </div>
          </div>
        </div>
      )}

      {error && (
        <div className="px-4 py-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
          {error}
        </div>
      )}

      <div className="flex items-center gap-3">
        <button
          onClick={onGenerate}
          disabled={generating}
          className="px-4 py-2 text-xs font-medium bg-accent text-white rounded-lg hover:bg-accent-dim transition-colors disabled:opacity-50 flex items-center gap-2"
        >
          {generating ? (
            <>
              <span className="w-3 h-3 border border-white/30 border-t-white rounded-full animate-spin" />
              {researchActive ? "Researching and generating…" : "Generating…"}
            </>
          ) : (
            compact ? "Generate new version" : "Generate cover letter"
          )}
        </button>
        <button
          onClick={onDraft}
          disabled={generating}
          title="Save as a draft to see exactly what will be sent to the model, and edit it, before anything is generated"
          className="px-4 py-2 text-xs font-medium bg-bg-elevated border border-bg-border text-text-secondary rounded-lg hover:text-text-primary transition-colors disabled:opacity-50"
        >
          Review prompt first
        </button>
        {researchActive && !generating && (
          <span className="text-[10px] text-text-muted">
            Research runs first — may take up to 30s
          </span>
        )}
      </div>
    </div>
  );
}

// ── Main panel ────────────────────────────────────────────────────────────────

export default function CoverLetterPanel({ appId }: { appId: string }) {
  const router                    = useRouter();
  const [cl, setCl]               = useState<CoverLetter | null>(null);
  const [loading, setLoading]     = useState(true);
  const [generating, setGenerating] = useState(false);
  const [rendering, setRendering]   = useState(false);
  const [error, setError]           = useState<string | null>(null);
  const [view, setView]             = useState<CoverLetterView>("preview");
  const [showRegenForm, setShowRegenForm] = useState(false);

  // Generation inputs — pre-populated from last generation when the cover letter loads
  const [researchCompany, setResearchCompany] = useState(true);
  const [researchRole, setResearchRole]       = useState(true);
  const [draftInput, setDraftInput]           = useState("");
  const [keyPoints, setKeyPoints]             = useState("");

  // Raw edit state
  const [edited, setEdited]   = useState("");
  const [dirty, setDirty]     = useState(false);
  const [saving, setSaving]   = useState(false);
  const saveTimer              = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => { loadCoverLetter(); }, [appId]);

  async function loadCoverLetter() {
    setLoading(true);
    try {
      const res = await fetch(`${API}/cover-letter/${appId}`);
      if (res.ok) {
        const data: CoverLetter = await res.json();
        hydrate(data);
      }
    } finally {
      setLoading(false);
    }
  }

  function hydrate(data: CoverLetter) {
    setCl(data);
    setEdited(data.markdown ?? "");
    setDirty(false);
    setResearchCompany(!!data.research_company);
    setResearchRole(!!data.research_role);
    setDraftInput(data.draft_input ?? "");
    setKeyPoints(data.key_points ?? "");
  }

  function generationParams() {
    return {
      research_company: researchCompany,
      research_role:    researchRole,
      draft_input:      draftInput.trim() || null,
      key_points:       keyPoints.trim()  || null,
    };
  }

  async function draft() {
    setError(null);
    try {
      const jobId = await createDraft("generate_cover_letter", { app_uuid: appId }, generationParams());
      router.push(`/drafts/${jobId}`);
    } catch (e: any) {
      setError(e.message);
    }
  }

  async function generate() {
    setGenerating(true);
    setError(null);
    try {
      // Queued as a background job; runJob follows it until the letter is written.
      const data: CoverLetter = await runJob(`/cover-letter/${appId}/generate`, generationParams());
      hydrate(data);
      setShowRegenForm(false);
      setView("preview");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setGenerating(false);
    }
  }

  async function saveMarkdown(value: string) {
    setSaving(true);
    try {
      const res = await fetch(`${API}/cover-letter/${appId}`, {
        method:  "PUT",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ markdown: value }),
      });
      if (res.ok) {
        const data: CoverLetter = await res.json();
        setCl(data);
        setDirty(false);
      }
    } finally {
      setSaving(false);
    }
  }

  function handleRawChange(value: string) {
    setEdited(value);
    setDirty(true);
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => saveMarkdown(value), 1200);
  }

  async function handleRawBlur() {
    if (!dirty) return;
    if (saveTimer.current) clearTimeout(saveTimer.current);
    await saveMarkdown(edited);
  }

  async function handleRender() {
    setRendering(true);
    setError(null);
    try {
      if (dirty) await saveMarkdown(edited);
      const res = await fetch(`${API}/cover-letter/${appId}/render`, { method: "POST" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "Render failed");
      }
      setCl((prev) => prev ? { ...prev, has_pdf: true } : prev);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setRendering(false);
    }
  }

  // ── Render ────────────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="bg-bg-elevated rounded-xl border border-bg-border p-8 min-h-[30vh] flex items-center justify-center">
        <p className="text-sm text-text-muted">Loading…</p>
      </div>
    );
  }

  const hasContent = !!cl?.markdown;
  const hasResearch = !!cl?.research_brief;

  // No content yet — show generation form only
  if (!hasContent) {
    return (
      <GenerateForm
        researchCompany={researchCompany}
        setResearchCompany={setResearchCompany}
        researchRole={researchRole}
        setResearchRole={setResearchRole}
        draftInput={draftInput}
        setDraftInput={setDraftInput}
        keyPoints={keyPoints}
        setKeyPoints={setKeyPoints}
        generating={generating}
        onGenerate={generate}
        onDraft={draft}
        error={error}
      />
    );
  }

  const views: CoverLetterView[] = [
    "preview",
    "raw",
    ...(hasResearch ? (["research"] as CoverLetterView[]) : []),
  ];

  const VIEW_LABELS: Record<CoverLetterView, string> = {
    preview:  "Preview",
    raw:      "Raw",
    research: "Research Brief",
  };

  return (
    <div className="space-y-4">

      {/* Action bar */}
      <div className="flex items-center gap-2 flex-wrap">
        <button
          onClick={() => setShowRegenForm((v) => !v)}
          className="px-3 py-1.5 text-xs font-medium border border-bg-border text-text-secondary rounded-lg hover:text-text-primary hover:border-accent/50 transition-colors flex items-center gap-1"
        >
          Re-generate
          <span className="opacity-40 text-[10px]">{showRegenForm ? "▴" : "▾"}</span>
        </button>
        <button
          onClick={handleRender}
          disabled={rendering}
          className="px-3 py-1.5 text-xs font-medium bg-accent text-white rounded-lg hover:bg-accent-dim transition-colors disabled:opacity-50 flex items-center gap-1.5"
        >
          {rendering
            ? <><span className="w-3 h-3 border border-white/30 border-t-white rounded-full animate-spin" />Rendering…</>
            : "Render PDF"}
        </button>
        {cl.has_pdf && (
          <a
            href={`${API}/cover-letter/${appId}/pdf`}
            target="_blank"
            rel="noopener noreferrer"
            className="px-3 py-1.5 text-xs font-medium bg-bg-elevated border border-bg-border text-text-secondary rounded-lg hover:text-text-primary transition-colors"
          >
            Download PDF
          </a>
        )}
        <div className="ml-auto flex items-center gap-3">
          {saving   && <span className="text-[10px] text-text-muted">Saving…</span>}
          {!saving && dirty && <span className="text-[10px] text-text-muted">Unsaved</span>}
          {cl.generated_at && (
            <span className="text-[10px] text-text-muted">
              Generated {new Date(cl.generated_at).toLocaleDateString("en-GB", {
                day: "2-digit", month: "short", year: "numeric",
              })}
            </span>
          )}
          {cl.model && (
            <span className="text-[10px] text-text-muted font-mono">{cl.model}</span>
          )}
        </div>
      </div>

      {error && (
        <div className="px-4 py-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Re-generate form — collapsed by default after first generation */}
      {showRegenForm && (
        <GenerateForm
          researchCompany={researchCompany}
          setResearchCompany={setResearchCompany}
          researchRole={researchRole}
          setResearchRole={setResearchRole}
          draftInput={draftInput}
          setDraftInput={setDraftInput}
          keyPoints={keyPoints}
          setKeyPoints={setKeyPoints}
          generating={generating}
          onGenerate={generate}
          onDraft={draft}
          error={null}
          compact
        />
      )}

      {/* Internal view tabs */}
      <div className="flex gap-1 bg-bg-surface rounded-lg p-1 w-fit border border-bg-border">
        {views.map((v) => (
          <button
            key={v}
            onClick={() => setView(v)}
            className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${
              view === v
                ? "bg-white text-text-primary shadow-sm"
                : "text-text-secondary hover:text-text-primary"
            }`}
          >
            {VIEW_LABELS[v]}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="rounded-xl border border-bg-border overflow-hidden">
        {view === "preview" && (
          <div className="bg-bg-elevated p-8 prose-cv max-w-none min-h-[50vh]">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{cl.markdown ?? ""}</ReactMarkdown>
          </div>
        )}
        {view === "raw" && (
          <textarea
            value={edited}
            onChange={(e) => handleRawChange(e.target.value)}
            onBlur={handleRawBlur}
            className="w-full h-[60vh] p-6 font-mono text-xs text-text-primary bg-bg-elevated resize-none focus:outline-none leading-relaxed"
            spellCheck={false}
          />
        )}
        {view === "research" && (
          <div className="bg-bg-elevated p-8 min-h-[40vh]">
            <div className="flex items-center gap-2 pb-3 mb-4 border-b border-bg-border">
              <span className="text-xs font-semibold text-accent uppercase tracking-wide">
                Research brief
              </span>
              <span className="text-xs text-text-muted">— captured at generation time</span>
            </div>
            <div className="prose-cv max-w-none">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {cl.research_brief ?? ""}
              </ReactMarkdown>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
