import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Repo root — anchored to this file's location, not the working directory ───
REPO_ROOT = Path(__file__).resolve().parent.parent

# ── Paths ─────────────────────────────────────────────────────────────────────
CV_LIBRARY_PATH = Path(os.getenv("CV_LIBRARY_PATH", str(REPO_ROOT / "../cv-library"))).resolve()
JDS_PATH        = Path(os.getenv("JDS_PATH",        str(REPO_ROOT / "jds"))).resolve()
OUTPUT_PATH     = Path(os.getenv("OUTPUT_PATH",     str(REPO_ROOT / "output"))).resolve()
PROMPTS_PATH    = REPO_ROOT / "prompts"

# Derived library paths — never hardcoded elsewhere
EXPERIENCE_PATH = CV_LIBRARY_PATH / "experience"
PERSONAS_PATH   = CV_LIBRARY_PATH / "personas"
SKILLS_PATH     = CV_LIBRARY_PATH / "skills" / "skills.md"
META_PATH       = CV_LIBRARY_PATH / "meta" / "meta.md"

# ── LLM ───────────────────────────────────────────────────────────────────────
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")
LLM_MODEL    = os.getenv("LLM_MODEL",    "claude-sonnet-4-6")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY")

# ── Model parameters ──────────────────────────────────────────────────────────
# Temperature: lower = more factual, less hallucinatory.
# Ignored when THINKING_BUDGET > 0 (Anthropic requires temperature=1 for thinking).
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.3"))

# Max output tokens for the CV itself (not including thinking budget).
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "8192"))

# Extended thinking budget in tokens (Anthropic only).
# Gives the model dedicated reasoning capacity before producing output.
# Set to 0 to disable. Recommended range: 5000–10000.
THINKING_BUDGET = int(os.getenv("THINKING_BUDGET", "8000"))

# Prompt caching: cache the stable system prompt to reduce cost and latency.
# Up to 90% cost reduction on cached tokens on subsequent runs.
ENABLE_CACHING = os.getenv("ENABLE_CACHING", "true").lower() == "true"

# ── Rendering ─────────────────────────────────────────────────────────────────
# When true, automatically render cv.md → cv.pdf via Typst after generation.
# Requires the 'typst' Python package (pip install typst).
# Set to false to produce Markdown only — useful when you want to review
# and edit the CV content before committing to a PDF.
RENDER_PDF = os.getenv("RENDER_PDF", "true").lower() == "true"

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
