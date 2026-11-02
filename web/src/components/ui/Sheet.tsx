"use client";

import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";

/**
 * A modal that is a bottom sheet on phones and a centred card from `sm` up.
 * Closes on backdrop tap and Escape. Unlike the old anchored dropdowns it does not
 * depend on scroll position or element coordinates, so it behaves on touch devices.
 */
export default function Sheet({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-[9999] flex items-end sm:items-center justify-center bg-black/40"
      onClick={(e) => { e.stopPropagation(); onClose(); }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
        className="w-full sm:max-w-sm max-h-[85vh] overflow-y-auto bg-bg-elevated rounded-t-2xl sm:rounded-2xl shadow-xl pb-[env(safe-area-inset-bottom)]"
      >
        <div className="flex items-center justify-between px-4 pt-4 pb-2">
          <h2 className="text-sm font-semibold text-text-primary">{title}</h2>
          <button
            onClick={onClose}
            aria-label="Close"
            className="min-h-11 min-w-11 -mr-2 flex items-center justify-center text-text-muted hover:text-text-primary"
          >
            ✕
          </button>
        </div>
        <div className="px-4 pb-4">{children}</div>
      </div>
    </div>,
    document.body
  );
}
