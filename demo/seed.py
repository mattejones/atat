"""
demo/seed.py — Generate a seeded demo database for ATAT.

Runs migrations against demo/atat.db, then inserts a realistic set of
fictional applications for the Jordan Blake demo persona.

Usage (from repo root, with venv active):
    python -m demo.seed

The resulting demo/atat.db is committed to git. Re-run this script any
time the schema changes or you want to reset the demo data.
"""

import sqlite3
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

# Ensure repo root is on the path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

DEMO_DB = REPO_ROOT / "demo" / "atat.db"


# ── Helpers ───────────────────────────────────────────────────────────────────

def uid() -> str:
    return str(uuid.uuid4())


def ts(days_ago: int = 0, hours_ago: int = 0) -> str:
    dt = datetime.utcnow() - timedelta(days=days_ago, hours=hours_ago)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def date(days_ago: int = 0) -> str:
    dt = datetime.utcnow() - timedelta(days=days_ago)
    return dt.strftime("%Y-%m-%d")


# ── Seed data ─────────────────────────────────────────────────────────────────

APPLICATIONS = [
    {
        "id":               "2025-12-03_luminary-health_head-of-sales-ops",
        "company":          "Luminary Health",
        "role":             "Head of Sales Operations",
        "tier":             "T1",
        "status":           "offered",
        "persona":          "sales-ops-leader",
        "location":         "London, UK",
        "work_arrangement": "hybrid",
        "hybrid_days":      2,
        "salary_min":       95000,
        "salary_max":       115000,
        "salary_currency":  "GBP",
        "source_url":       "https://example.com/jobs/luminary-health-sales-ops",
        "model":            "claude-sonnet-4-6",
        "provider":         "anthropic",
        "created_at":       ts(days_ago=35),
        "notes":            "Strong fit — Series B health tech, 200 employees. Great culture signals.",
        "jd_text": (
            "Luminary Health is seeking a Head of Sales Operations to build and lead our Sales Ops "
            "function as we scale from £8M to £25M ARR. You will own sales planning, forecasting, "
            "quota design, and cross-functional alignment across Sales, Marketing, and Customer Success. "
            "This is a senior IC-to-manager role with budget ownership and board-level reporting."
        ),
    },
    {
        "id":               "2025-12-10_stackpath_sales-operations-manager",
        "company":          "Stackpath",
        "role":             "Sales Operations Manager",
        "tier":             "T1",
        "status":           "interviewing",
        "persona":          "sales-ops-leader",
        "location":         "Remote (UK)",
        "work_arrangement": "remote",
        "hybrid_days":      None,
        "salary_min":       80000,
        "salary_max":       95000,
        "salary_currency":  "GBP",
        "source_url":       "https://example.com/jobs/stackpath-sales-ops",
        "model":            "claude-sonnet-4-6",
        "provider":         "anthropic",
        "created_at":       ts(days_ago=28),
        "notes":            "Second round scheduled for next Tuesday. Prepare for deep-dive on quota design.",
        "jd_text": (
            "Stackpath is hiring a Sales Operations Manager to support our VP of Sales and 30-person "
            "commercial team. You will own pipeline reporting, forecasting, territory design, and "
            "process improvement across three market segments."
        ),
    },
    {
        "id":               "2025-12-15_orion-analytics_regional-sales-manager",
        "company":          "Orion Analytics",
        "role":             "Regional Sales Manager",
        "tier":             "T1",
        "status":           "applied",
        "persona":          "sales-manager",
        "location":         "Manchester, UK",
        "work_arrangement": "hybrid",
        "hybrid_days":      3,
        "salary_min":       75000,
        "salary_max":       90000,
        "salary_currency":  "GBP",
        "source_url":       "https://example.com/jobs/orion-analytics-rsm",
        "model":            "claude-sonnet-4-6",
        "provider":         "anthropic",
        "created_at":       ts(days_ago=21),
        "notes":            "Applied via LinkedIn. Awaiting response.",
        "jd_text": (
            "Orion Analytics is looking for a Regional Sales Manager to lead our Northern UK commercial "
            "team of 8 AEs. You will own team quota, run weekly pipeline reviews, and coach AEs on "
            "discovery and qualification. Sales management experience in SaaS essential."
        ),
    },
    {
        "id":               "2025-12-20_veriflow_senior-account-executive",
        "company":          "Veriflow",
        "role":             "Senior Account Executive",
        "tier":             "T2",
        "status":           "acknowledged",
        "persona":          "account-executive",
        "location":         "London, UK",
        "work_arrangement": "hybrid",
        "hybrid_days":      2,
        "salary_min":       65000,
        "salary_max":       80000,
        "salary_currency":  "GBP",
        "source_url":       "https://example.com/jobs/veriflow-senior-ae",
        "model":            "claude-sonnet-4-6",
        "provider":         "anthropic",
        "created_at":       ts(days_ago=16),
        "notes":            "Recruiter confirmed receipt. Step back to IC but the company is interesting.",
        "jd_text": (
            "Veriflow is hiring a Senior Account Executive to own a mid-market territory across "
            "financial services and professional services. You will run full sales cycles from "
            "prospecting to close, targeting £50K–£200K ACV deals with 3–6 month cycle times."
        ),
    },
    {
        "id":               "2025-12-28_capsule-crm_sales-ops-manager",
        "company":          "Capsule CRM",
        "role":             "Sales Operations Manager",
        "tier":             "T2",
        "status":           "reviewing",
        "persona":          "sales-ops-leader",
        "location":         "Manchester, UK",
        "work_arrangement": "hybrid",
        "hybrid_days":      2,
        "salary_min":       70000,
        "salary_max":       85000,
        "salary_currency":  "GBP",
        "source_url":       "https://example.com/jobs/capsule-crm-sales-ops",
        "model":            "claude-sonnet-4-6",
        "provider":         "anthropic",
        "created_at":       ts(days_ago=8),
        "notes":            "Generated CV looks strong. Review judge flags before submitting.",
        "jd_text": (
            "Capsule CRM is looking for a Sales Operations Manager to own our revenue processes "
            "and reporting as we scale our commercial team. You will improve pipeline visibility, "
            "design quota structures, and build scalable forecasting for a 20-person sales team."
        ),
    },
    {
        "id":               "2026-01-03_bridgepoint-capital_sales-director",
        "company":          "Bridgepoint Capital",
        "role":             "Sales Director",
        "tier":             "T3",
        "status":           "rejected",
        "persona":          "sales-manager",
        "location":         "London, UK",
        "work_arrangement": "office",
        "hybrid_days":      None,
        "salary_min":       110000,
        "salary_max":       140000,
        "salary_currency":  "GBP",
        "source_url":       "https://example.com/jobs/bridgepoint-sales-director",
        "model":            "claude-sonnet-4-6",
        "provider":         "anthropic",
        "created_at":       ts(days_ago=5),
        "notes":            "Rejected at CV screen — likely wrong sector fit (PE/finance). Too early for director level.",
        "jd_text": (
            "Bridgepoint Capital seeks a Sales Director to lead a team of 15 across institutional "
            "and private client segments. Financial services background strongly preferred."
        ),
    },
    {
        "id":               "2026-01-06_synthiq_head-of-sales-ops",
        "company":          "Synthiq",
        "role":             "Head of Sales Operations",
        "tier":             "T1",
        "status":           "generated",
        "persona":          "sales-ops-leader",
        "location":         "Remote (UK)",
        "work_arrangement": "remote",
        "hybrid_days":      None,
        "salary_min":       90000,
        "salary_max":       120000,
        "salary_currency":  "GBP",
        "source_url":       "https://example.com/jobs/synthiq-sales-ops",
        "model":            "claude-sonnet-4-6",
        "provider":         "anthropic",
        "created_at":       ts(days_ago=2),
        "notes":            "Just generated — needs judge review before submitting.",
        "jd_text": (
            "Synthiq is an early-stage AI infrastructure company hiring its first Head of Sales Operations. "
            "You will build the function from scratch, owning forecasting, sales planning, and process "
            "design for a growing commercial team. Reports directly to the CRO."
        ),
    },
]

