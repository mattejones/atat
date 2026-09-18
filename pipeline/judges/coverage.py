"""
coverage.py — Tier 3: does the CV actually answer the job ad?

The gap this fills. Tier 1 checks prose mechanics. Tier 2 checks claims against the
cv-library. Both point BACKWARDS, at the source material. Neither has ever looked at the
job advertisement. The consequence is a failure mode that no existing judge can detect:
a CV that is mechanically clean, entirely factually accurate, fully supported by the
library — and aimed at the wrong target.

That is not hypothetical. It is the observed default. A CV can lead with exactly the
thing an ad explicitly disqualified, and score a clean pass on every tier, because every
word of it was true.

Tier 3 judges the CV against the jd_spec: for each requirement, is it addressed? For
each anti-pattern the ad stated, is it tripped? The rubric is the spec, which the human
has already reviewed — so this judge has no latitude to invent standards of its own.

Structurally different from tiers 1 and 2 in one important way: coverage is a property
of the WHOLE DOCUMENT. You cannot tell whether requirement R4 is addressed by reading the
Skills section alone. So this runs once per CV, against the composed markdown, and writes
to coverage_evaluations / coverage_findings rather than the per-section evaluations table.

Per the design decision: this tier ESCALATES, it does not block. A coverage gap is not a
defect to be regenerated away — it is information about fit. Retrying against the same
spec cannot close a gap that the library genuinely does not contain, and pretending
otherwise just burns tokens producing more confident-sounding evasion.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from pipeline.config import (
    LLM_PROVIDER, JUDGE_MODEL, ANTHROPIC_API_KEY, OPENAI_API_KEY, PROMPTS_PATH,
)
from pipeline.tailorer import load_text

log = logging.getLogger(__name__)


# Output budget. This judge emits one excerpt + one reason per requirement, plus any
# violations, in a single JSON document. At 2048 it truncated mid-string on a 12-requirement
# spec and the whole evaluation was lost to a JSON parse error. Coverage output scales with
# the size of the spec, so size for the worst case: a long spec is exactly when you most
# need the judge to work.
MAX_JUDGE_TOKENS = 8192


@dataclass
class CoverageFinding:
    kind:    str            # coverage | anti_pattern
    ref_id:  str            # requirement id or anti_pattern id
    quote:   str            # the JD quote being assessed
    status:  str            # covered | partial | absent | violated
    excerpt: str = ""       # verbatim CV text, if any
    reason:  str = ""


@dataclass
class CoverageResult:
    passed:            bool
    model:             str
    prompt_tokens:     int
    completion_tokens: int
    covered_count:     int = 0
    partial_count:     int = 0
    absent_count:      int = 0
    violation_count:   int = 0
    findings:          list[CoverageFinding] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return (
            f"{self.covered_count} covered, {self.partial_count} partial, "
            f"{self.absent_count} absent, {self.violation_count} violation(s)"
        )


# ── LLM callers ───────────────────────────────────────────────────────────────

def _call_anthropic(system: str, user: str) -> tuple[str, int, int]:
    import anthropic
    client  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=MAX_JUDGE_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in message.content if b.type == "text")
    return text, message.usage.input_tokens, message.usage.output_tokens


def _call_openai(system: str, user: str) -> tuple[str, int, int]:
    from openai import OpenAI
    client   = OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=JUDGE_MODEL,
        max_tokens=MAX_JUDGE_TOKENS,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    )
    u = response.usage
    return response.choices[0].message.content, u.prompt_tokens, u.completion_tokens


def _parse_response(raw: str) -> dict:
    cleaned = re.sub(r'^```(?:json)?\s*', '', raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*```$', '', cleaned).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        # A truncated response is the likeliest cause here, and it looks nothing like a
        # model error: it looks like malformed JSON. Say so plainly, so the next person
        # raises MAX_JUDGE_TOKENS instead of debugging the prompt.
        hint = ""
        if not cleaned.rstrip().endswith("}"):
            hint = (
                " The response does not end with '}', so it was almost certainly TRUNCATED. "
                f"Raise MAX_JUDGE_TOKENS (currently {MAX_JUDGE_TOKENS})."
            )
        raise ValueError(
            f"Coverage judge returned invalid JSON: {e}.{hint}\nRaw: {cleaned[:300]}"
        )
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object, got {type(data).__name__}")
    return data


# ── Spec rendering for the judge ──────────────────────────────────────────────

def _render_spec(spec: dict) -> str:
    lines = ["## REQUIREMENTS", ""]
    for r in spec.get("requirements", []):
        flag = "MUST-HAVE" if r.get("must_have") else "nice-to-have"
        lines.append(f'{r.get("id")} [{flag}]: "{r.get("quote")}"')
    lines += ["", "## ANTI-PATTERNS (the ad explicitly said it does NOT want these)", ""]
    anti = spec.get("anti_patterns", [])
    if anti:
        for a in anti:
            lines.append(f'{a.get("id")}: "{a.get("quote")}"')
    else:
        lines.append("(none stated)")
    return "\n".join(lines)


# ── Public entry point ────────────────────────────────────────────────────────

def run(cv_markdown: str, spec: dict) -> CoverageResult:
    """
    Run the Tier 3 coverage judge against the composed CV and a reviewed jd_spec.

    `passed` is True only when there are no absent MUST-HAVE requirements and no
    anti-pattern violations. Partials and absent nice-to-haves do not fail the CV —
    they are reported so the human can decide, which is the correct division of labour:
    the judge establishes facts, the applicant makes the call.
    """
    if not cv_markdown or not cv_markdown.strip():
        raise ValueError("cv_markdown cannot be empty")
    if not spec or not spec.get("requirements"):
        raise ValueError("jd_spec is missing or has no requirements — run analyse_job first")

    system = load_text(PROMPTS_PATH / "judge_coverage.md")

    user = f"""## JOB SPECIFICATION
{_render_spec(spec)}

