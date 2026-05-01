"""
settings.py — Read and write ATAT configuration settings.

Reads from the active environment (via pipeline.config) and writes
changes back to the .env file in the repo root.

Security: CORS is locked to localhost:3000, so these endpoints are
only reachable from the local UI. Never expose this API publicly.
"""

import os
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/settings", tags=["settings"])

# Repo root — .env lives here
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_ENV_FILE  = _REPO_ROOT / ".env"


# ── Models ────────────────────────────────────────────────────────────────────

class Settings(BaseModel):
    llm_provider:     str
    llm_model:        str
    anthropic_api_key: Optional[str] = None   # masked on read
    openai_api_key:    Optional[str] = None   # masked on read
    cv_library_path:  str
    output_path:      str
    thinking_budget:  int
    render_pdf:       bool
    temperature:      float


class SettingsUpdate(BaseModel):
    llm_provider:      Optional[str]   = None
    llm_model:         Optional[str]   = None
    anthropic_api_key: Optional[str]   = None
    openai_api_key:    Optional[str]   = None
    cv_library_path:   Optional[str]   = None
    output_path:       Optional[str]   = None
    thinking_budget:   Optional[int]   = None
    render_pdf:        Optional[bool]  = None
    temperature:       Optional[float] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mask(value: str | None) -> str | None:
    """Return a masked version of a sensitive value for display."""
    if not value or len(value) < 8:
        return None
    return value[:4] + "••••••••" + value[-4:]


def _read_env_file() -> dict[str, str]:
    """Parse the .env file into a key-value dict."""
    if not _ENV_FILE.exists():
        return {}
    result = {}
    for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def _write_env_value(key: str, value: str) -> None:
    """
    Update or append a single key in the .env file.
    Preserves comments, blank lines, and ordering.
    """
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
        # Append at end
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines.append("\n")
        new_lines.append(f"{key}={value}\n")

    _ENV_FILE.write_text("".join(new_lines), encoding="utf-8")


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("", response_model=Settings)
def get_settings():
    """Return current settings. API keys are masked."""
    from pipeline.config import (
        LLM_PROVIDER, LLM_MODEL, CV_LIBRARY_PATH, OUTPUT_PATH,
        THINKING_BUDGET, RENDER_PDF, TEMPERATURE,
        ANTHROPIC_API_KEY, OPENAI_API_KEY,
    )
    return Settings(
        llm_provider      = LLM_PROVIDER,
        llm_model         = LLM_MODEL,
        anthropic_api_key = _mask(ANTHROPIC_API_KEY),
        openai_api_key    = _mask(OPENAI_API_KEY),
        cv_library_path   = str(CV_LIBRARY_PATH),
        output_path       = str(OUTPUT_PATH),
        thinking_budget   = THINKING_BUDGET,
        render_pdf        = RENDER_PDF,
        temperature       = TEMPERATURE,
    )


@router.patch("")
def update_settings(body: SettingsUpdate):
    """
    Write updated values to the .env file.
    Requires a server restart to take full effect on in-process config.
    """
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Map model fields to .env key names
    key_map = {
        "llm_provider":      "LLM_PROVIDER",
        "llm_model":         "LLM_MODEL",
        "anthropic_api_key": "ANTHROPIC_API_KEY",
        "openai_api_key":    "OPENAI_API_KEY",
        "cv_library_path":   "CV_LIBRARY_PATH",
        "output_path":       "OUTPUT_PATH",
        "thinking_budget":   "THINKING_BUDGET",
        "render_pdf":        "RENDER_PDF",
        "temperature":       "TEMPERATURE",
    }

    written = []
    for field, value in updates.items():
        env_key = key_map.get(field)
        if not env_key:
            continue
        # Serialise booleans the way python-dotenv expects them
        if isinstance(value, bool):
            value = "true" if value else "false"
        _write_env_value(env_key, str(value))
        written.append(env_key)

    return {
        "status":  "saved",
        "updated": written,
        "note":    "Restart the API server for changes to take effect.",
    }
