"""
base.py — the contract every job kind implements, plus the pieces they share.

A job kind is one generative operation (generate_cv, run_coverage, ...). It declares:

  target   what it acts on: nothing yet ('none' — intake kinds create the application),
           an application, or a section report.
  params   the inputs a human or agent may edit while the job is a draft.
  check()  preconditions, re-run at draft creation, on every edit, at submit, and again
           at run time (state can change while a job waits in the queue).
  preview()  the prompt as it would be sent, built without calling a model.
  run()    the work itself. It must not hold a database connection across a model call:
           read, close, call the model, then open a new connection to write. SQLite
           allows one writer at a time, and a write transaction held open for the length
           of a model call locks out every other job and the web UI.

run() receives a JobHandle. Call handle.checkpoint() immediately before the first write,
so a cancel that arrived during the model call discards the output instead of saving it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


class JobError(ValueError):
    """A request that can't be accepted as given: bad params, unmet precondition, wrong status."""


class JobCancelled(Exception):
    """Raised by JobHandle.checkpoint() when a cancel was requested while the job ran."""


# ── Params ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Param:
    type:     type                  # str | bool | list (list means list[str])
    default:  Any  = None
    required: bool = False
    help:     str  = ""

    def describe(self) -> dict:
        return {
            "type":     "list[str]" if self.type is list else self.type.__name__,
            "default":  self.default,
            "required": self.required,
            "help":     self.help,
        }


def normalise_params(spec: dict[str, Param], params: Optional[dict]) -> dict:
    """Apply defaults, reject unknown keys, and type-check. Returns a new dict."""
    params  = dict(params or {})
    unknown = sorted(set(params) - set(spec))
    if unknown:
        raise JobError(f"Unknown param(s): {', '.join(unknown)}. Valid: {', '.join(sorted(spec)) or 'none'}")

    out: dict[str, Any] = {}
    for name, p in spec.items():
        value = params.get(name, p.default)
        if value is None:
            if p.required:
                raise JobError(f"Param {name!r} is required")
            out[name] = None
            continue
        if p.type is list:
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise JobError(f"Param {name!r} must be a list of strings")
        elif p.type is bool:
            if not isinstance(value, bool):
                raise JobError(f"Param {name!r} must be true or false")
        elif not isinstance(value, p.type):
            raise JobError(f"Param {name!r} must be a {p.type.__name__}")
        if p.required and p.type is str and not value.strip():
            raise JobError(f"Param {name!r} cannot be empty")
        out[name] = value
    return out


# ── Context ───────────────────────────────────────────────────────────────────

@dataclass
class JobContext:
    """The resolved target of a job, loaded fresh each time it is needed."""
    application: Optional[dict] = None     # applications row
    report:      Optional[dict] = None     # reports row, for section-level kinds


def load_context(db: sqlite3.Connection, target: str, application_id: Optional[str], target_id: Optional[str]) -> JobContext:
    ctx = JobContext()
    if target == "report":
        row = db.execute("SELECT * FROM reports WHERE id = ?", (target_id,)).fetchone()
        if not row:
            raise JobError(f"No report found with id={target_id!r}")
        ctx.report = dict(row)
        application_id = ctx.report["application_id"]
    if target in ("application", "report"):
        row = db.execute("SELECT * FROM applications WHERE id = ?", (application_id,)).fetchone()
        if not row:
            raise JobError(f"No application found for this job (id={application_id!r})")
        ctx.application = dict(row)
    return ctx


# ── Prompt preview ────────────────────────────────────────────────────────────

@dataclass
class Prompt:
    system: str
    user:   str
    notes:  list[str] = field(default_factory=list)   # caveats about what the preview can't show


def _library_blocks() -> list[tuple[str, str]]:
    """
    The large, unchanging cv-library blocks that most prompts embed verbatim.

    They're the same for every job, so a reviewer gains nothing from reading them and the
    preview would be tens of thousands of characters without eliding them. What's left is
    what actually varies per request: the job ad, spec, notes, flags, previous text.
    """
    from pipeline.config import EXPERIENCE_PATH, META_PATH, SKILLS_PATH
    from pipeline.tailorer import load_experience_files, load_persona_files, load_text

    return [
        ("experience library", load_experience_files()),
        ("experience library", "\n".join(load_text(p) for p in sorted(EXPERIENCE_PATH.glob("*.md"), reverse=True))),
        ("personas", load_persona_files()),
        ("skills inventory", load_text(SKILLS_PATH)),
        ("meta", load_text(META_PATH)),
    ]


def compact(text: str, blocks: Optional[list[tuple[str, str]]] = None) -> str:
    """Replace embedded cv-library blocks with a one-line placeholder."""
    for label, block in blocks if blocks is not None else _library_blocks():
        if len(block) >= 200 and block in text:
            text = text.replace(block, f"[… {label}: {len(block):,} chars of unchanged cv-library content, elided from preview …]")
    return text


def compact_prompt(prompt: Prompt) -> dict:
    blocks = _library_blocks()
    return {
        "system": compact(prompt.system, blocks),
        "user":   compact(prompt.user, blocks),
        "notes":  prompt.notes,
    }


# ── Kind contract ─────────────────────────────────────────────────────────────

class JobHandle:
    """What a running job can see of its own row."""

    def __init__(self, job_id: str, is_cancel_requested: Callable[[], bool], attach_application: Callable[[str], None]):
        self.job_id = job_id
        self._is_cancel_requested = is_cancel_requested
        self._attach_application  = attach_application

    def checkpoint(self) -> None:
        """Call right before the first write. Raises JobCancelled if a cancel came in."""
        if self._is_cancel_requested():
            raise JobCancelled()

    def attach_application(self, application_id: str) -> None:
        """For intake kinds: record the application this job created."""
        self._attach_application(application_id)


class JobKind:
    name:        str
    target:      str                    # none | application | report
    description: str
    params:      dict[str, Param] = {}

    def check(self, ctx: JobContext, params: dict) -> None:
        """Raise JobError if the job can't run against this target right now."""

    def preview(self, ctx: JobContext, params: dict) -> Prompt:
        raise NotImplementedError

    def run(self, ctx: JobContext, params: dict, handle: JobHandle) -> dict:
        raise NotImplementedError

    def describe_params(self) -> dict:
        return {name: p.describe() for name, p in self.params.items()}


REGISTRY: dict[str, JobKind] = {}


def register(kind_cls: type[JobKind]) -> type[JobKind]:
    kind = kind_cls()
    REGISTRY[kind.name] = kind
    return kind_cls


def get_kind(name: str) -> JobKind:
    try:
        return REGISTRY[name]
    except KeyError:
        raise JobError(f"Unknown job kind {name!r}. Valid: {', '.join(sorted(REGISTRY))}")
