"use client";

import { useState, useEffect, useCallback } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { api } from "@/lib/api";

// ── Types ─────────────────────────────────────────────────────────────────────

type FlagStatus = "active" | "dismissed" | "actioned";
type FlagType   = "hotword" | "em_dash" | "sentence_length" | "readability" | "accuracy";

interface Flag {
  id:               string;
  evaluation_id:    string;
  type:             FlagType;
  start_pos:        number;
  end_pos:          number;
  excerpt:          string;
  message:          string;
  status:           FlagStatus;
  user_comment:     string | null;
  dismissal_reason: string | null;
}

interface Evaluation {
  id:                string;
  tier:              string;
  passed:            boolean;
  flesch_score:      number | null;
  model:             string | null;
  flags:             Flag[];
}

interface Report {
  id:                string;
  attempt:           number;
  status:            string;
  section_name:      string;
  escalated:         boolean;
  escalation_reason: string | null;
  generated_text:    string | null;
  evaluations:       Evaluation[];
  all_flags:         Flag[];
  total_flags:       number;
  active_flags:      number;
}

interface ReportSummary {
  id:                string;
  attempt:           number;
  status:            string;
  escalated:         boolean;
  escalation_reason: string | null;
}

// ── Flag styling ──────────────────────────────────────────────────────────────

const FLAG_STYLES: Record<FlagType, { bg: string; border: string; label: string; pill: string }> = {
  hotword:         { bg: "bg-amber-100",  border: "border-amber-300",  label: "Hotword",         pill: "bg-amber-200 text-amber-800"  },
  em_dash:         { bg: "bg-orange-100", border: "border-orange-300", label: "Em dash",         pill: "bg-orange-200 text-orange-800" },
  sentence_length: { bg: "bg-blue-100",   border: "border-blue-300",   label: "Sentence length", pill: "bg-blue-200 text-blue-800"     },
  readability:     { bg: "bg-purple-100", border: "border-purple-300", label: "Readability",     pill: "bg-purple-200 text-purple-800" },
  accuracy:        { bg: "bg-red-100",    border: "border-red-300",    label: "Accuracy",        pill: "bg-red-200 text-red-800"       },
};

const defaultStyle = { bg: "bg-gray-100", border: "border-gray-300", label: "Issue", pill: "bg-gray-200 text-gray-800" };

function flagStyle(type: FlagType) {
  return FLAG_STYLES[type] ?? defaultStyle;
}

// ── Text annotator ────────────────────────────────────────────────────────────

/**
 * Only readability flags are truly document-level — they are computed across
 * the full text and have no meaningful inline position.
 *
 * All other flag types (hotword, em_dash, sentence_length, accuracy) have
 * precise character spans and must always be rendered inline, even when
 * start_pos=0 and end_pos=text.length (e.g. a sentence_length flag on a
 * single-paragraph section that spans the full content).
 */
function isDocumentLevel(flag: Flag): boolean {
  return flag.type === "readability";
}

function buildAnnotatedSegments(
  text: string,
  flags: Flag[],
  hoveredFlagId: string | null,
) {
  // Base set: active, non-document-level flags (always shown as highlights)
  const activeInline = flags.filter(
    f => f.status === "active" && !isDocumentLevel(f)
  );

  // When hovering a dismissed or actioned flag, temporarily include it in the
  // render set so the corresponding passage lights up on the left panel.
  const hoveredFlag = hoveredFlagId
    ? flags.find(f => f.id === hoveredFlagId)
    : null;

  const hoveredExtra =
    hoveredFlag &&
    hoveredFlag.status !== "active" &&
    !isDocumentLevel(hoveredFlag)
      ? [hoveredFlag]
      : [];

  const inlineFlags = [...activeInline, ...hoveredExtra]
    .sort((a, b) => a.start_pos - b.start_pos);

  // Merge overlapping spans — keep first flag's type for colour
  const merged: Array<{ start: number; end: number; flags: Flag[] }> = [];
  for (const flag of inlineFlags) {
    const last = merged[merged.length - 1];
    if (last && flag.start_pos < last.end) {
      last.end = Math.max(last.end, flag.end_pos);
      last.flags.push(flag);
    } else {
      merged.push({ start: flag.start_pos, end: flag.end_pos, flags: [flag] });
    }
  }

  const segments: Array<{ text: string; span: typeof merged[0] | null }> = [];
  let cursor = 0;

  for (const span of merged) {
    if (span.start > cursor) {
      segments.push({ text: text.slice(cursor, span.start), span: null });
    }
    segments.push({ text: text.slice(span.start, span.end), span });
    cursor = span.end;
  }
  if (cursor < text.length) {
    segments.push({ text: text.slice(cursor), span: null });
  }

  return segments.map((seg, i) => {
    if (!seg.span) return <span key={i}>{seg.text}</span>;

    const primaryFlag  = seg.span.flags[0];
    const style        = flagStyle(primaryFlag.type);
    const isHovered    = seg.span.flags.some(f => f.id === hoveredFlagId);
    // Span is non-active only when it's the hovered dismissed/actioned flag
    const isNonActive  = seg.span.flags.every(f => f.status !== "active");

    return (
      <mark
        key={i}
        className={[
          isNonActive ? "bg-gray-100" : style.bg,
          isHovered   ? "ring-2 ring-offset-1 ring-accent" : "",
          isNonActive ? "opacity-60" : "",
          "rounded px-0.5 cursor-default transition-all",
        ].filter(Boolean).join(" ")}
        title={seg.span.flags.map(f => f.message).join("\n")}
      >
        {seg.text}
      </mark>
    );
  });
}

