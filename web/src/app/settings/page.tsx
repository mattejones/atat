"use client";

import { useState, useEffect } from "react";

const API = process.env.NEXT_PUBLIC_API_URL;

interface Settings {
  llm_provider:             string;
  llm_model:                string;
  anthropic_api_key:        string | null;
  openai_api_key:           string | null;
  cv_library_path:          string;
  output_path:              string;
  thinking_budget:          number;
  render_pdf:               boolean;
  temperature:              number;
  demo_mode:                boolean;
  auto_ghost_enabled:       boolean;
  auto_ghost_days:          number;
  scheduler_interval_hours: number;
}

type SaveState = "idle" | "saving" | "saved" | "error";

function Field({ label, hint, children, locked }: {
  label:    string;
  hint?:    string;
  children: React.ReactNode;
  locked?:  boolean;
}) {
  return (
    <div className="grid grid-cols-3 gap-4 py-4 border-b border-bg-border last:border-0">
      <div>
        <p className="text-sm font-medium text-text-primary flex items-center gap-1.5">
          {label}
          {locked && (
            <span className="text-xs font-normal text-amber-600 bg-amber-50 border border-amber-200 px-1.5 py-0.5 rounded">
              demo
            </span>
          )}
        </p>
        {hint && <p className="text-xs text-text-muted mt-0.5">{hint}</p>}
      </div>
      <div className="col-span-2">{children}</div>
    </div>
  );
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="text-xs font-semibold text-text-muted uppercase tracking-wide pt-2 pb-1">
      {children}
    </h2>
  );
}

function MaskedKeyInput({
  value,
  placeholder,
  onChange,
}: {
  value:       string;
  placeholder: string;
  onChange:    (v: string) => void;
}) {
  const [show, setShow] = useState(false);
  return (
    <div className="flex gap-2">
      <input
        type={show ? "text" : "password"}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="flex-1 px-3 py-1.5 bg-bg-elevated border border-bg-border rounded-lg text-sm text-text-primary placeholder-text-muted focus:outline-none focus:ring-2 focus:ring-accent/40 font-mono"
      />
      <button
        onClick={() => setShow((s) => !s)}
        className="px-2 py-1.5 text-xs text-text-muted hover:text-text-secondary border border-bg-border rounded-lg"
      >
        {show ? "Hide" : "Show"}
      </button>
    </div>
  );
}

function LockedPathInput({ value }: { value: string }) {
  return (
    <input
      type="text"
      value={value}
      disabled
      className="w-full px-3 py-1.5 bg-bg-base border border-bg-border rounded-lg text-sm text-text-muted font-mono cursor-not-allowed opacity-60"
    />
  );
}

