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
                     Acronyms, proper nouns and skills lines are not scored as prose.

Checks that need a JudgeContext (pipeline/judges/context.py); skipped without one:
  - Profile shape:   paragraph count and word count against PROFILE_PARAGRAPHS / PROFILE_WORDS_*.
  - Ad echo:         a phrase of 4+ words copied from the job ad that is nowhere in the library,
                     and words that come only from the ad yet recur through the CV.
  - Repetition:      a non-stopword used three times within one role's bullets.
  - Fragments:       experience bullets too short to be a sentence.

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

from pipeline.judges.context import STOPWORDS, JudgeContext, content_tokens, stem, tokens

log = logging.getLogger(__name__)


# ── Thresholds ────────────────────────────────────────────────────────────────

MAX_SENTENCE_WORDS    = 35     # sentences / bullets exceeding this are flagged
FLESCH_MIN_SCORE      = 40.0   # below this triggers a readability flag per paragraph
FLESCH_MIN_WORD_COUNT = 15     # paragraphs with fewer words skipped for Flesch
MIN_BULLET_WORDS      = 8      # an experience bullet shorter than this reads as a fragment
REPEAT_LIMIT          = 3      # a word used this often within one role's bullets is flagged
AD_ECHO_NGRAM         = 4      # words in a phrase copied from the job ad
CONTEXT_ECHO_JACCARD  = 0.6    # a bullet this similar (shared content words) to its role's context line restates it
CONTEXT_PHRASE_WORDS  = 6      # a run of this many words repeated in two roles' context lines is flagged
AD_TERM_LIMIT         = 4      # an ad word used this often across the CV is flagged, even if the library has it


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
_SKILLS_LINE      = re.compile(r"^\*\*[^*\n]+:\*\*")      # "**Category:** a, b, c" is a list, not prose
_ABBREVIATION     = re.compile(r"\b(?:e\.g|i\.e|etc|vs|approx)\.", re.IGNORECASE)
_SENTENCE_PATTERN = re.compile(r"[^.!?]+[.!?]?")


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class DeterministicFlag:
    type:      str
    start_pos: int
    end_pos:   int
    excerpt:   str
    message:   str
    severity:  str = "error"      # "advisory" flags are shown but do not fail the section


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


def _is_skills_line(line: str) -> bool:
    return bool(_SKILLS_LINE.match(line.strip()))


def _drop_skills_lines(para: str) -> str:
    return "\n".join(l for l in para.splitlines() if not _is_skills_line(l))


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
    lines = [l for l in para.splitlines() if l.strip() and not _is_skills_line(l)]
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


def _protect_dots(prose: str) -> str:
    """Stop full stops inside names, numbers and abbreviations being counted as sentence ends."""
    text = _ABBREVIATION.sub(lambda m: m.group().replace(".", ""), prose)
    return re.sub(r"(?<=\w)\.(?=\w)", "", text)                         # Node.js, 3.5, U.S -> one token


def _without_names(prose: str) -> str:
    """
    The same prose with acronyms and mid-sentence proper nouns taken out. A CV is full of names
    (Registered Manager, Good Care Group, Oxford Aunts) that are long on a syllable count but
    easy for a person. Words that are dropped leave no trace in the word or syllable count.
    """
    text = re.sub(r"\([A-Z][A-Za-z]*[A-Z][A-Za-z]*\)", " ", prose)       # (CQC) introductions

    kept_sentences = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        out: list = []
        pending_terminator = ""
        for i, word in enumerate(sentence.split()):
            core = word.strip(".,;:!?\"'()")
            is_acronym = len(core) >= 2 and core.isupper()
            is_proper  = i > 0 and core[:1].isupper() and not core.isupper()
            if is_acronym or is_proper:
                m = re.search(r"[.!?]+$", word)
                pending_terminator = m.group() if m else ""
                continue
            out.append(word)
            pending_terminator = ""
        if out:
            if pending_terminator and not re.search(r"[.!?]$", out[-1]):
                out[-1] += pending_terminator          # keep the sentence end if its last word was dropped
            kept_sentences.append(" ".join(out))
    return " ".join(kept_sentences)


