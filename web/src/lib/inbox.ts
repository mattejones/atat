// Inbox — application emails read from Gmail, classified, and waiting for review.
// See api/routes/inbox.py. Nothing changes an application until an email is accepted.

export type ReviewStatus = "untriaged" | "pending" | "accepted" | "dismissed" | "auto_ignored";

export interface InboxEmail {
  id:                 number;
  gmail_message_id:   string;
  gmail_thread_id:    string | null;
  sender:             string | null;
  sender_domain:      string | null;
  subject:            string | null;
  received_at:        string | null;
  snippet:            string | null;
  body_excerpt:       string | null;
  classification:     string | null;
  confidence:         number | null;
  application_id:     string | null;
  app_uuid:           string | null;
  app_company:        string | null;
  app_role:           string | null;
  app_status:         string | null;
  match_reason:       string | null;
  evidence:           string | null;
  proposed_status:    string | null;
  proposed_date_type: string | null;
  proposed_date:      string | null;
  extracted:          { company?: string; role?: string; interview_at?: string; summary?: string };
  review_status:      ReviewStatus;
  reviewed_at:        string | null;
}

export interface InboxStatus {
  connected:           boolean;
  credentials_present: boolean;
  email:               string | null;
  detail:              string | null;
  connect_command:     string;
  scheduled:           boolean;
  interval_minutes:    number;
  sync: {
    last_synced_at: string | null;
    last_error:     string | null;
    last_result:    {
      fetched: number; queued: number; ignored: number; failed: number; remaining: number;
      untriaged?: number; classifier?: "api" | "agent";
      llm_calls?: number; input_tokens?: number; output_tokens?: number;
    } | null;
    pending:        number;
    untriaged:      number;
    classifier:     "api" | "agent";
  };
}

export const CLASSIFICATION_LABELS: Record<string, string> = {
  acknowledgement:    "Acknowledgement",
  rejection:          "Rejection",
  interview_request:  "Interview",
  assessment:         "Assessment",
  offer:              "Offer",
  progress_update:    "Progress update",
  recruiter_outreach: "Recruiter",
  other_job:          "Other",
  not_job:            "Not job-related",
};

export const CLASSIFICATION_STYLES: Record<string, string> = {
  acknowledgement:    "bg-purple-50 text-purple-700 border-purple-200",
  rejection:          "bg-red-50 text-red-600 border-red-200",
  interview_request:  "bg-orange-50 text-orange-700 border-orange-200",
  assessment:         "bg-violet-50 text-violet-700 border-violet-200",
  offer:              "bg-emerald-50 text-emerald-700 border-emerald-200",
  progress_update:    "bg-teal-50 text-teal-700 border-teal-200",
  recruiter_outreach: "bg-blue-50 text-blue-700 border-blue-200",
  other_job:          "bg-slate-100 text-slate-600 border-slate-300",
  not_job:            "bg-gray-50 text-gray-500 border-gray-200",
};

export const DATE_TYPE_LABELS: Record<string, string> = {
  applied:        "Applied",
  interview:      "Interview",
  offer_received: "Offer received",
  rejected:       "Rejected",
};

export function senderName(sender: string | null): string {
  if (!sender) return "Unknown sender";
  const m = sender.match(/^\s*"?([^"<]+?)"?\s*<[^>]+>\s*$/);
  return m ? m[1] : sender;
}
