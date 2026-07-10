"""
tools_prompt_tuning.py — durable, cross-application generation guidance.

Two different mechanisms, don't confuse them:

- generation_notes (submit_job) is per-application and one-shot — good for
  "this specific role wants X emphasized," useless for "stop doing X ever
  again," since it has to be re-typed every single call.
- personal_additions.md (this module) is loaded and appended to the system
  prompt on every submit_job generation (pipeline.tailorer.build_system_prompt)
  — the actual fix for a recurring mistake is one line added here, once,
  not the same correction re-typed into generation_notes forever. Gitignored,
  personal to this machine, already documented for exactly this ("DO NOT
  INCLUDE rules", "style guidance") — it just had no MCP access before now.

Note: regenerate_section (the retry path, pipeline.retry) loads a different,
narrower system prompt and does NOT include personal_additions.md — a rule
added here affects the next fresh submit_job call, not a retry already in
progress. Deliberately left that way; not an MCP-layer concern.
"""

from datetime import datetime

from mcp_server.app import mcp
from pipeline.config import PROMPTS_PATH

_ADDITIONS_PATH = PROMPTS_PATH / "personal_additions.md"

_TEMPLATE = """\
# Personal Prompt Additions
#
# This file is gitignored. It is loaded at runtime and appended to the
# system prompt. Use it for personal preferences, additional constraints,
# or style guidance that should not be committed to the public repo.

## Additional rules

"""


@mcp.tool()
def get_personal_additions() -> dict:
    """
    Return the current contents of personal_additions.md — the standing
    rules appended to every submit_job generation's system prompt. Empty
    `content` with `exists=False` means no rules have been added yet.
    """
    if not _ADDITIONS_PATH.exists():
        return {"content": "", "exists": False}
    return {"content": _ADDITIONS_PATH.read_text(encoding="utf-8"), "exists": True}


@mcp.tool()
def add_personal_rule(rule: str) -> dict:
    """
    Permanently add one rule to personal_additions.md, so it applies to
    every future generation — not just the CV in front of you right now.

    Use this when you notice yourself correcting the *same* thing across
    multiple CVs (a phrase to avoid, a formatting habit, a framing that
    keeps coming out wrong) rather than fixing it again in generation_notes.
    Creates the file from the standard template if it doesn't exist yet.
    Appends — doesn't rewrite existing rules. Call get_personal_additions()
    first if you want to review/dedupe before adding.

    rule: one rule, written as a direct instruction, e.g. "Never use the
    phrase 'passionate about' — it reads as hollow" or "Lead experience
    bullets with the outcome, not the activity."
    """
    if not rule.strip():
        raise ValueError("rule cannot be empty")

    _ADDITIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not _ADDITIONS_PATH.exists():
        _ADDITIONS_PATH.write_text(_TEMPLATE, encoding="utf-8")

    existing = _ADDITIONS_PATH.read_text(encoding="utf-8")
    stamp = datetime.now().strftime("%Y-%m-%d")
    addition = f"- [{stamp}] {rule.strip()}\n"
    _ADDITIONS_PATH.write_text(existing.rstrip("\n") + "\n" + addition, encoding="utf-8")

    return {"status": "added", "rule": rule.strip()}


@mcp.tool()
def update_personal_additions(content: str) -> dict:
    """
    Overwrite personal_additions.md entirely. Use for bulk edits (removing
    a stale rule, reorganizing) — for adding a single new rule, prefer
    add_personal_rule, which appends without touching existing content.
    """
    _ADDITIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ADDITIONS_PATH.write_text(content, encoding="utf-8")
    return {"status": "saved"}