def _without_terms(prose: str, terms) -> str:
    """The prose with the person's own long vocabulary taken out (as _without_names does for names)."""
    if not terms:
        return prose
    kept = [w for w in prose.split() if w.strip(".,;:!?\"'()").lower() not in terms]
    return " ".join(kept)


def _readability_score(prose: str, known=frozenset()) -> float:
    """
    Flesch Reading Ease, giving the benefit of the doubt to names and acronyms.

    Scores the prose as written and again with names and acronyms removed, and keeps the
    better of the two. Names can drag a score down (long proper nouns) or prop it up (cheap
    acronyms), so a paragraph is flagged only if it is hard to read under BOTH readings.
    """
    protected = _protect_dots(prose)
    scores = [_flesch_reading_ease(protected), _flesch_reading_ease(_without_names(protected))]
    if known:
        scores.append(_flesch_reading_ease(_without_terms(protected, known)))
    return max(scores)


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

_BAN_CUE = re.compile(
    r"\b(never use|do not use|don't use|must not use|should not use|avoid|banned?|forbidden|"
    r"prohibited|not to use)\b", re.IGNORECASE)
_QUOTED = re.compile(r"[\"“`]([^\"”`\n]{2,40})[\"”`]|\*\*([^*\n]{2,40})\*\*")
# After a banned term, wording that introduces what to use instead: later quotes are the WANTED words.
_SWITCH = re.compile(r"\b(instead|rather than|always|prefer)\b|\b(?:use|say|write)\s+[\"“`]", re.IGNORECASE)
_LEAD_FILLER = re.compile(r"^(?:the\s+)?(?:words?|terms?|phrases?)\s+", re.IGNORECASE)
MAX_BAN_LIST_WORDS = 10      # a bare list of banned words is short; anything longer is a sentence


def _usable_term(term: str) -> bool:
    """A real word or phrase: not empty, not punctuation, not a stopword, not a tiny word."""
    t = term.strip().lower()
    if len(t) < 3 or not re.search(r"[a-z]{3}", t):
        return False
    return not all(w in STOPWORDS for w in re.findall(r"[a-z']+", t))


def banned_terms(personal_additions: str) -> list:
    """
    Words and phrases the person's own rules forbid, read from their personal_additions.

    A line counts as a ban when it carries a cue ("never use", "banned words:", "avoid" ...). The
    terms are what it quotes ("genuine"), up to any "... instead" that introduces the wording to use
    in its place, or else a short bare list after the cue (a / b, a, b or c). Stopwords, punctuation
    and words under three letters are never terms, and a long sentence is never split into a list.
    A line that only says what to write ("Always write "Registered Manager" in full") is not a ban.
    """
    terms: list = []

    def add(t: str) -> None:
        t = t.strip(" .*`\"'“”")
        if t and _usable_term(t) and len(t.split()) <= 4 and t.lower() not in (x.lower() for x in terms):
            terms.append(t)

    for line in (personal_additions or "").splitlines():
        cue = _BAN_CUE.search(line)
        if not cue:
            continue
        quotes = list(_QUOTED.finditer(line))
        if quotes:
            switch = _SWITCH.search(line, quotes[0].end())
            limit = switch.start() if switch else len(line)
            for m in quotes:
                if m.start() < limit:
                    add(next(g for g in m.groups() if g))
            continue
        tail = line[cue.end():]
        if ":" in tail[:25]:
            tail = tail.split(":", 1)[1]
        tail = tail.strip().rstrip(".")
        if not tail or len(tail.split()) > MAX_BAN_LIST_WORDS:
            continue
        for item in re.split(r"\s*(?:/|,|;|\bor\b|\band\b)\s*", tail):
            add(_LEAD_FILLER.sub("", item.strip()))
    return terms


