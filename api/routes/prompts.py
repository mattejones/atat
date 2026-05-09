"""
prompts.py — Read, write, and surface signals for ATAT prompt files.

All prompts live on the filesystem under prompts/. This router exposes
a hardcoded registry of known prompt files — not a filesystem scan —
to keep the API surface explicit and prevent arbitrary file access.

Endpoints:
  GET  /prompts                    — list all prompts with metadata
  GET  /prompts/{name:path}        — fetch prompt content by slug
  PUT  /prompts/{name:path}        — write prompt content by slug
  GET  /prompts/{name:path}/signals — surface relevant DB feedback as inspiration

Security: CORS is locked to localhost. Never expose publicly.

system: bool — marks prompts that contain JSON schema or output format
  constraints. Editing structure rather than content in these prompts
  may break generation or PDF rendering.
"""

import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from db.database import get_db

router = APIRouter(prefix="/prompts", tags=["prompts"])

_REPO_ROOT   = Path(__file__).resolve().parent.parent.parent
_PROMPTS_DIR = _REPO_ROOT / "prompts"

# ── Registry ──────────────────────────────────────────────────────────────────

_REGISTRY: list[dict] = [
    {
        "slug":        "cv_generation",
        "label":       "CV Generation",
        "description": "Main system prompt for CV generation. Controls reasoning steps, output schema, content rules, and writing quality.",
        "personal":    False,
        "system":      True,
    },
    {
        "slug":        "personal_additions",
        "label":       "Personal Additions",
        "description": "Personal instructions appended to the CV generation prompt. Gitignored — not committed to the repo.",
        "personal":    True,
        "system":      False,
    },
    {
        "slug":        "judge_accuracy",
        "label":       "Judge — Accuracy",
        "description": "System prompt for the Tier 2 LLM accuracy judge. Defines what counts as an unsupported claim and the expected response schema.",
        "personal":    False,
        "system":      True,
    },
    {
        "slug":        "retry_system",
        "label":       "Retry — System",
        "description": "System prompt for section-level retries. Controls the retry model's role, constraints, and output expectations.",
        "personal":    False,
        "system":      True,
    },
    {
        "slug":        "retry_sections/profile",
        "label":       "Retry — Profile section",
        "description": "Return format instruction injected into the retry prompt for the profile section.",
        "personal":    False,
        "system":      True,
    },
    {
        "slug":        "retry_sections/experience",
        "label":       "Retry — Experience section",
        "description": "Return format instruction injected into the retry prompt for the experience section.",
        "personal":    False,
        "system":      True,
    },
    {
        "slug":        "retry_sections/skills",
        "label":       "Retry — Skills section",
        "description": "Return format instruction injected into the retry prompt for the skills section.",
        "personal":    False,
        "system":      True,
    },
    {
        "slug":        "retry_sections/education",
        "label":       "Retry — Education section",
        "description": "Return format instruction injected into the retry prompt for the education section.",
        "personal":    False,
        "system":      True,
    },
    {
        "slug":        "retry_sections/certifications",
        "label":       "Retry — Certifications section",
        "description": "Return format instruction injected into the retry prompt for the certifications section.",
        "personal":    False,
        "system":      True,
    },
]

_REGISTRY_BY_SLUG = {p["slug"]: p for p in _REGISTRY}

# ── Signal source mapping ─────────────────────────────────────────────────────

