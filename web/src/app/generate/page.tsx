"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";

const API = process.env.NEXT_PUBLIC_API_URL;

type InputMode = "paste" | "url";

const LOADING_MESSAGES = [
  // Genuine
  "Reading the job description…",
  "Auditing your experience library…",
  "Selecting the best persona…",
  "Grounding every claim in your history…",
  "Running anti-hallucination checks…",
  "Calibrating tone and register…",
  "Writing your tailored CV…",
  "Checking for forbidden words…",
  // Fun
  "Actually hallucinating (don't worry, we caught it)…",
  "Applying on your behalf without consent…",
  "Submitting Elon Musk's CV by mistake…",
  "Contemplating the meaning of work…",
  "Wondering if you really want this job…",
  "Briefly considering a career in goat farming…",
  "Reading your entire LinkedIn history and judging you…",
  "Asking the other AI for a second opinion…",
  "Suppressing the urge to add 'rockstar' to your title…",
  "Removing seventeen uses of the word 'leverage'…",
  "Debating whether Java counts as a personality…",
  "Googling what CCaaS means (just kidding, we know)…",
  "Checking if Salesforce is still a thing…",
  "Pretending to read the job description a second time…",
  "Quietly judging the company's Glassdoor rating…",
  "Negotiating salary on your behalf (going great)…",
  "Adding 'visionary' then immediately removing it…",
  "Making you sound impressive without lying (hardest part)…",
  "Cross-referencing with parallel universe where you got the last job…",
];

// ── Loading overlay ───────────────────────────────────────────────────────────

