"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { runJob } from "@/lib/jobs";

const API = process.env.NEXT_PUBLIC_API_URL;

// ── Types ─────────────────────────────────────────────────────────────────────

type ResponseLength = "short" | "paragraph";
type QaTone = "professional" | "direct" | "conversational" | "technical";
type FeedbackRating = "positive" | "negative";

interface Answer {
  id:           string;
  ai_answer:    string;
  user_answer:  string | null;
  created_at:   string;
}

interface Question {
  id:               string;
  application_id:   string;
  question_text:    string;
  response_length:  ResponseLength;
  needs_research:   number;
  sort_order:       number;
  created_at:       string;
  answer:           Answer | null;
  effective_answer: string | null;
  isDraft?:         boolean;
}

// ── Constants ─────────────────────────────────────────────────────────────────

const TONE_OPTIONS: { value: QaTone; label: string }[] = [
  { value: "professional",  label: "Professional" },
  { value: "direct",        label: "Direct" },
  { value: "conversational", label: "Conversational" },
  { value: "technical",     label: "Technical" },
];

const LENGTH_OPTIONS: { value: ResponseLength; label: string; hint: string }[] = [
  { value: "short",     label: "Short",     hint: "1–3 sentences" },
  { value: "paragraph", label: "Paragraph", hint: "4–6 sentences" },
];

// ── Tone selector ─────────────────────────────────────────────────────────────

function ToneSelector({
  appId,
  tone,
  onUpdate,
}: {
  appId:    string;
  tone:     QaTone;
  onUpdate: (t: QaTone) => void;
}) {
  const [saving, setSaving] = useState(false);

  async function handleSelect(t: QaTone) {
    if (t === tone) return;
    setSaving(true);
    try {
      const res = await fetch(`${API}/applications/${appId}`, {
        method:  "PATCH",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ qa_tone: t }),
      });
      if (res.ok) onUpdate(t);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex items-center gap-3">
      <span className="text-xs text-text-muted whitespace-nowrap">Answer tone</span>
      <div className="flex gap-1.5 flex-wrap">
        {TONE_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            onClick={() => handleSelect(opt.value)}
            disabled={saving}
            className={`px-3 py-1 text-xs rounded-full border transition-colors ${
              tone === opt.value
                ? "bg-accent text-white border-accent"
                : "border-bg-border text-text-secondary hover:border-accent/50 hover:text-text-primary"
            } disabled:opacity-50`}
          >
            {opt.label}
          </button>
        ))}
      </div>
    </div>
  );
}

// ── Feedback control ──────────────────────────────────────────────────────────

