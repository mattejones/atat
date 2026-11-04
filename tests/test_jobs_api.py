"""
Tests for api/routes/jobs.py — the HTTP surface over pipeline/jobs.

Reuses test_jobs' fake kind and fixtures (fresh SQLite DB, fresh runner). The TestClient
is used without its context manager so FastAPI's startup hook — which migrates the real
database — never runs.
"""

import time

from fastapi.testclient import TestClient

from api.main import app
from tests.test_jobs import CONTROL, _make_app, fresh_db, fresh_runner  # noqa: F401 — fixtures

client = TestClient(app)


def _wait(job_id: str, timeout: float = 5) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        job = client.get(f"/jobs/{job_id}").json()
        if job["status"] not in ("queued", "running") or time.monotonic() > deadline:
            return job
        time.sleep(0.05)


def test_draft_review_edit_and_submit_over_http():
    app_uuid = _make_app(company="Globex")

    res = client.post("/jobs", json={"kind": "_fake", "app_uuid": app_uuid, "params": {"note": "v1"}, "draft": True})
    assert res.status_code == 202
    draft = res.json()
    assert draft["status"] == "draft"
    assert draft["created_by"] == "human"
    assert draft["company"] == "Globex"
    assert draft["prompt_preview"]["user"] == "note=v1 app=Globex"

    res = client.patch(f"/jobs/{draft['job_id']}", json={"params": {"note": "v2"}, "expected_version": 1})
    assert res.status_code == 200
    assert res.json()["version"] == 2

    res = client.post(f"/jobs/{draft['job_id']}/submit", json={"expected_version": 2})
    assert res.status_code == 202
    assert res.json()["submitted_by"] == "human"

    done = _wait(draft["job_id"])
    assert done["status"] == "succeeded"
    assert done["result"]["echo"] == "v2"


def test_drafts_listed_as_the_review_queue():
    app_uuid = _make_app()
    draft = client.post("/jobs", json={"kind": "_fake", "app_uuid": app_uuid, "draft": True}).json()
    queue = client.get("/jobs", params={"status": "draft"}).json()
    assert [j["job_id"] for j in queue] == [draft["job_id"]]


def test_stale_edit_is_a_conflict():
    draft = client.post("/jobs", json={"kind": "_fake", "app_uuid": _make_app(), "draft": True}).json()
    client.patch(f"/jobs/{draft['job_id']}", json={"params": {"note": "someone else"}})
    res = client.post(f"/jobs/{draft['job_id']}/submit", json={"expected_version": 1})
    assert res.status_code == 409
    assert "edited since version 1" in res.json()["detail"]


def test_unknown_things_are_404():
    assert client.get("/jobs/nope").status_code == 404
    res = client.post("/jobs", json={"kind": "_fake", "app_uuid": "no-such-app"})
    assert res.status_code == 404


def test_bad_params_are_a_conflict_not_a_crash():
    res = client.post("/jobs", json={"kind": "_fake", "app_uuid": _make_app(), "params": {"bogus": 1}})
    assert res.status_code == 409
    assert "Unknown param" in res.json()["detail"]


def test_cancel_and_retry_over_http():
    CONTROL.fail = True
    job = client.post("/jobs", json={"kind": "_fake", "app_uuid": _make_app()}).json()
    assert _wait(job["job_id"])["status"] == "failed"

    CONTROL.fail = False
    retry = client.post(f"/jobs/{job['job_id']}/retry", json={"draft": True})
    assert retry.status_code == 202
    retried = retry.json()
    assert retried["status"] == "draft"

    assert client.post(f"/jobs/{retried['job_id']}/cancel").json()["status"] == "cancelled"


def test_kinds_lists_real_kinds_with_their_params():
    kinds = client.get("/jobs/kinds").json()
    assert "_fake" not in kinds
    assert kinds["generate_cover_letter"]["params"]["research_company"]["default"] is True
    assert kinds["regenerate_section"]["target"] == "report"


def test_legacy_generative_endpoints_return_a_job():
    # Refused by generate_cv-style preconditions or not, each route now answers with a
    # job (202) or a job error (409) — never a blocking model call.
    app_uuid = _make_app()  # no JD, no CV
    res = client.post(f"/cover-letter/{app_uuid}/generate", json={})
    assert res.status_code == 409
    assert "no JD or CV" in res.json()["detail"]

    res = client.post(f"/review/no-such-report/evaluate")
    assert res.status_code == 404


# ── Failed jobs that need attention ───────────────────────────────────────────────────────

def _fail(job_id: str, error: str = "JobError: LLM generation failed: Model returned invalid JSON"):
    import db.database
    with db.database.get_connection() as conn:
        conn.execute("UPDATE generation_jobs SET status = 'failed', error = ?, finished_at = datetime('now') WHERE id = ?",
                     (error, job_id))


def test_a_failed_job_is_listed_as_needing_attention_with_its_error():
    app_uuid = _make_app(company="Eucalyptus")
    job = client.post("/jobs", json={"kind": "_fake", "app_uuid": app_uuid, "draft": True}).json()
    _fail(job["job_id"])
    rows = client.get("/jobs/attention").json()
    assert [r["job_id"] for r in rows] == [job["job_id"]]
    assert rows[0]["company"] == "Eucalyptus" and "invalid JSON" in rows[0]["error"]


def test_a_job_that_was_retried_successfully_is_no_longer_listed():
    app_uuid = _make_app(company="Eucalyptus")
    first = client.post("/jobs", json={"kind": "_fake", "app_uuid": app_uuid, "draft": True}).json()
    _fail(first["job_id"])
    retry = client.post(f"/jobs/{first['job_id']}/retry")
    assert retry.status_code == 202
    _wait(retry.json()["job_id"])
    assert client.get("/jobs/attention").json() == []


def test_a_still_failing_retry_keeps_one_row_not_two():
    app_uuid = _make_app(company="Eucalyptus")
    first = client.post("/jobs", json={"kind": "_fake", "app_uuid": app_uuid, "draft": True}).json()
    second = client.post("/jobs", json={"kind": "_fake", "app_uuid": app_uuid, "draft": True}).json()
    _fail(first["job_id"])
    _fail(second["job_id"])
    assert len(client.get("/jobs/attention").json()) == 1


def test_old_failures_are_not_listed():
    import db.database
    app_uuid = _make_app()
    job = client.post("/jobs", json={"kind": "_fake", "app_uuid": app_uuid, "draft": True}).json()
    _fail(job["job_id"])
    with db.database.get_connection() as conn:
        conn.execute("UPDATE generation_jobs SET created_at = datetime('now', '-10 days'), "
                     "finished_at = datetime('now', '-10 days') WHERE id = ?", (job["job_id"],))
    assert client.get("/jobs/attention").json() == []
