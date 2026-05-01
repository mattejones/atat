"use client";

import {
  useState, useRef, useEffect, useCallback,
} from "react";
import { createPortal } from "react-dom";
import Link from "next/link";

const API = process.env.NEXT_PUBLIC_API_URL;

const STATUS_FLOW: Record<string, string[]> = {
  generated:    ["reviewing", "applied", "excluded", "archived"],
  reviewing:    ["applied", "excluded", "archived"],
  applied:      ["acknowledged", "rejected", "archived"],
  acknowledged: ["interviewing", "rejected", "archived"],
  interviewing: ["offered", "rejected", "archived"],
  offered:      ["rejected", "archived"],
  rejected:     ["archived"],
  excluded:     ["archived"],
  archived:     ["generated"],
};

const STATUS_STYLES: Record<string, string> = {
  generated:    "bg-accent/10 text-accent border border-accent/20",
  reviewing:    "bg-yellow-50 text-yellow-700 border border-yellow-200",
  applied:      "bg-blue-50 text-blue-700 border border-blue-200",
  acknowledged: "bg-purple-50 text-purple-700 border border-purple-200",
  interviewing: "bg-orange-50 text-orange-700 border border-orange-200",
  offered:      "bg-emerald-50 text-emerald-700 border border-emerald-200",
  rejected:     "bg-red-50 text-red-600 border border-red-200",
  excluded:     "bg-gray-100 text-gray-500 border border-gray-200",
  archived:     "bg-gray-50 text-gray-400 border border-gray-200",
};

interface Application {
  id:         string;
  created_at: string;
  company:    string;
  role:       string;
  has_pdf:    boolean;
  status:     string;
}

interface EditState {
  id:      string;
  company: string;
  role:    string;
}

// ── Portal dropdown hook ───────────────────────────────────────────────────────

interface PortalPos { top: number; left: number; width: number }

function usePortalDropdown() {
  const [open, setOpen]   = useState(false);
  const [pos, setPos]     = useState<PortalPos>({ top: 0, left: 0, width: 0 });
  const triggerRef        = useRef<HTMLButtonElement>(null);

  const toggle = useCallback(() => {
    if (!open && triggerRef.current) {
      const r = triggerRef.current.getBoundingClientRect();
      setPos({
        top:   r.bottom + window.scrollY + 4,
        left:  r.left   + window.scrollX,
        width: Math.max(r.width, 160),
      });
    }
    setOpen((o) => !o);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function close() { setOpen(false); }
    document.addEventListener("scroll", close, true);
    document.addEventListener("mousedown", close);
    return () => {
      document.removeEventListener("scroll", close, true);
      document.removeEventListener("mousedown", close);
    };
  }, [open]);

  return { open, setOpen, pos, triggerRef, toggle };
}

// ── Status dropdown ────────────────────────────────────────────────────────────

