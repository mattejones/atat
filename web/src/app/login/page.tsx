"use client";

import { useState } from "react";

import { api } from "@/lib/api";

export default function LoginPage() {
  const [password, setPassword] = useState("");
  const [busy, setBusy]         = useState(false);
  const [error, setError]       = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/auth/login", { password });
      // Only follow a path on this site, never an arbitrary address from the query string.
      const next = new URLSearchParams(window.location.search).get("next");
      window.location.assign(next && next.startsWith("/") && !next.startsWith("//") ? next : "/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't sign in.");
      setBusy(false);
    }
  }

  return (
    <div className="max-w-sm mx-auto pt-10 sm:pt-20">
      <h1 className="text-2xl font-semibold text-text-primary">Welcome to ATAT</h1>
      <p className="text-sm text-text-secondary mt-1">Enter the password to continue.</p>

      <form onSubmit={submit} className="mt-6 space-y-3">
        <label className="block text-xs font-medium text-text-secondary uppercase tracking-wide">
          Password
          <input
            type="password"
            autoComplete="current-password"
            autoFocus
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mt-1 w-full min-h-11 px-3 py-2 bg-bg-elevated border border-bg-border rounded-lg text-base text-text-primary normal-case tracking-normal focus:outline-none focus:ring-2 focus:ring-accent/40"
          />
        </label>

        {error && (
          <p className="px-3 py-2 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{error}</p>
        )}

        <button
          type="submit"
          disabled={busy || !password}
          className="w-full min-h-12 bg-accent text-white font-medium rounded-lg hover:bg-accent-dim transition-colors disabled:opacity-50"
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