CV_MARKDOWN_TEMPLATE = """\
# Jordan Blake
jordan.blake@example.com | Manchester, UK | linkedin.com/in/jordanblake-demo

## Profile
{profile}

## Experience

### Nexova Technologies — Head of Sales Operations
*2023–2025 | Manchester, UK*

- Led Sales Operations for a 60-person revenue organisation across EMEA and North America, reporting to the CRO.
- Owned the full sales planning cycle: territory design, quota setting, headcount modelling, and compensation administration.
- Reduced average sales cycle length by 22% through process re-engineering and structured deal review cadences.
- Improved forecast accuracy from 68% to 87% within two quarters by partnering with Finance on a unified ARR model.

### Meridian Software — Sales Operations Manager
*2020–2023 | London, UK*

- Built the Sales Operations function from scratch as the first dedicated hire for a 25-person sales team.
- Reduced average AE ramp time from 5 months to 3.5 months through a structured onboarding programme.
- Authored the company's first end-to-end sales playbook, adopted across all AEs within 6 weeks of launch.
- Reduced logo churn by 11% by building a structured renewal motion in collaboration with Customer Success.

## Skills
Sales planning · Quota design · Territory modelling · Forecast management · MEDDIC · Salesforce · Gong · Clari · Outreach · Looker

## Education
BA Business Management, University of Manchester, 2015
"""

