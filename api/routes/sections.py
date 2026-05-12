"""
sections.py (route) — Read surface for CV section versioning.

Accepts application uuid as the path parameter.
Internal queries use the slug id after lookup.
"""

import sqlite3
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from db.database import get_db, rows_to_list, row_to_dict

router = APIRouter(prefix="/sections", tags=["sections"])


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


def _get_app_id_by_uuid(app_uuid: str, db: sqlite3.Connection) -> str:
    row = db.execute("SELECT id FROM applications WHERE uuid = ?", (app_uuid,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Application not found")
    return row["id"]


def _get_latest_report(db: sqlite3.Connection, section_id: str) -> dict | None:
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
    rows = db.execute(
        """SELECT id, attempt, status, escalated, escalation_reason,
                  created_at, parent_report_id
           FROM reports
           WHERE section_id = ?
           ORDER BY attempt ASC""",
        (section_id,),
    ).fetchall()
    return rows_to_list(rows)


@router.get("/{app_uuid}", response_model=list[SectionSummary])
def list_sections(
    app_uuid: str,
    db: sqlite3.Connection = Depends(get_db),
):
    app_id = _get_app_id_by_uuid(app_uuid, db)

    section_rows = db.execute(
        """SELECT id, section_name, accepted_report_id, updated_at
           FROM sections
           WHERE application_id = ?
           ORDER BY created_at ASC""",
        (app_id,),
    ).fetchall()

    result: list[SectionSummary] = []
    for row in section_rows:
        section    = row_to_dict(row)
        latest_row = _get_latest_report(db, section["id"])
        latest     = ReportSummary(**latest_row) if latest_row else None
        result.append(SectionSummary(
            id=section["id"],
            section_name=section["section_name"],
            accepted_report_id=section["accepted_report_id"],
            updated_at=section["updated_at"],
            latest_report=latest,
        ))
    return result


@router.get("/{app_uuid}/{section_name}", response_model=SectionChain)
def get_section_chain(
    app_uuid:     str,
    section_name: str,
    db: sqlite3.Connection = Depends(get_db),
):
    app_id = _get_app_id_by_uuid(app_uuid, db)

    section_row = db.execute(
        """SELECT id, section_name, accepted_report_id
           FROM sections
           WHERE application_id = ? AND section_name = ?""",
        (app_id, section_name),
    ).fetchone()

    if not section_row:
        raise HTTPException(
            status_code=404,
            detail=f"Section '{section_name}' not found for this application",
        )

    section = row_to_dict(section_row)
    chain   = _get_report_chain(db, section["id"])

    return SectionChain(
        section_name=section["section_name"],
        accepted_report_id=section["accepted_report_id"],
        reports=[ReportSummary(**r) for r in chain],
    )