export default function SettingsPage() {
  const [settings, setSettings]   = useState<Settings | null>(null);
  const [form, setForm]           = useState<Partial<Settings>>({});
  const [keys, setKeys]           = useState({ anthropic: "", openai: "" });
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [error, setError]         = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API}/settings`)
      .then((r) => r.json())
      .then((data) => {
        setSettings(data);
        const { anthropic_api_key, openai_api_key, ...rest } = data;
        setForm(rest);
      })
      .catch(() => setError("Could not load settings — is the API running?"));
  }, []);

  const demoMode = settings?.demo_mode ?? false;

  async function save() {
    setSaveState("saving");
    setError(null);
    try {
      const payload: Record<string, unknown> = { ...form };
      delete payload.anthropic_api_key;
      delete payload.openai_api_key;
      delete payload.demo_mode;

      if (demoMode) {
        delete payload.cv_library_path;
        delete payload.output_path;
      }

      const anthropic = keys.anthropic.trim();
      const openai    = keys.openai.trim();
      if (anthropic) payload.anthropic_api_key = anthropic;
      if (openai)    payload.openai_api_key    = openai;

      const res = await fetch(`${API}/settings`, {
        method:  "PATCH",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Save failed");

      setKeys({ anthropic: "", openai: "" });
      setSaveState("saved");
      setTimeout(() => setSaveState("idle"), 3000);
    } catch (e: any) {
      setError(e.message);
      setSaveState("error");
    }
  }

  function set<K extends keyof Settings>(key: K, value: Settings[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  if (!settings && !error) {
    return (
      <div className="flex items-center justify-center py-24 text-text-muted text-sm">
        Loading settings…
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto space-y-8">
      <div>
        <h1 className="text-2xl font-semibold text-text-primary">Settings</h1>
        <p className="text-sm text-text-secondary mt-1">
          Changes are written to your{" "}
          <code className="text-xs bg-bg-surface px-1 py-0.5 rounded">.env</code> file.
          Restart the API server for changes to take effect.
        </p>
      </div>

      {error && (
        <div className="px-4 py-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
          {error}
        </div>
      )}

      {settings && (
        <div className="space-y-6">

          {/* ── LLM ──────────────────────────────────────────────────────── */}
          <div>
            <SectionHeading>LLM</SectionHeading>
            <div className="bg-bg-elevated border border-bg-border rounded-xl divide-y divide-bg-border px-6">

              <Field label="Provider" hint="Which AI provider to use for generation">
                <select
                  value={(form.llm_provider ?? settings.llm_provider) as string}
                  onChange={(e) => set("llm_provider", e.target.value)}
                  className="w-full px-3 py-1.5 bg-white border border-bg-border rounded-lg text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent/40"
                >
                  <option value="anthropic">Anthropic (Claude)</option>
                  <option value="openai">OpenAI (GPT)</option>
                </select>
              </Field>

              <Field label="Model" hint="Model identifier for CV generation">
                <input
                  type="text"
                  value={(form.llm_model ?? settings.llm_model) as string}
                  onChange={(e) => set("llm_model", e.target.value)}
                  className="w-full px-3 py-1.5 bg-white border border-bg-border rounded-lg text-sm text-text-primary font-mono focus:outline-none focus:ring-2 focus:ring-accent/40"
                />
              </Field>

              <Field
                label="Anthropic API Key"
                hint={settings.anthropic_api_key ? `Current key: ${settings.anthropic_api_key}` : "Not set"}
              >
                <MaskedKeyInput
                  value={keys.anthropic}
                  placeholder="sk-ant-… (leave blank to keep current)"
                  onChange={(v) => setKeys((k) => ({ ...k, anthropic: v }))}
                />
              </Field>

              <Field
                label="OpenAI API Key"
                hint={settings.openai_api_key ? `Current key: ${settings.openai_api_key}` : "Not set"}
              >
                <MaskedKeyInput
                  value={keys.openai}
                  placeholder="sk-… (leave blank to keep current)"
                  onChange={(v) => setKeys((k) => ({ ...k, openai: v }))}
                />
              </Field>

              <Field label="Thinking Budget" hint="Extended thinking tokens (0 to disable)">
                <div className="flex items-center gap-3">
                  <input
                    type="number"
                    min={0}
                    max={32000}
                    step={1000}
                    value={(form.thinking_budget ?? settings.thinking_budget) as number}
                    onChange={(e) => set("thinking_budget", parseInt(e.target.value) || 0)}
                    className="w-32 px-3 py-1.5 bg-white border border-bg-border rounded-lg text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent/40"
                  />
                  <span className="text-xs text-text-muted">tokens</span>
                </div>
              </Field>

            </div>
          </div>

          {/* ── Paths ────────────────────────────────────────────────────── */}
          <div>
            <SectionHeading>Paths</SectionHeading>
            <div className="bg-bg-elevated border border-bg-border rounded-xl divide-y divide-bg-border px-6">

              <Field
                label="CV Library Path"
                hint={demoMode ? "Fixed to demo/cv-library/ in demo mode" : "Path to your cv-library repo"}
                locked={demoMode}
              >
                {demoMode ? (
                  <LockedPathInput value={settings.cv_library_path} />
                ) : (
                  <input
                    type="text"
                    value={(form.cv_library_path ?? settings.cv_library_path) as string}
                    onChange={(e) => set("cv_library_path", e.target.value)}
                    className="w-full px-3 py-1.5 bg-white border border-bg-border rounded-lg text-sm text-text-primary font-mono focus:outline-none focus:ring-2 focus:ring-accent/40"
                  />
                )}
              </Field>

              <Field
                label="Output Path"
                hint={demoMode ? "Fixed to demo/output/ in demo mode" : "Where generated CVs are saved"}
                locked={demoMode}
              >
                {demoMode ? (
                  <LockedPathInput value={settings.output_path} />
                ) : (
                  <input
                    type="text"
                    value={(form.output_path ?? settings.output_path) as string}
                    onChange={(e) => set("output_path", e.target.value)}
                    className="w-full px-3 py-1.5 bg-white border border-bg-border rounded-lg text-sm text-text-primary font-mono focus:outline-none focus:ring-2 focus:ring-accent/40"
                  />
                )}
              </Field>

            </div>
          </div>

          {/* ── Rendering ────────────────────────────────────────────────── */}
          <div>
            <SectionHeading>Rendering</SectionHeading>
            <div className="bg-bg-elevated border border-bg-border rounded-xl divide-y divide-bg-border px-6">

              <Field label="Render PDF" hint="Automatically render PDF after generation">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={(form.render_pdf ?? settings.render_pdf) as boolean}
                    onChange={(e) => set("render_pdf", e.target.checked)}
                    className="w-4 h-4 accent-accent"
                  />
                  <span className="text-sm text-text-secondary">
                    {(form.render_pdf ?? settings.render_pdf) ? "Enabled" : "Disabled"}
                  </span>
                </label>
              </Field>

            </div>
          </div>

          {/* ── Auto-ghost ───────────────────────────────────────────────── */}
          <div>
            <SectionHeading>Auto-ghost</SectionHeading>
            <p className="text-xs text-text-muted mb-3">
              Automatically marks applications as ghosted when no status change
              has occurred within the threshold. Applies to: applied, acknowledged,
              interviewing, and case study stages. Restart the API to apply changes.
            </p>
            <div className="bg-bg-elevated border border-bg-border rounded-xl divide-y divide-bg-border px-6">

              <Field label="Enabled" hint="Run the background ghost check on a schedule">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={(form.auto_ghost_enabled ?? settings.auto_ghost_enabled) as boolean}
                    onChange={(e) => set("auto_ghost_enabled", e.target.checked)}
                    className="w-4 h-4 accent-accent"
                  />
                  <span className="text-sm text-text-secondary">
                    {(form.auto_ghost_enabled ?? settings.auto_ghost_enabled) ? "Enabled" : "Disabled"}
                  </span>
                </label>
              </Field>

              <Field label="Ghost after" hint="Days of inactivity before marking as ghosted">
                <div className="flex items-center gap-3">
                  <input
                    type="number"
                    min={1}
                    max={365}
                    value={(form.auto_ghost_days ?? settings.auto_ghost_days) as number}
                    onChange={(e) => set("auto_ghost_days", parseInt(e.target.value) || 21)}
                    className="w-24 px-3 py-1.5 bg-white border border-bg-border rounded-lg text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent/40"
                  />
                  <span className="text-xs text-text-muted">days</span>
                </div>
              </Field>

              <Field label="Check interval" hint="How often the scheduler runs the ghost check">
                <div className="flex items-center gap-3">
                  <input
                    type="number"
                    min={1}
                    max={168}
                    value={(form.scheduler_interval_hours ?? settings.scheduler_interval_hours) as number}
                    onChange={(e) => set("scheduler_interval_hours", parseInt(e.target.value) || 6)}
                    className="w-24 px-3 py-1.5 bg-white border border-bg-border rounded-lg text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent/40"
                  />
                  <span className="text-xs text-text-muted">hours</span>
                </div>
              </Field>

            </div>
          </div>

        </div>
      )}

      <div className="flex items-center gap-4">
        <button
          onClick={save}
          disabled={saveState === "saving"}
          className="px-5 py-2 bg-accent text-white text-sm font-medium rounded-lg hover:bg-accent-dim transition-colors disabled:opacity-50"
        >
          {saveState === "saving" ? "Saving…" : "Save settings"}
        </button>
        {saveState === "saved" && (
          <p className="text-sm text-accent animate-fade-in">
            Saved — restart the API to apply changes.
          </p>
        )}
      </div>
    </div>
  );
}