PROFILES = {
    "sales-ops-leader": (
        "Sales Operations leader with 10 years of experience building and scaling revenue functions "
        "for B2B SaaS companies from seed to Series C. Proven track record of improving forecast accuracy, "
        "designing fair quota structures, and enabling commercial teams to operate at their ceiling. "
        "Equally comfortable presenting to the board and coaching an AE through a stalled deal."
    ),
    "sales-manager": (
        "Sales Manager with a track record of building high-attainment teams through structured coaching, "
        "clear accountability, and genuine development of individual sellers. Experienced managing AE teams "
        "of 6–12 across mid-market and enterprise segments, with consistent team quota attainment above 90%."
    ),
    "account-executive": (
        "Senior Account Executive with 8 years of full-cycle B2B SaaS sales experience. Consistent quota "
        "attainer with a methodical approach to discovery and a track record of closing complex, "
        "multi-stakeholder enterprise deals across healthcare and financial services verticals."
    ),
}

SAMPLE_SECTION_CONTENT = {
    "profile": (
        "Sales Operations leader with 10 years of experience building and scaling revenue functions "
        "for B2B SaaS companies from seed to Series C. Proven track record of improving forecast "
        "accuracy, designing equitable quota structures, and enabling commercial teams to operate "
        "at their ceiling."
    ),
    "experience": (
        "**Nexova Technologies — Head of Sales Operations** *(2023–2025)*\n\n"
        "- Led Sales Operations for a 60-person revenue organisation across EMEA and North America.\n"
        "- Reduced average sales cycle length by 22% through process re-engineering and structured "
        "deal review cadences.\n"
        "- Improved forecast accuracy from 68% to 87% by partnering with Finance on a unified ARR model."
    ),
    "skills": (
        "Sales planning · Quota design · Territory modelling · Forecast management · MEDDIC · "
        "Salesforce · Gong · Clari · Outreach · Looker"
    ),
}

SAMPLE_FLAGS = [
    {
        "type":      "hotword",
        "excerpt":   "equitable",
        "message":   "Consider replacing with 'fair' or 'well-structured' — simpler and equally precise.",
        "start_pos": 145,
        "end_pos":   153,
    },
    {
        "type":      "sentence_length",
        "excerpt":   "Proven track record of improving forecast accuracy, designing equitable quota structures, and enabling commercial teams to operate at their ceiling.",
        "message":   "Sentence exceeds 30 words. Consider splitting for readability.",
        "start_pos": 88,
        "end_pos":   238,
    },
]


def make_cv(app: dict) -> str:
    persona = app.get("persona", "sales-ops-leader")
    profile = PROFILES.get(persona, PROFILES["sales-ops-leader"])
    return CV_MARKDOWN_TEMPLATE.format(profile=profile)


# ── Section / report / evaluation / flag helpers ──────────────────────────────

SECTION_NAMES = ["profile", "experience", "skills"]


