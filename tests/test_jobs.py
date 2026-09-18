"""
Tests for pipeline/jobs — the async job lifecycle, drafts, and runner recovery.

Most tests drive a fake job kind registered here, so nothing touches a model: the point
is the lifecycle (draft -> queued -> running -> terminal), the edit/submit guards, and
what happens when a process dies mid-job. One test exercises a real kind's precondition
check (generate_cv refuses an application with no jd_spec).

Each test gets a fresh SQLite database with every migration applied, and a fresh
Runner in place of the process-wide one.
"""

import threading
import uuid
from datetime import datetime, timedelta

import pytest

import db.database
from db.migrate import run_migrations
from pipeline.jobs import runner as runner_mod
from pipeline.jobs import service
from pipeline.jobs.base import REGISTRY, JobError, JobKind, Param, Prompt, register


# ── Fake kind ─────────────────────────────────────────────────────────────────

class _Control:
    """Lets a test hold a fake job mid-run and see whether it reached its write step."""

    def __init__(self):
        self.release = threading.Event()
        self.started = threading.Event()
        self.block   = False
        self.fail    = False
        self.wrote   = []

    def reset(self):
        self.__init__()


CONTROL = _Control()


@register
class _FakeKind(JobKind):
    name        = "_fake"
    target      = "application"
    description = "test kind"
    params      = {
        "note":  Param(str, help="free text"),
        "count": Param(int, 1),
        "flag":  Param(bool, False),
    }

    def preview(self, ctx, params):
        return Prompt("SYSTEM PROMPT", f"note={params['note']} app={ctx.application['company']}")

    def run(self, ctx, params, handle):
        CONTROL.started.set()
        if CONTROL.block:
            assert CONTROL.release.wait(10), "test never released the job"
        if CONTROL.fail:
            raise RuntimeError("model exploded")
        handle.checkpoint()
        CONTROL.wrote.append(handle.job_id)
        return {"echo": params["note"], "count": params["count"]}


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    path = tmp_path / "atat.db"
    monkeypatch.setattr(db.database, "DB_PATH", str(path))
    run_migrations(path)
    CONTROL.reset()
    yield path


@pytest.fixture(autouse=True)
def fresh_runner(monkeypatch):
    r = runner_mod.Runner(max_workers=2)
    monkeypatch.setattr(runner_mod, "_runner", r)
    yield r
    CONTROL.release.set()
    r.shutdown(wait=True)


def _make_app(company="Acme", **cols) -> str:
    app_id, app_uuid = f"app-{uuid.uuid4().hex[:6]}", str(uuid.uuid4())
    with db.database.get_connection() as conn:
        conn.execute(
            "INSERT INTO applications (id, uuid, company, role, status, output_dir) VALUES (?, ?, ?, 'Engineer', 'analysed', '')",
            (app_id, app_uuid, company),
        )
        for col, value in cols.items():
            conn.execute(f"UPDATE applications SET {col} = ? WHERE id = ?", (value, app_id))
    return app_uuid


def _status(job_id: str) -> str:
    return service.get(job_id)["status"]


# ── Drafts ────────────────────────────────────────────────────────────────────

def test_draft_is_created_without_running_and_shows_preview():
    app = _make_app()
    job = service.create("_fake", app_uuid=app, params={"note": "hello"}, draft=True)

    assert job["status"] == "draft"
    assert job["version"] == 1
    assert job["params"] == {"note": "hello", "count": 1, "flag": False}
    assert set(job["editable_params"]) == {"note", "count", "flag"}
    assert job["prompt_preview"]["user"] == "note=hello app=Acme"
    assert job["prompt_preview"]["system_chars"] == len("SYSTEM PROMPT")
    assert "system" not in job["prompt_preview"]
    assert not CONTROL.started.is_set()

    full = service.get(job["job_id"], include_prompt=True)
    assert full["prompt_preview"]["system"] == "SYSTEM PROMPT"


def test_update_draft_merges_params_and_bumps_version():
    job = service.create("_fake", app_uuid=_make_app(), params={"note": "a", "count": 3}, draft=True)

    updated = service.update_draft(job["job_id"], {"note": "b"}, expected_version=1)
    assert updated["params"] == {"note": "b", "count": 3, "flag": False}
    assert updated["version"] == 2
    assert updated["prompt_preview"]["user"].startswith("note=b")

    reset = service.update_draft(job["job_id"], {"count": None})
    assert reset["params"]["count"] == 1


