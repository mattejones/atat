"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export default function Nav() {
  const path = usePathname();

  const links = [
    { href: "/",         label: "Applications" },
    { href: "/generate", label: "New"          },
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
            </Link>
          ))}
        </div>
      </div>
    </nav>
  );
}