def seed_sections_for_app(conn: sqlite3.Connection, app_id: str, include_flags: bool = False) -> None:
    for section_name in SECTION_NAMES:
        section_id = uid()
        report_id  = uid()
        eval_id    = uid()

        report_path = f"demo/output/{app_id}/sections/{section_name}/{report_id}.md"
        content     = SAMPLE_SECTION_CONTENT.get(section_name, "")

        conn.execute(
            """INSERT INTO sections (id, application_id, section_name, accepted_report_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (section_id, app_id, section_name, report_id, ts(days_ago=20), ts(days_ago=20)),
        )
        conn.execute(
            """INSERT INTO reports
               (id, application_id, section_id, section_name, attempt, file_path, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (report_id, app_id, section_id, section_name, 1, report_path, "accepted", ts(days_ago=20)),
        )
        conn.execute(
            """INSERT INTO evaluations
               (id, report_id, tier, passed, flesch_score, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (eval_id, report_id, "deterministic", 1 if not include_flags else 0, 66.2, ts(days_ago=20)),
        )

        if include_flags and section_name == "profile":
            for flag in SAMPLE_FLAGS:
                conn.execute(
                    """INSERT INTO flags
                       (id, evaluation_id, type, start_pos, end_pos, excerpt, message, status, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        uid(), eval_id, flag["type"],
                        flag["start_pos"], flag["end_pos"],
                        flag["excerpt"], flag["message"],
                        "active", ts(days_ago=20),
                    ),
                )


def seed(conn: sqlite3.Connection) -> None:
    print("Seeding applications...")
    for app in APPLICATIONS:
        cv = make_cv(app)
        conn.execute(
            """INSERT INTO applications
               (id, company, role, tier, status, persona, location, work_arrangement,
                hybrid_days, salary_min, salary_max, salary_currency,
                source_url, jd_text, cv_markdown, model, provider,
                notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                app["id"], app["company"], app["role"], app["tier"], app["status"],
                app["persona"], app["location"], app["work_arrangement"], app.get("hybrid_days"),
                app.get("salary_min"), app.get("salary_max"), app.get("salary_currency"),
                app.get("source_url"), app.get("jd_text"), cv,
                app.get("model"), app.get("provider"), app.get("notes"),
                app["created_at"], app["created_at"],
            ),
        )
        print(f"  ✓ {app['id']}")

    conn.commit()

    apps_with_sections = [
        ("2025-12-03_luminary-health_head-of-sales-ops",       False),
        ("2025-12-10_stackpath_sales-operations-manager",       False),
        ("2025-12-15_orion-analytics_regional-sales-manager",   False),
        ("2025-12-20_veriflow_senior-account-executive",         False),
        ("2025-12-28_capsule-crm_sales-ops-manager",             True),
        ("2026-01-06_synthiq_head-of-sales-ops",                 True),
    ]

    print("\nSeeding sections, reports, evaluations and flags...")
    for app_id, include_flags in apps_with_sections:
        seed_sections_for_app(conn, app_id, include_flags=include_flags)
        print(f"  ✓ {app_id}{' (with flags)' if include_flags else ''}")

    conn.commit()

    print("\nSeeding application events...")
    events = [
        ("2025-12-03_luminary-health_head-of-sales-ops",      "status_change", "generated",    "reviewing",    ts(days_ago=34)),
        ("2025-12-03_luminary-health_head-of-sales-ops",      "status_change", "reviewing",    "applied",      ts(days_ago=33)),
        ("2025-12-03_luminary-health_head-of-sales-ops",      "status_change", "applied",      "acknowledged", ts(days_ago=30)),
        ("2025-12-03_luminary-health_head-of-sales-ops",      "status_change", "acknowledged", "interviewing", ts(days_ago=25)),
        ("2025-12-03_luminary-health_head-of-sales-ops",      "status_change", "interviewing", "offered",      ts(days_ago=5)),
        ("2025-12-10_stackpath_sales-operations-manager",     "status_change", "generated",    "reviewing",    ts(days_ago=27)),
        ("2025-12-10_stackpath_sales-operations-manager",     "status_change", "reviewing",    "applied",      ts(days_ago=26)),
        ("2025-12-10_stackpath_sales-operations-manager",     "status_change", "applied",      "acknowledged", ts(days_ago=22)),
        ("2025-12-10_stackpath_sales-operations-manager",     "status_change", "acknowledged", "interviewing", ts(days_ago=14)),
        ("2025-12-15_orion-analytics_regional-sales-manager", "status_change", "generated",    "reviewing",    ts(days_ago=20)),
        ("2025-12-15_orion-analytics_regional-sales-manager", "status_change", "reviewing",    "applied",      ts(days_ago=19)),
        ("2025-12-20_veriflow_senior-account-executive",       "status_change", "generated",    "reviewing",    ts(days_ago=15)),
        ("2025-12-20_veriflow_senior-account-executive",       "status_change", "reviewing",    "applied",      ts(days_ago=14)),
        ("2025-12-20_veriflow_senior-account-executive",       "status_change", "applied",      "acknowledged", ts(days_ago=10)),
        ("2026-01-03_bridgepoint-capital_sales-director",     "status_change", "generated",    "reviewing",    ts(days_ago=4)),
        ("2026-01-03_bridgepoint-capital_sales-director",     "status_change", "reviewing",    "applied",      ts(days_ago=4)),
        ("2026-01-03_bridgepoint-capital_sales-director",     "status_change", "applied",      "rejected",     ts(days_ago=2)),
    ]

    for app_id, event_type, from_s, to_s, occurred_at in events:
        conn.execute(
            """INSERT INTO application_events
               (application_id, event_type, from_status, to_status, occurred_at)
               VALUES (?, ?, ?, ?, ?)""",
            (app_id, event_type, from_s, to_s, occurred_at),
        )

    conn.commit()
    print(f"  ✓ {len(events)} events seeded")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"Target: {DEMO_DB}\n")

    if DEMO_DB.exists():
        DEMO_DB.unlink()
        print("Removed existing demo/atat.db\n")

    from db.migrate import run_migrations as _run_migrations
    _run_migrations(db_path=DEMO_DB)

    conn = sqlite3.connect(str(DEMO_DB))
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row

    try:
        seed(conn)
    finally:
        conn.close()

    print(f"\nDone. Demo database written to {DEMO_DB}")
