"""
jd_spec.py — Structured extraction of a job ad, mapped onto cv-library evidence.

The jd_spec is ATAT's answer to a structural gap in the judge pipeline: tiers 1 and 2
both validate BACKWARDS (prose mechanics; claims against the library). Nothing validated
FORWARDS, against the job ad. A CV could be mechanically clean, factually accurate, and
aimed squarely at the wrong target — and pass everything.

The spec makes the job ad a first-class artefact:

    analyse_job()  ->  extract_jd_spec()  ->  human review  ->  generate_cv()
                                                                     |
                                                          tier 3 coverage judge
                                                          (rubric = the same spec)

Two invariants make it falsifiable rather than vibes:

  1. Every requirement/anti-pattern/signal carries a VERBATIM quote from the job ad.
     If the model cannot quote it, it does not exist. validate_spec() checks this by
     substring match against jd_text.

  2. Every evidence reference resolves to a real cv-library heading path. The model is
     handed an index of valid references and may only cite from it. validate_spec()
     checks this against the index. An unresolvable ref is a hallucination, not a typo.

Both are enforced on write. A spec that fails validation is rejected outright — we do
not silently repair it, because a silently repaired spec is worse than no spec: it looks
authoritative and isn't.

A GAP (requirement with no evidence) is not an error. It is the most valuable signal the
spec produces — it tells you where the fit genuinely is not, before a generation is spent.
compute_tier() reads gaps directly.
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from pipeline.config import (
    REPO_ROOT,
    EXPERIENCE_PATH, PERSONAS_PATH, SKILLS_PATH, META_PATH, PROMPTS_PATH,
    LLM_PROVIDER, LLM_MODEL, ANTHROPIC_API_KEY, OPENAI_API_KEY,
    TEMPERATURE, MAX_OUTPUT_TOKENS,
)
from pipeline.tailorer import load_text, sanitise_text

log = logging.getLogger(__name__)


# Tier thresholds, expressed as the fraction of MUST-HAVE requirements that map to
# genuine library evidence. Deliberately blunt — this is a triage signal for deciding
# where to spend effort across a batch, not a precision instrument.
TIER_T1_MIN = 0.75      # strong fit — most must-haves genuinely evidenced
TIER_T2_MIN = 0.45      # real stretch, but a credible application exists
                        # below TIER_T2_MIN -> T3


class SpecValidationError(ValueError):
    """Raised when a spec fails the verbatim-quote or evidence-reference invariants."""


# ── Library reference index ───────────────────────────────────────────────────

def build_library_index() -> dict[str, str]:
    """
    Build the set of valid evidence references from the cv-library.

    A reference is a heading path into an experience file:

        2022-2025_aircall_support-operations-manager.md#AI-Powered Knowledge Article Generation System

    Returns {reference: achievement_body}. The body is used for nothing at extraction
    time (the model sees the full library anyway) but is returned so callers can render
    an evidence map for human review without re-reading the files.

    Achievements are '###' headings inside experience files. This works because the
    library already writes one '###' per achievement — no library changes were needed
    to make it addressable, which is the whole point.

    Note on encoding: load_text() sanitises to ASCII, so a heading like
    "AWS Lambda — User Enrichment Service" arrives as "AWS Lambda  User Enrichment
    Service" (em dash dropped, double space left behind). We collapse whitespace so the
    ref is clean and easy for the model to copy exactly. This is safe because the model
    is shown the SAME sanitised library body, so index and evidence agree.
    """
    index: dict[str, str] = {}

    for path in sorted(EXPERIENCE_PATH.glob("*.md")):
        text = load_text(path)
        # Split on ### headings, keeping the heading text
        parts = re.split(r'(?m)^###\s+(.+?)\s*$', text)
        # parts = [preamble, heading1, body1, heading2, body2, ...]
        for i in range(1, len(parts) - 1, 2):
            heading = re.sub(r'\s+', ' ', parts[i]).strip()
            body    = parts[i + 1].strip()
            if not heading:
                continue
            ref = f"{path.name}#{heading}"
            if ref in index:
                log.warning(f"Duplicate library reference after sanitisation: {ref!r}")
            index[ref] = body

    if not index:
        log.warning(f"Library index is empty — no '###' headings found in {EXPERIENCE_PATH}")

    return index


class LibraryStructureError(ValueError):
    """Raised when an experience file yields no addressable evidence references."""


def assert_library_addressable() -> None:
    """
    Fail loudly if any experience file produces zero evidence references.

    This guard exists because of a real incident. Two of seven experience files
    (MuleSoft Customer Architect, Sun/Oracle) wrote their achievements as a flat bullet
    list under '## Achievements' rather than as '### Achievement Name' blocks. The index
    silently skipped them. The evidence mapper then reported "no supporting evidence in
    library" for a requirement that read, almost verbatim, like the MuleSoft role's job
    description — and computed a tier from that false gap.

    Nothing errored. Nothing warned. The output just quietly excluded the applicant's
    single strongest customer-facing evidence from every application he made.

    That is the same shape as the parse_cv date bug: a silent, structural data loss that
    every downstream layer faithfully propagated. The lesson both times is that an empty
    result must never be mistaken for a negative result. So this raises.
    """
    index = build_library_index()
    empty = [
        p.name for p in sorted(EXPERIENCE_PATH.glob("*.md"))
        if not any(ref.startswith(f"{p.name}#") for ref in index)
    ]
    if empty:
        raise LibraryStructureError(
            "These experience files produce no addressable evidence references, so the "
            "evidence mapper cannot see them at all:\n  - "
            + "\n  - ".join(empty)
            + "\n\nEach achievement must sit under its own '### Achievement Name' heading. "
              "A flat bullet list under '## Achievements' is invisible to the index."
        )


def format_library_index(index: dict[str, str]) -> str:
    """Render the reference index for the extraction prompt — refs only, no bodies."""
    return "\n".join(f"- {ref}" for ref in index)


# ── Validation ────────────────────────────────────────────────────────────────

def _normalise_for_match(text: str) -> str:
    """Collapse whitespace so quote matching survives reflowed source text."""
    return re.sub(r'\s+', ' ', text).strip().lower()


def validate_spec(spec: dict, jd_text: str, index: dict[str, str]) -> list[str]:
    """
    Enforce the two invariants. Returns a list of human-readable errors.
    An empty list means the spec is sound.

    This is deliberately strict. A spec is the rubric that a downstream judge will hold
    the CV against; a spec containing an invented requirement will cause the judge to
    confidently flag a CV for failing to satisfy something the employer never asked for.
    Garbage here propagates with the appearance of rigour, which is the worst kind.
    """
    errors: list[str] = []
    haystack = _normalise_for_match(sanitise_text(jd_text))

    for key in ("requirements", "anti_patterns", "signals"):
        if key not in spec:
            errors.append(f"spec is missing required key: {key!r}")
            continue
        if not isinstance(spec[key], list):
            errors.append(f"spec[{key!r}] must be a list, got {type(spec[key]).__name__}")

    if errors:
        return errors

    seen_ids: set[str] = set()

    for key in ("requirements", "anti_patterns", "signals"):
        for i, entry in enumerate(spec[key]):
            label = f"{key}[{i}]"

            entry_id = str(entry.get("id", "")).strip()
            if not entry_id:
                errors.append(f"{label}: missing id")
            elif entry_id in seen_ids:
                errors.append(f"{label}: duplicate id {entry_id!r}")
            else:
                seen_ids.add(entry_id)

            # Invariant 1 — the quote must actually be in the job ad.
            quote = str(entry.get("quote", "")).strip()
            if not quote:
                errors.append(f"{label} ({entry_id}): missing quote")
            elif _normalise_for_match(quote) not in haystack:
                errors.append(
                    f"{label} ({entry_id}): quote is not verbatim in the job ad — "
                    f"{quote[:70]!r}"
                )

    # Invariant 2 — every evidence ref must resolve.
    for i, req in enumerate(spec["requirements"]):
        evidence = req.get("evidence", [])
        if not isinstance(evidence, list):
            errors.append(f"requirements[{i}]: evidence must be a list")
            continue
        for ref in evidence:
            if ref not in index:
                errors.append(
                    f"requirements[{i}] ({req.get('id')}): evidence reference does not "
                    f"resolve to any library heading — {ref!r}"
                )

    return errors


# ── Tier computation ──────────────────────────────────────────────────────────

def compute_tier(spec: dict) -> tuple[str, dict]:
    """
    Derive a tier from must-have evidence coverage.

    Returns (tier, stats). Stats are returned so the human reviewing the spec can see
    the working rather than being handed a letter and asked to trust it.

    If a role has no must-haves at all (rare, usually a badly written ad), we decline to
    guess and return T2 with a flag — an unconstrained ad tells us nothing about fit.
    """
    must_haves = [r for r in spec.get("requirements", []) if r.get("must_have")]
    total      = len(must_haves)

    if total == 0:
        return "T2", {
            "must_have_total":   0,
            "must_have_covered": 0,
            "ratio":             None,
            "note": "no must-have requirements found — tier not computable from the ad",
        }

    covered = sum(1 for r in must_haves if r.get("evidence"))
    ratio   = covered / total

    if ratio >= TIER_T1_MIN:
        tier = "T1"
    elif ratio >= TIER_T2_MIN:
        tier = "T2"
    else:
        tier = "T3"

    return tier, {
        "must_have_total":   total,
        "must_have_covered": covered,
        "ratio":             round(ratio, 2),
        "gaps": [
            {"id": r.get("id"), "quote": r.get("quote")}
            for r in must_haves if not r.get("evidence")
        ],
    }


# ── Prompt rendering ──────────────────────────────────────────────────────────

def spec_to_prompt_block(spec: dict) -> str:
    """
    Render the spec as the tailorer's brief.

    This replaces freehand generation_notes as the load-bearing instruction. Notes still
    exist and still apply (they carry things a spec cannot express — 'Lee knows my real
    background, zero tolerance for embellishment'), but they are no longer the only thing
    steering the generation.
    """
    lines: list[str] = []

    lines.append("### Requirements — the CV must visibly address each of these")
    lines.append("")
    for r in spec.get("requirements", []):
        flag = "MUST-HAVE" if r.get("must_have") else "nice-to-have"
        lines.append(f"**{r.get('id')}** ({flag}): {r.get('quote')}")
        evidence = r.get("evidence") or []
        if evidence:
            lines.append("  Evidence to draw on:")
            for ref in evidence:
                lines.append(f"    - {ref}")
        else:
            lines.append(
                "  NO EVIDENCE IN LIBRARY. Do not manufacture any. Do not stretch an "
                "unrelated achievement to cover it. Leave it uncovered."
            )
        lines.append("")

    anti = spec.get("anti_patterns", [])
    if anti:
        lines.append("### Anti-patterns — the ad explicitly said it does NOT want these")
        lines.append("")
        lines.append(
            "Do not write anything a reader would file under these. Pay particular "
            "attention to what leads: material that trips an anti-pattern in the first "
            "block on the page colours everything the reader sees afterwards."
        )
        lines.append("")
        for a in anti:
            lines.append(f"**{a.get('id')}**: {a.get('quote')}")
        lines.append("")

    signals = spec.get("signals", [])
    if signals:
        lines.append("### Signals — shape tone and emphasis, not claims")
        lines.append("")
        for s in signals:
            lines.append(f"**{s.get('id')}**: {s.get('quote')}")
        lines.append("")

    return "\n".join(lines)


# ── LLM call ──────────────────────────────────────────────────────────────────

LOG_DIR = REPO_ROOT / "logs" / "jd_spec_failures"


def _log_extraction_failure(raw: str, error: str) -> Path:
    """
    Persist the full, untruncated raw model response on a parse failure.

    The exception message alone only ever carried a 300-char preview of the response —
    nowhere near enough to diagnose a JSON-formatting failure that can break anywhere in
    a multi-KB response. This writes the whole thing to disk so a failure is actually
    debuggable rather than a shrug and a retry.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    path = LOG_DIR / f"{ts}.txt"
    path.write_text(f"ERROR: {error}\n\n{'-' * 20} RAW RESPONSE {'-' * 20}\n\n{raw}", encoding="utf-8")
    return path


def _parse_response(raw: str) -> dict:
    cleaned = re.sub(r'^```(?:json)?\s*', '', raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*```$', '', cleaned).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        log_path = _log_extraction_failure(raw, str(e))
        raise ValueError(
            f"Spec extraction returned invalid JSON: {e}\n"
            f"Full response ({len(raw)} chars) logged to: {log_path}"
        )
    if not isinstance(data, dict):
        log_path = _log_extraction_failure(raw, f"Expected JSON object, got {type(data).__name__}")
        raise ValueError(
            f"Expected JSON object, got {type(data).__name__}\n"
            f"Full response ({len(raw)} chars) logged to: {log_path}"
        )
    return data


def _call_anthropic(system: str, user: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    base_kwargs = dict(
        model=LLM_MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    try:
        message = client.messages.create(temperature=TEMPERATURE, **base_kwargs)
    except anthropic.BadRequestError as e:
        # Some newer models reject an explicit `temperature` value outright
        # ("temperature is deprecated for this model") instead of just
        # ignoring it — fall back to the model's default sampling rather
        # than hard-failing JD spec extraction against that model.
        if "temperature" in str(e).lower() and "deprecated" in str(e).lower():
            log.warning(
                f"Model {LLM_MODEL} does not accept `temperature` — retrying without it."
            )
            message = client.messages.create(**base_kwargs)
        else:
            raise
    return "".join(b.text for b in message.content if b.type == "text")


def _call_openai(system: str, user: str) -> str:
    from openai import OpenAI
    client   = OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=LLM_MODEL,
        temperature=TEMPERATURE,
        max_tokens=MAX_OUTPUT_TOKENS,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    )
    return response.choices[0].message.content


def extract_jd_spec(jd_text: str) -> dict:
    """
    Extract a validated jd_spec from a job ad.

    Raises SpecValidationError if the model produces unquotable requirements or
    unresolvable evidence references. We do not retry-and-hope or repair in place:
    a spec that failed its invariants once should be looked at, not laundered.

    Returns the spec dict, with computed_tier / tier_stats / extracted_at attached.
    """
    if not jd_text or not jd_text.strip():
        raise ValueError("jd_text cannot be empty")

    # Refuse to map evidence against a library the index cannot fully see. A partial
    # index does not produce a partial answer — it produces a confident wrong one.
    assert_library_addressable()

    index        = build_library_index()
    system, user = build_extraction_prompt(jd_text, index)

    if LLM_PROVIDER == "anthropic":
        raw = _call_anthropic(system, user)
    elif LLM_PROVIDER == "openai":
        raw = _call_openai(system, user)
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {LLM_PROVIDER!r}")

    spec   = _parse_response(raw)
    errors = validate_spec(spec, jd_text, index)

    if errors:
        raise SpecValidationError(
            "jd_spec failed validation — the extraction is not trustworthy:\n  - "
            + "\n  - ".join(errors)
        )

    tier, stats = compute_tier(spec)
    spec["computed_tier"] = tier
    spec["tier_stats"]    = stats
    spec["extracted_at"]  = datetime.now().isoformat()

    log.info(
        f"jd_spec extracted — {len(spec['requirements'])} requirements "
        f"({stats.get('must_have_total', 0)} must-have, "
        f"{stats.get('must_have_covered', 0)} evidenced), "
        f"{len(spec['anti_patterns'])} anti-patterns, tier={tier}"
    )

    return spec


def build_extraction_prompt(jd_text: str, index: Optional[dict[str, str]] = None) -> tuple[str, str]:
    """Assemble the (system, user) prompt for spec extraction without calling a model."""
    if index is None:
        index = build_library_index()
    system = load_text(PROMPTS_PATH / "jd_spec_extraction.md")

    user = f"""## VALID EVIDENCE REFERENCES
You may cite ONLY these references, copied exactly. Any other reference is invalid.

{format_library_index(index)}

---

## EXPERIENCE LIBRARY (the evidence itself)
{chr(10).join(load_text(p) for p in sorted(EXPERIENCE_PATH.glob("*.md"), reverse=True))}

---

## SKILLS INVENTORY
{load_text(SKILLS_PATH)}

---

## JOB ADVERTISEMENT (the only source of truth for quotes)
{sanitise_text(jd_text)}
"""
    return system, user


# ── Human-readable rendering ──────────────────────────────────────────────────

def render_spec_for_review(spec: dict) -> str:
    """
    Render the spec as markdown for a human to review before a generation is spent.

    This is the artefact that replaces 'read Claude's essay and hope'. It should be
    skimmable in under a minute and should make gaps impossible to miss.
    """
    stats = spec.get("tier_stats", {})
    lines = [
        f"# JD spec — computed tier: {spec.get('computed_tier', '?')}",
        "",
    ]

    if stats.get("ratio") is not None:
        lines.append(
            f"**Must-have coverage:** {stats.get('must_have_covered')} of "
            f"{stats.get('must_have_total')} ({int(stats['ratio'] * 100)}%)"
        )
    else:
        lines.append(f"**Must-have coverage:** {stats.get('note', 'unknown')}")
    lines.append("")

    lines.append("## Requirements")
    lines.append("")
    for r in spec.get("requirements", []):
        flag     = "MUST" if r.get("must_have") else "nice"
        evidence = r.get("evidence") or []
        mark     = "OK  " if evidence else "GAP "
        lines.append(f"- `{mark}` **{r.get('id')}** [{flag}] {r.get('quote')}")
        for ref in evidence:
            lines.append(f"    - {ref}")
        if not evidence:
            lines.append("    - _no supporting evidence in library_")
    lines.append("")

    anti = spec.get("anti_patterns", [])
    lines.append("## Anti-patterns")
    lines.append("")
    if anti:
        for a in anti:
            lines.append(f"- **{a.get('id')}** {a.get('quote')}")
    else:
        lines.append("_none stated_")
    lines.append("")

    signals = spec.get("signals", [])
    if signals:
        lines.append("## Signals")
        lines.append("")
        for s in signals:
            lines.append(f"- **{s.get('id')}** {s.get('quote')}")
        lines.append("")

    return "\n".join(lines)
