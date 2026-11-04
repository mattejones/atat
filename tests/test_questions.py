"""
The person's own answers to application questions: write one, have it improved, keep either.
Fresh database per test (test_jobs' fixtures); the model call is replaced.
"""

import time

import pytest
from fastapi.testclient import TestClient

import db.database
from api.main import app
from tests.test_jobs import _make_app, fresh_db, fresh_runner  # noqa: F401 — fixtures

client = TestClient(app)

MINE = "I ran the rota for twelve carers and cut agency use by a third."
BETTER = "I managed the rota for twelve carers and reduced agency use by a third."


def _wait(job_id: str, timeout: float = 5) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        job = client.get(f"/jobs/{job_id}").json()
        if job["status"] not in ("queued", "running") or time.monotonic() > deadline:
            return job
        time.sleep(0.05)


def _app_with_question():
    app_uuid = _make_app(jd_text="We need a regional lead.", cv_markdown="# Jane\n\nRan rotas.")
    q = client.post(f"/questions/{app_uuid}", json={"question_text": "Tell us about a time you improved something."}).json()
    return app_uuid, q["id"]


def _latest(q_id: str) -> dict:
    return client.get(f"/questions/{_app_uuid_of(q_id)}").json()


def _app_uuid_of(q_id: str) -> str:
    with db.database.get_connection() as conn:
        return conn.execute(
            "SELECT a.uuid FROM applications a JOIN application_questions q ON q.application_id = a.id WHERE q.id = ?",
            (q_id,)).fetchone()[0]


def test_a_person_can_write_an_answer_before_any_ai_answer_exists():
    app_uuid, q_id = _app_with_question()
    res = client.put(f"/questions/{app_uuid}/{q_id}/draft", json={"text": MINE})
    assert res.status_code == 200 and res.json()["effective_answer"] == MINE
    listed = client.get(f"/questions/{app_uuid}").json()[0]
    assert listed["effective_answer"] == MINE and listed["answer"]["ai_answer"] == ""


def test_saving_again_updates_the_same_answer():
    app_uuid, q_id = _app_with_question()
    client.put(f"/questions/{app_uuid}/{q_id}/draft", json={"text": MINE})
    client.put(f"/questions/{app_uuid}/{q_id}/draft", json={"text": MINE + " More."})
    with db.database.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM application_answers WHERE question_id = ?", (q_id,)).fetchone()[0] == 1
    assert client.get(f"/questions/{app_uuid}").json()[0]["effective_answer"].endswith("More.")


def test_improving_keeps_the_original_and_makes_the_improvement_live(monkeypatch):
    import pipeline.question_answerer as qa
    seen = {}

    def fake(question_text, response_length, draft, jd, cv, notes, tone, instruction=None):
        seen.update(draft=draft, instruction=instruction, cv=cv)
        return BETTER

    monkeypatch.setattr(qa, "improve_answer", fake)
    app_uuid, q_id = _app_with_question()
    client.put(f"/questions/{app_uuid}/{q_id}/draft", json={"text": MINE})

    res = client.post(f"/questions/{app_uuid}/{q_id}/improve", json={"instruction": "shorter"})
    assert res.status_code == 202
    done = _wait(res.json()["job_id"])
    assert done["status"] == "succeeded", done
    assert seen["draft"] == MINE and seen["instruction"] == "shorter" and "Ran rotas" in seen["cv"]

    answer = client.get(f"/questions/{app_uuid}").json()[0]
    assert answer["effective_answer"] == BETTER
    assert answer["answer"]["source_draft"] == MINE


def test_restore_puts_the_persons_own_wording_back(monkeypatch):
    import pipeline.question_answerer as qa
    monkeypatch.setattr(qa, "improve_answer", lambda *a, **k: BETTER)
    app_uuid, q_id = _app_with_question()
    client.put(f"/questions/{app_uuid}/{q_id}/draft", json={"text": MINE})
    _wait(client.post(f"/questions/{app_uuid}/{q_id}/improve", json={}).json()["job_id"])

    res = client.post(f"/questions/{app_uuid}/{q_id}/restore")
    assert res.status_code == 200
    assert client.get(f"/questions/{app_uuid}").json()[0]["effective_answer"] == MINE


def test_restore_without_an_improvement_is_a_clear_404():
    app_uuid, q_id = _app_with_question()
    client.put(f"/questions/{app_uuid}/{q_id}/draft", json={"text": MINE})
    assert client.post(f"/questions/{app_uuid}/{q_id}/restore").status_code == 404


def test_improving_with_nothing_written_fails_clearly():
    app_uuid, q_id = _app_with_question()
    res = client.post(f"/questions/{app_uuid}/{q_id}/improve", json={})
    job = res.json() if res.status_code != 202 else _wait(res.json()["job_id"])
    assert res.status_code >= 400 or job["status"] == "failed"
    assert "write one first" in str(job).lower() or "no answer" in str(job).lower()


def test_the_improve_prompt_carries_the_draft_the_question_and_the_rules(tmp_path, monkeypatch):
    import pipeline.question_answerer as qa
    monkeypatch.setattr("pipeline.tailorer.load_personal_additions", lambda: "Never use the word genuine.")
    system, user = qa.build_improve_prompt(
        "Why this role?", "short", MINE, "JD text", "CV text", "a note", "direct", "make it shorter")
    assert "OWN answer" in system and "Never use the word genuine." in system
    assert "Why this role?" in user and MINE in user and "make it shorter" in user
    assert "1–3 concise sentences" in user


def test_a_wrapping_quote_from_the_model_is_removed(monkeypatch):
    import pipeline.question_answerer as qa

    class Block:
        text = '"' + BETTER + '"'

    monkeypatch.setattr(qa, "_call_anthropic", lambda *a, **k: [Block()])
    assert qa.improve_answer("Q?", "short", MINE, "", "", None, "professional") == BETTER


def test_the_improve_prompt_forbids_inventing_outcomes():
    import pipeline.question_answerer as qa
    system, _ = qa.build_improve_prompt("Q?", "short", MINE, "", "", None, "professional")
    assert "never add what it led to" in system