function FeedbackControl({
  appId,
  qId,
  answerId,
}: {
  appId:    string;
  qId:      string;
  answerId: string;
}) {
  const [submitted,  setSubmitted]  = useState<FeedbackRating | null>(null);
  const [showInput,  setShowInput]  = useState(false);
  const [comment,    setComment]    = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function submit(rating: FeedbackRating) {
    setSubmitting(true);
    try {
      await fetch(`${API}/questions/${appId}/${qId}/feedback`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ rating, user_comment: comment || null }),
      });
      setSubmitted(rating);
      setShowInput(false);
      setComment("");
    } finally {
      setSubmitting(false);
    }
  }

  if (submitted) {
    return (
      <span className="text-[10px] text-text-muted italic">
        Feedback recorded — thanks
      </span>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <span className="text-[10px] text-text-muted">Helpful?</span>
      <button
        onClick={() => { setShowInput(true); }}
        disabled={submitting}
        title="Good answer"
        className="text-xs text-text-muted hover:text-green-600 transition-colors disabled:opacity-50"
      >
        👍
      </button>
      <button
        onClick={() => setShowInput(true)}
        disabled={submitting}
        title="Needs improvement"
        className="text-xs text-text-muted hover:text-red-500 transition-colors disabled:opacity-50"
      >
        👎
      </button>
      {showInput && (
        <div className="flex items-center gap-1.5 ml-1">
          <input
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="Optional comment…"
            className="px-2 py-1 text-xs bg-white border border-bg-border rounded-md w-40 focus:outline-none focus:ring-1 focus:ring-accent/40"
          />
          <button
            onClick={() => submit("positive")}
            disabled={submitting}
            className="px-2 py-0.5 text-xs text-green-700 border border-green-300 rounded hover:bg-green-50 disabled:opacity-50"
          >
            👍 Good
          </button>
          <button
            onClick={() => submit("negative")}
            disabled={submitting}
            className="px-2 py-0.5 text-xs text-red-700 border border-red-300 rounded hover:bg-red-50 disabled:opacity-50"
          >
            👎 Poor
          </button>
          <button
            onClick={() => setShowInput(false)}
            className="text-xs text-text-muted hover:text-text-secondary"
          >
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}

// ── Answer card ───────────────────────────────────────────────────────────────

function AnswerCard({
  appId,
  question,
  onRegenerate,
}: {
  appId:        string;
  question:     Question;
  onRegenerate: (q: Question) => void;
}) {
  const answer          = question.answer;
  const [text, setText] = useState(question.effective_answer ?? "");
  const [saved, setSaved]     = useState(true);
  const [saving, setSaving]   = useState(false);
  const [copying, setCopying] = useState(false);
  const [regen, setRegen]     = useState(false);

  // Sync if effective_answer changes (e.g. after regeneration)
  useEffect(() => {
    setText(question.effective_answer ?? "");
    setSaved(true);
  }, [question.effective_answer]);

  async function saveAnswer(value: string) {
    if (!answer) return;
    setSaving(true);
    try {
      await fetch(`${API}/questions/${appId}/${question.id}/answer`, {
        method:  "PATCH",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ user_answer: value }),
      });
      setSaved(true);
    } finally {
      setSaving(false);
    }
  }

  async function copyToClipboard() {
    try {
      await navigator.clipboard.writeText(text);
      setCopying(true);
      setTimeout(() => setCopying(false), 1500);
    } catch {
      // Clipboard API not available
    }
  }

  async function handleRegenerate() {
    setRegen(true);
    try {
      // Queued as a background job; runJob follows it until the answer is written.
      const result = await runJob(`/questions/${appId}/${question.id}/regenerate`);
      const data   = result?.answers?.[0];
      if (data) {
        onRegenerate({ ...question, effective_answer: data.effective_answer, answer: {
          id: data.answer_id,
          ai_answer: data.ai_answer,
          user_answer: null,
          created_at: new Date().toISOString(),
        }});
      }
    } catch {
      // Failure leaves the existing answer in place, as before.
    } finally {
      setRegen(false);
    }
  }

  if (!answer) return null;

  return (
    <div className="mt-2 pl-3 border-l-2 border-accent/20">
      <textarea
        value={text}
        onChange={(e) => { setText(e.target.value); setSaved(false); }}
        onBlur={(e) => { if (!saved) saveAnswer(e.target.value); }}
        rows={text.split("\n").length + 2}
        className="w-full px-3 py-2 text-sm text-text-primary bg-bg-elevated border border-bg-border rounded-lg resize-y focus:outline-none focus:ring-2 focus:ring-accent/40 leading-relaxed"
      />
      <div className="flex items-center justify-between mt-1.5 flex-wrap gap-2">
        <FeedbackControl
          appId={appId}
          qId={question.id}
          answerId={answer.id}
        />
        <div className="flex items-center gap-2">
          {saving && <span className="text-[10px] text-text-muted">Saving…</span>}
          {!saving && !saved && <span className="text-[10px] text-text-muted">Unsaved</span>}
          <button
            onClick={copyToClipboard}
            className="text-xs text-text-muted hover:text-accent transition-colors flex items-center gap-1"
          >
            {copying ? "✓ Copied" : "Copy"}
          </button>
          <button
            onClick={handleRegenerate}
            disabled={regen}
            className="text-xs text-text-muted hover:text-text-secondary transition-colors disabled:opacity-50 flex items-center gap-1"
          >
            {regen ? (
              <><span className="inline-block w-3 h-3 border border-text-muted/40 border-t-text-muted rounded-full animate-spin" />Regenerating…</>
            ) : "Regenerate"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Question row ──────────────────────────────────────────────────────────────

function QuestionRow({
  appId,
  question,
  onUpdate,
  onDelete,
  onRegenerate,
  isDraft,
  onPersist,
}: {
  appId:        string;
  question:     Question;
  onUpdate:     (q: Question) => void;
  onDelete:     (id: string) => void;
  onRegenerate: (q: Question) => void;
  isDraft?:     boolean;
  onPersist?:   (tempId: string, persisted: Question) => void;
}) {
  const [text, setText]               = useState(question.question_text);
  const [textSaved, setTextSaved]     = useState(true);
  const [draftLength, setDraftLength] = useState<ResponseLength>(question.response_length);
  const [draftResearch, setDraftResearch] = useState<number>(question.needs_research);
  const saveTimer                     = useRef<ReturnType<typeof setTimeout> | null>(null);

  const effectiveLength   = isDraft ? draftLength   : question.response_length;
  const effectiveResearch = isDraft ? draftResearch : question.needs_research;

  async function patchQuestion(patch: Partial<Question>) {
    if (isDraft) return;
    const res = await fetch(`${API}/questions/${appId}/${question.id}`, {
      method:  "PATCH",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(patch),
    });
    if (res.ok) {
      const updated = await res.json();
      onUpdate({ ...updated, answer: question.answer, effective_answer: question.effective_answer });
    }
  }

  function handleTextChange(val: string) {
    setText(val);
    setTextSaved(false);
    if (isDraft) return;
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(async () => {
      await patchQuestion({ question_text: val } as any);
      setTextSaved(true);
    }, 800);
  }

  async function handleTextBlur() {
    if (!isDraft || !text.trim()) return;
    const res = await fetch(`${API}/questions/${appId}`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({
        question_text:   text.trim(),
        response_length: draftLength,
        needs_research:  draftResearch,
        sort_order:      question.sort_order,
      }),
    });
    if (res.ok) {
      const persisted = await res.json();
      onPersist?.(question.id, { ...persisted, answer: null, effective_answer: null });
    }
  }

  async function handleDelete() {
    if (isDraft) {
      onDelete(question.id);
      return;
    }
    await fetch(`${API}/questions/${appId}/${question.id}`, { method: "DELETE" });
    onDelete(question.id);
  }

  function handleLengthChange(val: ResponseLength) {
    if (isDraft) {
      setDraftLength(val);
    } else {
      patchQuestion({ response_length: val } as any);
    }
  }

  function handleResearchChange(checked: boolean) {
    if (isDraft) {
      setDraftResearch(checked ? 1 : 0);
    } else {
      patchQuestion({ needs_research: checked ? 1 : 0 } as any);
    }
  }

  return (
    <div className="bg-bg-surface border border-bg-border rounded-xl px-4 py-3 space-y-3">
      {/* Question input row */}
      <div className="flex gap-2 items-start">
        <textarea
          value={text}
          onChange={(e) => handleTextChange(e.target.value)}
          onBlur={handleTextBlur}
          placeholder="Enter application question…"
          rows={2}
          // eslint-disable-next-line jsx-a11y/no-autofocus
          autoFocus={isDraft}
          className="flex-1 px-3 py-2 text-sm text-text-primary bg-bg-elevated border border-bg-border rounded-lg resize-y focus:outline-none focus:ring-2 focus:ring-accent/40 placeholder-text-muted leading-relaxed"
        />
        <button
          onClick={handleDelete}
          title="Remove question"
          className="mt-1 text-text-muted hover:text-red-500 transition-colors text-sm flex-shrink-0"
        >
          ✕
        </button>
      </div>

      {/* Controls row */}
      <div className="flex items-center gap-4 flex-wrap">
        {/* Response length */}
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-text-muted">Length</span>
          {LENGTH_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => handleLengthChange(opt.value)}
              title={opt.hint}
              className={`px-2.5 py-0.5 text-xs rounded-full border transition-colors ${
                effectiveLength === opt.value
                  ? "bg-accent text-white border-accent"
                  : "border-bg-border text-text-secondary hover:border-accent/50"
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>

        {/* Research toggle */}
        <label className="flex items-center gap-1.5 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={!!effectiveResearch}
            onChange={(e) => handleResearchChange(e.target.checked)}
            className="w-3.5 h-3.5 rounded border-bg-border accent-accent"
          />
          <span className="text-xs text-text-secondary">Needs research</span>
        </label>

        {!isDraft && !textSaved && (
          <span className="text-[10px] text-text-muted ml-auto">Saving…</span>
        )}
        {isDraft && (
          <span className="text-[10px] text-text-muted ml-auto italic">Unsaved — blur to save</span>
        )}
      </div>

      {/* Answer */}
      {question.effective_answer !== null && (
        <AnswerCard
          appId={appId}
          question={question}
          onRegenerate={onRegenerate}
        />
      )}
    </div>
  );
}

// ── Main panel ────────────────────────────────────────────────────────────────

export default function QuestionsPanel({
  appId,
  initialTone,
  onToneChange,
}: {
  appId:        string;
  initialTone:  QaTone;
  onToneChange: (t: QaTone) => void;
}) {
  const [questions,  setQuestions]  = useState<Question[]>([]);
  const [loading,    setLoading]    = useState(true);
  const [generating, setGenerating] = useState(false);
  const [tone,       setTone]       = useState<QaTone>(initialTone);
  const [error,      setError]      = useState<string | null>(null);

  useEffect(() => { loadQuestions(); }, [appId]);
  useEffect(() => { setTone(initialTone); }, [initialTone]);

  async function loadQuestions() {
    setLoading(true);
    try {
      const res = await fetch(`${API}/questions/${appId}`);
      if (res.ok) setQuestions(await res.json());
    } finally {
      setLoading(false);
    }
  }

  function addQuestion() {
    const tempId    = `draft-${crypto.randomUUID()}`;
    const nextOrder = questions.length;
    const draft: Question = {
      id:               tempId,
      application_id:   appId,
      question_text:    "",
      response_length:  "short",
      needs_research:   0,
      sort_order:       nextOrder,
      created_at:       new Date().toISOString(),
      answer:           null,
      effective_answer: null,
      isDraft:          true,
    };
    setQuestions((prev) => [...prev, draft]);
  }

  async function generateAnswers() {
    setGenerating(true);
    setError(null);
    try {
      // Queued as a background job; runJob follows it until the answers are written.
      await runJob(`/questions/${appId}/generate`, { force: false });
      // Reload questions to pick up the new answers
      await loadQuestions();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setGenerating(false);
    }
  }

  function handleToneChange(t: QaTone) {
    setTone(t);
    onToneChange(t);
  }

  function handleQuestionUpdate(updated: Question) {
    setQuestions((prev) =>
      prev.map((q) => (q.id === updated.id ? updated : q))
    );
  }

  function handlePersist(tempId: string, persisted: Question) {
    setQuestions((prev) =>
      prev.map((q) => (q.id === tempId ? persisted : q))
    );
  }

  function handleQuestionDelete(id: string) {
    setQuestions((prev) => prev.filter((q) => q.id !== id));
  }

  function handleRegenerate(updated: Question) {
    setQuestions((prev) =>
      prev.map((q) => (q.id === updated.id ? updated : q))
    );
  }

  const unansweredCount = questions.filter((q) => !q.effective_answer).length;
  const hasQuestions    = questions.length > 0;

  return (
    <div className="space-y-4">
      {/* Tone selector */}
      <div className="bg-bg-surface border border-bg-border rounded-xl px-5 py-3">
        <ToneSelector appId={appId} tone={tone} onUpdate={handleToneChange} />
      </div>

      {/* Error */}
      {error && (
        <div className="px-4 py-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Question list */}
      {loading ? (
        <p className="text-sm text-text-muted px-1">Loading questions…</p>
      ) : (
        <div className="space-y-3">
          {questions.map((q) => (
            <QuestionRow
              key={q.id}
              appId={appId}
              question={q}
              onUpdate={handleQuestionUpdate}
              onDelete={handleQuestionDelete}
              onRegenerate={handleRegenerate}
              isDraft={q.isDraft}
              onPersist={handlePersist}
            />
          ))}
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center gap-3 flex-wrap">
        <button
          onClick={addQuestion}
          className="px-3 py-1.5 text-xs font-medium border border-bg-border text-text-secondary rounded-lg hover:text-text-primary hover:border-accent/50 transition-colors"
        >
          + Add question
        </button>

        {hasQuestions && unansweredCount > 0 && (
          <button
            onClick={generateAnswers}
            disabled={generating}
            className="px-3 py-1.5 text-xs font-medium bg-accent text-white rounded-lg hover:bg-accent-dim transition-colors disabled:opacity-50 flex items-center gap-1.5"
          >
            {generating ? (
              <>
                <span className="w-3 h-3 border border-white/30 border-t-white rounded-full animate-spin" />
                Generating…
              </>
            ) : (
              `Generate ${unansweredCount === questions.length ? "answers" : `${unansweredCount} remaining`}`
            )}
          </button>
        )}

        {hasQuestions && unansweredCount === 0 && (
          <button
            onClick={generateAnswers}
            disabled={generating}
            className="px-3 py-1.5 text-xs font-medium border border-bg-border text-text-secondary rounded-lg hover:border-accent/50 transition-colors disabled:opacity-50"
          >
            {generating ? "Regenerating…" : "Regenerate all"}
          </button>
        )}
      </div>
    </div>
  );
}
