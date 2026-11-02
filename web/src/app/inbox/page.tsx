"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { waitForJob, type Job } from "@/lib/jobs";
import { relativeTime } from "@/components/JobBits";
import { decodeEntities } from "@/lib/text";
import { STATUS_LABELS, STATUS_STYLES } from "@/lib/statuses";
import {
  CLASSIFICATION_LABELS, CLASSIFICATION_STYLES, DATE_TYPE_LABELS, senderName,
  type InboxEmail, type InboxStatus, type ReviewStatus,
} from "@/lib/inbox";

interface AppOption { uuid: string; company: string; role: string; status: string }

const TABS: { key: ReviewStatus; label: string }[] = [
  { key: "pending",      label: "To review" },
  { key: "untriaged",    label: "Unsorted" },
  { key: "accepted",     label: "Accepted"  },
  { key: "dismissed",    label: "Dismissed" },
  { key: "auto_ignored", label: "Ignored"   },
];

// Relink dropdown groups: live applications first — they're what emails are usually about.
const APP_GROUPS: { label: string; statuses: string[] }[] = [
  { label: "In progress",     statuses: ["applied", "acknowledged", "interviewing", "case_study", "offered", "ghosted"] },
  { label: "Not yet applied", statuses: ["analysed", "generated", "reviewing"] },
  { label: "Closed",          statuses: ["rejected", "excluded", "archived"] },
];

function StatusPill({ status }: { status: string | null }) {
  if (!status) return null;
  return (
    <span className={`px-1.5 py-0.5 text-[11px] rounded ${STATUS_STYLES[status] ?? "bg-bg-surface text-text-secondary border border-bg-border"}`}>
      {STATUS_LABELS[status] ?? status}
    </span>
  );
}

function formatDate(value: string | null): string {
  if (!value) return "";
  const hasTime = value.length > 10;
  try {
    return new Date(value).toLocaleString("en-GB", {
      day: "2-digit", month: "short", year: "numeric",
      ...(hasTime ? { hour: "2-digit", minute: "2-digit" } : {}),
    });
  } catch {
    return value;
  }
}

// ── Connection / sync bar ─────────────────────────────────────────────────────

function SyncBar({ status, syncing, onSync }: { status: InboxStatus | null; syncing: boolean; onSync: () => void }) {
  if (!status) return null;
  if (!status.connected) {
    return (
      <div className="px-4 py-3 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-800 space-y-1">
        <p className="font-medium">Gmail isn&apos;t connected yet.</p>
        <p className="text-xs">
          Connecting is a one-time setup done on the computer that runs ATAT, so it can&apos;t be
          finished from this page. Ask whoever set ATAT up to connect it.
        </p>
        <details>
          <summary className="min-h-11 flex items-center text-xs underline cursor-pointer select-none">
            Setup steps (for whoever runs ATAT)
          </summary>
          <p className="text-xs pb-2">
            {status.credentials_present
              ? <>Run <code className="font-mono bg-white/60 px-1 rounded">{status.connect_command}</code> from the repo root to sign in (read-only access).</>
              : <>Create a Google Cloud OAuth client (type &quot;Desktop app&quot;, Gmail API enabled), save its JSON to <code className="font-mono bg-white/60 px-1 rounded">data/gmail/client_secret.json</code>, then run <code className="font-mono bg-white/60 px-1 rounded">{status.connect_command}</code>.</>}
          </p>
        </details>
      </div>
    );
  }
  const r = status.sync.last_result;
  return (
    <div className="flex flex-col sm:flex-row sm:items-center gap-3 sm:gap-4 px-4 py-3 bg-bg-elevated border border-bg-border rounded-xl">
      <div className="min-w-0 flex-1 text-xs text-text-secondary space-y-0.5">
        <p>
          <span className="text-text-primary font-medium break-all">{status.email ?? "Gmail"}</span> · read-only ·{" "}
          {status.scheduled ? `checks every ${status.interval_minutes} min` : "automatic checking off"}
        </p>
        <p className="text-text-muted">
          {status.sync.last_synced_at ? `Last synced ${relativeTime(status.sync.last_synced_at)}` : "Never synced"}
          {r && ` — ${r.fetched} checked, ${r.queued} queued, ${r.ignored} ignored${r.untriaged ? `, ${r.untriaged} for agent triage` : ""}${r.failed ? `, ${r.failed} unclassified` : ""}${r.remaining ? `, ${r.remaining} left for next sync` : ""}`}
        </p>
        {status.sync.untriaged > 0 && (
          <p className="text-amber-700">
            {status.sync.untriaged} email{status.sync.untriaged === 1 ? "" : "s"} waiting for your AI assistant to sort.
          </p>
        )}
        {status.sync.last_error && <p className="text-red-700">Last check failed: {status.sync.last_error}</p>}
        {/* The plumbing: how mail is classified, MCP tool names, model usage */}
        <details>
          <summary className="min-h-11 sm:min-h-0 flex items-center text-text-muted cursor-pointer select-none">
            Technical details
          </summary>
          <div className="space-y-0.5 pb-1">
            <p>
              {status.sync.classifier === "agent" ? "Classified by your AI agent over MCP" : "Classified by ATAT's model"}
            </p>
            {r && r.llm_calls != null && (
              <p className="text-text-muted">
                {r.llm_calls} model call{r.llm_calls === 1 ? "" : "s"} · {(r.input_tokens ?? 0).toLocaleString()} input / {(r.output_tokens ?? 0).toLocaleString()} output tokens
              </p>
            )}
            {status.sync.untriaged > 0 && (
              <p className="text-text-muted">
                Ask your agent to &ldquo;triage my ATAT inbox&rdquo; (it uses get_untriaged_emails and classify_inbox_emails).
              </p>
            )}
          </div>
        </details>
      </div>
      <button
        onClick={onSync}
        disabled={syncing}
        className="shrink-0 min-h-11 sm:min-h-0 px-4 py-1.5 bg-accent text-white text-sm font-medium rounded-lg hover:bg-accent-dim transition-colors disabled:opacity-50"
      >
        {syncing ? "Syncing…" : "Sync now"}
      </button>
    </div>
  );
}

