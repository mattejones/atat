"""
sections.py (route) — Read surface for CV section versioning.

Phase 1 endpoints:
  GET /sections/{app_id}              — all sections and their current report for an application
  GET /sections/{app_id}/{section}    — full report chain for a specific section

Phase 2 (judge loop) will add:
  POST /sections/{app_id}/{section}/evaluate   — run judge tiers against current pending report
  POST /sections/{app_id}/{section}/accept     — accept a report, recompose cv.md
"""

import sqlite3
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from db.database import get_db, rows_to_list, row_to_dict

router = APIRouter(prefix="/sections", tags=["sections"])


# ── Response models ───────────────────────────────────────────────────────────

class ReportSummary(BaseModel):
    id:                str
    attempt:           int
    status:            str
    escalated:         bool
    escalation_reason: str | None
    created_at:        str
    parent_report_id:  str | None


class SectionSummary(BaseModel):
    id:                 str
    section_name:       str
    accepted_report_id: str | None
    updated_at:         str
    latest_report:      ReportSummary | None


class SectionChain(BaseModel):
    section_name:       str
    accepted_report_id: str | None
    reports:            list[ReportSummary]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_latest_report(db: sqlite3.Connection, section_id: str) -> dict | None:
    """Return the most recent report for a section by created_at."""
    row = db.execute(
        """SELECT id, attempt, status, escalated, escalation_reason,
                  created_at, parent_report_id
           FROM reports
           WHERE section_id = ?
           ORDER BY attempt DESC
           LIMIT 1""",
        (section_id,),
    ).fetchone()
    return row_to_dict(row) if row else None


def _get_report_chain(db: sqlite3.Connection, section_id: str) -> list[dict]:
    """Return all reports for a section ordered by attempt ascending."""
    rows = db.execute(
        """SELECT id, attempt, status, escalated, escalation_reason,
                  created_at, parent_report_id
           FROM reports
           WHERE section_id = ?
           ORDER BY attempt ASC""",
        (section_id,),
    ).fetchall()
    return rows_to_list(rows)


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/{app_id}", response_model=list[SectionSummary])
def list_sections(
    app_id: str,
    db: sqlite3.Connection = Depends(get_db),
):
    """Return all sections for an application with their latest report."""
    app = db.execute(
        "SELECT id FROM applications WHERE id = ?", (app_id,)
    ).fetchone()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    section_rows = db.execute(
        """SELECT id, section_name, accepted_report_id, updated_at
           FROM sections
           WHERE application_id = ?
           ORDER BY created_at ASC""",
        (app_id,),
    ).fetchall()

    result: list[SectionSummary] = []
    for row in section_rows:
        section     = row_to_dict(row)
        latest_row  = _get_latest_report(db, section["id"])
        latest      = ReportSummary(**latest_row) if latest_row else None

        result.append(SectionSummary(
            id=section["id"],
            section_name=section["section_name"],
            accepted_report_id=section["accepted_report_id"],
            updated_at=section["updated_at"],
            latest_report=latest,
        ))

    return result


@router.get("/{app_id}/{section_name}", response_model=SectionChain)
def get_section_chain(
    app_id:       str,
    section_name: str,
    db: sqlite3.Connection = Depends(get_db),
):
    """Return the full report chain for a specific section."""
    section_row = db.execute(
        """SELECT id, section_name, accepted_report_id
           FROM sections
           WHERE application_id = ? AND section_name = ?""",
        (app_id, section_name),
    ).fetchone()

    if not section_row:
        raise HTTPException(
            status_code=404,
            detail=f"Section '{section_name}' not found for application '{app_id}'",
        )

    section = row_to_dict(section_row)
    chain   = _get_report_chain(db, section["id"])

    return SectionChain(
        section_name=section["section_name"],
        accepted_report_id=section["accepted_report_id"],
        reports=[ReportSummary(**r) for r in chain],
    )
