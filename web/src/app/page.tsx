"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import ApplicationsTable from "@/components/ApplicationsTable";

const API = process.env.NEXT_PUBLIC_API_URL;

// ── Manual log modal ──────────────────────────────────────────────────────────

const STATUSES = ["applied", "acknowledged", "interviewing", "offered", "rejected", "reviewing"];

function LogManualModal({
  onClose,
  onCreated,
}: {
  onClose:   () => void;
  onCreated: (app: any) => void;
}) {
  const [form, setForm] = useState({
    company:      "",
    role:         "",
    source_url:   "",
    status:       "applied",
    applied_date: new Date().toISOString().slice(0, 10),
    notes:        "",
  });
  const [saving, setSaving] = useState(false);
  const [error, setError]   = useState<string | null>(null);

  function set(k: string, v: string) {
    setForm((f) => ({ ...f, [k]: v }));
  }

  async function save() {
    if (!form.company.trim() || !form.role.trim()) {
      setError("Company and role are required.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`${API}/applications`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          company:      form.company.trim(),
          role:         form.role.trim(),
          source_url:   form.source_url.trim() || null,
          status:       form.status,
          applied_date: form.applied_date || null,
          notes:        form.notes.trim() || null,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to save");
      onCreated(data);
      onClose();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md p-6 space-y-5">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-text-primary">Log an application</h2>
          <button
            onClick={onClose}
            className="text-text-muted hover:text-text-secondary text-xl leading-none"
          >
            ×
          </button>
        </div>

        <p className="text-xs text-text-secondary">
          Record a job you applied for outside ATAT — no CV or JD needed.
        </p>

        <div className="space-y-3">
          {/* Company */}
          <div className="space-y-1">
            <label className="text-xs font-medium text-text-secondary uppercase tracking-wide">
              Company <span className="text-red-400">*</span>
            </label>
            <input
              type="text"
              value={form.company}
              onChange={(e) => set("company", e.target.value)}
              placeholder="e.g. Stripe"
              className="w-full px-3 py-2 border border-bg-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-accent/40"
            />
          </div>

          {/* Role */}
          <div className="space-y-1">
            <label className="text-xs font-medium text-text-secondary uppercase tracking-wide">
              Role <span className="text-red-400">*</span>
            </label>
            <input
              type="text"
              value={form.role}
              onChange={(e) => set("role", e.target.value)}
              placeholder="e.g. RevOps Manager"
              className="w-full px-3 py-2 border border-bg-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-accent/40"
            />
          </div>

          {/* Status + Date */}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <label className="text-xs font-medium text-text-secondary uppercase tracking-wide">
                Status
              </label>
              <select
                value={form.status}
                onChange={(e) => set("status", e.target.value)}
                className="w-full px-3 py-2 border border-bg-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-accent/40 bg-white capitalize"
              >
                {STATUSES.map((s) => (
                  <option key={s} value={s} className="capitalize">{s}</option>
                ))}
              </select>
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-text-secondary uppercase tracking-wide">
                Date applied
              </label>
              <input
                type="date"
                value={form.applied_date}
                onChange={(e) => set("applied_date", e.target.value)}
                className="w-full px-3 py-2 border border-bg-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-accent/40"
              />
            </div>
          </div>

          {/* Source URL */}
          <div className="space-y-1">
            <label className="text-xs font-medium text-text-secondary uppercase tracking-wide">
              Job URL <span className="text-text-muted font-normal normal-case ml-1">optional</span>
            </label>
            <input
              type="url"
              value={form.source_url}
              onChange={(e) => set("source_url", e.target.value)}
              placeholder="https://…"
              className="w-full px-3 py-2 border border-bg-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-accent/40"
            />
          </div>

          {/* Notes */}
          <div className="space-y-1">
            <label className="text-xs font-medium text-text-secondary uppercase tracking-wide">
              Notes <span className="text-text-muted font-normal normal-case ml-1">optional</span>
            </label>
            <textarea
              value={form.notes}
              onChange={(e) => set("notes", e.target.value)}
              rows={2}
              placeholder="Recruiter contact, referral, context…"
              className="w-full px-3 py-2 border border-bg-border rounded-lg text-sm resize-none focus:outline-none focus:ring-2 focus:ring-accent/40"
            />
          </div>
        </div>

        {error && (
          <p className="text-xs text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
            {error}
          </p>
        )}

        <div className="flex justify-end gap-2 pt-1">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary border border-bg-border rounded-lg transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={save}
            disabled={saving}
            className="px-4 py-2 text-sm font-medium bg-accent text-white rounded-lg hover:bg-accent-dim transition-colors disabled:opacity-50"
          >
            {saving ? "Saving…" : "Log application"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Home page ─────────────────────────────────────────────────────────────────

export default function HomePage() {
  const [apps, setApps]           = useState<any[]>([]);
  const [loading, setLoading]     = useState(true);
  const [showModal, setShowModal] = useState(false);

  useEffect(() => {
    fetch(`${API}/applications`, { cache: "no-store" } as any)
      .then((r) => r.json())
      .then((data) => setApps(Array.isArray(data) ? data : []))
      .catch(() => setApps([]))
      .finally(() => setLoading(false));
  }, []);

  function handleCreated(app: any) {
    setApps((prev) => [app, ...prev]);
  }

  return (
    <>
      {showModal && (
        <LogManualModal
          onClose={() => setShowModal(false)}
          onCreated={handleCreated}
        />
      )}

      <div className="space-y-8">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-text-primary">Applications</h1>
            <p className="text-sm text-text-secondary mt-1">
              {loading
                ? "Loading…"
                : apps.length > 0
                  ? `${apps.length} application${apps.length !== 1 ? "s" : ""} tracked`
                  : "No applications tracked yet"}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowModal(true)}
              className="px-4 py-2 border border-bg-border text-sm text-text-secondary rounded-lg hover:text-text-primary hover:border-accent/40 transition-colors"
            >
              Log manually
            </button>
            <Link
              href="/generate"
              className="inline-flex items-center gap-2 px-4 py-2 bg-accent text-white text-sm font-medium rounded-lg hover:bg-accent-dim transition-colors"
            >
              + New Application
            </Link>
          </div>
        </div>

        {/* Content */}
        {loading ? (
          <div className="flex items-center justify-center py-24 text-text-muted text-sm">
            Loading…
          </div>
        ) : apps.length === 0 ? (
          <div className="text-center py-24 text-text-muted border border-bg-border rounded-xl bg-bg-surface">
            <p className="text-lg font-medium text-text-secondary mb-2">Nothing here yet</p>
            <p className="text-sm">
              <Link href="/generate" className="text-accent hover:underline">
                Generate a tailored CV
              </Link>
              {" "}or{" "}
              <button
                onClick={() => setShowModal(true)}
                className="text-accent hover:underline"
              >
                log an existing application
              </button>
              .
            </p>
          </div>
        ) : (
          <ApplicationsTable applications={apps} />
        )}
      </div>
    </>
  );
}
