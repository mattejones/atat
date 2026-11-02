"use client";

import {
  useState, useRef, useEffect, useCallback, useMemo,
} from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";

import { STAGES, STATUS_LABELS } from "@/lib/statuses";
import { decodeEntities } from "@/lib/text";
import Sheet from "@/components/ui/Sheet";
import StatusPicker from "@/components/ui/StatusPicker";

import { api } from "@/lib/api";
const API = api.url;

type SortKey = "applied_date" | "company" | "role" | "status";
type SortDir = "asc" | "desc";

interface Application {
  id:           string;   // slug — used for display only
  uuid:         string;   // used for all navigation and API calls
  created_at:   string;
  applied_date: string | null;
  company:      string;
  role:         string;
  has_pdf:      boolean;
  status:       string;
}

interface EditState {
  uuid:         string;   // used for API calls
  company:      string;
  role:         string;
  applied_date: string;
}

interface PortalPos { top: number; left: number; width: number }

function usePortalDropdown() {
  const [open, setOpen] = useState(false);
  const [pos, setPos]   = useState<PortalPos>({ top: 0, left: 0, width: 0 });
  const triggerRef      = useRef<HTMLButtonElement>(null);

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

// ── Actions menu ──────────────────────────────────────────────────────────────

function ActionsMenu({
  app,
  onEdit,
  onArchive,
  onDelete,
}: {
  app:       Application;
  onEdit:    (app: Application) => void;
  onArchive: (uuid: string) => void;
  onDelete:  (uuid: string) => void;
}) {
  const [confirm, setConfirm] = useState(false);
  const [loading, setLoading] = useState(false);
  const { open, setOpen, pos, triggerRef, toggle } = usePortalDropdown();
  const menuLeft = pos.left - 150 + pos.width;

  async function archive() {
    setLoading(true);
    try {
      const res = await fetch(`${API}/applications/${app.uuid}`, {
        method:  "PATCH",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ status: "archived" }),
      });
      if (res.ok) onArchive(app.uuid);
    } finally {
      setLoading(false);
      setOpen(false);
    }
  }

  async function hardDelete() {
    setLoading(true);
    try {
      await fetch(`${API}/applications/${app.uuid}?delete_files=false`, { method: "DELETE" });
      onDelete(app.uuid);
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
        onClick={(e) => { e.stopPropagation(); toggle(); setConfirm(false); }}
        className="text-text-muted hover:text-text-secondary text-xs transition-colors px-1"
        title="Actions"
      >
        ···
      </button>

      {open && createPortal(
        <div
          onClick={(e) => e.stopPropagation()}
          onMouseDown={(e) => e.stopPropagation()}
          style={{ position: "absolute", top: pos.top, left: menuLeft, minWidth: 160, zIndex: 9999 }}
          className="bg-white border border-bg-border rounded-lg shadow-xl py-1"
        >
          {!confirm ? (
            <>
              <button
                onClick={(e) => { e.stopPropagation(); setOpen(false); onEdit(app); }}
                className="w-full text-left px-3 py-1.5 text-xs text-text-secondary hover:bg-bg-surface transition-colors"
              >
                Edit
              </button>
              <button
                onClick={archive}
                disabled={loading || app.status === "archived"}
                className="w-full text-left px-3 py-1.5 text-xs text-text-secondary hover:bg-bg-surface transition-colors disabled:opacity-40"
              >
                Archive
              </button>
              <div className="border-t border-bg-border my-1" />
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
                Remove from tracker?<br />
                <span className="text-text-muted">(Files kept on disk)</span>
              </p>
              <div className="flex gap-2">
                <button onClick={hardDelete} disabled={loading}
                  className="text-xs text-red-500 hover:underline disabled:opacity-50">
                  {loading ? "Deleting…" : "Confirm"}
                </button>
                <button onClick={() => setConfirm(false)}
                  className="text-xs text-text-muted hover:text-text-secondary">
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

function SortIndicator({ col, sortKey, sortDir }: { col: SortKey; sortKey: SortKey; sortDir: SortDir }) {
  if (col !== sortKey) return <span className="opacity-20 ml-1">↕</span>;
  return <span className="ml-1 text-accent">{sortDir === "asc" ? "↑" : "↓"}</span>;
}

// ── Main table ────────────────────────────────────────────────────────────────

export default function ApplicationsTable({
  applications: initial,
}: {
  applications: Application[];
}) {
  const router                      = useRouter();
  const [apps, setApps]             = useState<Application[]>(initial);
  const [editing, setEditing]       = useState<EditState | null>(null);
  const [saving, setSaving]         = useState(false);
  const [search, setSearch]         = useState("");
  const [sortKey, setSortKey]       = useState<SortKey>("applied_date");
  const [sortDir, setSortDir]       = useState<SortDir>("desc");
  const [stage, setStage]           = useState<string>("all");

  // Phone-only: an actions sheet per application, and an edit sheet (the table edits inline).
  const [actionsFor, setActionsFor]     = useState<Application | null>(null);
  const [confirmDelete, setConfirmDel]  = useState(false);
  const [sheetEdit, setSheetEdit]       = useState<EditState | null>(null);
  const [sheetBusy, setSheetBusy]       = useState(false);
  const [sheetError, setSheetError]     = useState<string | null>(null);

  function toggleSort(col: SortKey) {
    if (sortKey === col) {
      setSortDir((d) => d === "asc" ? "desc" : "asc");
    } else {
      setSortKey(col);
      setSortDir("desc");
    }
  }

  const visible = useMemo(() => {
    const q = search.toLowerCase().trim();
    const stageStatuses = STAGES.find((s) => s.key === stage)?.statuses as readonly string[] | undefined;
    const inStage = stageStatuses ? apps.filter((a) => stageStatuses.includes(a.status)) : apps;
    const filtered = q
      ? inStage.filter((a) =>
          decodeEntities(a.company).toLowerCase().includes(q) ||
          decodeEntities(a.role).toLowerCase().includes(q) ||
          a.status.toLowerCase().includes(q) ||
          (STATUS_LABELS[a.status] ?? "").toLowerCase().includes(q)
        )
      : inStage;

    return [...filtered].sort((a, b) => {
      let av = "", bv = "";
      switch (sortKey) {
        case "applied_date": av = a.applied_date ?? ""; bv = b.applied_date ?? ""; break;
        case "company":      av = a.company.toLowerCase(); bv = b.company.toLowerCase(); break;
        case "role":         av = a.role.toLowerCase();    bv = b.role.toLowerCase();    break;
        case "status":       av = a.status;                bv = b.status;                break;
      }
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return sortDir === "asc" ? cmp : -cmp;
    });
  }, [apps, search, stage, sortKey, sortDir]);

  function updateStatus(appUuid: string, status: string) {
    setApps((prev) => prev.map((a) => a.uuid === appUuid ? { ...a, status } : a));
  }
  function removeApp(appUuid: string) {
    setApps((prev) => prev.filter((a) => a.uuid !== appUuid));
  }
  function startEdit(app: Application) {
    setEditing({
      uuid:         app.uuid,
      company:      app.company,
      role:         app.role,
      applied_date: app.applied_date ?? "",
    });
  }
  function cancelEdit() { setEditing(null); }

  async function saveEdit() {
    if (!editing) return;
    setSaving(true);
    try {
      const patchRes = await fetch(`${API}/applications/${editing.uuid}`, {
        method:  "PATCH",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ company: editing.company, role: editing.role }),
      });

      await fetch(`${API}/applications/${editing.uuid}/dates/applied`, {
        method:  "PUT",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ date: editing.applied_date || null }),
      });

      if (patchRes.ok) {
        setApps((prev) =>
          prev.map((a) =>
            a.uuid === editing.uuid
              ? { ...a, company: editing.company, role: editing.role, applied_date: editing.applied_date || null }
              : a
          )
        );
        setEditing(null);
      }
    } finally {
      setSaving(false);
    }
  }

  function closeSheets() {
    setActionsFor(null);
    setConfirmDel(false);
    setSheetEdit(null);
    setSheetError(null);
  }

  async function sheetSave() {
    if (!sheetEdit) return;
    setSheetBusy(true);
    setSheetError(null);
    try {
      await api.patch(`/applications/${sheetEdit.uuid}`, {
        company: sheetEdit.company, role: sheetEdit.role,
      });
      await api.put(`/applications/${sheetEdit.uuid}/dates/applied`, {
        date: sheetEdit.applied_date || null,
      });
      setApps((prev) => prev.map((a) => a.uuid === sheetEdit.uuid
        ? { ...a, company: sheetEdit.company, role: sheetEdit.role, applied_date: sheetEdit.applied_date || null }
        : a));
      closeSheets();
    } catch (e) {
      setSheetError(e instanceof Error ? e.message : "Couldn't save.");
    } finally {
      setSheetBusy(false);
    }
  }

  async function sheetArchive(app: Application) {
    setSheetBusy(true);
    setSheetError(null);
    try {
      await api.patch(`/applications/${app.uuid}`, { status: "archived" });
      updateStatus(app.uuid, "archived");
      closeSheets();
    } catch (e) {
      setSheetError(e instanceof Error ? e.message : "Couldn't archive.");
    } finally {
      setSheetBusy(false);
    }
  }

  async function sheetDelete(app: Application) {
    setSheetBusy(true);
    setSheetError(null);
    try {
      await api.delete(`/applications/${app.uuid}?delete_files=false`);
      removeApp(app.uuid);
      closeSheets();
    } catch (e) {
      setSheetError(e instanceof Error ? e.message : "Couldn't remove it.");
    } finally {
      setSheetBusy(false);
    }
  }

  const thClass    = "text-left px-4 py-3 text-xs font-semibold text-text-muted uppercase tracking-wide select-none";
  const thSortable = `${thClass} cursor-pointer hover:text-text-secondary transition-colors`;

  return (
    <div className="space-y-3">
      <div className="relative">
        <span className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted text-sm">⌕</span>
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Filter by company, role or status…"
          className="w-full pl-8 pr-4 py-2 bg-bg-elevated border border-bg-border rounded-lg text-sm text-text-primary placeholder-text-muted focus:outline-none focus:ring-2 focus:ring-accent/40"
        />
        {search && (
          <button
            onClick={() => setSearch("")}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-secondary text-xs"
          >
            ✕
          </button>
        )}
      </div>

      <div className="flex items-center gap-2">
        <div className="flex-1 flex gap-1.5 overflow-x-auto -mx-1 px-1 pb-1">
          {[{ key: "all", label: "All" }, ...STAGES].map((s) => (
            <button
              key={s.key}
              onClick={() => setStage(s.key)}
              className={`shrink-0 min-h-9 px-3 rounded-full text-xs font-medium border transition-colors ${
                stage === s.key
                  ? "bg-accent text-white border-accent"
                  : "bg-bg-elevated text-text-secondary border-bg-border hover:text-text-primary"
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>
        {/* The table sorts by clicking headers; phones get a select instead */}
        <select
          aria-label="Sort"
          value={`${sortKey}:${sortDir}`}
          onChange={(e) => {
            const [k, d] = e.target.value.split(":");
            setSortKey(k as SortKey);
            setSortDir(d as SortDir);
          }}
          className="md:hidden min-h-9 px-2 text-xs bg-bg-elevated border border-bg-border rounded-lg text-text-secondary"
        >
          <option value="applied_date:desc">Newest</option>
          <option value="applied_date:asc">Oldest</option>
          <option value="company:asc">Company A–Z</option>
          <option value="status:asc">Status</option>
        </select>
      </div>

      {/* Phones: one card per application */}
      <ul className="md:hidden space-y-2">
        {visible.length === 0 && (
          <li className="py-8 text-center text-sm text-text-muted">
            {search ? `No applications matching "${search}"` : "No applications"}
          </li>
        )}
        {visible.map((app) => (
          <li
            key={app.uuid}
            onClick={() => router.push(`/applications/${app.uuid}`)}
            className={`bg-bg-elevated border border-bg-border rounded-xl px-4 py-3 active:bg-bg-surface ${
              app.status === "archived" ? "opacity-50" : ""
            }`}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="font-medium text-text-primary truncate">{decodeEntities(app.company)}</p>
                <p className="text-sm text-text-secondary line-clamp-2">{decodeEntities(app.role)}</p>
              </div>
              <button
                aria-label="Actions"
                onClick={(e) => { e.stopPropagation(); setActionsFor(app); }}
                className="shrink-0 -mr-2 -mt-1 min-h-11 min-w-11 flex items-center justify-center text-text-muted"
              >
                ···
              </button>
            </div>
            <div className="mt-1 flex items-center gap-3 text-xs text-text-muted">
              <StatusPicker uuid={app.uuid} status={app.status} onUpdate={(s) => updateStatus(app.uuid, s)} />
              <span>{app.applied_date ?? "Not applied"}</span>
              {app.has_pdf && <span className="text-accent">PDF ✓</span>}
            </div>
          </li>
        ))}
      </ul>

      <Sheet
        open={!!actionsFor}
        onClose={closeSheets}
        title={actionsFor ? decodeEntities(actionsFor.company) : ""}
      >
        {actionsFor && !confirmDelete && (
          <div className="space-y-2">
            <button
              onClick={() => {
                setSheetEdit({
                  uuid: actionsFor.uuid, company: decodeEntities(actionsFor.company),
                  role: decodeEntities(actionsFor.role), applied_date: actionsFor.applied_date ?? "",
                });
                setActionsFor(null);
              }}
              className="w-full min-h-11 px-3 text-left text-sm rounded-lg border border-bg-border bg-bg-surface"
            >
              Edit details
            </button>
            <button
              onClick={() => sheetArchive(actionsFor)}
              disabled={sheetBusy || actionsFor.status === "archived"}
              className="w-full min-h-11 px-3 text-left text-sm rounded-lg border border-bg-border bg-bg-surface disabled:opacity-40"
            >
              Archive
            </button>
            <button
              onClick={() => setConfirmDel(true)}
              className="w-full min-h-11 px-3 text-left text-sm rounded-lg border border-red-200 bg-red-50 text-red-600"
            >
              Delete…
            </button>
          </div>
        )}
        {actionsFor && confirmDelete && (
          <div className="space-y-3">
            <p className="text-sm text-text-secondary">
              Remove this application from the tracker? Its files stay on disk.
            </p>
            <div className="flex gap-2">
              <button
                onClick={() => sheetDelete(actionsFor)}
                disabled={sheetBusy}
                className="flex-1 min-h-11 rounded-lg bg-red-600 text-white text-sm disabled:opacity-50"
              >
                {sheetBusy ? "Removing…" : "Remove"}
              </button>
              <button
                onClick={() => setConfirmDel(false)}
                className="flex-1 min-h-11 rounded-lg border border-bg-border text-sm"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
        {sheetError && <p className="mt-3 text-xs text-red-600">{sheetError}</p>}
      </Sheet>

      <Sheet open={!!sheetEdit} onClose={closeSheets} title="Edit details">
        {sheetEdit && (
          <div className="space-y-3">
            {([
              ["Company", "company", "text"],
              ["Role", "role", "text"],
              ["Date applied", "applied_date", "date"],
            ] as const).map(([label, key, type]) => (
              <label key={key} className="block text-xs text-text-muted">
                {label}
                <input
                  type={type}
                  value={sheetEdit[key]}
                  onChange={(e) => setSheetEdit({ ...sheetEdit, [key]: e.target.value })}
                  className="mt-1 w-full min-h-11 px-3 text-base text-text-primary bg-white border border-bg-border rounded-lg"
                />
              </label>
            ))}
            {sheetError && <p className="text-xs text-red-600">{sheetError}</p>}
            <button
              onClick={sheetSave}
              disabled={sheetBusy}
              className="w-full min-h-11 rounded-lg bg-accent text-white text-sm font-medium disabled:opacity-50"
            >
              {sheetBusy ? "Saving…" : "Save"}
            </button>
          </div>
        )}
      </Sheet>

      {/* Tablet and desktop: the table */}
      <div className="hidden md:block rounded-xl border border-bg-border overflow-visible">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="bg-bg-surface border-b border-bg-border">
              <th className={thSortable} onClick={() => toggleSort("applied_date")}>
                Date applied <SortIndicator col="applied_date" sortKey={sortKey} sortDir={sortDir} />
              </th>
              <th className={thSortable} onClick={() => toggleSort("company")}>
                Company <SortIndicator col="company" sortKey={sortKey} sortDir={sortDir} />
              </th>
              <th className={thSortable} onClick={() => toggleSort("role")}>
                Role <SortIndicator col="role" sortKey={sortKey} sortDir={sortDir} />
              </th>
              <th className={thSortable} onClick={() => toggleSort("status")}>
                Status <SortIndicator col="status" sortKey={sortKey} sortDir={sortDir} />
              </th>
              <th className={thClass}>PDF</th>
              <th className={thClass} />
            </tr>
          </thead>
          <tbody className="divide-y divide-bg-border">
            {visible.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-sm text-text-muted">
                  {search ? `No applications matching "${search}"` : "No applications"}
                </td>
              </tr>
            )}
            {visible.map((app, rowIdx) => {
              const isEditing = editing?.uuid === app.uuid;
              const isLast    = rowIdx === visible.length - 1;
              return (
                <tr
                  key={app.uuid}
                  onClick={() => !isEditing && router.push(`/applications/${app.uuid}`)}
                  className={`bg-bg-elevated transition-colors ${
                    isEditing ? "" : "hover:bg-bg-surface cursor-pointer"
                  } ${app.status === "archived" ? "opacity-50" : ""}`}
                >
                  <td className={`px-4 py-3 text-xs whitespace-nowrap ${isLast ? "rounded-bl-xl" : ""}`}>
                    {isEditing ? (
                      <input
                        type="date"
                        value={editing.applied_date}
                        onClick={(e) => e.stopPropagation()}
                        onChange={(e) => setEditing((p) => p ? { ...p, applied_date: e.target.value } : p)}
                        className="px-2 py-1 text-xs border border-accent/50 rounded-md bg-white focus:outline-none focus:ring-1 focus:ring-accent"
                      />
                    ) : app.applied_date ? (
                      <span className="text-text-muted font-mono">{app.applied_date}</span>
                    ) : (
                      <span className="text-text-muted opacity-40">—</span>
                    )}
                  </td>

                  <td className="px-4 py-2">
                    {isEditing ? (
                      <input
                        autoFocus
                        value={editing.company}
                        onClick={(e) => e.stopPropagation()}
                        onChange={(e) => setEditing((p) => p ? { ...p, company: e.target.value } : p)}
                        className="w-full px-2 py-1 text-sm border border-accent/50 rounded-md bg-white focus:outline-none focus:ring-1 focus:ring-accent"
                      />
                    ) : (
                      <span className="font-medium text-text-primary">{decodeEntities(app.company)}</span>
                    )}
                  </td>

                  <td className="px-4 py-2">
                    {isEditing ? (
                      <input
                        value={editing.role}
                        onClick={(e) => e.stopPropagation()}
                        onChange={(e) => setEditing((p) => p ? { ...p, role: e.target.value } : p)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter")  saveEdit();
                          if (e.key === "Escape") cancelEdit();
                        }}
                        className="w-full px-2 py-1 text-sm border border-accent/50 rounded-md bg-white focus:outline-none focus:ring-1 focus:ring-accent"
                      />
                    ) : (
                      <span className="text-text-secondary">{decodeEntities(app.role)}</span>
                    )}
                  </td>

                  <td className="px-4 py-3">
                    <StatusPicker uuid={app.uuid} status={app.status} onUpdate={(s) => updateStatus(app.uuid, s)} />
                  </td>

                  <td className="px-4 py-3">
                    {app.has_pdf
                      ? <span className="text-accent text-xs">✓</span>
                      : <span className="text-text-muted text-xs">—</span>}
                  </td>

                  <td className={`px-4 py-3 ${isLast ? "rounded-br-xl" : ""}`}>
                    <div className="flex items-center justify-end">
                      {isEditing ? (
                        <div className="flex gap-3">
                          <button
                            onClick={(e) => { e.stopPropagation(); saveEdit(); }}
                            disabled={saving}
                            className="text-xs text-accent hover:underline disabled:opacity-50"
                          >
                            {saving ? "Saving…" : "Save"}
                          </button>
                          <button
                            onClick={(e) => { e.stopPropagation(); cancelEdit(); }}
                            className="text-xs text-text-muted hover:text-text-secondary"
                          >
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <ActionsMenu
                          app={app}
                          onEdit={startEdit}
                          onArchive={(u) => updateStatus(u, "archived")}
                          onDelete={removeApp}
                        />
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {search && visible.length > 0 && (
        <p className="text-xs text-text-muted text-right">
          {visible.length} of {apps.length} applications
        </p>
      )}
    </div>
  );
}
