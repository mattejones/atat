"use client";

// Insights — what actually happened to applications, derived from email and status
// history (pipeline/insights). Nothing here reads the status field alone.

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { decodeEntities } from "@/lib/text";
import {
  FLAG_LABELS, OUTCOME_STYLES, STAGE_LABELS, pct, range,
  type BreakdownRow, type Forecast, type FunnelStep, type Outcome, type OutcomeKey, type Rate, type Report,
} from "@/lib/outcomes";

const DIMENSIONS: { key: string; label: string }[] = [
  { key: "ats",              label: "Tracking system" },
  { key: "month",            label: "Month applied"   },
  { key: "tier",             label: "Tier"            },
  { key: "persona",          label: "Persona"         },
  { key: "work_arrangement", label: "Work arrangement" },
];

const BASIS: Record<string, string> = {
  data:    "from your data",
  default: "default — not enough answers yet to measure",
  setting: "set by hand in the server settings",
};

// States like "waiting_screen" arrive as machine keys; show them as words.
const plain = (s: string | null | undefined) => (s ?? "").replace(/_/g, " ");

// "Ghosted" is also an application status, so name the derived outcome differently.
const outcomeLabel = (key: string, label: string | null) => (key === "ghosted" ? "Went quiet after contact" : label ?? "");

function fmt(d: string | null) {
  return d ? new Date(d).toLocaleDateString("en-GB", { day: "2-digit", month: "short" }) : "—";
}

function Card({ title, children, note }: { title: string; children: React.ReactNode; note?: React.ReactNode }) {
  return (
    <section className="bg-bg-elevated border border-bg-border rounded-xl px-4 sm:px-6 py-5 space-y-4">
      <div>
        <h2 className="text-xs font-semibold text-text-muted uppercase tracking-wide">{title}</h2>
        {note && <p className="text-xs text-text-muted mt-1">{note}</p>}
      </div>
      {children}
    </section>
  );
}

// ── Funnel: one series, one hue; value at the bar tip; detail on hover ────────

function Funnel({ steps, receipts }: { steps: FunnelStep[]; receipts: Rate }) {
  const [hover, setHover] = useState<string | null>(null);
  // Hover shows the detail as a tooltip on desktop; on touch screens a tap opens it inline.
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div className="space-y-3">
      <div className="space-y-2" role="list">
        {steps.map((s) => {
          const r = s.of_applied;
          const width = Math.max((r.rate ?? 0) * 100, r.count > 0 ? 0.8 : 0);
          const detail = `${s.label}: ${r.count} of ${r.of} applications (${pct(r.rate)}, likely ${range(r)})${
            s.from_previous ? ` · ${s.from_previous.count} of ${s.from_previous.of} from the step before` : ""}`;
          return (
            <div
              key={s.key}
              role="listitem"
              className="relative grid grid-cols-[6.5rem_1fr] sm:grid-cols-[8.5rem_1fr_7.5rem] items-center gap-x-3 py-2 sm:py-1 -mx-2 px-2 rounded-md hover:bg-bg-surface cursor-pointer sm:cursor-default"
              onMouseEnter={() => setHover(s.key)}
              onMouseLeave={() => setHover(null)}
              onClick={() => setOpen((o) => (o === s.key ? null : s.key))}
            >
              <span className="text-sm text-text-secondary">{s.label}</span>
              <div className="flex items-center gap-2 min-w-0">
                <div className="h-4 rounded-r bg-accent" style={{ width: `${width}%` }} />
                <span className="text-xs text-text-primary whitespace-nowrap tabular-nums">
                  {r.count} · {pct(r.rate)}
                </span>
              </div>
              <span className="hidden sm:block text-xs text-text-muted text-right tabular-nums">
                {s.from_previous && s.from_previous.rate != null ? `${pct(s.from_previous.rate)} of previous` : ""}
              </span>
              {hover === s.key && s.key !== "in_scope" && (
                <div className="hidden sm:block absolute left-36 -top-9 z-10 px-2.5 py-1.5 text-xs bg-text-primary text-white rounded-md shadow-lg whitespace-nowrap pointer-events-none">
                  {detail}
                </div>
              )}
              {open === s.key && s.key !== "in_scope" && (
                <p className="sm:hidden col-span-2 mt-1.5 text-xs text-text-secondary">{detail}</p>
              )}
            </div>
          );
        })}
      </div>
      <p className="text-xs text-text-muted">
        Receipts arrived for {receipts.count} of {receipts.of} ({pct(receipts.rate)}) — proof the application
        reached the company, not progress. Tap or hover a step for its likely range.
      </p>
    </div>
  );
}