def test_update_draft_rejects_a_stale_version():
    job = service.create("_fake", app_uuid=_make_app(), draft=True)
    service.update_draft(job["job_id"], {"note": "theirs"})
    with pytest.raises(JobError, match="edited since version 1"):
        service.update_draft(job["job_id"], {"note": "mine"}, expected_version=1)


@pytest.mark.parametrize("params, message", [
    ({"nope": 1},        "Unknown param"),
    ({"count": "three"}, "must be a int"),
    ({"flag": "yes"},    "must be true or false"),
])
def test_bad_params_are_rejected(params, message):
    with pytest.raises(JobError, match=message):
        service.create("_fake", app_uuid=_make_app(), params=params, draft=True)


def test_submit_refuses_a_version_the_reviewer_did_not_see():
    job = service.create("_fake", app_uuid=_make_app(), draft=True)
    service.update_draft(job["job_id"], {"note": "changed after review"})
    with pytest.raises(JobError, match="edited since version 1"):
        service.submit(job["job_id"], expected_version=1)
    assert _status(job["job_id"]) == "draft"


def test_submitted_draft_runs_and_records_who_submitted_it():
    job = service.create("_fake", app_uuid=_make_app(), params={"note": "go"}, draft=True, created_by="agent")
    queued = service.submit(job["job_id"], submitted_by="human")
    assert queued["status"] in ("queued", "running", "succeeded")

    done = service.wait(job["job_id"], timeout=5)
    assert done["status"] == "succeeded"
    assert done["result"] == {"echo": "go", "count": 1}
    assert done["created_by"] == "agent"
    assert done["submitted_by"] == "human"
    # The prompt as submitted is kept on the job.
    assert done["prompt_preview"]["user"] == "note=go app=Acme"


def test_only_drafts_can_be_edited_or_submitted():
    job = service.create("_fake", app_uuid=_make_app())
    service.wait(job["job_id"], timeout=5)
    with pytest.raises(JobError, match="not a draft"):
        service.update_draft(job["job_id"], {"note": "x"})
    with pytest.raises(JobError, match="only a draft can be submitted"):
        service.submit(job["job_id"])


# ── Running ───────────────────────────────────────────────────────────────────

def test_create_returns_before_the_work_finishes():
    CONTROL.block = True
    job = service.create("_fake", app_uuid=_make_app(), params={"note": "slow"})

    assert job["status"] in ("queued", "running")
    assert CONTROL.started.wait(5)
    assert _status(job["job_id"]) == "running"

    CONTROL.release.set()
    assert service.wait(job["job_id"], timeout=5)["status"] == "succeeded"


def test_wait_times_out_while_the_job_is_still_running():
    CONTROL.block = True
    job = service.create("_fake", app_uuid=_make_app())
    assert CONTROL.started.wait(5)
    assert service.wait(job["job_id"], timeout=0.3)["status"] == "running"


def test_wait_on_a_draft_returns_immediately():
    job = service.create("_fake", app_uuid=_make_app(), draft=True)
    assert service.wait(job["job_id"], timeout=5)["status"] == "draft"


def test_a_failing_job_records_its_error_and_can_be_retried():
    CONTROL.fail = True
    job = service.create("_fake", app_uuid=_make_app(), params={"note": "again"})
    failed = service.wait(job["job_id"], timeout=5)
    assert failed["status"] == "failed"
    assert "model exploded" in failed["error"]

    CONTROL.fail = False
    retry = service.retry(job["job_id"])
    assert retry["job_id"] != job["job_id"]
    assert retry["params"]["note"] == "again"
    assert service.wait(retry["job_id"], timeout=5)["status"] == "succeeded"


def test_retry_as_draft_stops_for_review():
    CONTROL.fail = True
    job = service.create("_fake", app_uuid=_make_app())
    service.wait(job["job_id"], timeout=5)
    assert service.retry(job["job_id"], draft=True)["status"] == "draft"


def test_one_active_job_per_kind_and_target():
    app = _make_app()
    CONTROL.block = True
    first = service.create("_fake", app_uuid=app)
    assert CONTROL.started.wait(5)

    second = service.create("_fake", app_uuid=app, draft=True)
    with pytest.raises(JobError, match=first["job_id"]):
        service.submit(second["job_id"])

    # A different application is unaffected.
    other = service.create("_fake", app_uuid=_make_app(), draft=True)
    service.submit(other["job_id"])
    CONTROL.release.set()
    assert service.wait(other["job_id"], timeout=5)["status"] == "succeeded"


