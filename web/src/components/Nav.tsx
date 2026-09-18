"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { api } from "@/lib/api";

export default function Nav() {
  const path = usePathname();
  const [drafts, setDrafts] = useState(0);

  // Drafts waiting for review — e.g. ones the agent saved over MCP. Re-checked on
  // navigation and every 30s so a new one shows up without a reload.
  useEffect(() => {
    const check = () =>
      api.get("/jobs?status=draft&limit=100")
        .then((d: unknown[]) => setDrafts(d.length))
        .catch(() => {});
    check();
    const t = setInterval(check, 30000);
    return () => clearInterval(t);
  }, [path]);

  const links = [
    { href: "/",         label: "Applications" },
    { href: "/generate", label: "New"          },
    { href: "/drafts",   label: "Drafts"       },
    { href: "/prompts",  label: "Prompts"      },
    { href: "/settings", label: "Settings"     },
    { href: "/about",    label: "About"        },
  ];

  const isActive = (href: string) =>
    href === "/" ? path === "/" : path.startsWith(href);

  return (
    <nav className="border-b border-bg-border bg-bg-surface">
      <div className="max-w-6xl mx-auto px-6 h-14 flex items-center justify-between">
        <Link href="/" className="flex items-center gap-2">
          <span className="text-sm font-bold text-accent tracking-tight">ATAT</span>
          <span className="hidden sm:block text-xs text-text-muted">
            Application Tracking &amp; Automation Tool
          </span>
        </Link>

        <div className="flex items-center gap-1">
          {links.map(({ href, label }) => (
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
              {href === "/drafts" && drafts > 0 && (
                <span className="ml-1.5 inline-flex items-center justify-center min-w-[1.1rem] h-[1.1rem] px-1 text-[10px] font-semibold bg-status-reviewing text-white rounded-full">
                  {drafts}
                </span>
              )}
            </Link>
          ))}
        </div>
      </div>
    </nav>
  );
}
