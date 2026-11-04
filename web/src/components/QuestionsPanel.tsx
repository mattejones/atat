"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { runJob } from "@/lib/jobs";

// The questions an application form asks ("Why do you want this role?"). The person pastes each
// question, writes their own answer, and can ask for it to be improved. Their own wording is
// always kept: after an improvement "Keep mine" puts it back.

type Length = "short" | "paragraph";
type Tone = "professional" | "direct" | "conversational" | "technical";

interface Answer {
  id:           string;
  ai_answer:    string;
  user_answer:  string | null;
  source_draft: string | null;
}

interface Question {
  id:               string;
  question_text:    string;
  response_length:  Length;
  needs_research:   number;
  answer:           Answer | null;
  effective_answer: string | null;
}

const TONES: { value: Tone; label: string }[] = [
  { value: "professional",   label: "Professional" },
  { value: "direct",         label: "Direct" },
  { value: "conversational", label: "Warm" },
  { value: "technical",      label: "Technical" },
];

const LENGTHS: { value: Length; label: string; hint: string; max: number }[] = [
  { value: "short",     label: "Short",     hint: "1–3 sentences", max: 80 },
  { value: "paragraph", label: "Paragraph", hint: "4–6 sentences", max: 160 },
];

const wordCount = (s: string) => (s.trim() ? s.trim().split(/\s+/).length : 0);

const primaryBtn =
  "min-h-11 sm:min-h-0 px-4 py-2 text-sm sm:text-xs font-medium bg-accent text-white rounded-lg hover:bg-accent-dim transition-colors disabled:opacity-50";
const quietBtn =
  "min-h-11 sm:min-h-0 px-3 py-2 text-sm sm:text-xs font-medium text-text-secondary border border-bg-border rounded-lg hover:text-text-primary hover:bg-bg-elevated transition-colors disabled:opacity-50";

// ── One question ─────────────────────────────────────────────────────────────

