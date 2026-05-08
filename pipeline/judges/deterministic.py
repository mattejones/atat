"""
deterministic.py — Tier 1 deterministic judge for ATAT section review.

Runs a suite of rule-based checks against raw section text and returns
a list of DeterministicFlag objects, each with precise character span positions.

Checks performed:
  - Hotwords:        Known AI/corporate filler words and phrases.
  - Em dashes:       — (U+2014) and – (U+2013, en dash, also flagged).
  - Sentence length: Sentences or bullet points exceeding MAX_SENTENCE_WORDS words.
  - Readability:     Flesch Reading Ease evaluated per prose paragraph.
                     The worst-scoring paragraph is flagged if below FLESCH_MIN_SCORE.
                     Score is returned for evaluation metadata regardless of pass/fail.

Markdown awareness:
  - Sentence length operates line-by-line. Header lines are skipped. Bullet lines
    are treated as individual sentence units. Prose lines are split by terminators.
  - Flesch is computed on prose paragraphs only after stripping markdown formatting.
    A paragraph is prose only if: it is not all headers, not predominantly bullets
    (>=60% of lines), and contains at least one sentence terminator (.!?).
    Header-only, context/italic lines, and bullet-heavy paragraphs are all skipped.
  - Hotword and em-dash checks operate on the raw text directly — markdown tokens
    do not interfere with these checks.

Character positions are computed against the raw text exactly as stored —
no normalisation is applied. The UI must render against the same raw string.

All checks are pure functions with no side effects.
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


# ── Thresholds ────────────────────────────────────────────────────────────────

MAX_SENTENCE_WORDS    = 35     # sentences / bullets exceeding this are flagged
FLESCH_MIN_SCORE      = 40.0   # below this triggers a readability flag per paragraph
FLESCH_MIN_WORD_COUNT = 15     # paragraphs with fewer words skipped for Flesch


# ── Hotword corpus ────────────────────────────────────────────────────────────

HOTWORDS: list[str] = [
    # AI tells
    "delve", "spearhead", "leverage", "robust", "seamlessly",
    "foster", "navigate", "landscape", "testament", "unleash",
    "revolutionise", "revolutionize", "transformative", "impactful",
    "synergy", "synergies", "cutting-edge", "cutting edge",
    "best-in-class", "best in class", "world-class", "world class",
    "game-changer", "game changer", "game-changing", "game changing",
    "thought leader", "thought leadership",
    # Corporate filler
    "utilise", "utilize", "facilitate", "endeavour", "endeavor",
    "commence", "prior to", "in order to", "it is worth noting",
    "it should be noted", "notably", "importantly",
    # Passive/weak constructions caught lexically
    "responsible for", "duties included", "tasked with",
    "helped to", "assisted with",
]

_HOTWORD_PATTERNS: list[tuple[str, re.Pattern]] = [
    (hw, re.compile(rf"\b{re.escape(hw)}\b", re.IGNORECASE))
    for hw in HOTWORDS
]

_DASH_PATTERN     = re.compile(r"[—–]")
_SENTENCE_PATTERN = re.compile(r"[^.!?]+[.!?]?")


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class DeterministicFlag:
    type:      str
    start_pos: int
    end_pos:   int
    excerpt:   str
    message:   str


@dataclass
class DeterministicResult:
    passed:       bool
    flesch_score: float
    flags:        list[DeterministicFlag] = field(default_factory=list)


# ── Markdown helpers ──────────────────────────────────────────────────────────

def _is_header_line(line: str) -> bool:
    return bool(re.match(r"^\s*#{1,6}\s", line))


def _is_bullet_line(line: str) -> bool:
    return bool(re.match(r"^\s*[-*+]\s", line))


def _strip_markdown(text: str) -> str:
    """
    Strip markdown formatting for metric computation only.
    Not safe to use for position tracking — operates on a copy.
    """
    text = re.sub(r"(?m)^#{1,6}\s+", "", text)
    text = re.sub(r"\*{1,3}([^*\n]+)\*{1,3}", r"\1", text)
    text = re.sub(r"`[^`]+`", "", text)
    text = re.sub(r"(?m)^\s*[-*+]\s+", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    text = re.sub(r"(?m)^-{3,}\s*$", "", text)
    return text


# ── Paragraph splitter ────────────────────────────────────────────────────────

def _split_paragraphs(text: str) -> list[tuple[str, int, int]]:
    """
    Split text into paragraphs by blank lines, tracking original positions.
    Returns list of (paragraph_text, start_pos, end_pos).
    """
    result:  list[tuple[str, int, int]] = []
    pattern = re.compile(r"\n\s*\n")
    cursor  = 0

    for match in pattern.finditer(text):
        para = text[cursor:match.start()]
        if para.strip():
            result.append((para, cursor, match.start()))
        cursor = match.end()

    remainder = text[cursor:]
    if remainder.strip():
        result.append((remainder, cursor, len(text)))

    return result


def _is_prose_paragraph(para: str) -> bool:
    """
    Return True if a paragraph contains meaningful prose for Flesch analysis.

    Excluded if:
      - All lines are headers
      - >= 60% of lines are bullet points
      - No sentence terminator (.!?) exists in the stripped text
        (catches label:value lines, italic context lines, etc.)
    """
    lines = [l for l in para.splitlines() if l.strip()]
    if not lines:
        return False

    header_count = sum(1 for l in lines if _is_header_line(l))
    bullet_count = sum(1 for l in lines if _is_bullet_line(l))

    if header_count == len(lines):
        return False
    if (bullet_count / len(lines)) >= 0.6:
        return False

    # Must contain at least one sentence terminator to be considered prose
    stripped = _strip_markdown(para)
    if not re.search(r"[.!?]", stripped):
        return False

    return True


# ── Flesch Reading Ease ───────────────────────────────────────────────────────

def _count_syllables(word: str) -> int:
    word   = word.lower().strip(".,;:!?\"'()-")
    if not word:
        return 0
    vowels = "aeiouy"
    count  = 0
    prev_v = False
    for ch in word:
        is_v = ch in vowels
        if is_v and not prev_v:
            count += 1
        prev_v = is_v
    if word.endswith("e") and count > 1:
        count -= 1
    return max(1, count)


def _flesch_reading_ease(text: str) -> float:
    """
    Compute Flesch Reading Ease on plain prose text (markdown stripped).
    206.835 - 1.015*(words/sentences) - 84.6*(syllables/words)
    """
    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
    words     = [w for w in re.findall(r"\b\w+\b", text) if w]
    if not sentences or not words:
        return 100.0
    syllables     = sum(_count_syllables(w) for w in words)
    avg_words_per = len(words) / len(sentences)
    avg_syl_per   = syllables  / len(words)
    return round(206.835 - (1.015 * avg_words_per) - (84.6 * avg_syl_per), 1)


# ── Individual checks ─────────────────────────────────────────────────────────

def _check_hotwords(text: str) -> list[DeterministicFlag]:
    """Flag all hotword occurrences with their character spans."""
    flags: list[DeterministicFlag] = []
    for hotword, pattern in _HOTWORD_PATTERNS:
        for match in pattern.finditer(text):
            flags.append(DeterministicFlag(
                type="hotword",
                start_pos=match.start(),
                end_pos=match.end(),
                excerpt=match.group(),
                message=f"Hotword detected: '{match.group()}' — remove or rephrase.",
            ))
    return flags


def _check_em_dashes(text: str) -> list[DeterministicFlag]:
    """Flag em dashes (—) and en dashes (–)."""
    flags: list[DeterministicFlag] = []
    for match in _DASH_PATTERN.finditer(text):
        char = match.group()
        kind = "em dash" if char == "—" else "en dash"
        flags.append(DeterministicFlag(
            type="em_dash",
            start_pos=match.start(),
            end_pos=match.end(),
            excerpt=char,
            message=(
                f"{kind.capitalize()} detected ('{char}'). "
                "Use a hyphen or rewrite the sentence."
            ),
        ))
    return flags


def _check_sentence_length(text: str) -> list[DeterministicFlag]:
    """
    Flag sentences or bullet points exceeding MAX_SENTENCE_WORDS words.

    Line-by-line strategy:
      - Header lines (# ...) are skipped entirely.
      - Bullet lines (- / * ...) are treated as individual sentence units.
        The full bullet content (minus the marker) is checked as one unit.
      - Prose lines are stripped of markdown then split by sentence terminators.

    Character positions point to the full original line, giving the UI
    an unambiguous highlight target even after markdown stripping.
    """
    flags:  list[DeterministicFlag] = []
    cursor: int = 0

    for line in text.split("\n"):
        line_start = cursor
        line_end   = cursor + len(line)
        cursor     = line_end + 1

        stripped = line.strip()
        if not stripped:
            continue

        if _is_header_line(stripped):
            continue

        if _is_bullet_line(stripped):
            content    = re.sub(r"^\s*[-*+]\s+", "", stripped)
            content    = _strip_markdown(content)
            word_count = len(content.split())
            if word_count > MAX_SENTENCE_WORDS:
                flags.append(DeterministicFlag(
                    type="sentence_length",
                    start_pos=line_start,
                    end_pos=line_end,
                    excerpt=stripped[:120] + ("..." if len(stripped) > 120 else ""),
                    message=(
                        f"Bullet point is {word_count} words "
                        f"(limit: {MAX_SENTENCE_WORDS}). Consider splitting."
                    ),
                ))
        else:
            content = _strip_markdown(stripped)
            for match in _SENTENCE_PATTERN.finditer(content):
                sentence   = match.group().strip()
                word_count = len(sentence.split())
                if word_count > MAX_SENTENCE_WORDS:
                    flags.append(DeterministicFlag(
                        type="sentence_length",
                        start_pos=line_start,
                        end_pos=line_end,
                        excerpt=stripped[:120] + ("..." if len(stripped) > 120 else ""),
                        message=(
                            f"Sentence is {word_count} words "
                            f"(limit: {MAX_SENTENCE_WORDS}). Consider splitting."
                        ),
                    ))
                    break  # one flag per line is sufficient

    return flags


def _check_readability(text: str) -> tuple[float, Optional[DeterministicFlag]]:
    """
    Compute Flesch Reading Ease per prose paragraph.

    Only paragraphs passing _is_prose_paragraph() with >= FLESCH_MIN_WORD_COUNT
    words are evaluated. The worst-scoring paragraph is flagged if below threshold.

    Returns (worst_score, flag_or_None).
    Score of 100.0 returned when no evaluable paragraphs exist (treated as pass).
    """
    paragraphs   = _split_paragraphs(text)
    worst_score: float                   = 100.0
    worst_flag:  Optional[DeterministicFlag] = None

    for para, start, end in paragraphs:
        if not _is_prose_paragraph(para):
            continue

        prose = _strip_markdown(para)
        words = re.findall(r"\b\w+\b", prose)
        if len(words) < FLESCH_MIN_WORD_COUNT:
            continue

        score = _flesch_reading_ease(prose)
        if score < worst_score:
            worst_score = score
            if score < FLESCH_MIN_SCORE:
                excerpt    = para.strip()
                worst_flag = DeterministicFlag(
                    type="readability",
                    start_pos=start,
                    end_pos=end,
                    excerpt=excerpt[:120] + ("..." if len(excerpt) > 120 else ""),
                    message=(
                        f"Flesch Reading Ease is {score:.1f} for this paragraph "
                        f"(minimum: {FLESCH_MIN_SCORE}). Text may be overly complex."
                    ),
                )

    return worst_score, worst_flag


# ── Public entry point ────────────────────────────────────────────────────────

def run(text: str) -> DeterministicResult:
    """
    Run all deterministic checks against raw section text.

    Args:
        text: Raw section content as stored in the section file.
              Must not be transformed before passing — character positions
              in returned flags are relative to this exact string.

    Returns:
        DeterministicResult with a flag list and Flesch score.
        passed=True only when zero flags are raised.
    """
    if not text or not text.strip():
        log.warning("Deterministic judge received empty text — skipping.")
        return DeterministicResult(passed=True, flesch_score=0.0)

    flags: list[DeterministicFlag] = []

    flags.extend(_check_hotwords(text))
    flags.extend(_check_em_dashes(text))
    flags.extend(_check_sentence_length(text))

    flesch_score, readability_flag = _check_readability(text)
    if readability_flag:
        flags.append(readability_flag)

    log.debug(
        f"Deterministic judge: {len(flags)} flag(s), "
        f"Flesch={flesch_score:.1f}, passed={len(flags) == 0}"
    )

    return DeterministicResult(
        passed=len(flags) == 0,
        flesch_score=flesch_score,
        flags=flags,
    )
