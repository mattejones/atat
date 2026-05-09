"use client";

import { useState, useEffect } from "react";
import Link from "next/link";

const API = process.env.NEXT_PUBLIC_API_URL;

interface PromptMeta {
  slug:        string;
  label:       string;
  description: string;
  personal:    boolean;
  exists:      boolean;
}

export default function PromptsPage() {
  const [prompts, setPrompts] = useState<PromptMeta[]>([]);
  const [error, setError]     = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API}/prompts`)
      .then((r) => r.json())
      .then(setPrompts)
      .catch(() => setError("Could not load prompts — is the API running?"));
  }, []);

  const groups = [
    { label: "Generation",  slugs: ["cv_generation", "personal_additions"] },
    { label: "Judge",       slugs: ["judge_accuracy"] },
    { label: "Retry",       slugs: ["retry_system", "retry_sections/profile", "retry_sections/experience", "retry_sections/skills", "retry_sections/education", "retry_sections/certifications"] },
  ];

  const bySlug = Object.fromEntries(prompts.map((p) => [p.slug, p]));

  return (
    <div className="max-w-2xl mx-auto space-y-8">
      <div>
        <h1 className="text-2xl font-semibold text-text-primary">Prompts</h1>
        <p className="text-sm text-text-secondary mt-1">
          All prompt files are stored on the filesystem and can also be edited
          directly in any markdown editor.
          Changes take effect immediately — no restart required.
        </p>
      </div>

      {error && (
        <div className="px-4 py-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
          {error}
        </div>
      )}

      {groups.map(({ label, slugs }) => (
        <div key={label}>
          <h2 className="text-xs font-semibold text-text-muted uppercase tracking-wide mb-3">
            {label}
          </h2>
          <div className="bg-bg-elevated border border-bg-border rounded-xl divide-y divide-bg-border">
            {slugs.map((slug) => {
              const p = bySlug[slug];
              if (!p) return null;
              return (
                <Link
                  key={slug}
                  href={`/prompts/${slug}`}
                  className="flex items-center justify-between px-5 py-4 hover:bg-bg-surface transition-colors group"
                >
                  <div className="space-y-0.5 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-text-primary group-hover:text-accent transition-colors">
                        {p.label}
                      </span>
                      {p.personal && (
                        <span className="text-[10px] font-medium text-amber-600 bg-amber-50 border border-amber-200 px-1.5 py-0.5 rounded">
                          personal
                        </span>
                      )}
                      {!p.exists && (
                        <span className="text-[10px] font-medium text-text-muted bg-bg-surface border border-bg-border px-1.5 py-0.5 rounded">
                          not created
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-text-muted truncate">{p.description}</p>
                  </div>
                  <span className="text-text-muted group-hover:text-accent transition-colors ml-4 flex-shrink-0">
                    →
                  </span>
                </Link>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