function GeneratingOverlay({ company, role }: { company: string; role: string }) {
  const [tick, setTick]    = useState(0);
  const [startIdx]         = useState(() => Math.floor(Math.random() * LOADING_MESSAGES.length));

  useEffect(() => {
    const interval = setInterval(() => setTick((t) => t + 1), 5000);
    return () => clearInterval(interval);
  }, []);

  const message = LOADING_MESSAGES[(startIdx + tick) % LOADING_MESSAGES.length];

  return (
    <div className="fixed inset-0 bg-bg-base/95 backdrop-blur-sm z-50 flex flex-col items-center justify-center gap-8">
      <div className="relative">
        <div className="w-16 h-16 rounded-full border-4 border-bg-border border-t-accent animate-spin" />
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="w-3 h-3 rounded-full bg-accent" />
        </div>
      </div>

      <div className="text-center space-y-2 max-w-sm">
        <p className="text-lg font-semibold text-text-primary">Generating your CV</p>
        {(company || role) && (
          <p className="text-sm text-text-secondary">
            {company && <span className="font-medium">{company}</span>}
            {company && role && " — "}
            {role}
          </p>
        )}
        <p key={tick} className="text-sm text-text-muted mt-4 animate-fade-in">{message}</p>
      </div>

      <p className="text-xs text-text-muted">
        This usually takes 30–60 seconds with extended thinking enabled.
      </p>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function GeneratePage() {
  const router = useRouter();

  const [mode, setMode]           = useState<InputMode>("paste");
  const [jdText, setJdText]       = useState("");
  const [url, setUrl]             = useState("");
  const [company, setCompany]     = useState("");
  const [role, setRole]           = useState("");
  const [notes, setNotes]         = useState("");
  const [generating, setGenerating] = useState(false);
  const [scraping, setScraping]   = useState(false);
  const [error, setError]         = useState<string | null>(null);

  async function fetchFromUrl() {
    if (!url.trim()) return;
    setScraping(true);
    setError(null);
    try {
      const res  = await fetch(`${API}/scrape?url=${encodeURIComponent(url.trim())}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Scrape failed");
      setJdText(data.text);
      if (data.title && !company && !role) {
        const hint = data.title.split("|")[0].split("–")[0].trim();
        if (hint.length < 80) setRole(hint);
      }
      setMode("paste");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setScraping(false);
    }
  }

  async function handleGenerate() {
    if (!jdText.trim()) {
      setError("Please add a job description.");
      return;
    }
    setError(null);
    setGenerating(true);
    try {
      const res = await fetch(`${API}/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          jd_text:          jdText,
          company:          company || "Unknown",
          role:             role    || "Unknown Role",
          generation_notes: notes.trim() || null,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Generation failed");
      router.push(`/applications/${data.app_id}`);
    } catch (e: any) {
      setError(e.message);
      setGenerating(false);
    }
  }

  return (
    <>
      {generating && <GeneratingOverlay company={company} role={role} />}

      <div className="max-w-3xl mx-auto space-y-6">
        <div>
          <h1 className="text-2xl font-semibold text-text-primary">New Application</h1>
          <p className="text-sm text-text-secondary mt-1">
            Add a job description and ATAT will generate a tailored CV.
          </p>
        </div>

        {/* Company + Role */}
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1">
            <label className="text-xs font-medium text-text-secondary uppercase tracking-wide">Company</label>
            <input type="text" value={company} onChange={(e) => setCompany(e.target.value)}
              placeholder="e.g. Stripe" disabled={generating}
              className="w-full px-3 py-2 bg-bg-elevated border border-bg-border rounded-lg text-sm text-text-primary placeholder-text-muted focus:outline-none focus:ring-2 focus:ring-accent/40 disabled:opacity-50" />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-text-secondary uppercase tracking-wide">Role title</label>
            <input type="text" value={role} onChange={(e) => setRole(e.target.value)}
              placeholder="e.g. RevOps Manager" disabled={generating}
              className="w-full px-3 py-2 bg-bg-elevated border border-bg-border rounded-lg text-sm text-text-primary placeholder-text-muted focus:outline-none focus:ring-2 focus:ring-accent/40 disabled:opacity-50" />
          </div>
        </div>

        {/* Mode toggle */}
        <div className="space-y-3">
          <div className="flex gap-1 bg-bg-surface rounded-lg p-1 w-fit border border-bg-border">
            {(["paste", "url"] as InputMode[]).map((m) => (
              <button key={m} onClick={() => setMode(m)} disabled={generating}
                className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${
                  mode === m ? "bg-white text-text-primary shadow-sm" : "text-text-secondary hover:text-text-primary"
                }`}>
                {m === "paste" ? "Paste JD" : "From URL"}
              </button>
            ))}
          </div>

          {mode === "url" && (
            <div className="space-y-2">
              <div className="flex gap-2">
                <input type="url" value={url} onChange={(e) => setUrl(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && fetchFromUrl()}
                  placeholder="https://company.com/jobs/role" disabled={generating}
                  className="flex-1 px-3 py-2 bg-bg-elevated border border-bg-border rounded-lg text-sm text-text-primary placeholder-text-muted focus:outline-none focus:ring-2 focus:ring-accent/40" />
                <button onClick={fetchFromUrl} disabled={scraping || !url.trim() || generating}
                  className="px-4 py-2 bg-accent text-white text-sm font-medium rounded-lg hover:bg-accent-dim transition-colors disabled:opacity-50 flex items-center gap-2">
                  {scraping ? (
                    <><span className="w-3 h-3 border border-white/30 border-t-white rounded-full animate-spin" />Fetching…</>
                  ) : "Fetch"}
                </button>
              </div>
              <p className="text-xs text-text-muted">Works best with direct job posting URLs. LinkedIn may block access.</p>
            </div>
          )}

          {(mode === "paste" || jdText) && (
            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <label className="text-xs font-medium text-text-secondary uppercase tracking-wide">Job Description</label>
                {jdText && mode === "url" && (
                  <button onClick={() => { setJdText(""); setMode("url"); }}
                    className="text-xs text-text-muted hover:text-text-secondary">Clear</button>
                )}
              </div>
              <textarea value={jdText} onChange={(e) => setJdText(e.target.value)}
                placeholder="Paste the full job description here…" rows={16} disabled={generating}
                className="w-full px-3 py-2 bg-bg-elevated border border-bg-border rounded-lg text-sm text-text-primary placeholder-text-muted focus:outline-none focus:ring-2 focus:ring-accent/40 resize-y font-mono leading-relaxed disabled:opacity-50" />
              <p className="text-xs text-text-muted">
                {jdText.length > 0 ? `${jdText.length.toLocaleString()} characters` : ""}
              </p>
            </div>
          )}
        </div>

        {/* Notes */}
        <div className="space-y-1">
          <label className="text-xs font-medium text-text-secondary uppercase tracking-wide">
            Notes for the AI{" "}
            <span className="normal-case font-normal text-text-muted ml-1">optional</span>
          </label>
          <textarea value={notes} onChange={(e) => setNotes(e.target.value)}
            placeholder={`e.g. "This role clearly values n8n experience — highlight integration work across the library."`}
            rows={3} disabled={generating}
            className="w-full px-3 py-2 bg-bg-elevated border border-bg-border rounded-lg text-sm text-text-primary placeholder-text-muted focus:outline-none focus:ring-2 focus:ring-accent/40 resize-none leading-relaxed disabled:opacity-50" />
          <p className="text-xs text-text-muted">Guidance for the model on this specific application. Stored for reference.</p>
        </div>

        {error && (
          <div className="px-4 py-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{error}</div>
        )}

        <button onClick={handleGenerate} disabled={generating || !jdText.trim()}
          className="w-full py-3 bg-accent text-white font-medium rounded-lg hover:bg-accent-dim transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2">
          Generate CV
        </button>
      </div>
    </>
  );
}
