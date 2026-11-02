"use client";

import { useState } from "react";

import { api } from "@/lib/api";
import {
  NEXT_STEPS, STATUS_FLOW, STATUS_LABELS, STATUS_STYLES,
} from "@/lib/statuses";
import Sheet from "./Sheet";

export function StatusPill({ status, className = "" }: { status: string; className?: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium ${
        STATUS_STYLES[status] ?? STATUS_STYLES.generated
      } ${className}`}
    >
      {STATUS_LABELS[status] ?? status}
    </span>
  );
}

/**
 * The one place a status is changed. Shows the status as a pill; tapping it opens a
 * sheet with the likely next steps up front and the rest under "Other options".
 * Saves itself and reports failures instead of swallowing them.
 */
export default function StatusPicker({
  uuid,
  status,
  onUpdate,
}: {
  uuid: string;
  status: string;
  onUpdate: (next: string) => void;
}) {
  const [open, setOpen]       = useState(false);
  const [saving, setSaving]   = useState(false);
  const [error, setError]     = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  const allowed   = STATUS_FLOW[status] ?? [];
  const suggested = (NEXT_STEPS[status] ?? []).filter((s) => allowed.includes(s));
  const others    = allowed.filter((s) => !suggested.includes(s));

  function close() {
    setOpen(false);
    setShowAll(false);
    setError(null);
  }

  async function choose(next: string) {
    setSaving(true);
    setError(null);
    try {
      await api.patch(`/applications/${uuid}`, { status: next });
      onUpdate(next);
      close();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't update the status.");
    } finally {
      setSaving(false);
    }
  }

  if (allowed.length === 0) return <StatusPill status={status} />;

  return (
    <>
      <button
        onClick={(e) => { e.stopPropagation(); setOpen(true); }}
        className={`inline-flex items-center gap-1 px-2.5 py-1.5 sm:py-1 rounded-full text-xs font-medium hover:opacity-80 transition-opacity ${
          STATUS_STYLES[status] ?? STATUS_STYLES.generated
        }`}
      >
        {STATUS_LABELS[status] ?? status}
        <span className="opacity-50 text-[10px]">▾</span>
      </button>

      <Sheet open={open} onClose={close} title="Where is this application at?">
        <p className="text-xs text-text-muted mb-3">
          Now: <span className="font-medium text-text-secondary">{STATUS_LABELS[status] ?? status}</span>
        </p>

        <div className="space-y-2">
          {suggested.map((s) => (
            <OptionButton key={s} status={s} disabled={saving} onClick={() => choose(s)} />
          ))}
        </div>

        {others.length > 0 && (
          <div className="mt-3">
            <button
              onClick={() => setShowAll((v) => !v)}
              className="min-h-11 text-xs text-text-muted hover:text-text-primary"
            >
              {showAll ? "Hide other options" : "Other options"}
            </button>
            {showAll && (
              <div className="space-y-2 mt-1">
                {others.map((s) => (
                  <OptionButton key={s} status={s} disabled={saving} onClick={() => choose(s)} />
                ))}
              </div>
            )}
          </div>
        )}

        {error && <p className="mt-3 text-xs text-red-600">{error}</p>}
      </Sheet>
    </>
  );
}

function OptionButton({
  status,
  disabled,
  onClick,
}: {
  status: string;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="w-full min-h-11 flex items-center gap-3 px-3 rounded-lg border border-bg-border bg-bg-surface text-sm text-text-primary hover:bg-bg-base disabled:opacity-50 transition-colors"
    >
      <span
        className={`inline-block w-2.5 h-2.5 rounded-full border ${
          STATUS_STYLES[status]?.split(" ").find((c) => c.startsWith("border")) ?? ""
        }`}
      />
      {STATUS_LABELS[status] ?? status}
    </button>
  );
}