def _check_hotwords(text: str, extra: tuple = ()) -> list[DeterministicFlag]:
    """Flag all hotword occurrences with their character spans. `extra` are the person's own bans."""
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
    for term in extra:
        for match in re.finditer(rf"\b{re.escape(term)}\b", text, re.IGNORECASE):
            flags.append(DeterministicFlag(
                type="hotword", start_pos=match.start(), end_pos=match.end(), excerpt=match.group(),
                message=f"'{match.group()}' is on your own list of words not to use — rephrase.",
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

        if _is_header_line(stripped) or _is_skills_line(stripped):
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


def _check_readability(text: str, known=frozenset()) -> tuple[float, Optional[DeterministicFlag]]:
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

        prose = _strip_markdown(_drop_skills_lines(para))
        words = re.findall(r"\b\w+\b", prose)
        if len(words) < FLESCH_MIN_WORD_COUNT:
            continue

        score = _readability_score(prose, known)
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


# ── Checks that need context ──────────────────────────────────────────────────

def _check_profile_shape(text: str) -> list[DeterministicFlag]:
    """The profile must have the paragraph count and length this instance is set up for."""
    from pipeline import profile_shape

    want_paras = profile_shape.paragraphs()
    lo, hi     = profile_shape.word_bounds()
    paras      = profile_shape.split_paragraphs(text)
    words      = profile_shape.word_count(text)
    excerpt    = text.strip()[:120] + ("..." if len(text.strip()) > 120 else "")
    flags: list[DeterministicFlag] = []

    if len(paras) != want_paras:
        flags.append(DeterministicFlag(
            type="profile_shape", start_pos=0, end_pos=len(text), excerpt=excerpt,
            message=(
                f"Profile has {len(paras)} paragraph{'s' if len(paras) != 1 else ''}; this "
                f"instance expects {want_paras}."
            ),
        ))
    if not lo <= words <= hi:
        flags.append(DeterministicFlag(
            type="profile_shape", start_pos=0, end_pos=len(text), excerpt=excerpt,
            message=f"Profile is {words} words; this instance expects {lo}-{hi}.",
        ))
    return flags


_THIRD_PERSON = re.compile(r"\b(she|her|hers|he|him|his|they|their)\b", re.IGNORECASE)


def _check_third_person(text: str) -> list[DeterministicFlag]:
    """A profile speaks as the candidate ("Led teams ..."), not about them ("She leads teams ...")."""
    return [
        DeterministicFlag(
            type="third_person", start_pos=m.start(), end_pos=m.end(), excerpt=m.group(),
            message=(
                f"'{m.group()}' is third person. Write the profile from the candidate's own "
                "point of view, with the subject implied (\"Led teams ...\")."
            ),
        )
        for m in _THIRD_PERSON.finditer(text)
    ]


_IRREGULAR_PARTICIPLES = (
    "built|made|run|led|held|kept|set|taught|won|sent|done|given|known|seen|shown|taken|written|"
    "chosen|driven|brought|bought|thought|found|met|paid|put|read|said|sold|spent|understood"
)
_PASSIVE = re.compile(
    rf"\b(?:am|is|are|was|were|be|been|being)\s+(?:\w+ly\s+)?(\w{{3,}}ed|{_IRREGULAR_PARTICIPLES})\b",
    re.IGNORECASE,
)


def _check_passive(text: str) -> list[DeterministicFlag]:
    """Passive voice in the profile ("rotas were redesigned"). Advisory: it weakens a claim of
    ownership, but is sometimes the right way to say something."""
    return [
        DeterministicFlag(
            type="passive", start_pos=m.start(), end_pos=m.end(), excerpt=m.group(),
            message=(
                f"'{m.group()}' is passive voice. Say who did it (\"I redesigned the rotas\") "
                "if it was you."
            ),
            severity="advisory",
        )
        for m in _PASSIVE.finditer(text)
    ]


def _non_stop(window: list) -> int:
    return sum(1 for t in window if t[0] not in STOPWORDS)


def _check_ad_echo(text: str, ctx: JudgeContext) -> list[DeterministicFlag]:
    """
    Flag phrases lifted from the job ad.

    A CV that quietly absorbs the ad's wording ends up claiming things the person never said
    ("market leads making calls themselves" becoming "coaching managers to decide without
    escalating"). Two signals, both deterministic:
      1. a run of 4+ words that appears in the ad but nowhere in the person's own materials;
      2. a meaningful word that is in the ad and absent from the library, yet is used more than
         once across the CV (the ad's emphasis leaking into the candidate's identity).
    """
    if not ctx.jd_text.strip():
        return []
    toks = tokens(text)
    flags: list[DeterministicFlag] = []

    # 1. copied phrases: merge overlapping 4-word windows into one span
    jd4, known4 = ctx.jd_ngrams(AD_ECHO_NGRAM), ctx.known_ngrams(AD_ECHO_NGRAM)
    spans: list = []
    for i in range(len(toks) - AD_ECHO_NGRAM + 1):
        window = toks[i:i + AD_ECHO_NGRAM]
        gram   = tuple(t[0] for t in window)
        if gram in jd4 and gram not in known4 and _non_stop(window) >= 2:
            start, end = window[0][1], window[-1][2]
            if spans and start <= spans[-1][1]:
                spans[-1][1] = max(spans[-1][1], end)
            else:
                spans.append([start, end])
    for start, end in spans:
        phrase = text[start:end]
        flags.append(DeterministicFlag(
            type="ad_echo", start_pos=start, end_pos=end, excerpt=phrase,
            message=(
                f"'{phrase}' is worded like the job ad and appears nowhere in your library. "
                "Rewrite it from your own experience, or remove it."
            ),
        ))

    # 2. ad-only words that recur through the CV
    counts = ctx.cv_stem_counts(fallback_text=text)
    seen: set = set()
    for word, start, end in toks:
        k = stem(word)
        if k in seen or len(word) < 5 or word in STOPWORDS:
            continue
        if k in ctx.ad_only_stems() and counts.get(k, 0) >= 2:
            seen.add(k)
            flags.append(DeterministicFlag(
                type="ad_echo", start_pos=start, end_pos=end, excerpt=text[start:end],
                message=(
                    f"'{text[start:end]}' comes from the job ad, not your library, and appears "
                    f"{counts[k]} times in this CV. Use it at most once, or remove it."
                ),
            ))

    # 3. any ad word used AD_TERM_LIMIT+ times, even one the library also uses: a CV that says
    #    "clinical" four times is echoing the ad's emphasis whether or not the person ever did
    ad_stems = {stem(t[0]) for t in content_tokens(ctx.jd_text)}
    for word, start, end in toks:
        k = stem(word)
        if k in seen or len(word) < 5 or word in STOPWORDS:
            continue
        if k in ad_stems and counts.get(k, 0) >= AD_TERM_LIMIT:
            seen.add(k)
            flags.append(DeterministicFlag(
                type="ad_echo", start_pos=start, end_pos=end, excerpt=text[start:end],
                message=(
                    f"'{text[start:end]}' is a word from the job ad and appears {counts[k]} times "
                    "in this CV. Vary it or cut some."
                ),
            ))
    return flags


# Everyday workplace words. A CV about people-facing work says "customers" or "team" often and
# naturally, so repeating them is not a style problem.
GENERIC_WORDS = frozenset("""
team teams staff service services customer customers client clients people person business company
work working works manager managers management project projects process processes system systems
support care training report reports reporting plan plans quality performance development operation
operations delivery region regional product products sales across group groups department
organisation organization leadership lead led leading role roles year years month months week weekly
""".split())


def _rep_key(word: str) -> str:
    return word.rstrip("s") if len(word) > 4 else word


def _check_repetition(text: str, ctx: Optional[JudgeContext] = None) -> list[DeterministicFlag]:
    """
    Flag a word used REPEAT_LIMIT times within one role's bullets (reset at each ### heading).

    Everyday workplace words (GENERIC_WORDS) and the person's own domain vocabulary (words their
    library uses often) are exempt: repeating those is natural, not a tic.
    """
    exempt = {_rep_key(w) for w in GENERIC_WORDS}
    if ctx is not None:
        exempt |= ctx.frequent_known_keys(_rep_key)
    flags: list[DeterministicFlag] = []
    counts: dict = {}
    flagged: set = set()
    cursor = 0
    for line in text.split("\n"):
        line_start = cursor
        cursor += len(line) + 1
        stripped = line.strip()
        if re.match(r"^###\s", stripped):
            counts, flagged = {}, set()
            continue
        if not _is_bullet_line(stripped):
            continue
        for word, start, end in tokens(line):
            shouting = len(word) == 3 and line[start:end].isupper()      # MVP, CQC, KPI
            if (len(word) < 4 and not shouting) or word in STOPWORDS or word.isdigit():
                continue
            key = _rep_key(word)
            if key in exempt:
                continue
            counts[key] = counts.get(key, 0) + 1
            if counts[key] == REPEAT_LIMIT and key not in flagged:
                flagged.add(key)
                flags.append(DeterministicFlag(
                    type="repetition", start_pos=line_start + start, end_pos=line_start + end,
                    excerpt=line[start:end],
                    message=(
                        f"'{line[start:end]}' is used {REPEAT_LIMIT} times in this role. "
                        "Vary the wording or cut one."
                    ),
                ))
    return flags


_ITALIC_LINE = re.compile(r"^\*[^*].*[^*]\*$|^\*[^*]+\*$")


def _roles(text: str) -> list:
    """
    The experience section as a list of roles: {"context": (text, start, end) | None,
    "bullets": [(text, start, end), ...]}, each position the character span of its line in `text`.
    A role starts at a "### " heading; its context is the first italic line under it.
    """
    roles: list = []
    cursor = 0
    for line in text.split("\n"):
        start, end = cursor, cursor + len(line)
        cursor = end + 1
        s = line.strip()
        if re.match(r"^###\s", s):
            roles.append({"context": None, "bullets": []})
        elif not roles or not s:
            continue
        elif _is_bullet_line(s):
            roles[-1]["bullets"].append((_strip_markdown(re.sub(r"^\s*[-*+]\s+", "", s)), start, end))
        elif roles[-1]["context"] is None and _ITALIC_LINE.match(s) and not roles[-1]["bullets"]:
            roles[-1]["context"] = (s.strip("*").strip(), start, end)
    return roles


def _content_stems(text: str) -> set:
    return {stem(w) for w, _, _ in tokens(text) if len(w) >= 4 and w not in STOPWORDS}


def _check_context_echo(text: str) -> list[DeterministicFlag]:
    """
    Flag wording that is said twice.

      1. a bullet that restates its own role's context line (content-word overlap above
         CONTEXT_ECHO_JACCARD): the context line already said it, so the bullet adds nothing;
      2. a phrase of CONTEXT_PHRASE_WORDS+ words that appears in the context lines of two
         different roles (the same sentence reused to describe different jobs).
    """
    roles = _roles(text)
    flags: list[DeterministicFlag] = []

    for role in roles:
        if not role["context"]:
            continue
        ctx_stems = _content_stems(role["context"][0])
        if len(ctx_stems) < 3:
            continue
        for bullet, start, end in role["bullets"]:
            b_stems = _content_stems(bullet)
            shared = ctx_stems & b_stems
            union = ctx_stems | b_stems
            if len(shared) >= 3 and len(shared) / len(union) > CONTEXT_ECHO_JACCARD:
                flags.append(DeterministicFlag(
                    type="context_echo", start_pos=start, end_pos=end, excerpt=bullet[:120],
                    message=(
                        "This bullet restates the role's context line above it. Say something the "
                        "context line does not (what you did, and what changed), or remove it."
                    ),
                ))

    n = CONTEXT_PHRASE_WORDS
    seen: dict = {}                         # n-gram -> index of the first role whose context has it
    for i, role in enumerate(roles):
        if not role["context"]:
            continue
        ctext, cstart, cend = role["context"]
        toks = tokens(ctext)
        hits: list = []
        for k in range(len(toks) - n + 1):
            gram = tuple(t[0] for t in toks[k:k + n])
            if gram in seen and seen[gram] != i:
                span = [toks[k][1], toks[k + n - 1][2]]
                if hits and span[0] <= hits[-1][1]:
                    hits[-1][1] = max(hits[-1][1], span[1])
                else:
                    hits.append(span)
        for k in range(len(toks) - n + 1):
            seen.setdefault(tuple(t[0] for t in toks[k:k + n]), i)
        for a, b in hits:
            phrase = ctext[a:b]
            flags.append(DeterministicFlag(
                type="context_echo", start_pos=cstart, end_pos=cend, excerpt=phrase,
                message=(
                    f"'{phrase}' is also in another role's context line. Describe each role in its "
                    "own words."
                ),
            ))
    return flags


def _check_bullet_counts(text: str, weights: list) -> list[DeterministicFlag]:
    """
    Flag a role with more or fewer bullets than its "CV weight" allows (lead 4-6, standard 2-4,
    brief 0-1). Roles with no weight in the library are not checked, as before.
    """
    from pipeline import weights as w
    flags: list[DeterministicFlag] = []
    if not weights:
        return flags
    cursor = 0
    heads: list = []                       # (company, role, start, end) per "### " heading
    for line in text.split("\n"):
        start, end = cursor, cursor + len(line)
        cursor = end + 1
        if re.match(r"^###\s", line.strip()):
            title = re.sub(r"\s*\|.*$", "", line.strip()[3:]).strip()
            company, role = w._split_title(title)
            heads.append((company, role, start, end))
    roles = _roles(text)
    for (company, role, start, end), r in zip(heads, roles):
        entry = w.weight_for(company, role, weights)
        if not entry:
            continue
        n = len(r["bullets"])
        if n < entry["min"] or n > entry["max"]:
            want = (f"{entry['min']}-{entry['max']}" if entry["min"] != entry["max"] else str(entry["max"]))
            flags.append(DeterministicFlag(
                type="bullet_count", start_pos=start, end_pos=end, excerpt=line_excerpt(text, start, end),
                message=(
                    f"{company} has {n} bullet{'s' if n != 1 else ''}; its CV weight "
                    f"('{entry['weight']}') calls for {want}."
                ),
            ))
    return flags


def line_excerpt(text: str, start: int, end: int) -> str:
    return text[start:end].strip()[:120]


def _check_bullet_fragments(text: str) -> list[DeterministicFlag]:
    """Flag experience bullets too short to be a sentence (a length fix that left a stump)."""
    flags: list[DeterministicFlag] = []
    cursor = 0
    for line in text.split("\n"):
        line_start, line_end = cursor, cursor + len(line)
        cursor = line_end + 1
        stripped = line.strip()
        if not _is_bullet_line(stripped):
            continue
        content = _strip_markdown(re.sub(r"^\s*[-*+]\s+", "", stripped))
        n = len(content.split())
        if 0 < n < MIN_BULLET_WORDS:
            flags.append(DeterministicFlag(
                type="fragment", start_pos=line_start, end_pos=line_end, excerpt=stripped[:120],
                message=(
                    f"Bullet is only {n} words, which reads as a fragment. Make it a complete "
                    f"sentence of at least {MIN_BULLET_WORDS} words, or merge it into the bullet "
                    "it belongs to."
                ),
            ))
    return flags


# ── Public entry point ────────────────────────────────────────────────────────

def run(text: str, ctx: Optional[JudgeContext] = None) -> DeterministicResult:
    """
    Run all deterministic checks against raw section text.

    Args:
        text: Raw section content as stored in the section file.
              Must not be transformed before passing — character positions
              in returned flags are relative to this exact string.
        ctx:  Optional JudgeContext. With it, the section-aware checks (profile shape,
              ad echo, repetition, fragments) run too.

    Returns:
        DeterministicResult with a flag list and Flesch score.
        passed=True only when zero flags are raised.
    """
    if not text or not text.strip():
        log.warning("Deterministic judge received empty text — skipping.")
        return DeterministicResult(passed=True, flesch_score=0.0)

    flags: list[DeterministicFlag] = []

    flags.extend(_check_hotwords(text, tuple(banned_terms(ctx.personal_additions)) if ctx else ()))
    flags.extend(_check_em_dashes(text))
    flags.extend(_check_sentence_length(text))

    section = (ctx.section if ctx else "").lower()
    if section == "profile":
        flags.extend(_check_profile_shape(text))
        import pipeline.config as _cfg
        if _cfg.FLAG_THIRD_PERSON:
            flags.extend(_check_third_person(text))
        flags.extend(_check_passive(text))
    if section == "experience":
        flags.extend(_check_repetition(text, ctx))
        flags.extend(_check_bullet_fragments(text))
        flags.extend(_check_context_echo(text))
        if ctx is not None:
            flags.extend(_check_bullet_counts(text, ctx.role_weights))
    if ctx is not None:
        flags.extend(_check_ad_echo(text, ctx))

    flesch_score, readability_flag = _check_readability(
        text, ctx.known_long_words() if ctx else frozenset())
    if readability_flag:
        flags.append(readability_flag)

    log.debug(
        f"Deterministic judge: {len(flags)} flag(s), "
        f"Flesch={flesch_score:.1f}, passed={len(flags) == 0}"
    )

    return DeterministicResult(
        passed=not any(f.severity != "advisory" for f in flags),
        flesch_score=flesch_score,
        flags=flags,
    )
