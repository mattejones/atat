"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { api } from "@/lib/api";
import Sheet from "@/components/ui/Sheet";

// The everyday pages, always one tap away. Everything else lives under "More".
const PRIMARY = [
  { href: "/",         label: "Applications", icon: "▤" },
  { href: "/generate", label: "New",          icon: "＋" },
  { href: "/inbox",    label: "Inbox",        icon: "✉" },
] as const;

const MORE = [
  { href: "/drafts",   label: "Drafts",   hint: "Requests waiting for you to review" },
  { href: "/insights", label: "Insights", hint: "How your applications are going" },
  { href: "/about",    label: "About",    hint: "" },
];

const ADVANCED = [
  { href: "/prompts",  label: "Prompts",  hint: "Edit the instructions the AI follows" },
  { href: "/settings", label: "Settings", hint: "Connections, schedules and keys" },
];

function Badge({ count }: { count: number }) {
  if (count <= 0) return null;
  return (
    <span className="ml-1.5 inline-flex items-center justify-center min-w-[1.1rem] h-[1.1rem] px-1 text-[10px] font-semibold bg-status-reviewing text-white rounded-full">
      {count}
    </span>
  );
}

export default function Nav() {
  const path = usePathname();
  const [counts, setCounts] = useState({ drafts: 0, inbox: 0 });
  const [moreOpen, setMoreOpen] = useState(false);

  // Badge counts (drafts the agent saved over MCP, emails waiting for review). Re-checked
  // on navigation and every 30s so a new one shows up without a reload — but not while the
  // tab is hidden, and again as soon as it's shown. One cheap call for both.
  useEffect(() => {
    if (path === "/login") return;
    const check = () => {
      if (document.hidden) return;
      api.get("/counts")
        .then((c: { drafts: number; inbox: number }) => setCounts({ drafts: c.drafts, inbox: c.inbox }))
        .catch(() => {});
    };
    check();
    document.addEventListener("visibilitychange", check);
    const t = setInterval(check, 30000);
    return () => {
      clearInterval(t);
      document.removeEventListener("visibilitychange", check);
    };
  }, [path]);

  // Close the More sheet whenever we navigate.
  useEffect(() => setMoreOpen(false), [path]);

  // Only instances with a password get a Sign out entry.
  const [authRequired, setAuthRequired] = useState(false);
  useEffect(() => {
    api.get("/auth/status").then((s: { auth_required: boolean }) => setAuthRequired(s.auth_required)).catch(() => {});
  }, []);

  async function signOut() {
    try { await api.post("/auth/logout"); } finally { window.location.assign("/login"); }
  }

  if (path === "/login") return null;

  const isActive = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));
  const countFor = (href: string) =>
    href === "/inbox" ? counts.inbox : href === "/drafts" ? counts.drafts : 0;
  const moreActive = [...MORE, ...ADVANCED].some((l) => isActive(l.href));
  const moreBadge = counts.drafts;

  return (
    <>
      {/* Top bar: logo everywhere, links from md up */}
      <nav className="border-b border-bg-border bg-bg-surface">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 h-14 flex items-center justify-between">
          <Link href="/" className="flex items-center gap-2">
            <span className="text-sm font-bold text-accent tracking-tight">ATAT</span>
            <span className="hidden sm:block text-xs text-text-muted">
              Application Tracking &amp; Automation Tool
            </span>
          </Link>

          <div className="hidden md:flex items-center gap-1">
            {PRIMARY.map(({ href, label }) => (
              <Link
                key={href}
                href={href}
                className={`px-3 py-1.5 text-sm rounded-md transition-colors ${
                  isActive(href)
                    ? "text-text-primary bg-bg-elevated font-medium"
                    : "text-text-secondary hover:text-text-primary hover:bg-bg-elevated"
                }`}
              >
                {label}
                <Badge count={countFor(href)} />
              </Link>
            ))}
            <button
              onClick={() => setMoreOpen(true)}
              className={`px-3 py-1.5 text-sm rounded-md transition-colors ${
                moreActive
                  ? "text-text-primary bg-bg-elevated font-medium"
                  : "text-text-secondary hover:text-text-primary hover:bg-bg-elevated"
              }`}
            >
              More
              <Badge count={moreBadge} />
            </button>
          </div>
        </div>
      </nav>

      {/* Bottom tab bar: phones only */}
      <nav
        aria-label="Main"
        className="md:hidden fixed bottom-0 inset-x-0 z-40 border-t border-bg-border bg-bg-surface pb-[env(safe-area-inset-bottom)]"
      >
        <ul className="flex">
          {PRIMARY.map(({ href, label, icon }) => (
            <li key={href} className="flex-1">
              <Link
                href={href}
                className={`flex flex-col items-center justify-center min-h-14 gap-0.5 text-[11px] ${
                  isActive(href) ? "text-accent font-medium" : "text-text-muted"
                }`}
              >
                <span className="relative text-lg leading-none" aria-hidden>
                  {icon}
                  {countFor(href) > 0 && (
                    <span className="absolute -top-1 -right-2.5 min-w-[1rem] h-4 px-1 text-[10px] leading-4 text-center font-semibold bg-status-reviewing text-white rounded-full">
                      {countFor(href)}
                    </span>
                  )}
                </span>
                {label}
              </Link>
            </li>
          ))}
          <li className="flex-1">
            <button
              onClick={() => setMoreOpen(true)}
              className={`w-full flex flex-col items-center justify-center min-h-14 gap-0.5 text-[11px] ${
                moreActive ? "text-accent font-medium" : "text-text-muted"
              }`}
            >
              <span className="relative text-lg leading-none" aria-hidden>
                ⋯
                {moreBadge > 0 && (
                  <span className="absolute -top-1 -right-2.5 min-w-[1rem] h-4 px-1 text-[10px] leading-4 text-center font-semibold bg-status-reviewing text-white rounded-full">
                    {moreBadge}
                  </span>
                )}
              </span>
              More
            </button>
          </li>
        </ul>
      </nav>

      <Sheet open={moreOpen} onClose={() => setMoreOpen(false)} title="More">
        <ul className="space-y-1">
          {MORE.map((l) => (
            <MoreLink key={l.href} {...l} count={countFor(l.href)} active={isActive(l.href)} />
          ))}
        </ul>
        <p className="mt-4 mb-1 px-1 text-[10px] font-semibold text-text-muted uppercase tracking-wide">
          Advanced
        </p>
        <ul className="space-y-1">
          {ADVANCED.map((l) => (
            <MoreLink key={l.href} {...l} count={0} active={isActive(l.href)} />
          ))}
        </ul>
        {authRequired && (
          <button
            onClick={signOut}
            className="mt-4 w-full min-h-11 px-3 text-left text-sm text-text-secondary rounded-lg border border-bg-border hover:bg-bg-surface"
          >
            Sign out
          </button>
        )}
      </Sheet>
    </>
  );
}

function MoreLink({
  href, label, hint, count, active,
}: {
  href: string; label: string; hint: string; count: number; active: boolean;
}) {
  return (
    <li>
      <Link
        href={href}
        className={`block min-h-11 px-3 py-2 rounded-lg hover:bg-bg-surface ${active ? "bg-bg-surface" : ""}`}
      >
        <span className="text-sm text-text-primary">
          {label}
          <Badge count={count} />
        </span>
        {hint && <span className="block text-xs text-text-muted">{hint}</span>}
      </Link>
    </li>
  );
}