_SIGNAL_SOURCES: dict[str, list[str]] = {
    "cv_generation":                  ["application_notes", "common_flags"],
    "personal_additions":             ["application_notes", "common_flags"],
    "judge_accuracy":                 ["flag_patterns"],
    "retry_system":                   ["retry_comments", "actioned_flags"],
    "retry_sections/profile":         ["actioned_flags_by_section", "retry_comments_by_section"],
    "retry_sections/experience":      ["actioned_flags_by_section", "retry_comments_by_section"],
    "retry_sections/skills":          ["actioned_flags_by_section", "retry_comments_by_section"],
    "retry_sections/education":       ["actioned_flags_by_section", "retry_comments_by_section"],
    "retry_sections/certifications":  ["actioned_flags_by_section", "retry_comments_by_section"],
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slug_to_path(slug: str) -> Path:
    resolved = (_PROMPTS_DIR / f"{slug}.md").resolve()
    if not str(resolved).startswith(str(_PROMPTS_DIR.resolve())):
        raise HTTPException(status_code=400, detail="Invalid prompt slug.")
    return resolved


def _section_from_slug(slug: str) -> Optional[str]:
    """Extract section name from retry_sections/* slugs."""
    prefix = "retry_sections/"
    if slug.startswith(prefix):
        return slug[len(prefix):]
    return None


# ── Signal fetchers ───────────────────────────────────────────────────────────

def _fetch_application_notes(db: sqlite3.Connection) -> list[dict]:
    rows = db.execute(
        """
        SELECT DISTINCT notes, company, role
        FROM applications
        WHERE notes IS NOT NULL AND trim(notes) != ''
        ORDER BY created_at DESC
        LIMIT 8
        """
    ).fetchall()
    return [
        {
            "text":    row["notes"],
            "source":  "application note",
            "context": f"{row['company']} — {row['role']}",
            "count":   1,
        }
        for row in rows
    ]


def _fetch_common_flags(db: sqlite3.Connection) -> list[dict]:
    rows = db.execute(
        """
        SELECT message, type, COUNT(*) as count
        FROM flags
        GROUP BY message
        ORDER BY count DESC
        LIMIT 10
        """
    ).fetchall()
    return [
        {
            "text":    row["message"],
            "source":  f"flag · {row['type'].replace('_', ' ')}",
            "context": None,
            "count":   row["count"],
        }
        for row in rows
    ]


def _fetch_flag_patterns(db: sqlite3.Connection) -> list[dict]:
    rows = db.execute(
        """
        SELECT message, type, COUNT(*) as count
        FROM flags
        WHERE type = 'accuracy'
        GROUP BY message
        ORDER BY count DESC
        LIMIT 10
        """
    ).fetchall()
    if not rows:
        return _fetch_common_flags(db)
    return [
        {
            "text":    row["message"],
            "source":  "accuracy flag",
            "context": None,
            "count":   row["count"],
        }
        for row in rows
    ]


def _fetch_actioned_flags(db: sqlite3.Connection) -> list[dict]:
    rows = db.execute(
        """
        SELECT f.message, f.type, f.user_comment, COUNT(*) as count
        FROM flags f
        WHERE f.status = 'actioned'
        GROUP BY f.message
        ORDER BY count DESC
        LIMIT 10
        """
    ).fetchall()
    results = []
    for row in rows:
        text = row["message"]
        if row["user_comment"]:
            text = f"{row['message']} — reviewer note: {row['user_comment']}"
        results.append({
            "text":    text,
            "source":  f"actioned flag · {row['type'].replace('_', ' ')}",
            "context": None,
            "count":   row["count"],
        })
    return results


def _fetch_actioned_flags_by_section(db: sqlite3.Connection, section_name: str) -> list[dict]:
    rows = db.execute(
        """
        SELECT f.message, f.type, f.user_comment, COUNT(*) as count
        FROM flags f
        JOIN evaluations e ON f.evaluation_id = e.id
        JOIN reports r     ON e.report_id = r.id
        WHERE f.status = 'actioned'
          AND r.section_name = ?
        GROUP BY f.message
        ORDER BY count DESC
        LIMIT 10
        """,
        (section_name,),
    ).fetchall()
    if not rows:
        return _fetch_actioned_flags(db)
    results = []
    for row in rows:
        text = row["message"]
        if row["user_comment"]:
            text = f"{row['message']} — reviewer note: {row['user_comment']}"
        results.append({
            "text":    text,
            "source":  f"actioned flag · {section_name} · {row['type'].replace('_', ' ')}",
            "context": None,
            "count":   row["count"],
        })
    return results


def _fetch_retry_comments(db: sqlite3.Connection) -> list[dict]:
    rows = db.execute(
        """
        SELECT DISTINCT r.global_comment, r.section_name, a.company, a.role
        FROM reports r
        JOIN applications a ON r.application_id = a.id
        WHERE r.global_comment IS NOT NULL
          AND trim(r.global_comment) != ''
        ORDER BY r.created_at DESC
        LIMIT 8
        """
    ).fetchall()
    return [
        {
            "text":    row["global_comment"],
            "source":  f"retry comment · {row['section_name']}",
            "context": f"{row['company']} — {row['role']}",
            "count":   1,
        }
        for row in rows
    ]


def _fetch_retry_comments_by_section(db: sqlite3.Connection, section_name: str) -> list[dict]:
    rows = db.execute(
        """
        SELECT DISTINCT r.global_comment, a.company, a.role
        FROM reports r
        JOIN applications a ON r.application_id = a.id
        WHERE r.global_comment IS NOT NULL
          AND trim(r.global_comment) != ''
          AND r.section_name = ?
        ORDER BY r.created_at DESC
        LIMIT 8
        """,
        (section_name,),
    ).fetchall()
    if not rows:
        return _fetch_retry_comments(db)
    return [
        {
            "text":    row["global_comment"],
            "source":  f"retry comment · {section_name}",
            "context": f"{row['company']} — {row['role']}",
            "count":   1,
        }
        for row in rows
    ]


_FETCHERS = {
    "application_notes":          lambda db, _: _fetch_application_notes(db),
    "common_flags":               lambda db, _: _fetch_common_flags(db),
    "flag_patterns":              lambda db, _: _fetch_flag_patterns(db),
    "actioned_flags":             lambda db, _: _fetch_actioned_flags(db),
    "retry_comments":             lambda db, _: _fetch_retry_comments(db),
    "actioned_flags_by_section":  lambda db, s: _fetch_actioned_flags_by_section(db, s or ""),
    "retry_comments_by_section":  lambda db, s: _fetch_retry_comments_by_section(db, s or ""),
}


# ── Models ────────────────────────────────────────────────────────────────────

class PromptMeta(BaseModel):
    slug:        str
    label:       str
    description: str
    personal:    bool
    system:      bool
    exists:      bool


class PromptContent(BaseModel):
    slug:     str
    label:    str
    content:  str
    personal: bool
    system:   bool


class PromptUpdate(BaseModel):
    content: str


class Signal(BaseModel):
    text:    str
    source:  str
    context: Optional[str] = None
    count:   int = 1


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[PromptMeta])
def list_prompts(db: sqlite3.Connection = Depends(get_db)):
    result = []
    for entry in _REGISTRY:
        path = _slug_to_path(entry["slug"])
        result.append(PromptMeta(
            slug=entry["slug"],
            label=entry["label"],
            description=entry["description"],
            personal=entry["personal"],
            system=entry["system"],
            exists=path.exists(),
        ))
    return result


