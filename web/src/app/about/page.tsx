import Link from "next/link";

export default function AboutPage() {
  return (
    <div className="max-w-2xl mx-auto space-y-10 py-4">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-semibold text-text-primary">About ATAT</h1>
        <p className="text-sm text-text-secondary mt-1">
          Application Tracking and Automation Tool
        </p>
      </div>

      {/* What it is */}
      <section className="space-y-3">
        <h2 className="text-xs font-semibold text-accent uppercase tracking-widest">
          What it is
        </h2>
        <p className="text-sm text-text-secondary leading-relaxed">
          ATAT is a personal job application tool built around a structured CV library and an
          LLM-powered tailoring pipeline. Drop in a job description, get back a tailored CV in
          Markdown, review and edit it, then render it to a polished PDF via Typst.
        </p>
        <p className="text-sm text-text-secondary leading-relaxed">
          Applications are tracked through their lifecycle — from generation through to offer or
          rejection — with a full event log and support for email receipt matching.
        </p>
      </section>

      {/* How it works */}
      <section className="space-y-3">
        <h2 className="text-xs font-semibold text-accent uppercase tracking-widest">
          How it works
        </h2>
        <div className="space-y-2">
          {[
            ["CV library", "A structured Markdown library of experience, skills, and personas that the LLM draws from when tailoring."],
            ["LLM tailoring", "An extended-thinking pipeline that selects the best persona, grounds every claim in the library, and writes without hallucination."],
            ["Typst rendering", "Markdown is parsed and rendered to a professionally designed PDF via Typst — warm palette, Poppins typeface, clean hierarchy."],
            ["Tracking", "Every application is stored in SQLite with status, event history, and key dates."],
          ].map(([title, desc]) => (
            <div key={title} className="flex gap-3 py-2 border-b border-bg-border last:border-0">
              <span className="text-sm font-medium text-text-primary w-36 flex-shrink-0">
                {title}
              </span>
              <span className="text-sm text-text-secondary leading-relaxed">{desc}</span>
            </div>
          ))}
        </div>
      </section>

      {/* Built by */}
      <section className="space-y-3">
        <h2 className="text-xs font-semibold text-accent uppercase tracking-widest">
          Built by
        </h2>
        <p className="text-sm text-text-secondary leading-relaxed">
          Matt Jones — systems and operations professional, occasional builder of things that
          should exist but don't.
        </p>
        <div className="flex gap-4">
          <a
            href="https://mej.xyz"
            target="_blank"
            rel="noopener noreferrer"
            className="text-sm text-accent hover:underline"
          >
            mej.xyz ↗
          </a>
          <a
            href="https://linkedin.com/in/j0n35"
            target="_blank"
            rel="noopener noreferrer"
            className="text-sm text-accent hover:underline"
          >
            LinkedIn ↗
          </a>
        </div>
      </section>

      {/* CTA */}
      <div className="pt-2">
        <Link
          href="/generate"
          className="inline-flex items-center gap-2 px-4 py-2 bg-accent text-white text-sm font-medium rounded-lg hover:bg-accent-dim transition-colors"
        >
          + New Application
        </Link>
      </div>
    </div>
  );
}