// ── Breakdown table ───────────────────────────────────────────────────────────

function RateCell({ r }: { r: Rate }) {
  return (
    <td className="px-3 py-2 text-right tabular-nums">
      <span className="text-text-primary">{pct(r.rate)}</span>
      <span className="block text-[10px] text-text-muted">{range(r)}</span>
    </td>
  );
}

function Breakdown({ rows, small }: { rows: BreakdownRow[]; small: number }) {
  if (!rows.length) return <p className="text-sm text-text-muted">No in-scope applications yet.</p>;
  const smallTip = `Fewer than ${small} applications — too few to read a rate from`;
  return (
    <>
    {/* Phones: one card per group instead of a six-column table */}
    <ul className="md:hidden divide-y divide-bg-border">
      {rows.map((r) => (
        <li key={r.group} className={`py-3 ${r.small ? "text-text-muted" : ""}`}>
          <p className="text-sm">
            <span className={r.small ? "text-text-muted" : "text-text-primary font-medium"}>{r.group}</span>
            <span className="ml-2 text-xs text-text-muted">{r.applications} applications</span>
            {r.small && (
              <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded bg-bg-surface border border-bg-border">
                small group — {smallTip.split(" — ")[0].toLowerCase()}
              </span>
            )}
          </p>
          <dl className="mt-1.5 grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
            {([["Answered", r.answered], ["Engaged", r.engaged], ["Interviewed", r.interviewed], ["2nd round", r.second_round]] as [string, Rate][]).map(([label, rate]) => (
              <div key={label} className="flex items-baseline justify-between">
                <dt className="text-text-muted">{label}</dt>
                <dd className="tabular-nums text-text-primary">
                  {pct(rate.rate)} <span className="text-[10px] text-text-muted">{range(rate)}</span>
                </dd>
              </div>
            ))}
          </dl>
        </li>
      ))}
    </ul>
    <div className="hidden md:block overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-xs text-text-muted border-b border-bg-border">
            <th className="text-left font-medium px-3 py-2">Group</th>
            <th className="text-right font-medium px-3 py-2">Applications</th>
            <th className="text-right font-medium px-3 py-2">Answered</th>
            <th className="text-right font-medium px-3 py-2">Engaged</th>
            <th className="text-right font-medium px-3 py-2">Interviewed</th>
            <th className="text-right font-medium px-3 py-2">2nd round</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-bg-border">
          {rows.map((r) => (
            <tr key={r.group} className={r.small ? "text-text-muted" : ""}>
              <td className="px-3 py-2">
                <span className={r.small ? "text-text-muted" : "text-text-primary"}>{r.group}</span>
                {r.small && (
                  <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded bg-bg-surface border border-bg-border" title={`Fewer than ${small} applications — too few to read a rate from`}>
                    small
                  </span>
                )}
              </td>
              <td className="px-3 py-2 text-right tabular-nums text-text-primary">{r.applications}</td>
              <RateCell r={r.answered} />
              <RateCell r={r.engaged} />
              <RateCell r={r.interviewed} />
              <RateCell r={r.second_round} />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
    </>
  );
}

// ── Applications table ────────────────────────────────────────────────────────

function OutcomeBadge({ o }: { o: Outcome }) {
  if (!o.outcome) return null;
  return (
    <span className={`px-2 py-0.5 text-[11px] font-medium border rounded-full whitespace-nowrap ${OUTCOME_STYLES[o.outcome]}`}>
      {outcomeLabel(o.outcome, o.outcome_label)}
    </span>
  );
}

function Applications({ rows, labels, counts }: {
  rows:   Outcome[];
  labels: Record<OutcomeKey, string>;
  counts: Record<OutcomeKey, number>;
}) {
  const [filter, setFilter] = useState<OutcomeKey | "all">("all");
  const shown = filter === "all" ? rows : rows.filter((o) => o.outcome === filter);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5">
        <button
          onClick={() => setFilter("all")}
          className={`min-h-11 sm:min-h-0 px-3 sm:px-2.5 py-1 text-xs rounded-full border ${filter === "all" ? "bg-text-primary text-white border-text-primary" : "border-bg-border text-text-secondary hover:bg-bg-surface"}`}
        >
          All {rows.length}
        </button>
        {(Object.keys(labels) as OutcomeKey[]).map((k) => (
          <button
            key={k}
            onClick={() => setFilter(k)}
            disabled={!counts[k]}
            className={`min-h-11 sm:min-h-0 px-3 sm:px-2.5 py-1 text-xs rounded-full border disabled:opacity-40 ${filter === k ? "bg-text-primary text-white border-text-primary" : "border-bg-border text-text-secondary hover:bg-bg-surface"}`}
          >
            {outcomeLabel(k, labels[k])} {counts[k] ?? 0}
          </button>
        ))}
      </div>
      {/* Phones: one card per application */}
      <ul className="md:hidden divide-y divide-bg-border">
        {shown.map((o) => (
          <li key={o.uuid} className="py-3">
            <div className="flex items-start justify-between gap-2">
              <Link href={`/applications/${o.uuid}`} className="min-w-0 text-text-primary hover:text-accent">
                <span className="block font-medium">{decodeEntities(o.company?.trim())}</span>
                <span className="block text-xs text-text-muted">{decodeEntities(o.role)}</span>
              </Link>
              <OutcomeBadge o={o} />
            </div>
            <p className="mt-1 text-xs text-text-secondary">
              {STAGE_LABELS[o.furthest_stage]}
              {o.rounds > 0 && ` · ${o.rounds} round${o.rounds === 1 ? "" : "s"}`}
              {o.applied_on && ` · applied ${fmt(o.applied_on)}`}
              {o.decision_on && ` · decision ${fmt(o.decision_on)}`}
            </p>
            {o.flags.length > 0 && (
              <p className="text-[11px] text-amber-700 mt-0.5">{o.flags.map((f) => FLAG_LABELS[f] ?? f).join(" · ")}</p>
            )}
          </li>
        ))}
      </ul>
      <div className="hidden md:block overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-text-muted border-b border-bg-border">
              <th className="text-left font-medium px-3 py-2">Application</th>
              <th className="text-left font-medium px-3 py-2">Outcome</th>
              <th className="text-left font-medium px-3 py-2">Furthest stage</th>
              <th className="text-right font-medium px-3 py-2">Rounds</th>
              <th className="text-right font-medium px-3 py-2">Applied</th>
              <th className="text-right font-medium px-3 py-2">First answer</th>
              <th className="text-right font-medium px-3 py-2">Decision</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-bg-border">
            {shown.map((o) => (
              <tr key={o.uuid} className="align-top">
                <td className="px-3 py-2 min-w-[14rem]">
                  <Link href={`/applications/${o.uuid}`} className="text-text-primary hover:text-accent">
                    {o.company?.trim()}
                  </Link>
                  <span className="block text-xs text-text-muted">{o.role}</span>
                  {o.flags.length > 0 && (
                    <span className="block text-[11px] text-amber-700 mt-0.5">{o.flags.map((f) => FLAG_LABELS[f] ?? f).join(" · ")}</span>
                  )}
                </td>
                <td className="px-3 py-2"><OutcomeBadge o={o} /></td>
                <td className="px-3 py-2 text-text-secondary whitespace-nowrap">{STAGE_LABELS[o.furthest_stage]}</td>
                <td className="px-3 py-2 text-right tabular-nums text-text-secondary">{o.rounds || "—"}</td>
                <td className="px-3 py-2 text-right tabular-nums text-text-secondary whitespace-nowrap">{fmt(o.applied_on)}</td>
                <td className="px-3 py-2 text-right tabular-nums text-text-secondary whitespace-nowrap">{fmt(o.first_answer)}</td>
                <td className="px-3 py-2 text-right tabular-nums text-text-secondary whitespace-nowrap">{fmt(o.decision_on)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Excluded({ rows, labels }: { rows: Outcome[]; labels: Record<string, string> }) {
  const groups = useMemo(() => {
    const g: Record<string, Outcome[]> = {};
    rows.forEach((o) => { (g[o.exclusion ?? "other"] ||= []).push(o); });
    return Object.entries(g).sort((a, b) => b[1].length - a[1].length);
  }, [rows]);
  if (!rows.length) return null;
  return (
    <div className="space-y-2">
      {groups.map(([reason, list]) => (
        <details key={reason} className="group">
          <summary className="min-h-11 flex items-center cursor-pointer text-sm text-text-secondary hover:text-text-primary">
            {labels[reason] ?? reason} <span className="text-text-muted">({list.length})</span>
          </summary>
          <ul className="mt-2 ml-4 space-y-1">
            {list.map((o) => (
              <li key={o.uuid} className="text-xs">
                <Link href={`/applications/${o.uuid}`} className="text-text-secondary hover:text-accent">
                  {o.company?.trim()} — {o.role}
                </Link>
              </li>
            ))}
          </ul>
        </details>
      ))}
    </div>
  );
}

// ── Pipeline health ───────────────────────────────────────────────────────────

function Stat({ value, label, sub }: { value: string; label: string; sub?: string }) {
  return (
    <div className="min-w-0">
      <p className="text-2xl font-semibold text-text-primary tabular-nums">{value}</p>
      <p className="text-xs text-text-secondary">{label}</p>
      {sub && (
        <>
          <p className="hidden sm:block text-[11px] text-text-muted mt-0.5">{sub}</p>
          {/* Phones: the long working-out sits behind a tap so four stats fit on one screen */}
          <details className="sm:hidden">
            <summary className="min-h-11 flex items-center text-[11px] text-text-muted cursor-pointer select-none">How?</summary>
            <p className="text-[11px] text-text-muted pb-1">{sub}</p>
          </details>
        </>
      )}
    </div>
  );
}

function ChanceBar({ p }: { p: number }) {
  return (
    <div className="flex items-center gap-2 justify-end">
      <div className="w-20 h-2 rounded-full bg-bg-surface overflow-hidden">
        <div className="h-full rounded-r bg-accent" style={{ width: `${Math.max(p * 100, p > 0 ? 2 : 0)}%` }} />
      </div>
      <span className="w-10 text-right tabular-nums text-text-primary">{pct(p)}</span>
    </div>
  );
}

function PipelineHealth({ f }: { f: Forecast }) {
  const [all, setAll] = useState(false);
  const m = f.model;
  const rows = all ? f.applications : f.applications.slice(0, 12);
  const obs = m.screen_observed;
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-x-4 gap-y-5">
        <Stat
          value={`≈ ${f.pipeline.headline.screens.toFixed(1)}`}
          label="screens to come"
          sub={`from ${f.pipeline.awaiting_screen} open applications yet to screen — ≈ ${f.pipeline.expected_from.awaiting_screen_live.screens.toFixed(1)} of it from the ${f.pipeline.live} inside the ${f.model.window_days}-day window`}
        />
        <Stat
          value={`≈ ${f.pipeline.headline.second_rounds.toFixed(1)}`}
          label="second rounds to come"
          sub={`≈ ${f.pipeline.headline.second_rounds_from_screens.toFixed(1)} from those screens at ${pct(f.pipeline.headline.second_round_rate)}, ≈ ${f.pipeline.headline.second_rounds_in_process.toFixed(1)} from the ${f.pipeline.in_process} already screened (${f.pipeline.stalled} stalled excluded)`}
        />
        <Stat
          value={pct(f.per_application.screen)}
          label="chance of a screen per new application"
          sub={`${obs.count} of ${obs.of} settled; likely ${pct(obs.low)}–${pct(obs.high)}`}
        />
        <Stat
          value={`${f.per_ten.screen}`}
          label="screens per 10 applications"
          sub={`and ${f.per_ten.second} second rounds`}
        />
      </div>

      <p className="text-xs text-text-muted">
        You&apos;ve sent about {f.recent_weekly_rate} applications a week over the last 4 weeks
        {f.plan
          ? ` — about ${f.plan.expected_weekly_screens} screens a week at your current rate. Your target of ${f.plan.weekly_screen_target} a week needs about ${f.plan.applications_needed} applications a week.`
          : ". Set a weekly screen target in Settings to see how many applications a week it takes."}
        {" "}Offers aren&apos;t forecast until there&apos;s one to learn from.
      </p>

      <div>
        <p className="text-xs text-text-secondary mb-2">
          Chance of a screen, by days since applying with no reply beyond a receipt
          <span className="sm:hidden text-text-muted"> (numbers under the bars are days)</span>
        </p>
        <div className="flex items-end gap-1 sm:gap-3 h-24" role="list">
          {f.decay.map((d) => (
            <div key={d.day} role="listitem" className="flex-1 flex flex-col items-center justify-end h-full" title={`Day ${d.day}: ${pct(d.p_screen)} chance of a screen`}>
              <span className="text-[10px] text-text-secondary tabular-nums mb-1">{pct(d.p_screen)}</span>
              <div
                className="w-full max-w-[24px] rounded-t bg-accent"
                style={{ height: `${Math.max((d.p_screen / (f.decay[0]?.p_screen || 1)) * 64, 2)}px` }}
              />
              <span className="text-[10px] text-text-muted mt-1 tabular-nums"><span className="hidden sm:inline">day </span>{d.day}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Phones: one card per open application */}
      <ul className="md:hidden divide-y divide-bg-border">
        {rows.map((a) => (
          <li key={a.uuid} className="py-3 space-y-1.5">
            <Link href={`/applications/${a.uuid}`} className="block text-text-primary hover:text-accent">
              <span className="block font-medium">{decodeEntities(a.company?.trim())}</span>
              <span className="block text-xs text-text-muted">{decodeEntities(a.role)}</span>
            </Link>
            <p className="text-xs text-text-secondary">
              {plain(a.state)} <span className="text-text-muted">· next: {plain(a.waiting_for)}</span>
            </p>
            <div className="flex items-center gap-2 text-xs text-text-muted">
              Chance of next step <ChanceBar p={a.p_next} />
            </div>
          </li>
        ))}
        {f.applications.length > 12 && (
          <li>
            <button onClick={() => setAll((x) => !x)} className="min-h-11 text-xs text-accent hover:underline">
              {all ? "Show fewer" : `Show all ${f.applications.length}`}
            </button>
          </li>
        )}
      </ul>

      <div className="hidden md:block overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-text-muted border-b border-bg-border">
              <th className="text-left font-medium px-3 py-2">Open application</th>
              <th className="text-left font-medium px-3 py-2">Where it stands</th>
              <th className="text-right font-medium px-3 py-2">Chance of next step</th>
              <th className="text-right font-medium px-3 py-2">Screen</th>
              <th className="text-right font-medium px-3 py-2">2nd round</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-bg-border">
            {rows.map((a) => (
              <tr key={a.uuid}>
                <td className="px-3 py-2 min-w-[12rem]">
                  <Link href={`/applications/${a.uuid}`} className="text-text-primary hover:text-accent">{a.company?.trim()}</Link>
                  <span className="block text-xs text-text-muted">{a.role}</span>
                </td>
                <td className="px-3 py-2 text-xs text-text-secondary">
                  {plain(a.state)}
                  <span className="block text-text-muted">next: {plain(a.waiting_for)}</span>
                </td>
                <td className="px-3 py-2"><ChanceBar p={a.p_next} /></td>
                <td className="px-3 py-2 text-right tabular-nums text-text-secondary">{pct(a.p_screen)}</td>
                <td className="px-3 py-2 text-right tabular-nums text-text-secondary">{pct(a.p_second)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {f.applications.length > 12 && (
          <button onClick={() => setAll((x) => !x)} className="mt-2 text-xs text-accent hover:underline">
            {all ? "Show fewer" : `Show all ${f.applications.length}`}
          </button>
        )}
      </div>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function InsightsPage() {
  const [report, setReport]     = useState<Report | null>(null);
  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [error, setError]       = useState<string | null>(null);
  const [dim, setDim]           = useState("ats");

  useEffect(() => {
    api.get("/insights/report").then(setReport).catch((e) => setError(e.message));
    api.get("/insights/forecast").then(setForecast).catch(() => {});
  }, []);

  if (error) {
    return <div className="max-w-5xl mx-auto px-4 py-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{error}</div>;
  }
  if (!report) return <p className="max-w-5xl mx-auto text-sm text-text-muted">Loading…</p>;

  const s = report.summary;
  const w = s.response_window;
  const excludedCount = s.total - s.in_scope;

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-text-primary">Insights</h1>
        <p className="text-sm text-text-secondary mt-1">
          {s.interviewed} of {s.in_scope} applications ({pct(s.in_scope ? s.interviewed / s.in_scope : null)}) reached an
          interview; {s.second_round} went on to a second round. Outcomes come from email and status history, not the
          status field.
        </p>
        <details className="mt-1">
          <summary className="min-h-11 sm:min-h-0 flex items-center text-xs text-text-muted cursor-pointer select-none">
            What do these words mean?
          </summary>
          <div className="text-xs text-text-muted space-y-1 pb-2">
            <p>
              <span className="text-text-secondary">Screen</span> — a first call or interview with the company.{" "}
              <span className="text-text-secondary">Engaged</span> — they replied with more than an automatic receipt.{" "}
              <span className="text-text-secondary">In scope</span> — applications counted in the figures;
              excluded ones (for example ones sent outside the inbox you connected) are left out.
            </p>
            <p>
              Inbox coverage since {s.coverage_start ? fmt(s.coverage_start) + " " + s.coverage_start.slice(0, 4) : "—"} ·{" "}
              {s.in_scope} in scope, {excludedCount} excluded ·{" "}
              response window {w?.days ?? s.response_window_days} days ({BASIS[w?.basis ?? "default"]}
              {w?.basis === "data" ? `: 90% of ${w.sample} first answers arrived within ${w.p90} days` : ""})
            </p>
          </div>
        </details>
      </div>

      {forecast && (
        <Card
          title="Pipeline health"
          note="Each open application's chance of its next step, falling the longer it stays quiet — learned from how long real replies took. A receipt counts for nothing."
        >
          <PipelineHealth f={forecast} />
        </Card>
      )}

      <Card title="Funnel" note="Share of in-scope applications reaching each step.">
        <Funnel steps={report.funnel.steps} receipts={report.funnel.receipts} />
      </Card>

      <Card
        title="Breakdown"
        note={`Rates with a 95% likely range underneath. Groups under ${report.small_group} applications are too small to read.`}
      >
        <div className="flex flex-wrap gap-1.5">
          {DIMENSIONS.map((d) => (
            <button
              key={d.key}
              onClick={() => setDim(d.key)}
              className={`min-h-11 sm:min-h-0 px-3 sm:px-2.5 py-1 text-xs rounded-md border ${dim === d.key ? "bg-bg-surface border-text-muted text-text-primary font-medium" : "border-bg-border text-text-secondary hover:bg-bg-surface"}`}
            >
              {d.label}
            </button>
          ))}
        </div>
        <Breakdown rows={report.breakdowns[dim] ?? []} small={report.small_group} />
      </Card>

      <Card title="Timing" note="Days from applying, for applications that reached each milestone.">
        <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-text-muted border-b border-bg-border">
              <th className="text-left font-medium px-3 py-2">Milestone</th>
              <th className="text-right font-medium px-3 py-2">Applications</th>
              <th className="text-right font-medium px-3 py-2">Median days</th>
              <th className="text-right font-medium px-3 py-2">90% within</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-bg-border">
            {report.timing.map((t) => (
              <tr key={t.key}>
                <td className="px-3 py-2 text-text-primary">{t.label}</td>
                <td className="px-3 py-2 text-right tabular-nums text-text-secondary">{t.n}</td>
                <td className="px-3 py-2 text-right tabular-nums text-text-primary">{t.median ?? "—"}</td>
                <td className="px-3 py-2 text-right tabular-nums text-text-secondary">{t.p90 != null ? `${t.p90} days` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      </Card>

      <Card title="Applications" note="Every in-scope application and what happened to it.">
        <Applications rows={report.applications} labels={report.labels.outcomes} counts={s.by_outcome} />
      </Card>

      {report.excluded.length > 0 && (
        <Card title={`Excluded (${report.excluded.length})`} note="Left out of every rate above, so the totals always add up.">
          <Excluded rows={report.excluded} labels={report.labels.exclusions} />
        </Card>
      )}
    </div>
  );
}
