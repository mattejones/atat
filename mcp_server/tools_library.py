"""
tools_library.py — read access to the cv-library source material.

The cv-library (separate private repo, path set by CV_LIBRARY_PATH) holds
the canonical, untailored experience entries, personas, and skills that
every generation draws from — see pipeline.tailorer.load_experience_files/
load_persona_files. Distinct from an application's cv_markdown (the tailored
*output* of a generation, already accessible via get_cv_markdown): this
module exposes the raw *input* material, which is the better source for
"what could I include" — it's the ground truth, not a derivative of it.

Split into list + get-one-file rather than a single dump-everything tool:
submit_job's own generation already sends the whole library to the LLM in
one shot (that's a deliberate, bounded, one-time cost inside generation).
An agent poking around for ideas mid-conversation doesn't need — and
shouldn't default to — the same all-at-once load; list first, fetch what's
relevant. search_library covers the "find me the right entry" case in one
call without loading everything.
"""

from pathlib import Path
from typing import Optional

from mcp_server.app import mcp
from pipeline.config import EXPERIENCE_PATH, META_PATH, PERSONAS_PATH, SKILLS_PATH

MAX_SEARCH_RESULTS = 50


def _safe_read(base: Path, filename: str) -> str:
    """Resolve filename under base and read it — rejects any path escaping base."""
    candidate = (base / filename).resolve()
    if base.resolve() not in candidate.parents and candidate != base.resolve():
        raise ValueError(f"Invalid filename: {filename!r}")
    if not candidate.exists():
        raise ValueError(f"Not found: {filename!r}")
    return candidate.read_text(encoding="utf-8")


@mcp.tool()
def list_experience_entries() -> list[dict]:
    """
    List experience library entries (job history) without their content —
    filename plus a parsed label. Filenames follow the convention
    {dates}_{company}_{role}.md, e.g. "2023-2025_nexova-technologies_head-of-
    sales-operations.md". Most recent first. Follow up with
    get_experience_entry(filename) for the entries relevant to the JD at hand.
    """
    if not EXPERIENCE_PATH.exists():
        return []
    files = sorted(EXPERIENCE_PATH.glob("*.md"), reverse=True)
    return [{"filename": f.name, "label": f.stem.replace("_", " · ").replace("-", " ")} for f in files]


@mcp.tool()
def get_experience_entry(filename: str) -> str:
    """
    Return the raw markdown content of one experience library entry.
    filename must be one returned by list_experience_entries().
    """
    return _safe_read(EXPERIENCE_PATH, filename)


@mcp.tool()
def list_personas() -> list[str]:
    """
    List available persona names — different framings of the same
    underlying experience (e.g. "sales-ops-leader" vs "account-executive")
    used to steer which angle a generation takes. Follow up with
    get_persona(name) for the one relevant to a given role.
    """
    if not PERSONAS_PATH.exists():
        return []
    return sorted(f.stem for f in PERSONAS_PATH.glob("*.md"))


@mcp.tool()
def get_persona(name: str) -> str:
    """Return the raw markdown content of one persona. name must be one returned by list_personas()."""
    return _safe_read(PERSONAS_PATH, f"{name}.md")


@mcp.tool()
def get_skills() -> str:
    """Return the raw skills inventory (skills/skills.md) — the full canonical skills list, not tailored to any one application."""
    if not SKILLS_PATH.exists():
        raise ValueError(f"Skills file not found: {SKILLS_PATH}")
    return SKILLS_PATH.read_text(encoding="utf-8")


@mcp.tool()
def get_meta() -> str:
    """Return contact/identity info (meta/meta.md) — name, email, phone, location, links used to compose every CV's header."""
    if not META_PATH.exists():
        raise ValueError(f"Meta file not found: {META_PATH}")
    return META_PATH.read_text(encoding="utf-8")


@mcp.tool()
def search_library(query: str, limit: int = 20) -> list[dict]:
    """
    Search across experience entries, personas, and the skills inventory for
    a keyword — e.g. a technology, methodology, or achievement mentioned in
    a JD — without loading the whole library. Case-insensitive substring
    match. Returns one snippet (the line the match was found on, trimmed)
    per match, not full file content — follow up with get_experience_entry/
    get_persona/get_skills for the full text of anything relevant.

    limit: capped at 50.
    """
    limit = max(1, min(limit, MAX_SEARCH_RESULTS))
    query_lower = query.lower()
    results: list[dict] = []

    sources: list[tuple[str, Path]] = []
    if EXPERIENCE_PATH.exists():
        sources += [("experience", f) for f in sorted(EXPERIENCE_PATH.glob("*.md"), reverse=True)]
    if PERSONAS_PATH.exists():
        sources += [("persona", f) for f in sorted(PERSONAS_PATH.glob("*.md"))]
    if SKILLS_PATH.exists():
        sources.append(("skills", SKILLS_PATH))

    for category, path in sources:
        if len(results) >= limit:
            break
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            if query_lower in line.lower():
                snippet = line.strip()
                if len(snippet) > 240:
                    snippet = snippet[:240] + "..."
                results.append({"category": category, "file": path.name, "snippet": snippet})
                if len(results) >= limit:
                    break

    return results
