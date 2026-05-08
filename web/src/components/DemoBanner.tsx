"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL;

export default function DemoBanner() {
  const [demoMode, setDemoMode] = useState(false);

  useEffect(() => {
    fetch(`${API}/settings`)
      .then((r) => r.json())
      .then((data) => setDemoMode(data.demo_mode === true))
      .catch(() => {/* silently ignore — banner is non-critical */});
  }, []);

  if (!demoMode) return null;

  return (
    <div className="bg-amber-50 border-b border-amber-200 px-6 py-2 flex items-center justify-center gap-2">
      <span className="text-xs font-medium text-amber-700 tracking-wide uppercase">
        Demo Mode
      </span>
      <span className="text-amber-400">·</span>
      <span className="text-xs text-amber-600">
        Using fictional data. CV library and output paths are fixed to{" "}
        <code className="font-mono bg-amber-100 px-1 py-0.5 rounded">demo/</code>.
        Add a real API key in Settings to generate CVs.
      </span>
    </div>
  );
}
