"use client";

import { useState, useEffect } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const API = process.env.NEXT_PUBLIC_API_URL;

type ViewMode  = "raw" | "preview";
type SaveState = "idle" | "saving" | "saved" | "error";

interface Signal {
  text:     string;
  source:   string;
  context:  string | null;
  count:    number;
}

// ── Signals panel ─────────────────────────────────────────────────────────────

function SignalsPanel({ slug }: { slug: string }) {
  const [signals, setSignals]   = useState<Signal[]>([]);
  const [loading, setLoading]   = useState(true);
  const [open, setOpen]         = useState(true);

  useEffect(() => {
    fetch(`${API}/prompts/${slug}/signals`)
      .then((r) => r.json())
      .then((data) => {
        setSignals(Array.isArray(data) ? data : []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [slug]);

  const hasSignals = signals.length > 0;

  return (
    <div className="border border-bg-border rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-5 py-3 bg-bg-surface hover:bg-bg-elevated transition-colors text-left"
      >
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-text-muted uppercase tracking-wide">
            Signals
          </span>
          {!loading && hasSignals && (
            <span className="text-[10px] font-medium text-text-muted bg-bg-elevated border border-bg-border px-1.5 py-0.5 rounded-full">
              {signals.length}
            </span>
          )}
        </div>
        <span className="text-text-muted text-xs">{open ? "▲" : "▼"}</span>
      </button>

      {open && (
        <div className="bg-bg-elevated divide-y divide-bg-border">
          {loading && (
            <p className="px-5 py-4 text-xs text-text-muted">Loading signals…</p>
          )}
          {!loading && !hasSignals && (
            <p className="px-5 py-4 text-xs text-text-muted italic">
              No signals yet — they appear as you generate CVs and review sections.
            </p>
          )}
          {!loading && signals.map((signal, i) => (
            <div key={i} className="px-5 py-3 space-y-1">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[10px] font-medium text-text-muted bg-bg-surface border border-bg-border px-1.5 py-0.5 rounded">
                  {signal.source}
                </span>
                {signal.count > 1 && (
                  <span className="text-[10px] font-medium text-accent bg-accent/5 border border-accent/20 px-1.5 py-0.5 rounded">
                    ×{signal.count}
                  </span>
                )}
                {signal.context && (
                  <span className="text-[10px] text-text-muted">{signal.context}</span>
                )}
              </div>
              <p className="text-xs text-text-secondary leading-relaxed">{signal.text}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function PromptEditorPage() {
  const params    = useParams();
  const slugParts = Array.isArray(params.slug) ? params.slug : [params.slug];
  const slug      = slugParts.join("/");

  const [label, setLabel]         = useState("");
  const [personal, setPersonal]   = useState(false);
  const [content, setContent]     = useState("");
  const [edited, setEdited]       = useState("");
  const [view, setView]           = useState<ViewMode>("raw");
  const [dirty, setDirty]         = useState(false);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [error, setError]         = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API}/prompts/${slug}`)
      .then((r) => {
        if (!r.ok) throw new Error("Prompt not found");
        return r.json();
      })
      .then((data) => {
        setLabel(data.label);
        setPersonal(data.personal);
        setContent(data.content);
        setEdited(data.content);
      })
      .catch((e) => setError(e.message));
  }, [slug]);

  async function save() {
    setSaveState("saving");
    setError(null);
    try {
      const res = await fetch(`${API}/prompts/${slug}`, {
        method:  "PUT",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ content: edited }),
      });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || "Save failed");
      }
      setContent(edited);
      setDirty(false);
      setSaveState("saved");
      setTimeout(() => setSaveState("idle"), 3000);
    } catch (e: any) {
      setError(e.message);
      setSaveState("error");
    }
  }

  return (
    <div className="max-w-4xl mx-auto space-y-6">

      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 text-sm text-text-muted mb-1">
            <Link href="/prompts" className="hover:text-accent transition-colors">
              Prompts
            </Link>
            <span>/</span>
            <span className="text-text-secondary">{label || slug}</span>
          </div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-semibold text-text-primary">
              {label || slug}
            </h1>
            {personal && (
              <span className="text-xs font-medium text-amber-600 bg-amber-50 border border-amber-200 px-2 py-0.5 rounded">
                personal · not committed
              </span>
            )}
          </div>
        </div>

        <div className="flex items-center gap-3 flex-shrink-0">
          {saveState === "saved" && (
            <span className="text-sm text-accent">Saved</span>
          )}
          {saveState === "error" && (
            <span className="text-sm text-red-600">Save failed</span>
          )}
          <button
            onClick={save}
            disabled={!dirty || saveState === "saving"}
            className="px-4 py-1.5 text-xs font-medium bg-accent text-white rounded-lg hover:bg-accent-dim transition-colors disabled:opacity-40"
          >
            {saveState === "saving" ? "Saving…" : "Save"}
          </button>
        </div>
      </div>

      {error && (
        <div className="px-4 py-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
          {error}
        </div>
      )}

      {/* View toggle */}
      <div className="flex gap-1 bg-bg-surface rounded-lg p-1 w-fit border border-bg-border">
        {(["raw", "preview"] as ViewMode[]).map((v) => (
          <button
            key={v}
            onClick={() => setView(v)}
            className={`px-3 py-1 text-xs font-medium rounded-md capitalize transition-colors ${
              view === v
                ? "bg-white text-text-primary shadow-sm"
                : "text-text-secondary hover:text-text-primary"
            }`}
          >
            {v}
          </button>
        ))}
      </div>

      {/* Editor */}
      <div className="rounded-xl border border-bg-border overflow-hidden">
        {view === "raw" && (
          <textarea
            value={edited}
            onChange={(e) => {
              setEdited(e.target.value);
              setDirty(e.target.value !== content);
            }}
            className="w-full h-[60vh] p-6 font-mono text-xs text-text-primary bg-bg-elevated resize-none focus:outline-none leading-relaxed"
            spellCheck={false}
            placeholder="Prompt is empty — start typing to create it."
          />
        )}
        {view === "preview" && (
          <div className="bg-bg-elevated p-8 min-h-[60vh] prose-cv max-w-none">
            {edited
              ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{edited}</ReactMarkdown>
              : <p className="text-sm text-text-muted italic">Nothing to preview yet.</p>
            }
          </div>
        )}
      </div>

      {/* Signals panel */}
      <SignalsPanel slug={slug} />

    </div>
  );
}