// ── One email ─────────────────────────────────────────────────────────────────

function EmailCard({ email, apps, onChanged }: { email: InboxEmail; apps: AppOption[]; onChanged: (notice?: string) => void }) {
  const [applyStatus, setApplyStatus] = useState(true);
  const [applyDate, setApplyDate]     = useState(true);
  const [expanded, setExpanded]       = useState(false);
  const [busy, setBusy]               = useState(false);
  const [error, setError]             = useState<string | null>(null);

  const reviewable = email.review_status !== "accepted";
  const pending    = email.review_status === "pending";
  const cls        = email.classification;
  const confidence = email.confidence != null ? Math.round(email.confidence * 100) : null;

  async function act(fn: () => Promise<any>) {
    setBusy(true);
    setError(null);
    try {
      const out = await fn();
      const skipped: string[] = out?.applied?.skipped ?? [];
      onChanged(skipped.length ? `Accepted “${email.subject}”, but skipped the ${skipped.join("; ")}` : undefined);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const relink = (uuid: string) =>
    act(() => api.post(`/inbox/${email.id}/relink`, { application_uuid: uuid || null }));
  const accept = () =>
    act(() => api.post(`/inbox/${email.id}/accept`, { apply_status: applyStatus, apply_date: applyDate }));
  const dismiss = () => act(() => api.post(`/inbox/${email.id}/dismiss`));

  return (
    <div className="px-4 sm:px-5 py-4 space-y-3">
      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start gap-1.5 sm:gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-text-primary sm:truncate">{email.subject || "(no subject)"}</p>
          <p className="text-xs text-text-secondary truncate">
            {senderName(email.sender)} · {formatDate(email.received_at)}
          </p>
        </div>
        <div className="shrink-0 flex items-center gap-2">
          {cls ? (
            <span className={`px-2 py-0.5 text-[11px] font-medium border rounded-full ${CLASSIFICATION_STYLES[cls] ?? ""}`}>
              {CLASSIFICATION_LABELS[cls] ?? cls}
            </span>
          ) : email.review_status === "untriaged" ? (
            <span className="px-2 py-0.5 text-[11px] font-medium border rounded-full bg-blue-50 text-blue-700 border-blue-200">
              Awaiting triage
            </span>
          ) : email.review_status === "auto_ignored" ? (
            <span className={`px-2 py-0.5 text-[11px] font-medium border rounded-full ${CLASSIFICATION_STYLES.not_job}`}>
              Not job-related
            </span>
          ) : (
            <span className="px-2 py-0.5 text-[11px] font-medium border rounded-full bg-amber-50 text-amber-700 border-amber-200">
              Unclassified
            </span>
          )}
        </div>
      </div>

      {email.extracted?.summary && <p className="text-sm text-text-secondary">{email.extracted.summary}</p>}
      {email.evidence && (
        <blockquote className="text-xs text-text-secondary border-l-2 border-accent/40 pl-3 italic">
          &ldquo;{email.evidence}&rdquo;
        </blockquote>
      )}
      {!cls && email.match_reason && (
        <p className={`text-xs ${pending ? "text-amber-700" : "text-text-muted"}`}>{email.match_reason}</p>
      )}

      {/* Link */}
      <div className="flex flex-col items-start sm:flex-row sm:flex-wrap sm:items-center gap-1 sm:gap-2 text-xs">
        <span className="text-text-muted sm:w-20">Application</span>
        {reviewable ? (
          <select
            value={email.app_uuid ?? ""}
            onChange={(e) => relink(e.target.value)}
            disabled={busy}
            className="w-full sm:w-auto sm:max-w-sm min-h-11 sm:min-h-0 px-2 py-1 bg-white border border-bg-border rounded-md text-base sm:text-xs text-text-primary focus:outline-none focus:ring-2 focus:ring-accent/40"
          >
            <option value="">— Not linked —</option>
            {APP_GROUPS.map(({ label, statuses }) => {
              const group = apps.filter((a) => statuses.includes(a.status));
              return group.length > 0 && (
                <optgroup key={label} label={label}>
                  {group.map((a) => (
                    <option key={a.uuid} value={a.uuid}>{decodeEntities(a.company)} — {decodeEntities(a.role)} ({STATUS_LABELS[a.status] ?? a.status})</option>
                  ))}
                </optgroup>
              );
            })}
          </select>
        ) : email.app_uuid ? (
          <Link href={`/applications/${email.app_uuid}`} className="text-accent hover:underline">
            {decodeEntities(email.app_company)} — {decodeEntities(email.app_role)}
          </Link>
        ) : (
          <span className="text-text-muted">Not linked</span>
        )}
        {email.app_uuid && <StatusPill status={email.app_status} />}
        {email.app_uuid && (
          <span className="text-text-muted">
            {email.match_reason === "manual" ? "linked by you" : `matched on ${email.match_reason ?? "model"}`}
            {confidence != null && email.match_reason !== "manual" && ` · ${confidence}% confident`}
          </span>
        )}
      </div>

      {/* Proposed change */}
      {pending && email.app_uuid && (email.proposed_status || email.proposed_date_type) && (
        <div className="flex flex-col items-start sm:flex-row sm:flex-wrap sm:items-center gap-x-5 gap-y-0 sm:gap-y-1 text-xs">
          <span className="text-text-muted sm:w-20">Will apply</span>
          {email.proposed_status && (
            <label className="flex flex-wrap items-center gap-1.5 min-h-11 sm:min-h-0 cursor-pointer">
              <input type="checkbox" checked={applyStatus} onChange={(e) => setApplyStatus(e.target.checked)} className="w-5 h-5 sm:w-3.5 sm:h-3.5 accent-accent" />
              <StatusPill status={email.app_status} /> <span className="text-text-muted">→</span> <StatusPill status={email.proposed_status} />
            </label>
          )}
          {email.proposed_date_type && (
            <label className="flex items-center gap-1.5 min-h-11 sm:min-h-0 cursor-pointer text-text-secondary">
              <input type="checkbox" checked={applyDate} onChange={(e) => setApplyDate(e.target.checked)} className="w-5 h-5 sm:w-3.5 sm:h-3.5 accent-accent" />
              {DATE_TYPE_LABELS[email.proposed_date_type] ?? email.proposed_date_type} date: {formatDate(email.proposed_date)}
            </label>
          )}
        </div>
      )}
      {pending && email.app_uuid && !email.proposed_status && !email.proposed_date_type && (
        <p className="text-xs text-text-muted">Accepting links the email to the application; no status or date change.</p>
      )}

      {/* Body */}
      {(email.body_excerpt || email.snippet) && (
        <div>
          <button onClick={() => setExpanded((x) => !x)} className="min-h-11 sm:min-h-0 text-xs text-accent hover:underline">
            {expanded ? "Hide email" : "Show email"}
          </button>
          {expanded && (
            <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap font-sans text-xs text-text-secondary bg-bg-surface border border-bg-border rounded-lg p-3">
              {email.body_excerpt || email.snippet}
            </pre>
          )}
        </div>
      )}

      {error && <p className="text-xs text-red-700">{error}</p>}

      {/* Actions */}
      {pending && (
        <div className="flex items-center gap-2 pt-1">
          <button
            onClick={accept}
            disabled={busy || !email.app_uuid}
            title={email.app_uuid ? undefined : "Link an application first"}
            className="flex-1 sm:flex-none min-h-11 sm:min-h-0 px-3 py-1.5 bg-accent text-white text-sm sm:text-xs font-medium rounded-lg hover:bg-accent-dim transition-colors disabled:opacity-40"
          >
            Accept
          </button>
          <button
            onClick={dismiss}
            disabled={busy}
            className="flex-1 sm:flex-none min-h-11 sm:min-h-0 px-3 py-1.5 text-sm sm:text-xs font-medium text-text-secondary border border-bg-border rounded-lg hover:bg-bg-surface transition-colors disabled:opacity-40"
          >
            Dismiss
          </button>
        </div>
      )}
      {(email.review_status === "dismissed" || email.review_status === "auto_ignored") && !email.app_uuid && (
        <p className="text-[11px] text-text-muted">Link it to an application to move it back to review.</p>
      )}
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function InboxPage() {
  const [tab, setTab]         = useState<ReviewStatus>("pending");
  const [emails, setEmails]   = useState<InboxEmail[] | null>(null);
  const [status, setStatus]   = useState<InboxStatus | null>(null);
  const [apps, setApps]       = useState<AppOption[]>([]);
  const [syncing, setSyncing] = useState(false);
  const [error, setError]     = useState<string | null>(null);
  const [notice, setNotice]   = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [list, st] = await Promise.all([
        api.get(`/inbox?review_status=${tab}&limit=200`),
        api.get("/inbox/status"),
      ]);
      setEmails(list);
      setStatus(st);
      setError(null);
    } catch (e: any) {
      setError(e.message);
    }
  }, [tab]);

  useEffect(() => { setEmails(null); load(); }, [load]);
  useEffect(() => {
    api.get("/applications?include_archived=true")
      .then((list: AppOption[]) => setApps([...list].sort((a, b) => a.company.trim().localeCompare(b.company.trim()) || a.role.localeCompare(b.role))))
      .catch(() => {});
  }, []);

  async function sync() {
    setSyncing(true);
    setError(null);
    try {
      const job: Job = await api.post("/inbox/sync", {});
      await waitForJob(job.job_id, { intervalMs: 2500 });
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSyncing(false);
      load();
    }
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-text-primary">Inbox</h1>
        <p className="text-sm text-text-secondary mt-1">
          Job emails from Gmail, matched to your applications. Nothing changes until you tap Accept, so
          check the match first and fix it if it&apos;s wrong.
        </p>
      </div>

      <SyncBar status={status} syncing={syncing} onSync={sync} />

      {error && (
        <div className="px-4 py-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{error}</div>
      )}
      {notice && (
        <div className="px-4 py-3 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-800">{notice}</div>
      )}

      <div className="flex gap-1 border-b border-bg-border overflow-x-auto">
        {TABS.filter(({ key }) =>
          // "Unsorted" only means something when an AI agent does the sorting
          key !== "untriaged" || tab === "untriaged" || status?.sync.classifier === "agent" || (status?.sync.untriaged ?? 0) > 0
        ).map(({ key, label }) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`shrink-0 whitespace-nowrap min-h-11 px-3 py-2 text-sm -mb-px border-b-2 transition-colors ${
              tab === key ? "border-accent text-text-primary font-medium" : "border-transparent text-text-secondary hover:text-text-primary"
            }`}
          >
            {label}
            {key === "pending" && status && status.sync.pending > 0 && (
              <span className="ml-1.5 text-xs text-text-muted">({status.sync.pending})</span>
            )}
            {key === "untriaged" && status && status.sync.untriaged > 0 && (
              <span className="ml-1.5 text-xs text-text-muted">({status.sync.untriaged})</span>
            )}
          </button>
        ))}
      </div>

      <div className="bg-bg-elevated border border-bg-border rounded-xl divide-y divide-bg-border overflow-hidden">
        {emails === null ? (
          <p className="px-5 py-6 text-sm text-text-muted">Loading…</p>
        ) : emails.length === 0 ? (
          <p className="px-5 py-6 text-sm text-text-muted">
            {tab === "pending" ? "Nothing to review. New application emails appear here after a sync."
              : tab === "untriaged" ? "Nothing waiting. New mail lands here until your AI assistant has sorted it."
              : "Nothing here."}
          </p>
        ) : (
          emails.map((e) => <EmailCard key={`${e.id}-${e.review_status}-${e.app_uuid}`} email={e} apps={apps} onChanged={(n) => { setNotice(n ?? null); load(); }} />)
        )}
      </div>
    </div>
  );
}
