"use client";

import { useState, useEffect } from "react";

const API = process.env.NEXT_PUBLIC_API_URL;

interface Settings {
  llm_provider:      string;
  llm_model:         string;
  anthropic_api_key: string | null;
  openai_api_key:    string | null;
  cv_library_path:   string;
  output_path:       string;
  thinking_budget:   number;
  render_pdf:        boolean;
  temperature:       number;
}

type SaveState = "idle" | "saving" | "saved" | "error";

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-3 gap-4 py-4 border-b border-bg-border last:border-0">
      <div>
        <p className="text-sm font-medium text-text-primary">{label}</p>
        {hint && <p className="text-xs text-text-muted mt-0.5">{hint}</p>}
      </div>
      <div className="col-span-2">{children}</div>
    </div>
  );
}

function MaskedKeyInput({
  value,
  placeholder,
  onChange,
}: {
  value: string;
  placeholder: string;
  onChange: (v: string) => void;
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

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [form, setForm]         = useState<Partial<Settings>>({});
  const [keys, setKeys]         = useState({ anthropic: "", openai: "" });
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [error, setError]       = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API}/settings`)
      .then((r) => r.json())
      .then((data) => {
        setSettings(data);
        setForm(data);
      })
      .catch(() => setError("Could not load settings — is the API running?"));
  }, []);

  async function save() {
    setSaveState("saving");
    setError(null);
    try {
      const payload: Record<string, unknown> = { ...form };
      // Only send key values if they were actually changed (not empty)
      if (keys.anthropic.trim()) payload.anthropic_api_key = keys.anthropic.trim();
      if (keys.openai.trim())    payload.openai_api_key    = keys.openai.trim();

      const res = await fetch(`${API}/settings`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Save failed");
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
          Changes are written to your <code className="text-xs bg-bg-surface px-1 py-0.5 rounded">.env</code> file.
          Restart the API server for changes to take effect.
        </p>
      </div>

      {error && (
        <div className="px-4 py-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
          {error}
        </div>
      )}

      {settings && (
        <div className="bg-bg-elevated border border-bg-border rounded-xl divide-y divide-bg-border px-6">

          {/* LLM Provider */}
          <Field label="LLM Provider" hint="Which AI provider to use for generation">
            <select
              value={form.llm_provider ?? settings.llm_provider}
              onChange={(e) => set("llm_provider", e.target.value)}
              className="w-full px-3 py-1.5 bg-white border border-bg-border rounded-lg text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent/40"
            >
              <option value="anthropic">Anthropic (Claude)</option>
              <option value="openai">OpenAI (GPT)</option>
            </select>
          </Field>

          {/* Model */}
          <Field label="Model" hint="Model identifier string">
            <input
              type="text"
              value={form.llm_model ?? settings.llm_model}
              onChange={(e) => set("llm_model", e.target.value)}
              className="w-full px-3 py-1.5 bg-white border border-bg-border rounded-lg text-sm text-text-primary font-mono focus:outline-none focus:ring-2 focus:ring-accent/40"
            />
          </Field>

          {/* Anthropic API Key */}
          <Field
            label="Anthropic API Key"
            hint={settings.anthropic_api_key ? `Current: ${settings.anthropic_api_key}` : "Not set"}
          >
            <MaskedKeyInput
              value={keys.anthropic}
              placeholder="sk-ant-… (leave blank to keep current)"
              onChange={(v) => setKeys((k) => ({ ...k, anthropic: v }))}
            />
          </Field>

          {/* OpenAI API Key */}
          <Field
            label="OpenAI API Key"
            hint={settings.openai_api_key ? `Current: ${settings.openai_api_key}` : "Not set"}
          >
            <MaskedKeyInput
              value={keys.openai}
              placeholder="sk-… (leave blank to keep current)"
              onChange={(v) => setKeys((k) => ({ ...k, openai: v }))}
            />
          </Field>

          {/* CV Library Path */}
          <Field label="CV Library Path" hint="Path to your cv-library repo">
            <input
              type="text"
              value={form.cv_library_path ?? settings.cv_library_path}
              onChange={(e) => set("cv_library_path", e.target.value)}
              className="w-full px-3 py-1.5 bg-white border border-bg-border rounded-lg text-sm text-text-primary font-mono focus:outline-none focus:ring-2 focus:ring-accent/40"
            />
          </Field>

          {/* Output Path */}
          <Field label="Output Path" hint="Where generated CVs are saved">
            <input
              type="text"
              value={form.output_path ?? settings.output_path}
              onChange={(e) => set("output_path", e.target.value)}
              className="w-full px-3 py-1.5 bg-white border border-bg-border rounded-lg text-sm text-text-primary font-mono focus:outline-none focus:ring-2 focus:ring-accent/40"
            />
          </Field>

          {/* Thinking Budget */}
          <Field label="Thinking Budget" hint="Extended thinking tokens (0 to disable)">
            <div className="flex items-center gap-3">
              <input
                type="number"
                min={0}
                max={32000}
                step={1000}
                value={form.thinking_budget ?? settings.thinking_budget}
                onChange={(e) => set("thinking_budget", parseInt(e.target.value) || 0)}
                className="w-32 px-3 py-1.5 bg-white border border-bg-border rounded-lg text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent/40"
              />
              <span className="text-xs text-text-muted">tokens</span>
            </div>
          </Field>

          {/* Render PDF */}
          <Field label="Render PDF" hint="Automatically render PDF after generation">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={form.render_pdf ?? settings.render_pdf}
                onChange={(e) => set("render_pdf", e.target.checked)}
                className="w-4 h-4 accent-accent"
              />
              <span className="text-sm text-text-secondary">
                {(form.render_pdf ?? settings.render_pdf) ? "Enabled" : "Disabled"}
              </span>
            </label>
          </Field>

        </div>
      )}

      {/* Save */}
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
