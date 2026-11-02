"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { waitForJob, type Job } from "@/lib/jobs";

const API = api.url;

type InputMode = "paste" | "url";

// The stages of a generation, in order. The server doesn't report which one it is on, so
// these advance on a timer and settle on the last one rather than looping.
const STEPS = [
  "Reading the job ad…",
  "Choosing the most relevant experience…",
  "Writing your CV…",
  "Checking the wording and facts…",
  "Nearly there — still working…",
];

function GeneratingOverlay({ company, role }: { company: string; role: string }) {
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => setTick((t) => t + 1), 12000);
    return () => clearInterval(interval);
  }, []);

  const message = STEPS[Math.min(tick, STEPS.length - 1)];

  return (
    <div className="fixed inset-0 bg-bg-base/95 backdrop-blur-sm z-50 flex flex-col items-center justify-center gap-8 px-6">
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
      <p className="text-xs text-text-muted text-center max-w-xs">
        This usually takes about a minute. You can leave this page — it will still finish and
        appear in Applications.
      </p>
    </div>
  );
}

interface PreviousNote {
  text:    string;
  company: string;
  role:    string;
}

function PreviousNotes({ onSelect }: { onSelect: (text: string) => void }) {
  const [notes, setNotes] = useState<PreviousNote[]>([]);
  const [open, setOpen]   = useState(false);

  useEffect(() => {
    fetch(`${API}/applications/recent-notes`)
      .then((r) => r.json())
      .then((data) => setNotes(Array.isArray(data) ? data : []))
      .catch(() => {});
  }, []);

  if (notes.length === 0) return null;

  return (
    <div className="border border-bg-border rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-4 py-2.5 bg-bg-surface hover:bg-bg-elevated transition-colors text-left"
      >
        <span className="text-xs font-medium text-text-secondary">Previous instructions</span>
        <span className="text-text-muted text-xs">{open ? "▲" : "▼"}</span>
      </button>

      {open && (
        <div className="divide-y divide-bg-border bg-bg-elevated">
          {notes.map((note, i) => (
            <button
              key={i}
              onClick={() => onSelect(note.text)}
              className="w-full text-left px-4 py-3 hover:bg-bg-surface transition-colors group"
            >
              <p className="text-[10px] text-text-muted mb-1">{note.company} — {note.role}</p>
              <p className="text-xs text-text-secondary group-hover:text-text-primary transition-colors leading-relaxed">
                {note.text}
              </p>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function GeneratePage() {
  const router = useRouter();

  // A link is the easy thing to bring from a phone, so start there.
  const [mode, setMode]             = useState<InputMode>("url");
  const [jdText, setJdText]         = useState("");
  const [url, setUrl]               = useState("");
  const [company, setCompany]       = useState("");
  const [role, setRole]             = useState("");
  const [notes, setNotes]           = useState("");
  const [generating, setGenerating] = useState(false);
  const [drafting, setDrafting]     = useState(false);
  const [scraping, setScraping]     = useState(false);
  const [error, setError]           = useState<string | null>(null);

  // Opened from the phone's Share sheet (see app/manifest.ts): the link arrives as ?url=,
  // or inside ?text= (Android puts it there). Fetch the ad straight away.
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    const shared = p.get("url") || (p.get("text") ?? "").match(/https?:\/\/\S+/)?.[0];
    if (!shared) return;
    setUrl(shared);
    setMode("url");
    fetchFromUrl(shared);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function fetchFromUrl(target: string = url) {
    if (!target.trim()) return;
    setScraping(true);
    setError(null);
    try {
      const res  = await fetch(`${API}/scrape?url=${encodeURIComponent(target.trim())}`);
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

  function request(draft: boolean) {
    return {
      jd_text:          jdText,
      company:          company || "Unknown",
      role:             role    || "Unknown Role",
      generation_notes: notes.trim() || null,
      draft,
    };
  }

  async function handleGenerate() {
    if (!jdText.trim()) {
      setError("Please add a job description.");
      return;
    }
    setError(null);
    setGenerating(true);
    try {
      // Returns at once with a job; the CV is generated in the background.
      const job: Job = await api.post("/generate", request(false));
      const done     = await waitForJob(job.job_id);
      router.push(`/applications/${done.result.uuid}`);
    } catch (e: any) {
      setError(e.message);
      setGenerating(false);
    }
  }

  async function handleDraft() {
    if (!jdText.trim()) {
      setError("Please add a job description.");
      return;
    }
    setError(null);
    setDrafting(true);
    try {
      const job: Job = await api.post("/generate", request(true));
      router.push(`/drafts/${job.job_id}`);
    } catch (e: any) {
      setError(e.message);
      setDrafting(false);
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

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="space-y-1">
            <label className="text-xs font-medium text-text-secondary uppercase tracking-wide">Company</label>
            <input type="text" value={company} onChange={(e) => setCompany(e.target.value)}
              placeholder="e.g. Stripe" disabled={generating}
              className="w-full px-3 py-2 min-h-11 bg-bg-elevated border border-bg-border rounded-lg text-base sm:text-sm text-text-primary placeholder-text-muted focus:outline-none focus:ring-2 focus:ring-accent/40 disabled:opacity-50" />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-text-secondary uppercase tracking-wide">Role title</label>
            <input type="text" value={role} onChange={(e) => setRole(e.target.value)}
              placeholder="e.g. RevOps Manager" disabled={generating}
              className="w-full px-3 py-2 min-h-11 bg-bg-elevated border border-bg-border rounded-lg text-base sm:text-sm text-text-primary placeholder-text-muted focus:outline-none focus:ring-2 focus:ring-accent/40 disabled:opacity-50" />
          </div>
        </div>

        <div className="space-y-3">
          <div className="flex gap-1 bg-bg-surface rounded-lg p-1 w-fit border border-bg-border">
            {(["url", "paste"] as InputMode[]).map((m) => (
              <button key={m} onClick={() => setMode(m)} disabled={generating}
                className={`min-h-11 sm:min-h-0 px-4 sm:px-3 py-1 text-xs font-medium rounded-md transition-colors ${
                  mode === m ? "bg-white text-text-primary shadow-sm" : "text-text-secondary hover:text-text-primary"
                }`}>
                {m === "paste" ? "Paste text" : "From a link"}
              </button>
            ))}
          </div>

          {mode === "url" && (
            <div className="space-y-2">
              <div className="flex flex-col sm:flex-row gap-2">
                <input type="url" value={url} onChange={(e) => setUrl(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && fetchFromUrl()}
                  placeholder="https://company.com/jobs/role" disabled={generating}
                  className="flex-1 px-3 py-2 min-h-11 bg-bg-elevated border border-bg-border rounded-lg text-base sm:text-sm text-text-primary placeholder-text-muted focus:outline-none focus:ring-2 focus:ring-accent/40" />
                <button onClick={() => fetchFromUrl()} disabled={scraping || !url.trim() || generating}
                  className="min-h-11 justify-center px-4 py-2 bg-accent text-white text-sm font-medium rounded-lg hover:bg-accent-dim transition-colors disabled:opacity-50 flex items-center gap-2">
                  {scraping
                    ? <><span className="w-3 h-3 border border-white/30 border-t-white rounded-full animate-spin" />Fetching…</>
                    : "Fetch"}
                </button>
              </div>
              <p className="text-xs text-text-muted">
                Works best with a direct link to the job posting. If a site blocks it (LinkedIn often
                does), switch to "Paste text" and paste the ad instead.
              </p>
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
                className="w-full px-3 py-2 min-h-11 bg-bg-elevated border border-bg-border rounded-lg text-base sm:text-sm text-text-primary placeholder-text-muted focus:outline-none focus:ring-2 focus:ring-accent/40 resize-y font-mono leading-relaxed disabled:opacity-50" />
              <p className="text-xs text-text-muted">
                {jdText.length > 0 ? `${jdText.length.toLocaleString()} characters` : ""}
              </p>
            </div>
          )}
        </div>

        <div className="space-y-2">
          <div className="space-y-1">
            <label className="text-xs font-medium text-text-secondary uppercase tracking-wide">
              Notes for the AI{" "}
              <span className="normal-case font-normal text-text-muted ml-1">optional</span>
            </label>
            <textarea value={notes} onChange={(e) => setNotes(e.target.value)}
              placeholder={`e.g. "This role clearly values n8n experience — highlight integration work across the library."`}
              rows={3} disabled={generating}
              className="w-full px-3 py-2 min-h-11 bg-bg-elevated border border-bg-border rounded-lg text-base sm:text-sm text-text-primary placeholder-text-muted focus:outline-none focus:ring-2 focus:ring-accent/40 resize-y leading-relaxed disabled:opacity-50" />
          </div>
          <PreviousNotes onSelect={setNotes} />
        </div>

        {error && (
          <div className="px-4 py-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
            {error}
          </div>
        )}

        <div className="flex flex-col items-stretch gap-1">
          <button onClick={handleGenerate} disabled={generating || drafting || !jdText.trim()}
            className="min-h-12 py-3 bg-accent text-white font-medium rounded-lg hover:bg-accent-dim transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
            Generate CV
          </button>
          {/* The review-the-prompt flow is for tinkering, so it is a quiet secondary action */}
          <button onClick={handleDraft} disabled={generating || drafting || !jdText.trim()}
            title="Save the request as a draft and see exactly what will be sent to the AI before anything is generated"
            className="min-h-11 text-sm text-text-muted hover:text-text-primary underline-offset-2 hover:underline disabled:opacity-50 disabled:cursor-not-allowed">
            {drafting ? "Saving draft…" : "Check the request before sending"}
          </button>
        </div>
      </div>
    </>
  );
}
