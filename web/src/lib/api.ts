// Same-origin by default: the browser calls /api/*, which next.config.js proxies to the
// FastAPI server. That works from a phone or behind a reverse proxy with no per-host build.
// Set NEXT_PUBLIC_API_URL only to talk to the API directly (dev.sh does this).
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "/api";

export const api = {
  url: API_URL,

  async get(path: string) {
    const res = await fetch(`${API_URL}${path}`, { cache: "no-store" });
    if (!res.ok) throw new Error(`GET ${path} failed: ${res.status}`);
    return res.json();
  },

  async post(path: string, body?: unknown) {
    const res = await fetch(`${API_URL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `POST ${path} failed: ${res.status}`);
    }
    return res.json();
  },

  async put(path: string, body: unknown) {
    const res = await fetch(`${API_URL}${path}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`PUT ${path} failed: ${res.status}`);
    return res.json();
  },

  async patch(path: string, body: unknown) {
    const res = await fetch(`${API_URL}${path}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `PATCH ${path} failed: ${res.status}`);
    }
    return res.json();
  },

  async delete(path: string) {
    const res = await fetch(`${API_URL}${path}`, { method: "DELETE" });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `DELETE ${path} failed: ${res.status}`);
    }
    return res.json().catch(() => ({}));
  },
};
