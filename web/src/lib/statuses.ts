// Shared status configuration — used by ApplicationsTable and the application detail page.
// Update both STATUS_FLOW and STATUS_STYLES here when adding new statuses.

export const STATUS_LABELS: Record<string, string> = {
  analysed:     "Analysed",
  generated:    "Generated",
  reviewing:    "Reviewing",
  applied:      "Applied",
  acknowledged: "Acknowledged",
  interviewing: "Interviewing",
  case_study:   "Case Study",
  offered:      "Offered",
  rejected:     "Rejected",
  ghosted:      "Ghosted",
  excluded:     "Excluded",
  archived:     "Archived",
};

// Allowed transitions per status — determines what options appear in status dropdowns.
// Any open application can move straight to a closing status (rejected, ghosted,
// archived) or jump forward a stage: outcomes are derived from history, so the status
// doesn't need to walk through every step. Keep in step with pipeline/statuses.py.
export const STATUS_FLOW: Record<string, string[]> = {
  analysed:     ["generated", "applied", "excluded", "archived"],
  generated:    ["reviewing", "applied", "excluded", "archived"],
  reviewing:    ["applied", "excluded", "archived"],
  applied:      ["acknowledged", "interviewing", "case_study", "offered", "rejected", "ghosted", "archived"],
  acknowledged: ["interviewing", "case_study", "offered", "rejected", "ghosted", "archived"],
  interviewing: ["case_study", "offered", "rejected", "ghosted", "archived"],
  case_study:   ["interviewing", "offered", "rejected", "ghosted", "archived"],
  offered:      ["rejected", "archived"],
  rejected:     ["archived"],
  ghosted:      ["applied", "acknowledged", "interviewing", "case_study", "offered", "rejected", "archived"],
  excluded:     ["archived"],
  archived:     ["generated", "applied"],
};

// The likely next step(s) from each status — what the status picker offers up front.
// Everything else in STATUS_FLOW sits behind "Other options" so a phone user isn't
// faced with seven choices. Each entry must also appear in STATUS_FLOW.
export const NEXT_STEPS: Record<string, string[]> = {
  analysed:     ["generated", "applied"],
  generated:    ["applied", "reviewing"],
  reviewing:    ["applied"],
  applied:      ["acknowledged", "interviewing", "rejected"],
  acknowledged: ["interviewing", "rejected"],
  interviewing: ["offered", "rejected"],
  case_study:   ["interviewing", "offered", "rejected"],
  offered:      ["rejected"],
  rejected:     ["archived"],
  ghosted:      ["acknowledged", "interviewing", "rejected"],
  excluded:     ["archived"],
  archived:     ["applied", "generated"],
};

// Plain-language grouping of the 12 statuses, for places that need less detail than a
// full status (summaries, filters). The stored status is unchanged.
export const STAGES = [
  { key: "todo",    label: "To do",   statuses: ["analysed", "generated", "reviewing"] },
  { key: "applied", label: "Applied", statuses: ["applied", "acknowledged"] },
  { key: "talking", label: "Talking", statuses: ["interviewing", "case_study", "offered"] },
  { key: "closed",  label: "Closed",  statuses: ["rejected", "ghosted", "excluded", "archived"] },
] as const;

export function stageOf(status: string) {
  return STAGES.find((s) => (s.statuses as readonly string[]).includes(status));
}

export const STATUS_STYLES: Record<string, string> = {
  analysed:     "bg-accent/5 text-accent border border-accent/20",
  generated:    "bg-accent/10 text-accent border border-accent/20",
  reviewing:    "bg-yellow-50 text-yellow-700 border border-yellow-200",
  applied:      "bg-blue-50 text-blue-700 border border-blue-200",
  acknowledged: "bg-purple-50 text-purple-700 border border-purple-200",
  interviewing: "bg-orange-50 text-orange-700 border border-orange-200",
  case_study:   "bg-violet-50 text-violet-700 border border-violet-200",
  offered:      "bg-emerald-50 text-emerald-700 border border-emerald-200",
  rejected:     "bg-red-50 text-red-600 border border-red-200",
  ghosted:      "bg-slate-100 text-slate-500 border border-slate-300",
  excluded:     "bg-gray-100 text-gray-500 border border-gray-200",
  archived:     "bg-gray-50 text-gray-400 border border-gray-200",
};
