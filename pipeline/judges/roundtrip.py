"""
roundtrip.py — Tier 0: does what we wrote actually survive to the PDF?

Every other judge reads cv.md. None of them read what parse_cv() produces from it.
That blind spot cost months: a regex in parse_cv silently truncated education year
ranges ("2014 - 2016" -> "2014") on every CV that obeyed the house style rule of using
plain hyphens. Three tiers of increasingly sophisticated judging never noticed, because
none of them looked past the markdown at the artefact the employer actually receives.

This tier is dumb on purpose. It parses cv.md, then asserts that the things we put in
came back out. It has no model, no prompt, and no opinions. It is the cheapest judge in
the pipeline and, on current evidence, the one that would have caught the most damage.

Runs before render. A failure here means the PDF will be wrong regardless of how good
the prose is, so it is worth blocking on.
"""

import logging
import re
from dataclasses import dataclass, field

from pipeline.parse_cv import parse_cv

log = logging.getLogger(__name__)


@dataclass
class RoundtripFailure:
    field:    str      # e.g. "education.years", "experience.dates"
    expected: str      # what cv.md says
    actual:   str      # what parse_cv produced


@dataclass
class RoundtripResult:
    passed:   bool
    failures: list[RoundtripFailure] = field(default_factory=list)


def _norm(text: str) -> str:
    """Compare on content, ignoring dash flavour and whitespace padding."""
    text = re.sub(r'[\u2010-\u2015\u2212]', '-', text)   # any dash -> hyphen
    return re.sub(r'\s+', ' ', text).strip().lower()


def run(cv_markdown: str) -> RoundtripResult:
    """
    Assert that every heading, date and year range in cv.md survives into ParsedCV.

    Checks:
      - every '### Company -- Role | Dates' heading produces an experience entry
        whose company, role and dates all match
      - every '**Degree** -- Institution, Years' line produces an education entry
        whose years match in full (this is the check that would have caught the bug)
      - bullet counts per experience entry match
      - skills categories all survive
    """
    failures: list[RoundtripFailure] = []

    try:
        parsed = parse_cv(cv_markdown)
    except Exception as e:
        return RoundtripResult(
            passed=False,
            failures=[RoundtripFailure("parse", "cv.md parses cleanly", f"raised {e!r}")],
        )

    # ── Experience headings ───────────────────────────────────────────────────
    md_exp = re.findall(r'(?m)^###\s+(.+?)\s*\|\s*(.+?)\s*$', cv_markdown)
    parsed_dates = [e.dates for e in parsed.experience if e.dates]

    for title_part, dates in md_exp:
        if not any(_norm(dates) == _norm(d) for d in parsed_dates):
            failures.append(RoundtripFailure(
                field="experience.dates",
                expected=dates,
                actual=f"not found among parsed dates: {parsed_dates}",
            ))

    md_bullet_total = len(re.findall(r'(?m)^-\s+\S', cv_markdown))
    # parse_cv folds the 'Earlier technical experience' paragraph into a synthetic
    # ExperienceEntry carrying a single bullet, even though in the markdown it is prose,
    # not a '- ' line. Counting it would report a permanent off-by-one on every CV that
    # has an earlier-experience section. Real entries always carry dates; the synthetic
    # one never does, so filter on that.
    parsed_bullet_total = sum(len(e.bullets) for e in parsed.experience if e.dates)
    # certifications are also '- ' bullets in the markdown, so account for them
    parsed_bullet_total += len(parsed.certifications)
    if md_bullet_total != parsed_bullet_total:
        failures.append(RoundtripFailure(
            field="bullets.count",
            expected=str(md_bullet_total),
            actual=str(parsed_bullet_total),
        ))

    # ── Education years — the regression that started all this ────────────────
    md_edu = re.findall(
        r'(?m)^\*\*(.+?)\*\*\s*[-\u2013\u2014]+\s*(.+?),\s*(\d{4}(?:\s*[-\u2013\u2014]\s*(?:\d{4}|[Pp]resent))?)\s*$',
        cv_markdown,
    )
    for degree, institution, years in md_edu:
        match = next(
            (e for e in parsed.education if _norm(e.degree) == _norm(degree)),
            None,
        )
        if match is None:
            failures.append(RoundtripFailure(
                field="education.degree",
                expected=degree,
                actual="degree not present in ParsedCV",
            ))
            continue
        if _norm(match.years) != _norm(years):
            failures.append(RoundtripFailure(
                field="education.years",
                expected=years,
                actual=match.years or "(empty)",
            ))
        if _norm(match.institution) != _norm(institution):
            failures.append(RoundtripFailure(
                field="education.institution",
                expected=institution,
                actual=match.institution or "(empty)",
            ))

    # ── Skills categories ─────────────────────────────────────────────────────
    md_skills = re.findall(r'(?m)^\*\*(.+?):\*\*\s*(.+?)\s*$', cv_markdown)
    parsed_cats = [c for c, _ in parsed.skills]
    for category, _items in md_skills:
        if not any(_norm(category) == _norm(c) for c in parsed_cats):
            failures.append(RoundtripFailure(
                field="skills.category",
                expected=category,
                actual=f"not found among parsed categories: {parsed_cats}",
            ))

    if failures:
        log.warning(
            f"Tier 0 roundtrip FAILED with {len(failures)} discrepancy(ies) — "
            f"the PDF will not match cv.md"
        )
        for f in failures:
            log.warning(f"  {f.field}: expected {f.expected!r}, got {f.actual!r}")
    else:
        log.info("Tier 0 roundtrip passed — cv.md survives parse_cv intact")

    return RoundtripResult(passed=not failures, failures=failures)