# ── Cancelling ────────────────────────────────────────────────────────────────

def test_cancel_a_draft():
    job = service.create("_fake", app_uuid=_make_app(), draft=True)
    assert service.cancel(job["job_id"])["status"] == "cancelled"
    with pytest.raises(JobError):
        service.submit(job["job_id"])
    with pytest.raises(JobError, match="already cancelled"):
        service.cancel(job["job_id"])


def test_cancelling_a_running_job_discards_its_output():
    CONTROL.block = True
    job = service.create("_fake", app_uuid=_make_app())
    assert CONTROL.started.wait(5)

    requested = service.cancel(job["job_id"])
    assert requested["status"] == "running"
    assert requested["cancel_requested"] is True

    CONTROL.release.set()
    done = service.wait(job["job_id"], timeout=5)
    assert done["status"] == "cancelled"
    assert CONTROL.wrote == []   # checkpoint() stopped it before the write


# ── Recovery ──────────────────────────────────────────────────────────────────

def _old(seconds: int) -> str:
    return (datetime.now() - timedelta(seconds=seconds)).isoformat(timespec="microseconds")


def test_sweep_fails_a_running_job_whose_worker_died(fresh_runner):
    job = service.create("_fake", app_uuid=_make_app(), draft=True)
    with db.database.get_connection() as conn:
        conn.execute(
            "UPDATE generation_jobs SET status = 'running', worker_id = 'dead:1:x', heartbeat_at = ? WHERE id = ?",
            (_old(600), job["job_id"]),
        )

    fresh_runner.sweep()

    swept = service.get(job["job_id"])
    assert swept["status"] == "failed"
    assert swept["error"].startswith("Interrupted")
    assert CONTROL.wrote == []   # not re-run


def test_sweep_leaves_a_healthy_running_job_alone(fresh_runner):
    CONTROL.block = True
    job = service.create("_fake", app_uuid=_make_app())
    assert CONTROL.started.wait(5)
    fresh_runner.sweep()
    assert _status(job["job_id"]) == "running"


def test_sweep_adopts_a_queued_job_nobody_claimed(fresh_runner):
    job = service.create("_fake", app_uuid=_make_app(), params={"note": "orphan"}, draft=True)
    with db.database.get_connection() as conn:
        conn.execute(
            "UPDATE generation_jobs SET status = 'queued', submitted_by = 'agent', submitted_at = ? WHERE id = ?",
            (_old(600), job["job_id"]),
        )

    fresh_runner.sweep()

    assert service.wait(job["job_id"], timeout=5)["result"]["echo"] == "orphan"


def test_a_job_is_only_claimed_once(fresh_runner):
    CONTROL.block = True
    job = service.create("_fake", app_uuid=_make_app())
    assert CONTROL.started.wait(5)
    assert fresh_runner._claim(job["job_id"]) is False


# ── Listing ───────────────────────────────────────────────────────────────────

def test_list_jobs_filters_by_status_and_application():
    app = _make_app()
    draft = service.create("_fake", app_uuid=app, draft=True)
    ran   = service.create("_fake", app_uuid=_make_app())
    service.wait(ran["job_id"], timeout=5)

    drafts = service.list_jobs(status="draft")
    assert [j["job_id"] for j in drafts] == [draft["job_id"]]
    assert [j["job_id"] for j in service.list_jobs(app_uuid=app)] == [draft["job_id"]]
    assert {j["job_id"] for j in service.list_jobs()} == {draft["job_id"], ran["job_id"]}


# ── Real kinds ────────────────────────────────────────────────────────────────

def test_every_generative_tool_has_a_registered_kind():
    assert {
        "analyse_job", "submit_job", "generate_cv", "run_coverage", "run_judges",
        "regenerate_section", "generate_cover_letter", "generate_answers",
    } <= set(REGISTRY)


def test_generate_cv_refuses_an_application_without_a_spec():
    with pytest.raises(JobError, match="has no jd_spec"):
        service.create("generate_cv", app_uuid=_make_app(), draft=True)


def test_intake_kinds_take_no_target():
    with pytest.raises(JobError, match="creates its own application"):
        service.create("analyse_job", app_uuid=_make_app(), params={"jd_text": "x"}, draft=True)


def test_intake_kinds_require_jd_text():
    with pytest.raises(JobError, match="jd_text"):
        service.create("analyse_job", params={"jd_text": "   "}, draft=True)