function QuestionCard({
  appId, q, onChanged, onDeleted,
}: {
  appId: string; q: Question; onChanged: () => Promise<void>; onDeleted: () => void;
}) {
  const saved = q.effective_answer ?? "";
  const [text, setText] = useState(saved);
  const [question, setQuestion] = useState(q.question_text);
  const [steer, setSteer] = useState("");
  const [showSteer, setShowSteer] = useState(false);
  const [busy, setBusy] = useState<null | "improving" | "writing">(null);
  const [state, setState] = useState<"" | "saving" | "saved">("");
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [showOriginal, setShowOriginal] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastSaved = useRef(saved);

  // Take the server's text when it changes underneath us (an improvement landing, a restore).
  useEffect(() => {
    setText(q.effective_answer ?? "");
    lastSaved.current = q.effective_answer ?? "";
  }, [q.effective_answer, q.answer?.id]);

  const length = LENGTHS.find((l) => l.value === q.response_length) ?? LENGTHS[0];
  const words = wordCount(text);
  const original = q.answer?.source_draft ?? null;
  const improved = !!original && original.trim() !== text.trim();

  const save = useCallback(async (value: string) => {
    if (value === lastSaved.current) return;
    setState("saving");
    try {
      await api.put(`/questions/${appId}/${q.id}/draft`, { text: value });
      lastSaved.current = value;
      setState("saved");
    } catch {
      setState("");
      setError("Couldn't save your answer. Check your connection and try again.");
    }
  }, [appId, q.id]);

  function onType(value: string) {
    setText(value);
    setError(null);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => save(value), 900);
  }

  async function flush() {
    if (timer.current) clearTimeout(timer.current);
    await save(text);
  }

  async function run(kind: "improving" | "writing") {
    setError(null);
    setBusy(kind);
    try {
      await flush();
      if (kind === "improving") {
        await runJob(`/questions/${appId}/${q.id}/improve`, { instruction: steer.trim() || null });
      } else {
        await runJob(`/questions/${appId}/${q.id}/regenerate`);
      }
      setSteer("");
      await onChanged();
    } catch (e: any) {
      setError(e?.message || "That didn't work. Please try again.");
    } finally {
      setBusy(null);
    }
  }

  async function keepMine() {
    setError(null);
    try {
      await api.post(`/questions/${appId}/${q.id}/restore`);
      await onChanged();
    } catch (e: any) {
      setError(e?.message || "Couldn't put your version back.");
    }
  }

  async function saveQuestion(patch: Partial<{ question_text: string; response_length: Length; needs_research: number }>) {
    try {
      await api.patch(`/questions/${appId}/${q.id}`, patch);
      await onChanged();
    } catch (e: any) {
      setError(e?.message || "Couldn't save that change.");
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch { /* clipboard blocked: the text is on screen to select */ }
  }

  async function remove() {
    if (!window.confirm("Remove this question and its answer?")) return;
    try {
      await api.delete(`/questions/${appId}/${q.id}`);
      onDeleted();
    } catch (e: any) {
      setError(e?.message || "Couldn't remove the question.");
    }
  }

  return (
    <div className="bg-bg-surface border border-bg-border rounded-xl p-4 space-y-3">
      <div className="flex items-start gap-2">
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onBlur={() => question.trim() && question !== q.question_text && saveQuestion({ question_text: question.trim() })}
          rows={2}
          aria-label="Question"
          className="flex-1 text-sm font-medium text-text-primary bg-transparent resize-none leading-snug focus:outline-none focus:bg-bg-elevated rounded-md px-1 -mx-1"
        />
        <button onClick={remove} aria-label="Remove question"
          className="shrink-0 min-h-11 min-w-11 sm:min-h-0 sm:min-w-0 sm:p-1 text-text-muted hover:text-red-600 text-lg leading-none">×</button>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {LENGTHS.map((l) => (
          <button key={l.value} onClick={() => saveQuestion({ response_length: l.value })}
            className={`min-h-11 sm:min-h-0 px-3 py-1.5 text-xs rounded-full border transition-colors ${
              q.response_length === l.value
                ? "bg-accent text-white border-accent"
                : "text-text-secondary border-bg-border hover:bg-bg-elevated"
            }`}>
            {l.label} <span className="opacity-70">· {l.hint}</span>
          </button>
        ))}
      </div>

      <div>
        <textarea
          value={text}
          onChange={(e) => onType(e.target.value)}
          onBlur={flush}
          rows={q.response_length === "paragraph" ? 7 : 4}
          disabled={busy !== null}
          placeholder="Write your answer here, in your own words. Rough is fine: you can ask for it to be improved."
          aria-label="Your answer"
          className="w-full px-3 py-2 text-sm bg-bg-elevated border border-bg-border rounded-lg resize-y leading-relaxed text-text-primary placeholder-text-muted focus:outline-none focus:ring-2 focus:ring-accent/40 disabled:opacity-60"
        />
        <div className="flex justify-between text-xs text-text-muted mt-1">
          <span className={words > length.max ? "text-amber-700" : ""}>
            {words} word{words === 1 ? "" : "s"}{words > length.max ? ` · a bit long for "${length.label.toLowerCase()}"` : ""}
          </span>
          <span>{state === "saving" ? "Saving…" : state === "saved" ? "Saved" : ""}</span>
        </div>
      </div>

      {improved && (
        <div className="rounded-lg border border-accent/30 bg-accent/5 px-3 py-2.5 space-y-2">
          <p className="text-xs text-text-secondary">
            Improved from your version. Your original is kept.
          </p>
          <div className="flex flex-wrap gap-2">
            <button onClick={keepMine} className={quietBtn}>Keep mine instead</button>
            <button onClick={() => setShowOriginal((v) => !v)} className={quietBtn}>
              {showOriginal ? "Hide my original" : "See my original"}
            </button>
          </div>
          {showOriginal && (
            <p className="text-sm text-text-secondary whitespace-pre-wrap border-t border-bg-border pt-2 leading-relaxed">{original}</p>
          )}
        </div>
      )}

      {showSteer && (
        <input
          value={steer}
          onChange={(e) => setSteer(e.target.value)}
          placeholder="Optional: e.g. make it shorter, or lead with the care work"
          aria-label="What to change"
          className="w-full px-3 py-2 text-sm bg-bg-elevated border border-bg-border rounded-lg text-text-primary placeholder-text-muted focus:outline-none focus:ring-2 focus:ring-accent/40"
        />
      )}

      {error && <p role="alert" className="text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</p>}

      <div className="flex flex-wrap items-center gap-2">
        <button onClick={() => run("improving")} disabled={busy !== null || !text.trim()} className={primaryBtn}>
          {busy === "improving" ? "Improving…" : "Improve my answer"}
        </button>
        <button onClick={() => setShowSteer((v) => !v)} className={quietBtn}>{showSteer ? "No steer" : "Add a steer"}</button>
        <button
          onClick={() => {
            if (text.trim() && !window.confirm("Write a fresh answer? Your current text will be replaced (your original stays in the history).")) return;
            run("writing");
          }}
          disabled={busy !== null}
          className={quietBtn}>
          {busy === "writing" ? "Writing…" : "Write one for me"}
        </button>
        {text.trim() && <button onClick={copy} className={quietBtn}>{copied ? "Copied" : "Copy"}</button>}
      </div>
    </div>
  );
}

// ── The panel ────────────────────────────────────────────────────────────────

export default function QuestionsPanel({ appId }: { appId: string }) {
  const [questions, setQuestions] = useState<Question[] | null>(null);
  const [tone, setTone] = useState<Tone>("professional");
  const [error, setError] = useState<string | null>(null);
  const [newText, setNewText] = useState("");
  const [newLength, setNewLength] = useState<Length>("short");
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    try {
      setQuestions(await api.get(`/questions/${appId}`));
    } catch {
      setError("Couldn't load the questions. Please refresh.");
    }
  }, [appId]);

  useEffect(() => {
    load();
    api.get(`/applications/${appId}`).then((a: any) => a?.qa_tone && setTone(a.qa_tone)).catch(() => {});
  }, [appId, load]);

  async function chooseTone(t: Tone) {
    const before = tone;
    setTone(t);
    try { await api.patch(`/applications/${appId}`, { qa_tone: t }); } catch { setTone(before); }
  }

  async function addQuestion() {
    const text = newText.trim();
    if (!text) return;
    setAdding(true);
    setError(null);
    try {
      await api.post(`/questions/${appId}`, { question_text: text, response_length: newLength, sort_order: questions?.length ?? 0 });
      setNewText("");
      await load();
    } catch (e: any) {
      setError(e?.message || "Couldn't add that question.");
    } finally {
      setAdding(false);
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-sm font-semibold text-text-primary">Application questions</h2>
        <p className="text-xs text-text-muted mt-0.5 leading-relaxed">
          Paste each question from the application form, then write your own answer. Ask for it to be
          improved and it keeps your facts and your voice; your original is always kept.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-text-muted">Style</span>
        {TONES.map((t) => (
          <button key={t.value} onClick={() => chooseTone(t.value)}
            className={`min-h-11 sm:min-h-0 px-3 py-1.5 text-xs rounded-full border transition-colors ${
              tone === t.value ? "bg-accent text-white border-accent" : "text-text-secondary border-bg-border hover:bg-bg-elevated"
            }`}>
            {t.label}
          </button>
        ))}
      </div>

      {error && <p role="alert" className="text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</p>}

      {questions === null && !error && <p className="text-sm text-text-muted">Loading…</p>}

      {questions?.map((q) => (
        <QuestionCard key={q.id} appId={appId} q={q} onChanged={load} onDeleted={load} />
      ))}

      {questions?.length === 0 && (
        <p className="text-sm text-text-muted bg-bg-surface border border-dashed border-bg-border rounded-xl px-4 py-6 text-center">
          No questions yet. Add the first one below.
        </p>
      )}

      <div className="bg-bg-surface border border-bg-border rounded-xl p-4 space-y-3">
        <label htmlFor="new-question" className="text-xs font-medium text-text-secondary">Add a question</label>
        <textarea
          id="new-question"
          value={newText}
          onChange={(e) => setNewText(e.target.value)}
          rows={2}
          placeholder="Paste the question exactly as the application asks it"
          className="w-full px-3 py-2 text-sm bg-bg-elevated border border-bg-border rounded-lg resize-y text-text-primary placeholder-text-muted focus:outline-none focus:ring-2 focus:ring-accent/40"
        />
        <div className="flex flex-wrap items-center gap-2">
          {LENGTHS.map((l) => (
            <button key={l.value} onClick={() => setNewLength(l.value)}
              className={`min-h-11 sm:min-h-0 px-3 py-1.5 text-xs rounded-full border transition-colors ${
                newLength === l.value ? "bg-accent text-white border-accent" : "text-text-secondary border-bg-border hover:bg-bg-elevated"
              }`}>
              {l.label} <span className="opacity-70">· {l.hint}</span>
            </button>
          ))}
          <button onClick={addQuestion} disabled={adding || !newText.trim()} className={`${primaryBtn} sm:ml-auto`}>
            {adding ? "Adding…" : "Add question"}
          </button>
        </div>
      </div>
    </div>
  );
}