function StatusDropdown({
  app,
  onUpdate,
}: {
  app: Application;
  onUpdate: (id: string, status: string) => void;
}) {
  const [loading, setLoading]      = useState(false);
  const { open, setOpen, pos, triggerRef, toggle } = usePortalDropdown();
  const options = STATUS_FLOW[app.status] ?? [];

  async function changeStatus(next: string) {
    setOpen(false);
    setLoading(true);
    try {
      const res = await fetch(`${API}/applications/${app.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: next }),
      });
      if (res.ok) onUpdate(app.id, next);
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <button
        ref={triggerRef}
        onClick={() => options.length > 0 && toggle()}
        disabled={loading || options.length === 0}
        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium capitalize transition-opacity ${
          STATUS_STYLES[app.status] ?? STATUS_STYLES.generated
        } ${options.length > 0 ? "cursor-pointer hover:opacity-80" : "cursor-default"}`}
      >
        {loading
          ? <span className="w-2 h-2 border border-current/40 border-t-current rounded-full animate-spin" />
          : app.status}
        {options.length > 0 && !loading && <span className="opacity-50 text-[10px]">▾</span>}
      </button>

      {open && options.length > 0 && createPortal(
        <div
          onMouseDown={(e) => e.stopPropagation()}
          style={{ position: "absolute", top: pos.top, left: pos.left, minWidth: pos.width, zIndex: 9999 }}
          className="bg-white border border-bg-border rounded-lg shadow-xl py-1"
        >
          <p className="px-3 py-1 text-[10px] font-semibold text-text-muted uppercase tracking-wide">
            Move to
          </p>
          {options.map((s) => (
            <button
              key={s}
              onClick={() => changeStatus(s)}
              className="w-full text-left px-3 py-1.5 text-xs text-text-secondary hover:bg-bg-surface hover:text-text-primary capitalize transition-colors"
            >
              {s}
            </button>
          ))}
        </div>,
        document.body
      )}
    </>
  );
}

// ── Delete / archive menu ──────────────────────────────────────────────────────

function DeleteMenu({
  app,
  onArchive,
  onDelete,
}: {
  app: Application;
  onArchive: (id: string) => void;
  onDelete:  (id: string) => void;
}) {
  const [confirm, setConfirm] = useState(false);
  const [loading, setLoading] = useState(false);
  const { open, setOpen, pos, triggerRef, toggle } = usePortalDropdown();

  const menuLeft = pos.left - 150 + pos.width;

  async function archive() {
    setLoading(true);
    try {
      const res = await fetch(`${API}/applications/${app.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "archived" }),
      });
      if (res.ok) onArchive(app.id);
    } finally {
      setLoading(false);
      setOpen(false);
    }
  }

  async function hardDelete() {
    setLoading(true);
    try {
      await fetch(`${API}/applications/${app.id}?delete_files=false`, {
        method: "DELETE",
      });
      onDelete(app.id);
    } finally {
      setLoading(false);
      setOpen(false);
      setConfirm(false);
    }
  }

  return (
    <>
      <button
        ref={triggerRef}
        onClick={() => { toggle(); setConfirm(false); }}
        className="text-text-muted hover:text-text-secondary text-xs transition-colors"
        title="Delete or archive"
      >
        ···
      </button>

      {open && createPortal(
        <div
          onMouseDown={(e) => e.stopPropagation()}
          style={{ position: "absolute", top: pos.top, left: menuLeft, minWidth: 160, zIndex: 9999 }}
          className="bg-white border border-bg-border rounded-lg shadow-xl py-1"
        >
          {!confirm ? (
            <>
              <button
                onClick={archive}
                disabled={loading || app.status === "archived"}
                className="w-full text-left px-3 py-1.5 text-xs text-text-secondary hover:bg-bg-surface transition-colors disabled:opacity-40"
              >
                Archive
              </button>
              <button
                onClick={() => setConfirm(true)}
                className="w-full text-left px-3 py-1.5 text-xs text-red-500 hover:bg-red-50 transition-colors"
              >
                Delete…
              </button>
            </>
          ) : (
            <div className="px-3 py-2 space-y-2">
              <p className="text-xs text-text-secondary leading-tight">
                Remove from tracker?
                <br />
                <span className="text-text-muted">(Files kept on disk)</span>
              </p>
              <div className="flex gap-2">
                <button
                  onClick={hardDelete}
                  disabled={loading}
                  className="text-xs text-red-500 hover:underline disabled:opacity-50"
                >
                  {loading ? "Deleting…" : "Confirm"}
                </button>
                <button
                  onClick={() => setConfirm(false)}
                  className="text-xs text-text-muted hover:text-text-secondary"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>,
        document.body
      )}
    </>
  );
}

// ── Main table ────────────────────────────────────────────────────────────────

export default function ApplicationsTable({
  applications: initial,
}: {
  applications: Application[];
}) {
  const [apps, setApps]       = useState<Application[]>(initial);
  const [editing, setEditing] = useState<EditState | null>(null);
  const [saving, setSaving]   = useState(false);

  function updateStatus(id: string, status: string) {
    setApps((prev) => prev.map((a) => (a.id === id ? { ...a, status } : a)));
  }
  function removeApp(id: string) {
    setApps((prev) => prev.filter((a) => a.id !== id));
  }
  function startEdit(app: Application) {
    setEditing({ id: app.id, company: app.company, role: app.role });
  }
  function cancelEdit() { setEditing(null); }

  async function saveEdit() {
    if (!editing) return;
    setSaving(true);
    try {
      const res = await fetch(`${API}/applications/${editing.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ company: editing.company, role: editing.role }),
      });
      if (res.ok) {
        const updated = await res.json();
        setApps((prev) =>
          prev.map((a) =>
            a.id === editing.id
              ? { ...a, company: updated.company, role: updated.role }
              : a
          )
        );
        setEditing(null);
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="rounded-xl border border-bg-border overflow-visible">
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="bg-bg-surface border-b border-bg-border">
            {["Date", "Company", "Role", "Status", "PDF", ""].map((h, i) => (
              <th
                key={i}
                className="text-left px-4 py-3 text-xs font-semibold text-text-muted uppercase tracking-wide"
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-bg-border">
          {apps.map((app, rowIdx) => {
            const isEditing = editing?.id === app.id;
            const isLast    = rowIdx === apps.length - 1;
            return (
              <tr
                key={app.id}
                className={`bg-bg-elevated hover:bg-bg-surface transition-colors ${
                  app.status === "archived" ? "opacity-50" : ""
                }`}
              >
                <td className={`px-4 py-3 text-text-muted font-mono text-xs whitespace-nowrap ${isLast ? "rounded-bl-xl" : ""}`}>
                  {app.created_at ? app.created_at.slice(0, 10) : "—"}
                </td>

                <td className="px-4 py-2">
                  {isEditing ? (
                    <input
                      autoFocus
                      value={editing.company}
                      onChange={(e) => setEditing((p) => p ? { ...p, company: e.target.value } : p)}
                      className="w-full px-2 py-1 text-sm border border-accent/50 rounded-md bg-white focus:outline-none focus:ring-1 focus:ring-accent"
                    />
                  ) : (
                    <span className="font-medium text-text-primary">{app.company}</span>
                  )}
                </td>

                <td className="px-4 py-2">
                  {isEditing ? (
                    <input
                      value={editing.role}
                      onChange={(e) => setEditing((p) => p ? { ...p, role: e.target.value } : p)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") saveEdit();
                        if (e.key === "Escape") cancelEdit();
                      }}
                      className="w-full px-2 py-1 text-sm border border-accent/50 rounded-md bg-white focus:outline-none focus:ring-1 focus:ring-accent"
                    />
                  ) : (
                    <span className="text-text-secondary">{app.role}</span>
                  )}
                </td>

                <td className="px-4 py-3">
                  <StatusDropdown app={app} onUpdate={updateStatus} />
                </td>

                <td className="px-4 py-3">
                  {app.has_pdf
                    ? <span className="text-accent text-xs">✓</span>
                    : <span className="text-text-muted text-xs">—</span>}
                </td>

                <td className={`px-4 py-3 ${isLast ? "rounded-br-xl" : ""}`}>
                  <div className="flex items-center justify-end gap-3">
                    {isEditing ? (
                      <>
                        <button onClick={saveEdit} disabled={saving}
                          className="text-xs text-accent hover:underline disabled:opacity-50">
                          {saving ? "Saving…" : "Save"}
                        </button>
                        <button onClick={cancelEdit}
                          className="text-xs text-text-muted hover:text-text-secondary">
                          Cancel
                        </button>
                      </>
                    ) : (
                      <>
                        <button onClick={() => startEdit(app)}
                          className="text-xs text-text-muted hover:text-text-secondary transition-colors">
                          Edit
                        </button>
                        <Link href={`/applications/${app.id}`}
                          className="text-xs text-accent hover:underline">
                          View →
                        </Link>
                        <DeleteMenu
                          app={app}
                          onArchive={(id) => updateStatus(id, "archived")}
                          onDelete={removeApp}
                        />
                      </>
                    )}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
