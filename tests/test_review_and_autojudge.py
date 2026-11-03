"""
Review status warnings (render / submission / bundle) and the judges that now run
automatically after a CV is generated. A fresh database per test; no model calls.
"""

import json
import uuid
from pathlib import Path

import pytest

import db.database
from db.migrate import run_migrations
from pipeline.judges import cheap_llm, orchestrator
from pipeline.jobs.kinds._common import insert_sections, now_iso
from pipeline.review_status import review_status, warning_fields

CV_SECTIONS = {
    "profile":        "A steady operator who builds order where there was none.\n\nSecond paragraph.",
    "experience":     "### Acme -- Head | 2020 - Present\n\n- Led a team of twelve across three sites in two regions.",
    "skills":         "**Ops:** Planning, Rota design",
    "education":      "**BA** -- Uni, 2012",
    "certifications": "- First Aid",
}
CV_MD = "# Jane\n\n---\n\n" + "\n\n".join(f"## {k.title()}\n\n{v}" for k, v in CV_SECTIONS.items())


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    path = tmp_path / "atat.db"
    monkeypatch.setattr(db.database, "DB_PATH", str(path))
    run_migrations(path)
    yield path


def _make_app(tmp_path, with_sections=True) -> dict:
    app_id, app_uuid = f"app-{uuid.uuid4().hex[:6]}", str(uuid.uuid4())
    out_dir = tmp_path / app_id
    out_dir.mkdir()
    with db.database.get_connection() as conn:
        conn.execute(
            "INSERT INTO applications (id, uuid, company, role, status, output_dir, jd_text, cv_markdown, jd_spec) "
            "VALUES (?, ?, 'Acme', 'Head', 'generated', ?, ?, ?, ?)",
            (app_id, app_uuid, str(out_dir), "We need a regional lead.", CV_MD,
             json.dumps({"requirements": [], "anti_patterns": [], "signals": []})),
        )
        if with_sections:
            insert_sections(conn, app_id, out_dir, CV_SECTIONS, now_iso())
    return {"id": app_id, "uuid": app_uuid, "jd_text": "We need a regional lead.", "output_dir": str(out_dir)}


def _accept(app_id, names):
    with db.database.get_connection() as conn:
        for name in names:
            conn.execute(
                "UPDATE sections SET accepted_report_id = "
                "(SELECT id FROM reports WHERE section_id = sections.id LIMIT 1) "
                "WHERE application_id = ? AND section_name = ?", (app_id, name),
            )


# ── Review status ─────────────────────────────────────────────────────────────

def test_an_application_with_no_sections_has_nothing_to_review(tmp_path):
    app = _make_app(tmp_path, with_sections=False)
    with db.database.get_connection() as conn:
        status = review_status(conn, app["id"])
    assert status["total"] == 0 and status["warning"] is None


def test_unreviewed_sections_are_named_in_cv_order(tmp_path):
    app = _make_app(tmp_path)
    _accept(app["id"], ["experience"])
    with db.database.get_connection() as conn:
        status = review_status(conn, app["id"])
    assert (status["total"], status["accepted"]) == (5, 1)
    assert status["unaccepted"] == ["Profile", "Skills", "Education", "Certifications"]
    assert status["warning"].startswith("1 of 5 sections have been reviewed and accepted")


def test_a_fully_accepted_cv_has_no_warning(tmp_path):
    app = _make_app(tmp_path)
    _accept(app["id"], list(CV_SECTIONS))
    with db.database.get_connection() as conn:
        fields = warning_fields(conn, app["id"])
    assert fields["warnings"] == [] and fields["review"]["accepted"] == 5


def test_recording_a_submission_warns_but_still_records_it(tmp_path):
    from mcp_server.tools_submission import record_submission

    app = _make_app(tmp_path)
    result = record_submission(app["uuid"], portal="Careers page")
    assert result["status"] == "recorded"                       # warn only: never blocked
    assert result["warnings"] and "have been reviewed" in result["warnings"][0]
    with db.database.get_connection() as conn:
        assert conn.execute("SELECT status FROM applications WHERE id = ?", (app["id"],)).fetchone()["status"] == "applied"


def test_recording_a_reviewed_cv_has_no_warning(tmp_path):
    from mcp_server.tools_submission import record_submission

    app = _make_app(tmp_path)
    _accept(app["id"], list(CV_SECTIONS))
    assert record_submission(app["uuid"])["warnings"] == []


# ── Auto-judge ────────────────────────────────────────────────────────────────

def _fake_t2(text, ctx=None):
    return cheap_llm.CheapLLMResult(passed=True, model="m", prompt_tokens=0, completion_tokens=0)


def _evaluations(app_id) -> int:
    with db.database.get_connection() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM evaluations e JOIN reports r ON e.report_id = r.id WHERE r.application_id = ?",
            (app_id,),
        ).fetchone()[0]


def test_every_section_is_judged_and_coverage_is_queued(tmp_path, monkeypatch):
    from pipeline.jobs import service
    from pipeline.jobs.kinds.cv import _auto_judge

    app = _make_app(tmp_path)
    queued = []
    monkeypatch.setattr(cheap_llm, "run", _fake_t2)
    monkeypatch.setattr(service, "create", lambda kind, **kw: queued.append((kind, kw)) or {})

    summary = _auto_judge(app, CV_MD)

    assert summary["judged"] == 5 and summary["errors"] == 0 and summary["coverage_queued"] is True
    assert _evaluations(app["id"]) >= 5                         # tier 1 and tier 2 rows per section
    assert queued == [("run_coverage", {"app_uuid": app["uuid"], "created_by": "agent"})]


def test_a_new_check_actually_fires_through_the_auto_judge(tmp_path, monkeypatch):
    from pipeline.jobs import service
    from pipeline.jobs.kinds.cv import _auto_judge

    app = _make_app(tmp_path)
    monkeypatch.setattr(cheap_llm, "run", _fake_t2)
    monkeypatch.setattr(service, "create", lambda *a, **k: {})
    _auto_judge(app, CV_MD)
    with db.database.get_connection() as conn:
        types = {r["type"] for r in conn.execute(
            "SELECT DISTINCT f.type FROM flags f JOIN evaluations e ON f.evaluation_id = e.id "
            "JOIN reports r ON e.report_id = r.id WHERE r.application_id = ?", (app["id"],)
        ).fetchall()}
    # the sample profile is two short paragraphs, far under the 80-120 word default
    assert "profile_shape" in types


def test_a_judge_error_never_loses_the_cv(tmp_path, monkeypatch):
    from pipeline.jobs import service
    from pipeline.jobs.kinds.cv import _auto_judge

    app = _make_app(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("judge exploded")

    monkeypatch.setattr(orchestrator, "evaluate", boom)
    monkeypatch.setattr(service, "create", lambda *a, **k: {})
    summary = _auto_judge(app, CV_MD)                           # must not raise
    assert summary["judged"] == 0 and summary["errors"] == 5
    with db.database.get_connection() as conn:
        assert conn.execute("SELECT cv_markdown FROM applications WHERE id = ?", (app["id"],)).fetchone()[0] == CV_MD


def test_a_failure_to_queue_coverage_is_not_fatal(tmp_path, monkeypatch):
    from pipeline.jobs import service
    from pipeline.jobs.kinds.cv import _auto_judge

    app = _make_app(tmp_path)
    monkeypatch.setattr(cheap_llm, "run", _fake_t2)

    def nope(*a, **k):
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(service, "create", nope)
    summary = _auto_judge(app, CV_MD)
    assert summary["judged"] == 5 and summary["coverage_queued"] is False and summary["errors"] == 1
