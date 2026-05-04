"""
deterministic.py — Tier 1 deterministic judge for ATAT section review.

Runs a suite of rule-based checks against raw section text and returns
a list of DeterministicFlag objects, each with precise character span positions.

Checks performed:
  - Hotwords:        Known AI/corporate filler words and phrases.
  - Em dashes:       — (U+2014) and – (U+2013, en dash, also flagged).
  - Sentence length: Any sentence exceeding MAX_SENTENCE_WORDS words.
  - Readability:     Flesch Reading Ease below FLESCH_MIN_SCORE raises a
                     document-level flag. Score is also returned for the
                     evaluation metadata regardless of pass/fail.

Flesch Reading Ease is implemented directly with no external dependencies.
Formula: 206.835 - 1.015*(words/sentences) - 84.6*(syllables/words)
Syllable counting uses a heuristic adequate for CV prose.

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

MAX_SENTENCE_WORDS = 35       # sentences exceeding this are flagged
FLESCH_MIN_SCORE   = 40.0     # below this triggers a readability flag
                              # Flesch scale: 0–30 very difficult, 30–50 difficult,
                              # 50–60 fairly difficult, 60–70 standard


# ── Hotword corpus ────────────────────────────────────────────────────────────
# Curated list of AI-generated and corporate-filler terms.
# Matched as whole words (case-insensitive).

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

# Build whole-word regex patterns once at import time
_HOTWORD_PATTERNS: list[tuple[str, re.Pattern]] = [
    (hw, re.compile(rf"\b{re.escape(hw)}\b", re.IGNORECASE))
    for hw in HOTWORDS
]

# Em dash and en dash
_DASH_PATTERN = re.compile(r"[—–]")

# Sentence splitter — splits on . ! ? followed by whitespace or end of string.
# Deliberately simple: adequate for CV prose.
_SENTENCE_PATTERN = re.compile(r"[^.!?]+[.!?]?")


# ── Flag dataclass ────────────────────────────────────────────────────────────

@dataclass
class DeterministicFlag:
    type:      str              # hotword | em_dash | sentence_length | readability
    start_pos: int
    end_pos:   int
    excerpt:   str
    message:   str


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class DeterministicResult:
    passed:       bool
    flesch_score: float
    flags:        list[DeterministicFlag] = field(default_factory=list)


# ── Flesch Reading Ease ───────────────────────────────────────────────────────

def _count_syllables(word: str) -> int:
    """
    Heuristic syllable counter. Accurate enough for CV prose.
    Counts vowel groups, subtracts silent trailing 'e', floors at 1.
    """
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
    Compute Flesch Reading Ease score.
    206.835 - 1.015*(words/sentences) - 84.6*(syllables/words)
    Higher is easier. Below 40 is considered difficult.
    """
    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
    words     = [w for w in re.findall(r"\b\w+\b", text) if w]
    if not sentences or not words:
        return 0.0
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
    """Flag sentences exceeding MAX_SENTENCE_WORDS words."""
    flags: list[DeterministicFlag] = []
    for match in _SENTENCE_PATTERN.finditer(text):
        sentence   = match.group().strip()
        word_count = len(sentence.split())
        if word_count > MAX_SENTENCE_WORDS:
            flags.append(DeterministicFlag(
                type="sentence_length",
                start_pos=match.start(),
                end_pos=match.end(),
                excerpt=sentence[:120] + ("..." if len(sentence) > 120 else ""),
                message=(
                    f"Sentence is {word_count} words "
                    f"(limit: {MAX_SENTENCE_WORDS}). Consider splitting."
                ),
            ))
    return flags


def _check_readability(text: str) -> tuple[float, Optional[DeterministicFlag]]:
    """
    Compute Flesch Reading Ease score.
    Returns (score, flag_or_None).
    Flag spans the full text if score < FLESCH_MIN_SCORE.
    Score is returned regardless of pass/fail for evaluation metadata.
    """
    score = _flesch_reading_ease(text)
    flag  = None
    if score < FLESCH_MIN_SCORE:
        flag = DeterministicFlag(
            type="readability",
            start_pos=0,
            end_pos=len(text),
            excerpt=text[:120] + ("..." if len(text) > 120 else ""),
            message=(
                f"Flesch Reading Ease score is {score:.1f} "
                f"(minimum: {FLESCH_MIN_SCORE}). Text may be overly complex."
            ),
        )
    return score, flag


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
