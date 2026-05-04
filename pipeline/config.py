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

# ── Database ───────────────────────────────────────────────────────────────────
# SQLite — embedded, no server required.
# Override in .env to point to a different location (e.g. on VPS).
DB_PATH = os.getenv("DB_PATH", str(REPO_ROOT / "data" / "atat.db"))

# ── LLM ───────────────────────────────────────────────────────────────────────
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")
LLM_MODEL    = os.getenv("LLM_MODEL",    "claude-sonnet-4-6")

# Judge model — cheaper/faster model used for Tier 2 accuracy checks.
# Defaults to Haiku for Anthropic, gpt-4o-mini for OpenAI.
JUDGE_MODEL = os.getenv(
    "JUDGE_MODEL",
    "claude-haiku-4-5-20251001" if os.getenv("LLM_PROVIDER", "anthropic") == "anthropic"
    else "gpt-4o-mini",
)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY")

# ── Model parameters ──────────────────────────────────────────────────────────
TEMPERATURE       = float(os.getenv("TEMPERATURE",       "0.3"))
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS",   "8192"))
THINKING_BUDGET   = int(os.getenv("THINKING_BUDGET",     "8000"))
ENABLE_CACHING    = os.getenv("ENABLE_CACHING",  "true").lower() == "true"

# ── Rendering ─────────────────────────────────────────────────────────────────
RENDER_PDF = os.getenv("RENDER_PDF", "true").lower() == "true"

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