@router.get("/{name:path}/signals", response_model=list[Signal])
def get_signals(name: str, db: sqlite3.Connection = Depends(get_db)):
    if name not in _REGISTRY_BY_SLUG:
        raise HTTPException(status_code=404, detail=f"Unknown prompt: {name!r}")

    sources     = _SIGNAL_SOURCES.get(name, [])
    section     = _section_from_slug(name)
    signals: list[dict] = []

    for source_key in sources:
        fetcher = _FETCHERS.get(source_key)
        if fetcher:
            try:
                signals.extend(fetcher(db, section))
            except Exception:
                pass

    seen: dict[str, dict] = {}
    for s in signals:
        key = s["text"].strip()
        if key not in seen or s["count"] > seen[key]["count"]:
            seen[key] = s

    return [Signal(**s) for s in seen.values()]


@router.get("/{name:path}", response_model=PromptContent)
def get_prompt(name: str):
    entry = _REGISTRY_BY_SLUG.get(name)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Unknown prompt: {name!r}")

    path    = _slug_to_path(name)
    content = path.read_text(encoding="utf-8") if path.exists() else ""

    return PromptContent(
        slug=name,
        label=entry["label"],
        content=content,
        personal=entry["personal"],
        system=entry["system"],
    )


@router.put("/{name:path}")
def update_prompt(name: str, body: PromptUpdate):
    entry = _REGISTRY_BY_SLUG.get(name)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Unknown prompt: {name!r}")

    path = _slug_to_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body.content, encoding="utf-8")

    return {"status": "saved", "slug": name}
