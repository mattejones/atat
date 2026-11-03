import os
from pathlib import Path
from dotenv import load_dotenv

# ── Repo root — anchored to this file's location, not the working directory ───
REPO_ROOT = Path(__file__).resolve().parent.parent

# ── Instance home ─────────────────────────────────────────────────────────────
# Everything that belongs to ONE person — database, generated CVs, logs, Gmail token,
# personal prompt files and their .env — lives under ATAT_HOME. Unset, it is the repo root
# and nothing changes. Run a second instance (e.g. for a partner) by giving it its own
# ATAT_HOME; the code, tracked prompts and fonts are shared, the data is not.
#
# ATAT_HOME must be set in the real environment (docker-compose does). When it is, ONLY
# ATAT_HOME/.env is read: a repo-root .env belongs to the default instance, and letting it
# leak in (it usually pins DB_PATH and friends) would point this instance at someone
# else's data.
_HOME_SET = bool(os.getenv("ATAT_HOME"))
ATAT_HOME = Path(os.getenv("ATAT_HOME") or REPO_ROOT).resolve()
if _HOME_SET:
    load_dotenv(ATAT_HOME / ".env")   # real environment variables still win
else:
    load_dotenv()

# Personal prompt files (personal_additions.md, cover_letter_personal.md) are read from here.
# A fresh instance has none, so it never inherits someone else's rules.
PERSONAL_PROMPTS_PATH = ATAT_HOME / "prompts"
LOGS_PATH             = ATAT_HOME / "logs"          # failure dumps — can contain CV and job-ad text
FONTS_PATH            = Path(os.getenv("FONTS_PATH", str(REPO_ROOT / "fonts"))).resolve()

# ── Demo mode ─────────────────────────────────────────────────────────────────
# When True, all path-sensitive config is overridden to point at ./demo/.
# Set DEMO_MODE=true in .env to activate. See ./demo/ for seed data and cv-library.
DEMO_MODE = os.getenv("DEMO_MODE", "false").lower() == "true"

# ── Paths ─────────────────────────────────────────────────────────────────────
if DEMO_MODE:
    # Convention-based: always relative to repo root, no additional config required.
    CV_LIBRARY_PATH = (REPO_ROOT / "demo" / "cv-library").resolve()
    DB_PATH         = str((REPO_ROOT / "demo" / "atat.db").resolve())
    OUTPUT_PATH     = (REPO_ROOT / "demo" / "output").resolve()
else:
    _default_library = ATAT_HOME / "cv-library" if _HOME_SET else REPO_ROOT / "../cv-library"
    CV_LIBRARY_PATH = Path(os.getenv("CV_LIBRARY_PATH", str(_default_library))).resolve()
    DB_PATH         = os.getenv("DB_PATH", str(ATAT_HOME / "data" / "atat.db"))
    OUTPUT_PATH     = Path(os.getenv("OUTPUT_PATH", str(ATAT_HOME / "output"))).resolve()

JDS_PATH     = Path(os.getenv("JDS_PATH", str(ATAT_HOME / "jds"))).resolve()
PROMPTS_PATH = REPO_ROOT / "prompts"

# Derived library paths — never hardcoded elsewhere
EXPERIENCE_PATH = CV_LIBRARY_PATH / "experience"
PERSONAS_PATH   = CV_LIBRARY_PATH / "personas"
SKILLS_PATH     = CV_LIBRARY_PATH / "skills" / "skills.md"
META_PATH       = CV_LIBRARY_PATH / "meta" / "meta.md"

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

# Retry model — used for section-level rewrites during human-in-the-loop review.
# Intentionally cheaper than LLM_MODEL: retries are targeted edits with explicit
# constraints, not full generation. No thinking budget applied.
# Defaults to same as JUDGE_MODEL — override to use a mid-tier model if needed.
RETRY_MODEL = os.getenv("RETRY_MODEL", os.getenv(
    "JUDGE_MODEL",
    "claude-haiku-4-5-20251001" if os.getenv("LLM_PROVIDER", "anthropic") == "anthropic"
    else "gpt-4o-mini",
))

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY")

# ── Model parameters ──────────────────────────────────────────────────────────
TEMPERATURE       = float(os.getenv("TEMPERATURE",       "0.3"))
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS",   "8192"))
THINKING_BUDGET   = int(os.getenv("THINKING_BUDGET",     "8000"))
ENABLE_CACHING    = os.getenv("ENABLE_CACHING",  "true").lower() == "true"

# ── CV shape (per instance) ───────────────────────────────────────────────────
# PROFILE_PARAGRAPHS / PROFILE_WORDS_MIN / PROFILE_WORDS_MAX — the profile's shape. It is written
#   into the generation prompt and checked by the deterministic judge. Default: two paragraphs,
#   80-120 words in total.
# EARLIER_EXPERIENCE_HEADING — heading over the compressed pre-main-roles paragraph.
PROFILE_PARAGRAPHS         = int(os.getenv("PROFILE_PARAGRAPHS", "2"))
PROFILE_WORDS_MIN          = int(os.getenv("PROFILE_WORDS_MIN",  "80"))
PROFILE_WORDS_MAX          = int(os.getenv("PROFILE_WORDS_MAX",  "120"))
EARLIER_EXPERIENCE_HEADING = os.getenv("EARLIER_EXPERIENCE_HEADING", "Earlier experience").strip() or "Earlier experience"

