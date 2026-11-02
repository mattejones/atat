"use client";

import { useEffect, useState, type ReactNode } from "react";
import { usePathname } from "next/navigation";

import { api } from "@/lib/api";

function goToLogin() {
  if (window.location.pathname === "/login") return;   // already there — don't loop
  const here = window.location.pathname + window.location.search;
  window.location.assign(`/login?next=${encodeURIComponent(here)}`);
}

/**
 * Sends signed-out visitors to /login when the instance has a password (ATAT_PASSWORD).
 * Also catches a session that expires mid-use: any API call that comes back 401 bounces to
 * the login page instead of leaving a page full of silent failures. With no password set
 * it does nothing.
 */
export default function AuthGate({ children }: { children: ReactNode }) {
  const path = usePathname();
  const onLogin = path === "/login";
  const [ready, setReady] = useState(onLogin);

  useEffect(() => {
    const original = window.fetch;
    window.fetch = async (...args) => {
      const res = await original(...args);
      const url = typeof args[0] === "string" ? args[0] : args[0] instanceof Request ? args[0].url : String(args[0]);
      if (res.status === 401 && url.includes(api.url) && !url.includes("/auth/")) goToLogin();
      return res;
    };
    return () => { window.fetch = original; };
  }, []);

  useEffect(() => {
    if (onLogin) { setReady(true); return; }
    api.get("/auth/status")
      .then((s: { auth_required: boolean; authenticated: boolean }) => {
        if (s.auth_required && !s.authenticated) goToLogin();
        else setReady(true);
      })
      // If the status call itself fails the API is down; let the page show its own errors.
      .catch(() => setReady(true));
  }, [onLogin]);

  return ready ? <>{children}</> : null;
}
