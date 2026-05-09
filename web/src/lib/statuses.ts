// Shared status configuration — used by ApplicationsTable and the application detail page.
// Update both STATUS_FLOW and STATUS_STYLES here when adding new statuses.

export const STATUS_LABELS: Record<string, string> = {
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

// Allowed transitions per status — determines what options appear in status dropdowns
export const STATUS_FLOW: Record<string, string[]> = {
  generated:    ["reviewing", "applied", "excluded", "archived"],
  reviewing:    ["applied", "excluded", "archived"],
  applied:      ["acknowledged", "rejected", "ghosted", "archived"],
  acknowledged: ["interviewing", "rejected", "ghosted", "archived"],
  interviewing: ["case_study", "offered", "rejected", "ghosted", "archived"],
  case_study:   ["interviewing", "offered", "rejected", "ghosted", "archived"],
  offered:      ["rejected", "archived"],
  rejected:     ["archived"],
  ghosted:      ["applied", "acknowledged", "interviewing", "archived"],
  excluded:     ["archived"],
  archived:     ["generated"],
};

export const STATUS_STYLES: Record<string, string> = {
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