// ── Flag card ─────────────────────────────────────────────────────────────────

function FlagCard({
  flag,
  onHover,
  onUpdate,
}: {
  flag:     Flag;
  onHover:  (id: string | null) => void;
  onUpdate: (id: string, updates: Partial<Flag>) => Promise<void>;
}) {
  const style                               = flagStyle(flag.type);
  const [comment, setComment]               = useState(flag.user_comment ?? "");
  const [dismissReason, setDismiss]         = useState(flag.dismissal_reason ?? "");
  const [saving, setSaving]                 = useState(false);
  const [expanded, setExpanded]             = useState(false);
  const dismissed = flag.status === "dismissed";
  const actioned  = flag.status === "actioned";

  async function handleDismiss() {
    setSaving(true);
    try {
      await onUpdate(flag.id, { status: "dismissed", dismissal_reason: dismissReason || null });
    } finally { setSaving(false); }
  }

  async function handleAction() {
    setSaving(true);
    try {
      await onUpdate(flag.id, { status: "actioned", user_comment: comment || null });
    } finally { setSaving(false); }
  }

  async function handleRestore() {
    setSaving(true);
    try {
      await onUpdate(flag.id, { status: "active", user_comment: null, dismissal_reason: null });
    } finally { setSaving(false); }
  }

  return (
    <div
      className={`border rounded-lg transition-all ${style.border} ${
        dismissed ? "opacity-40" : actioned ? "opacity-60" : ""
      }`}
      onMouseEnter={() => onHover(flag.id)}
      onMouseLeave={() => onHover(null)}
    >
      {/* Header */}
      <div
        className={`flex items-start gap-2 px-3 py-2.5 cursor-pointer ${style.bg} rounded-t-lg`}
        onClick={() => setExpanded(e => !e)}
      >
        <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded-full ${style.pill} flex-shrink-0 mt-0.5`}>
          {style.label}
        </span>
        <p className="text-xs text-text-primary leading-snug flex-1">{flag.message}</p>
        <span className="text-text-muted text-[10px] flex-shrink-0 mt-0.5">
          {expanded ? "▲" : "▼"}
        </span>
      </div>

      {/* Expanded body */}
      {expanded && (
        <div className="px-3 pb-3 pt-2 space-y-2.5 bg-bg-elevated rounded-b-lg">
          {/* Excerpt */}
          {flag.excerpt && !isDocumentLevel(flag) && (
            <div className="font-mono text-[11px] text-text-secondary bg-bg-surface px-2 py-1.5 rounded border border-bg-border">
              "{flag.excerpt}"
            </div>
          )}

          {/* Status badge */}
          {(dismissed || actioned) && (
            <div className="flex items-center justify-between">
              <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
                dismissed ? "bg-gray-100 text-gray-600" : "bg-green-100 text-green-700"
              }`}>
                {dismissed ? "Dismissed" : "Actioned"}
              </span>
              <button
                onClick={handleRestore}
                disabled={saving}
                className="text-[10px] text-text-muted hover:text-accent underline"
              >
                Restore
              </button>
            </div>
          )}

          {/* Active controls */}
          {flag.status === "active" && (
            <div className="space-y-2">
              <textarea
                value={comment}
                onChange={e => setComment(e.target.value)}
                placeholder="Add feedback for retry…"
                rows={2}
                className="w-full text-xs px-2 py-1.5 bg-bg-surface border border-bg-border rounded resize-none focus:outline-none focus:ring-1 focus:ring-accent/50 text-text-primary placeholder:text-text-muted"
              />
              <div className="flex gap-1.5">
                <button
                  onClick={handleAction}
                  disabled={saving}
                  className="flex-1 py-1.5 text-[11px] font-medium bg-accent text-white rounded hover:bg-accent-dim transition-colors disabled:opacity-50"
                >
                  {saving ? "Saving…" : "Flag for retry"}
                </button>
                <button
                  onClick={() => setExpanded(true)}
                  className="px-2 py-1.5 text-[11px] text-text-muted border border-bg-border rounded hover:text-text-primary"
                  title="Dismiss this flag"
                >
                  ↓ Dismiss
                </button>
              </div>

              <div className="pt-1 border-t border-bg-border space-y-1.5">
                <p className="text-[10px] text-text-muted">Dismiss reason (optional)</p>
                <input
                  value={dismissReason}
                  onChange={e => setDismiss(e.target.value)}
                  placeholder="e.g. False positive — claim is accurate"
                  className="w-full text-xs px-2 py-1 bg-bg-surface border border-bg-border rounded focus:outline-none focus:ring-1 focus:ring-accent/50 text-text-primary"
                />
                <button
                  onClick={handleDismiss}
                  disabled={saving}
                  className="w-full py-1 text-[11px] text-text-muted border border-bg-border rounded hover:text-text-primary hover:border-text-muted transition-colors disabled:opacity-50"
                >
                  {saving ? "Saving…" : "Dismiss flag"}
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Version dropdown ──────────────────────────────────────────────────────────

function VersionDropdown({
  chain,
  selectedId,
  onSelect,
}: {
  chain:      ReportSummary[];
  selectedId: string;
  onSelect:   (id: string) => void;
}) {
  if (chain.length <= 1) return null;

  function label(r: ReportSummary): string {
    const parts = [`Attempt ${r.attempt}`];
    if (r.status === "accepted")      parts.push("✓ accepted");
    else if (r.escalated)             parts.push("⚠ escalated");
    else if (r.status === "rejected") parts.push("rejected");
    return parts.join(" — ");
  }

  return (
    <select
      value={selectedId}
      onChange={e => onSelect(e.target.value)}
      className="text-xs px-2 py-1.5 bg-bg-surface border border-bg-border rounded-lg text-text-secondary focus:outline-none focus:ring-1 focus:ring-accent/50"
    >
      {[...chain].reverse().map(r => (
        <option key={r.id} value={r.id}>{label(r)}</option>
      ))}
    </select>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function ReviewPage() {
  const params      = useParams();
  const appId       = params.id as string;
  const sectionName = params.section as string;

  const [chain,         setChain]         = useState<ReportSummary[]>([]);
  const [selectedId,    setSelectedId]    = useState<string>("");
  const [report,        setReport]        = useState<Report | null>(null);
  const [flags,         setFlags]         = useState<Flag[]>([]);
  const [hoveredFlagId, setHoveredFlagId] = useState<string | null>(null);
  const [globalComment, setGlobalComment] = useState("");
  const [evaluating,    setEvaluating]    = useState(false);
  const [retrying,      setRetrying]      = useState(false);
  const [accepting,     setAccepting]     = useState(false);
  const [error,         setError]         = useState<string | null>(null);
  const [appName,       setAppName]       = useState("");

  useEffect(() => {
    api.get(`/sections/${appId}/${sectionName}`)
      .then((data: { reports: ReportSummary[]; section_name: string }) => {
        setChain(data.reports);
        if (data.reports.length > 0) {
          const latest = data.reports[data.reports.length - 1];
          setSelectedId(latest.id);
        }
      })
      .catch(e => setError(e.message));

    api.get(`/applications/${appId}`)
      .then((a: { company: string; role: string }) => {
        setAppName(`${a.company} — ${a.role}`);
      })
      .catch(() => {});
  }, [appId, sectionName]);

  useEffect(() => {
    if (!selectedId) return;
    api.get(`/review/${selectedId}`)
      .then((r: Report) => {
        setReport(r);
        setFlags(r.all_flags ?? []);
      })
      .catch(e => setError(e.message));
  }, [selectedId]);

  const handleFlagUpdate = useCallback(async (flagId: string, updates: Partial<Flag>) => {
    await api.patch(`/review/${selectedId}/flags/${flagId}`, updates);
    const updated: Report = await api.get(`/review/${selectedId}`);
    setFlags(updated.all_flags ?? []);
    setReport(updated);
  }, [selectedId]);

  async function handleEvaluate() {
    setEvaluating(true);
    setError(null);
    try {
      await api.post(`/review/${selectedId}/evaluate`);
      const updated: Report = await api.get(`/review/${selectedId}`);
      setReport(updated);
      setFlags(updated.all_flags ?? []);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setEvaluating(false);
    }
  }

  async function handleRetry() {
    setRetrying(true);
    setError(null);
    try {
      const result = await api.post(`/review/${selectedId}/retry`, {
        global_comment: globalComment || null,
      });
      const updated = await api.get(`/sections/${appId}/${sectionName}`);
      setChain(updated.reports);
      setSelectedId(result.new_report_id);
      setGlobalComment("");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setRetrying(false);
    }
  }

  async function handleAccept() {
    setAccepting(true);
    setError(null);
    try {
      await api.post(`/review/${selectedId}/accept`);
      const updated: Report = await api.get(`/review/${selectedId}`);
      setReport(updated);
      const chain = await api.get(`/sections/${appId}/${sectionName}`);
      setChain(chain.reports);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setAccepting(false);
    }
  }

  const text        = report?.generated_text ?? "";
  const activeFlags = flags.filter(f => f.status === "active");
  // Document-level flags are only readability — they render as banners
  const docLevelFlags = activeFlags.filter(f => isDocumentLevel(f));
  const isAccepted  = report?.status === "accepted";
  const isEscalated = report?.escalated;
  const hasEvals    = (report?.evaluations?.length ?? 0) > 0;

  const SECTION_LABELS: Record<string, string> = {
    profile:        "Profile",
    experience:     "Experience",
    skills:         "Skills",
    education:      "Education",
    certifications: "Certifications",
  };

  return (
    <div className="flex flex-col h-screen overflow-hidden bg-bg-base">

      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <header className="flex items-center justify-between px-6 py-3 bg-bg-surface border-b border-bg-border flex-shrink-0">
        <div className="flex items-center gap-3 min-w-0">
          <Link
            href={`/applications/${appId}`}
            className="text-xs text-text-muted hover:text-accent transition-colors flex-shrink-0"
          >
            ← {appName || "Application"}
          </Link>
          <span className="text-text-muted text-xs flex-shrink-0">/</span>
          <span className="text-sm font-semibold text-text-primary flex-shrink-0">
            {SECTION_LABELS[sectionName] ?? sectionName} review
          </span>
          <VersionDropdown chain={chain} selectedId={selectedId} onSelect={setSelectedId} />
        </div>

        <div className="flex items-center gap-2 flex-shrink-0">
          {isAccepted && (
            <span className="text-[11px] px-2 py-0.5 bg-green-100 text-green-700 rounded-full font-medium">
              ✓ Accepted
            </span>
          )}
          {isEscalated && !isAccepted && (
            <span className="text-[11px] px-2 py-0.5 bg-amber-100 text-amber-700 rounded-full font-medium">
              ⚠ {report?.escalation_reason === "confidence_failure" ? "Needs review" : "Max retries"}
            </span>
          )}
          {activeFlags.length > 0 && !isAccepted && (
            <span className="text-[11px] px-2 py-0.5 bg-red-100 text-red-700 rounded-full font-medium">
              {activeFlags.length} active flag{activeFlags.length !== 1 ? "s" : ""}
            </span>
          )}
          {!hasEvals && (
            <button
              onClick={handleEvaluate}
              disabled={evaluating}
              className="px-3 py-1.5 text-xs font-medium bg-bg-elevated border border-bg-border text-text-secondary rounded-lg hover:text-text-primary transition-colors disabled:opacity-50"
            >
              {evaluating ? "Evaluating…" : "Run evaluation"}
            </button>
          )}
        </div>
      </header>

      {/* ── Error banner ────────────────────────────────────────────────────── */}
      {error && (
        <div className="px-6 py-2 bg-red-50 border-b border-red-200 text-xs text-red-700 flex-shrink-0">
          {error}
          <button onClick={() => setError(null)} className="ml-3 underline">Dismiss</button>
        </div>
      )}

      {/* ── Document-level banners (readability only) ───────────────────────── */}
      {docLevelFlags.map(f => (
        <div
          key={f.id}
          className={`px-6 py-2 text-xs flex-shrink-0 flex items-center gap-2 ${flagStyle(f.type).bg} border-b ${flagStyle(f.type).border}`}
        >
          <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded-full ${flagStyle(f.type).pill}`}>
            {flagStyle(f.type).label}
          </span>
          <span className="text-text-primary">{f.message}</span>
        </div>
      ))}

      {/* ── Main split panel ────────────────────────────────────────────────── */}
      <div className="flex flex-1 overflow-hidden">

        {/* Left — annotated text */}
        <div className="flex-1 overflow-y-auto p-8 border-r border-bg-border">
          {!report ? (
            <div className="text-sm text-text-muted">Loading…</div>
          ) : (
            <>
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-xs font-semibold text-text-muted uppercase tracking-wide">
                  Generated text — attempt {report.attempt}
                </h2>
                {report.evaluations.length > 0 && (
                  <div className="flex items-center gap-3">
                    {report.evaluations.map(e => (
                      <span key={e.id} className="text-[10px] text-text-muted">
                        {e.tier}{" "}
                        {e.passed
                          ? <span className="text-green-600">✓</span>
                          : <span className="text-red-500">✗</span>
                        }
                        {e.flesch_score != null && ` · Flesch ${e.flesch_score}`}
                      </span>
                    ))}
                  </div>
                )}
              </div>

              <div className="text-sm text-text-primary leading-relaxed font-sans whitespace-pre-wrap bg-bg-elevated rounded-xl border border-bg-border p-6">
                {text
                  ? buildAnnotatedSegments(text, flags, hoveredFlagId)
                  : <span className="text-text-muted italic">No text available.</span>
                }
              </div>
            </>
          )}
        </div>

        {/* Right — flag list + actions */}
        <div className="w-96 flex-shrink-0 flex flex-col overflow-hidden">

          <div className="flex-1 overflow-y-auto p-4 space-y-2">
            <div className="flex items-center justify-between mb-1">
              <h2 className="text-xs font-semibold text-text-muted uppercase tracking-wide">Flags</h2>
              <span className="text-[10px] text-text-muted">
                {activeFlags.length} active · {flags.filter(f => f.status === "dismissed").length} dismissed
              </span>
            </div>

            {flags.length === 0 && (
              <div className="text-xs text-text-muted py-8 text-center">
                {hasEvals
                  ? "No flags raised — this section looks clean."
                  : "Run evaluation to check this section."
                }
              </div>
            )}

            {flags.map(flag => (
              <FlagCard
                key={flag.id}
                flag={flag}
                onHover={setHoveredFlagId}
                onUpdate={handleFlagUpdate}
              />
            ))}
          </div>

          {/* Actions footer */}
          <div className="flex-shrink-0 border-t border-bg-border p-4 space-y-3 bg-bg-surface">
            <div>
              <label className="text-[10px] font-semibold text-text-muted uppercase tracking-wide block mb-1.5">
                Overall feedback
              </label>
              <textarea
                value={globalComment}
                onChange={e => setGlobalComment(e.target.value)}
                placeholder="Additional direction for the retry…"
                rows={3}
                disabled={isAccepted}
                className="w-full text-xs px-3 py-2 bg-bg-elevated border border-bg-border rounded-lg resize-none focus:outline-none focus:ring-1 focus:ring-accent/50 text-text-primary placeholder:text-text-muted disabled:opacity-50"
              />
            </div>

            <div className="flex flex-col gap-2">
              <button
                onClick={handleRetry}
                disabled={retrying || isAccepted || activeFlags.length === 0}
                className="w-full py-2 text-xs font-medium bg-bg-elevated border border-bg-border text-text-secondary rounded-lg hover:text-text-primary hover:border-text-muted transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {retrying ? (
                  <span className="flex items-center justify-center gap-2">
                    <span className="w-3 h-3 border border-text-muted/30 border-t-text-muted rounded-full animate-spin" />
                    Retrying…
                  </span>
                ) : (
                  `↺ Retry with ${activeFlags.length} flag${activeFlags.length !== 1 ? "s" : ""}`
                )}
              </button>

              <button
                onClick={handleAccept}
                disabled={accepting || isAccepted}
                className="w-full py-2 text-xs font-semibold bg-accent text-white rounded-lg hover:bg-accent-dim transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {accepting ? (
                  <span className="flex items-center justify-center gap-2">
                    <span className="w-3 h-3 border border-white/30 border-t-white rounded-full animate-spin" />
                    Accepting…
                  </span>
                ) : isAccepted ? "✓ Accepted" : "Accept this version"}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