---

## GENERATED CV (assess this against the specification above)
{cv_markdown}"""

    if LLM_PROVIDER == "anthropic":
        raw, prompt_tokens, completion_tokens = _call_anthropic(system, user)
    elif LLM_PROVIDER == "openai":
        raw, prompt_tokens, completion_tokens = _call_openai(system, user)
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {LLM_PROVIDER!r}")

    data = _parse_response(raw)

    # Index the spec so we can attach quotes and must_have flags to findings, and so a
    # judge that invents an id gets caught rather than silently believed.
    req_by_id  = {r.get("id"): r for r in spec.get("requirements", [])}
    anti_by_id = {a.get("id"): a for a in spec.get("anti_patterns", [])}

    findings: list[CoverageFinding] = []
    covered = partial = absent = 0
    absent_must_haves: list[str] = []

    for item in data.get("coverage", []):
        rid = item.get("id")
        req = req_by_id.get(rid)
        if req is None:
            log.warning(f"Coverage judge returned unknown requirement id {rid!r} — ignoring")
            continue

        status = str(item.get("status", "")).strip().lower()
        if status not in ("covered", "partial", "absent"):
            log.warning(f"Coverage judge returned invalid status {status!r} for {rid} — ignoring")
            continue

        if status == "covered":
            covered += 1
        elif status == "partial":
            partial += 1
        else:
            absent += 1
            if req.get("must_have"):
                absent_must_haves.append(rid)

        findings.append(CoverageFinding(
            kind="coverage",
            ref_id=rid,
            quote=req.get("quote", ""),
            status=status,
            excerpt=str(item.get("excerpt", "")).strip(),
            reason=str(item.get("reason", "")).strip(),
        ))

    # Any requirement the judge failed to assess is treated as absent. Silence is not
    # coverage — a judge that skips a requirement must not be read as approving it.
    assessed = {f.ref_id for f in findings}
    for rid, req in req_by_id.items():
        if rid in assessed:
            continue
        absent += 1
        if req.get("must_have"):
            absent_must_haves.append(rid)
        findings.append(CoverageFinding(
            kind="coverage", ref_id=rid, quote=req.get("quote", ""),
            status="absent", excerpt="",
            reason="judge did not assess this requirement — treated as absent",
        ))

    violations = 0
    for item in data.get("violations", []):
        aid  = item.get("id")
        anti = anti_by_id.get(aid)
        if anti is None:
            log.warning(f"Coverage judge returned unknown anti-pattern id {aid!r} — ignoring")
            continue
        violations += 1
        findings.append(CoverageFinding(
            kind="anti_pattern",
            ref_id=aid,
            quote=anti.get("quote", ""),
            status="violated",
            excerpt=str(item.get("excerpt", "")).strip(),
            reason=str(item.get("reason", "")).strip(),
        ))

    passed = not absent_must_haves and violations == 0

    result = CoverageResult(
        passed=passed,
        model=JUDGE_MODEL,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        covered_count=covered,
        partial_count=partial,
        absent_count=absent,
        violation_count=violations,
        findings=findings,
    )

    log.info(f"Tier 3 coverage — passed={passed}, {result.summary}")
    if absent_must_haves:
        log.warning(f"Tier 3 — MUST-HAVE requirements unaddressed: {absent_must_haves}")

    return result
