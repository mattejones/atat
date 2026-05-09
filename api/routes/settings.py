"""
settings.py — Read and write ATAT configuration settings.

Reads from the active environment (via pipeline.config) and writes
changes back to the .env file in the repo root.

Security: CORS is locked to localhost, so these endpoints are
only reachable from the local UI. Never expose this API publicly.
"""

import os
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/settings", tags=["settings"])

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_ENV_FILE  = _REPO_ROOT / ".env"

# Sentinel pattern for masked keys — never write these to disk
_MASK_PATTERN = re.compile(r'^.{2,8}•+.{2,8}$')

# Fields that are locked in demo mode — overridden by convention, not .env
_DEMO_LOCKED_FIELDS = {"cv_library_path", "output_path"}


# ── Models ────────────────────────────────────────────────────────────────────

class Settings(BaseModel):
    llm_provider:             str
    llm_model:                str
    anthropic_api_key:        Optional[str] = None
    openai_api_key:           Optional[str] = None
    cv_library_path:          str
    output_path:              str
    thinking_budget:          int
    render_pdf:               bool
    temperature:              float
    demo_mode:                bool = False
    auto_ghost_enabled:       bool
    auto_ghost_days:          int
    scheduler_interval_hours: int


class SettingsUpdate(BaseModel):
    llm_provider:             Optional[str]   = None
    llm_model:                Optional[str]   = None
    anthropic_api_key:        Optional[str]   = None
    openai_api_key:           Optional[str]   = None
    cv_library_path:          Optional[str]   = None
    output_path:              Optional[str]   = None
    thinking_budget:          Optional[int]   = None
    render_pdf:               Optional[bool]  = None
    temperature:              Optional[float] = None
    auto_ghost_enabled:       Optional[bool]  = None
    auto_ghost_days:          Optional[int]   = None
    scheduler_interval_hours: Optional[int]   = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mask(value: str | None) -> str | None:
    if not value or len(value) < 8:
        return None
    return value[:4] + "••••••••" + value[-4:]


def _is_masked(value: str) -> bool:
    """Return True if the value looks like a masked key — never write these."""
    return bool(_MASK_PATTERN.search(value))


def _write_env_value(key: str, value: str) -> None:
    if not _ENV_FILE.exists():
        _ENV_FILE.write_text(f"{key}={value}\n", encoding="utf-8")
        return

    lines    = _ENV_FILE.read_text(encoding="utf-8").splitlines(keepends=True)
    pattern  = re.compile(rf"^{re.escape(key)}\s*=.*", re.IGNORECASE)
    replaced = False
    new_lines = []

    for line in lines:
        if pattern.match(line):
            new_lines.append(f"{key}={value}\n")
            replaced = True
        else:
            new_lines.append(line)

    if not replaced:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines.append("\n")
        new_lines.append(f"{key}={value}\n")

    _ENV_FILE.write_text("".join(new_lines), encoding="utf-8")


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("", response_model=Settings)
def get_settings():
    from pipeline.config import (
        LLM_PROVIDER, LLM_MODEL, CV_LIBRARY_PATH, OUTPUT_PATH,
        THINKING_BUDGET, RENDER_PDF, TEMPERATURE,
        ANTHROPIC_API_KEY, OPENAI_API_KEY, DEMO_MODE,
        AUTO_GHOST_ENABLED, AUTO_GHOST_DAYS, SCHEDULER_INTERVAL_HOURS,
    )
    return Settings(
        llm_provider             = LLM_PROVIDER,
        llm_model                = LLM_MODEL,
        anthropic_api_key        = _mask(ANTHROPIC_API_KEY),
        openai_api_key           = _mask(OPENAI_API_KEY),
        cv_library_path          = str(CV_LIBRARY_PATH),
        output_path              = str(OUTPUT_PATH),
        thinking_budget          = THINKING_BUDGET,
        render_pdf               = RENDER_PDF,
        temperature              = TEMPERATURE,
        demo_mode                = DEMO_MODE,
        auto_ghost_enabled       = AUTO_GHOST_ENABLED,
        auto_ghost_days          = AUTO_GHOST_DAYS,
        scheduler_interval_hours = SCHEDULER_INTERVAL_HOURS,
    )


@router.patch("")
def update_settings(body: SettingsUpdate):
    from pipeline.config import DEMO_MODE

    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    # In demo mode, path fields are convention-based and cannot be overridden via settings
    if DEMO_MODE:
        locked = _DEMO_LOCKED_FIELDS.intersection(updates.keys())
        if locked:
            raise HTTPException(
                status_code=403,
                detail=f"Path settings are locked in demo mode: {', '.join(sorted(locked))}",
            )

    key_map = {
        "llm_provider":             "LLM_PROVIDER",
        "llm_model":                "LLM_MODEL",
        "anthropic_api_key":        "ANTHROPIC_API_KEY",
        "openai_api_key":           "OPENAI_API_KEY",
        "cv_library_path":          "CV_LIBRARY_PATH",
        "output_path":              "OUTPUT_PATH",
        "thinking_budget":          "THINKING_BUDGET",
        "render_pdf":               "RENDER_PDF",
        "temperature":              "TEMPERATURE",
        "auto_ghost_enabled":       "AUTO_GHOST_ENABLED",
        "auto_ghost_days":          "AUTO_GHOST_DAYS",
        "scheduler_interval_hours": "SCHEDULER_INTERVAL_HOURS",
    }

    # API key fields — skip if empty or masked
    key_fields = {"anthropic_api_key", "openai_api_key"}

    written = []
    skipped = []

    for field, value in updates.items():
        env_key = key_map.get(field)
        if not env_key:
            continue

        # Never write masked or empty key values back to disk
        if field in key_fields:
            str_value = str(value).strip()
            if not str_value or _is_masked(str_value):
                skipped.append(env_key)
                continue

        if isinstance(value, bool):
            value = "true" if value else "false"

        _write_env_value(env_key, str(value))
        written.append(env_key)

    return {
        "status":  "saved",
        "updated": written,
        "skipped": skipped,
        "note":    "Restart the API server for changes to take effect.",
    }
