"""
settings.py — Read and write ATAT configuration settings.

Reads from the active environment (via pipeline.config) and writes
changes back to this instance's .env file (ATAT_HOME/.env).

Security: protected by the access password (api/auth.py) when ATAT_PASSWORD is set; without
one, only run the API on localhost. Set ATAT_LOCK_ADVANCED=true on an instance used by
someone non-technical to make the provider, keys, models and paths read-only here.
"""

import os
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from pipeline.config import ATAT_HOME, ATAT_LOCK_ADVANCED

router = APIRouter(prefix="/settings", tags=["settings"])

_ENV_FILE = ATAT_HOME / ".env"

# Sentinel pattern for masked keys — never write these to disk
_MASK_PATTERN = re.compile(r'^.{2,8}•+.{2,8}$')

# Fields that are locked in demo mode — overridden by convention, not .env
_DEMO_LOCKED_FIELDS = {"cv_library_path", "output_path"}

# Fields a locked instance refuses to change: the AI setup, secrets and file locations.
# Everyday behaviour (ghosting, Gmail schedule, targets) stays editable.
_ADVANCED_FIELDS = {
    "llm_provider", "llm_model", "judge_model", "retry_model",
    "anthropic_api_key", "openai_api_key", "cv_library_path", "output_path",
    "thinking_budget", "render_pdf", "temperature",
}


# ── Models ────────────────────────────────────────────────────────────────────

class Settings(BaseModel):
    llm_provider:             str
    llm_model:                str
    judge_model:              str
    retry_model:              str
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
    gmail_enabled:               bool = False
    gmail_sync_interval_minutes: int  = 30
    inbox_classifier:            str  = "api"
    auto_ghost_use_window:       bool  = False
    weekly_screen_target:        float = 0
    advanced_locked:             bool  = False


class SettingsUpdate(BaseModel):
    llm_provider:             Optional[str]   = None
    llm_model:                Optional[str]   = None
    judge_model:              Optional[str]   = None
    retry_model:              Optional[str]   = None
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
    gmail_enabled:               Optional[bool] = None
    gmail_sync_interval_minutes: Optional[int]  = None
    inbox_classifier:            Optional[str]  = None
    auto_ghost_use_window:       Optional[bool]  = None
    weekly_screen_target:        Optional[float] = None


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
        LLM_PROVIDER, LLM_MODEL, JUDGE_MODEL, RETRY_MODEL,
        CV_LIBRARY_PATH, OUTPUT_PATH,
        THINKING_BUDGET, RENDER_PDF, TEMPERATURE,
        ANTHROPIC_API_KEY, OPENAI_API_KEY, DEMO_MODE,
        AUTO_GHOST_ENABLED, AUTO_GHOST_DAYS, SCHEDULER_INTERVAL_HOURS,
        GMAIL_ENABLED, GMAIL_SYNC_INTERVAL_MINUTES, INBOX_CLASSIFIER,
        AUTO_GHOST_USE_WINDOW, WEEKLY_SCREEN_TARGET,
    )
    return Settings(
        llm_provider             = LLM_PROVIDER,
        llm_model                = LLM_MODEL,
        judge_model              = JUDGE_MODEL,
        retry_model              = RETRY_MODEL,
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
        gmail_enabled               = GMAIL_ENABLED,
        gmail_sync_interval_minutes = GMAIL_SYNC_INTERVAL_MINUTES,
        inbox_classifier            = INBOX_CLASSIFIER,
        auto_ghost_use_window       = AUTO_GHOST_USE_WINDOW,
        weekly_screen_target        = WEEKLY_SCREEN_TARGET,
        advanced_locked             = ATAT_LOCK_ADVANCED,
    )


@router.patch("")
def update_settings(body: SettingsUpdate):
    from pipeline.config import DEMO_MODE

    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    if updates.get("weekly_screen_target") is not None and updates["weekly_screen_target"] < 0:
        raise HTTPException(status_code=422, detail="weekly_screen_target can't be negative")
    if updates.get("inbox_classifier") not in (None, "api", "agent"):
        raise HTTPException(status_code=422, detail="inbox_classifier must be 'api' or 'agent'")

    if ATAT_LOCK_ADVANCED:
        locked = _ADVANCED_FIELDS.intersection(updates.keys())
        if locked:
            raise HTTPException(
                status_code=403,
                detail=f"These settings are locked on this instance: {', '.join(sorted(locked))}",
            )

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
        "judge_model":              "JUDGE_MODEL",
        "retry_model":              "RETRY_MODEL",
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
        "gmail_enabled":               "GMAIL_ENABLED",
        "gmail_sync_interval_minutes": "GMAIL_SYNC_INTERVAL_MINUTES",
        "inbox_classifier":            "INBOX_CLASSIFIER",
        "auto_ghost_use_window":       "AUTO_GHOST_USE_WINDOW",
        "weekly_screen_target":        "WEEKLY_SCREEN_TARGET",
    }

    key_fields = {"anthropic_api_key", "openai_api_key"}

    written = []
    skipped = []

    for field, value in updates.items():
        env_key = key_map.get(field)
        if not env_key:
            continue

        if field in key_fields:
            str_value = str(value).strip()
            if not str_value or _is_masked(str_value):
                skipped.append(env_key)
                continue

        if isinstance(value, bool):
            value = "true" if value else "false"

        try:
            _write_env_value(env_key, str(value))
        except OSError as e:   # e.g. .env mounted read-only in a container
            raise HTTPException(
                status_code=500,
                detail=f"Couldn't save settings — the settings file isn't writable ({e.strerror or e}).",
            )
        written.append(env_key)

    return {
        "status":  "saved",
        "updated": written,
        "skipped": skipped,
        "note":    "Restart the API server for changes to take effect.",
    }