# ── Rendering ─────────────────────────────────────────────────────────────────
RENDER_PDF = os.getenv("RENDER_PDF", "true").lower() == "true"
# AUTO_JUDGE — run the judges (tiers 1-3) on a CV as soon as it is generated, so a CV is never left
#   unjudged waiting for someone to remember. Costs a few extra cheap model calls per CV.
AUTO_JUDGE = os.getenv("AUTO_JUDGE", "true").lower() == "true"
# HEADING_RULE  — the rule beside each section heading in the PDF: none | short | full.
#   "full" (a line across the page, beside the title) is the original look.
# LINK_UNDERLINE — underline links in the PDF (colour always marks them).
HEADING_RULE   = os.getenv("HEADING_RULE", "full").strip().lower()
if HEADING_RULE not in ("none", "short", "full"):
    HEADING_RULE = "full"
LINK_UNDERLINE = os.getenv("LINK_UNDERLINE", "true").lower() == "true"

# ── Scheduler / auto-ghost ────────────────────────────────────────────────────
# AUTO_GHOST_ENABLED   — whether the background scheduler runs at all
# AUTO_GHOST_DAYS      — days of inactivity before an application is ghosted
# SCHEDULER_INTERVAL_HOURS — how often the ghost check runs
AUTO_GHOST_ENABLED       = os.getenv("AUTO_GHOST_ENABLED",       "true").lower() == "true"
AUTO_GHOST_DAYS          = int(os.getenv("AUTO_GHOST_DAYS",          "21"))
SCHEDULER_INTERVAL_HOURS = int(os.getenv("SCHEDULER_INTERVAL_HOURS", "6"))
# AUTO_GHOST_USE_WINDOW — use the response window learned from your own data (the day by
# which 90% of first answers arrive) instead of AUTO_GHOST_DAYS
AUTO_GHOST_USE_WINDOW    = os.getenv("AUTO_GHOST_USE_WINDOW", "false").lower() == "true"

# ── Insights ──────────────────────────────────────────────────────────────────
# WEEKLY_SCREEN_TARGET — screens a week you're aiming for; the forecast works out how many
# applications a week that takes at your current screen rate. Unset or 0 = no target.
WEEKLY_SCREEN_TARGET     = float(os.getenv("WEEKLY_SCREEN_TARGET", "0") or 0)

# ── Gmail inbox sync ──────────────────────────────────────────────────────────
# Read-only (gmail.readonly). Connect once with `python -m pipeline.inbox.auth`.
# GMAIL_ENABLED               — whether the scheduler polls Gmail at all (manual sync works regardless)
# GMAIL_SYNC_INTERVAL_MINUTES — how often the scheduled sync runs
# GMAIL_MAX_PER_SYNC          — cap on messages fetched per run; the rest wait for the next one
# GMAIL_MAX_LOOKBACK_DAYS     — never read mail older than this, on the first sync or after a long gap
# INBOX_MODEL                 — model for triage/classification; a cheap model is plenty
# INBOX_CLASSIFIER            — api: classify with INBOX_MODEL during the sync;
#                               agent: the sync only gathers mail, an AI agent classifies it over MCP
GMAIL_ENABLED               = os.getenv("GMAIL_ENABLED", "false").lower() == "true"
GMAIL_SYNC_INTERVAL_MINUTES = int(os.getenv("GMAIL_SYNC_INTERVAL_MINUTES", "30"))
# Relative paths are taken from ATAT_HOME (the repo root unless set), so the API, MCP server
# and CLI agree.
GMAIL_CREDENTIALS_PATH      = (ATAT_HOME / os.getenv("GMAIL_CREDENTIALS_PATH", "data/gmail/client_secret.json")).resolve()
GMAIL_TOKEN_PATH            = (ATAT_HOME / os.getenv("GMAIL_TOKEN_PATH",       "data/gmail/token.json")).resolve()
GMAIL_MAX_PER_SYNC          = int(os.getenv("GMAIL_MAX_PER_SYNC", "200"))
GMAIL_MAX_LOOKBACK_DAYS     = int(os.getenv("GMAIL_MAX_LOOKBACK_DAYS", "90"))
INBOX_MODEL                 = os.getenv("INBOX_MODEL", JUDGE_MODEL)
INBOX_CLASSIFIER            = os.getenv("INBOX_CLASSIFIER", "api").lower()

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ── Access control (web/API) ──────────────────────────────────────────────────
# ATAT_PASSWORD  — when set, the API and web UI require this password (see api/auth.py).
#                  Unset = no login, which is only appropriate on localhost.
# ATAT_LOCK_ADVANCED — when true, the AI provider/keys/models/paths settings and the prompt
#                  editor are read-only over the API, so a non-technical user can't break the
#                  setup. Whoever runs the instance changes them in the instance's .env.
ATAT_PASSWORD      = os.getenv("ATAT_PASSWORD", "")
ATAT_LOCK_ADVANCED = os.getenv("ATAT_LOCK_ADVANCED", "false").lower() == "true"
