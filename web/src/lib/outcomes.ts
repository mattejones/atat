// Outcomes — what actually happened to an application, derived from its email and
// status history by the API (pipeline/insights/outcomes.py). Never stored.

export type OutcomeKey =
  | "offer" | "in_progress" | "rejected_after_engagement" | "ghosted"
  | "screened_out" | "awaiting_reply" | "unanswered";

export interface Outcome {
  uuid:              string;
  company:           string | null;
  role:              string | null;
  status:            string | null;
  in_scope:          boolean;
  exclusion:         string | null;
  exclusion_label:   string | null;
  outcome:           OutcomeKey | null;
  outcome_label:     string | null;
  engaged:           boolean;
  answered:          boolean;
  furthest_stage:    "none" | "receipt" | "update" | "assessment" | "interview" | "offer";
  evidence_source:   "email" | "manual" | "both" | null;
  applied_on:        string | null;
  applied_estimated: boolean;
  first_engagement:  string | null;
  first_interview:   string | null;
  decision_on:       string | null;
  last_email_on:     string | null;
  email_count:       number;
  rounds:            number;
  first_answer:      string | null;
  has_receipt:       boolean;
  tier:              string | null;
  persona:           string | null;
  work_arrangement:  string | null;
  ats:               string | null;
  flags:             string[];
  score?:            Score | null;
}

export const OUTCOME_STYLES: Record<OutcomeKey, string> = {
  offer:                     "bg-emerald-50 text-emerald-700 border-emerald-200",
  in_progress:               "bg-orange-50 text-orange-700 border-orange-200",
  rejected_after_engagement: "bg-rose-50 text-rose-700 border-rose-200",
  ghosted:                   "bg-slate-100 text-slate-600 border-slate-300",
  screened_out:              "bg-red-50 text-red-600 border-red-200",
  awaiting_reply:            "bg-blue-50 text-blue-700 border-blue-200",
  unanswered:                "bg-gray-100 text-gray-500 border-gray-200",
};

export const OUTCOME_HINTS: Record<OutcomeKey, string> = {
  offer:                     "An offer arrived.",
  in_progress:               "The company engaged and the process is still live.",
  rejected_after_engagement: "The company engaged, then declined.",
  ghosted:                   "The company engaged, then went silent.",
  screened_out:              "Declined with no engagement before it.",
  awaiting_reply:            "Only a receipt so far, still inside the response window.",
  unanswered:                "Nothing beyond a receipt, past the response window.",
};

export const STAGE_LABELS: Record<Outcome["furthest_stage"], string> = {
  none:       "Nothing received",
  receipt:    "Receipt",
  update:     "Progress update",
  assessment: "Assessment",
  interview:  "Interview",
  offer:      "Offer",
};

export const FLAG_LABELS: Record<string, string> = {
  no_receipt:                     "No receipt: the application may not have arrived",
  suggest_outside_inbox:          "No email at all: was this applied through another channel?",
  applied_date_after_first_email: "The recorded applied date is after the first email; using the email date",
};

// ── Insights report (GET /insights/report) ────────────────────────────────────

export interface Rate {
  count: number;
  of:    number;
  rate:  number | null;
  low:   number | null;   // 95% range, as fractions
  high:  number | null;
}

export interface FunnelStep {
  key:           string;
  label:         string;
  of_applied:    Rate;
  from_previous: Rate | null;
}

export interface BreakdownRow {
  group:        string;
  applications: number;
  small:        boolean;
  answered:     Rate;
  engaged:      Rate;
  interviewed:  Rate;
  second_round: Rate;
  by_outcome:   Record<OutcomeKey, number>;
}

export interface TimingRow {
  key:    string;
  label:  string;
  n:      number;
  median: number | null;
  p90:    number | null;
}

export interface ResponseWindow {
  days:   number;
  basis:  "data" | "default" | "setting";
  sample: number;
  p90:    number | null;
}

export interface Report {
  summary: {
    total: number; in_scope: number; answered: number; engaged: number; interviewed: number;
    second_round: number; third_round: number;
    by_outcome: Record<OutcomeKey, number>;
    by_exclusion: Record<string, number>;
    flagged: Record<string, number>;
    coverage_start: string | null;
    response_window_days: number;
    response_window: ResponseWindow | null;
  };
  funnel:       { steps: FunnelStep[]; receipts: Rate };
  breakdowns:   Record<string, BreakdownRow[]>;
  timing:       TimingRow[];
  applications: Outcome[];
  excluded:     Outcome[];
  small_group:  number;
  labels:       { outcomes: Record<OutcomeKey, string>; exclusions: Record<string, string> };
}

export const pct = (r: number | null | undefined) => (r == null ? "—" : `${Math.round(r * 100)}%`);
export const range = (r: Rate) =>
  r.low == null || r.high == null ? "" : `${Math.round(r.low * 100)}–${Math.round(r.high * 100)}%`;

// ── Forecast (GET /insights/forecast) ─────────────────────────────────────────

export interface Score {
  uuid:          string;
  company:       string | null;
  role:          string | null;
  outcome:       OutcomeKey | null;
  outcome_label: string;
  state:         string;
  quiet_days:    number;
  waiting_for:   string;
  p_screen:      number;
  p_second:      number;
  p_third:       number;
  p_next:        number;
}

export interface Expected { screens: number; second_rounds: number; third_rounds: number }

export interface Forecast {
  as_of: string;
  model: {
    screen_rate: number;
    screen_observed: { count: number; of: number; low: number | null; high: number | null };
    round_rates: Record<string, number>;
    round_observed: Record<string, { count: number; of: number }>;
    wait_to_screen: { n: number; median: number | null };
    wait_between_rounds: { n: number; median: number | null };
    offers_seen: number;
    window_days: number;
  };
  per_application: { screen: number; second: number; third: number; offer: number | null };
  per_ten:         { screen: number; second: number; third: number; offer: number | null };
  pipeline: {
    open: number; awaiting_screen: number; in_process: number; stalled: number;
    live: number; cold: number;
    headline: {
      screens: number; second_rounds: number; second_round_rate: number;
      second_rounds_from_screens: number; second_rounds_in_process: number;
    };
    expected: Expected;
    expected_from: {
      awaiting_screen: Expected; awaiting_screen_live: Expected; awaiting_screen_cold: Expected;
      in_process: Expected; stalled: Expected;
    };
  };
  recent_weekly_rate: number;
  plan: null | {
    weekly_screen_target: number;
    applications_needed: number | null;
    recent_weekly_rate: number;
    expected_weekly_screens: number;
  };
  applications: Score[];
  decay: { day: number; p_screen: number }[];
}
