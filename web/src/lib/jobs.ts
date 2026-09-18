// Generation jobs — the API's generative endpoints return a job at once (202) and the
// work runs in the background. These helpers start a job and follow it to completion,
// so a button can keep its familiar "working…" state without the request itself
// hanging open for minutes.

import { api } from "@/lib/api";

export type JobStatus = "draft" | "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface ParamSpec {
  type:     "str" | "bool" | "int" | "list[str]";
  default:  unknown;
  required: boolean;
  help:     string;
}

export interface PromptPreview {
  system?:       string;
  user?:         string;
  notes?:        string[];
  system_chars?: number;
  error?:        string;
}

export interface Job {
  job_id:           string;
  kind:             string;
  status:           JobStatus;
  app_uuid:         string | null;
  company:          string | null;
  role:             string | null;
  report_id:        string | null;
  params:           Record<string, unknown>;
  version:          number;
  created_by:       "agent" | "human";
  submitted_by:     "agent" | "human" | null;
  created_at:       string;
  submitted_at:     string | null;
  started_at:       string | null;
  finished_at:      string | null;
  cancel_requested: boolean;
  result?:          any;
  error?:           string;
  editable_params?: Record<string, ParamSpec>;
  prompt_preview?:  PromptPreview;
}

export type JobSummary = Pick<
  Job,
  "job_id" | "kind" | "status" | "app_uuid" | "company" | "role" | "report_id" |
  "created_by" | "submitted_by" | "error" | "created_at" | "submitted_at" | "finished_at"
>;

export const KIND_LABELS: Record<string, string> = {
  analyse_job:           "Analyse job ad",
  submit_job:            "Generate CV (single-phase)",
  generate_cv:           "Generate CV",
  run_coverage:          "Coverage check",
  run_judges:            "Run judges",
  regenerate_section:    "Retry section",
  generate_cover_letter: "Cover letter",
  generate_answers:      "Answer questions",
};

export const ACTIVE: JobStatus[] = ["queued", "running"];

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** Re-read a job until it leaves queued/running. Throws if it fails or is cancelled. */
export async function waitForJob(
  jobId: string,
  opts: { intervalMs?: number; onUpdate?: (job: Job) => void; signal?: AbortSignal } = {},
): Promise<Job> {
  const interval = opts.intervalMs ?? 2000;
  for (;;) {
    if (opts.signal?.aborted) throw new Error("Stopped waiting");
    const job: Job = await api.get(`/jobs/${jobId}`);
    opts.onUpdate?.(job);
    if (job.status === "succeeded") return job;
    if (job.status === "failed")    throw new Error(job.error || "Generation failed");
    if (job.status === "cancelled") throw new Error("Cancelled");
    if (job.status === "draft")     return job;
    await sleep(interval);
  }
}

/** POST to an endpoint that returns a job, wait for it, and return the job's result. */
export async function runJob<T = any>(path: string, body?: unknown, onUpdate?: (job: Job) => void): Promise<T> {
  const job: Job = await api.post(path, body);
  const done = await waitForJob(job.job_id, { onUpdate });
  return done.result as T;
}

/** Create a draft for review; returns the draft's job_id. */
export async function createDraft(
  kind: string,
  target: { app_uuid?: string; report_id?: string },
  params: Record<string, unknown>,
): Promise<string> {
  const job: Job = await api.post("/jobs", { kind, ...target, params, draft: true });
  return job.job_id;
}
