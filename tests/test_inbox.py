"""
Tests for pipeline/inbox — Gmail sync, matching, proposals, and the review queue.

Nothing here touches Gmail or a model: a fake mail source stands in for the Gmail API
and llm.call_json is stubbed to answer from per-subject fixtures. Each test gets a fresh
SQLite database (and job runner) from test_jobs' fixtures.
"""

import base64
import time

import pytest
from fastapi.testclient import TestClient

import db.database
from api.main import app as api_app
from pipeline.inbox import llm, matcher, review, sync
from pipeline.inbox.gmail_client import EmailMessage, parse_message
from pipeline.inbox.proposals import propose, status_allowed
from pipeline.jobs import service
from tests.test_jobs import _make_app, fresh_db, fresh_runner  # noqa: F401 — fixtures

T0 = int(time.time() * 1000)


# ── Fakes ─────────────────────────────────────────────────────────────────────

def _msg(i: int, sender: str, subject: str, body: str = "", thread: str | None = None) -> EmailMessage:
    name, _, addr = sender.partition(" <")
    addr = addr.rstrip(">") or name
    return EmailMessage(
        id=f"m{i}", thread_id=thread or f"t{i}", internal_date=T0 + i * 60_000,
        sender=sender, sender_name=name if addr != name else "", sender_email=addr,
        sender_domain=addr.split("@")[-1], subject=subject, snippet=body[:100], body=body,
    )


class FakeGmail:
    def __init__(self, messages):
        self.messages = {m.id: m for m in messages}
        self.fetched: list[str] = []
        self.list_calls: list[int] = []

    def list_message_ids(self, after_ms):
        self.list_calls.append(after_ms)
        floor = (after_ms // 1000) * 1000
        return [m.id for m in sorted(self.messages.values(), key=lambda m: m.internal_date) if m.internal_date >= floor]

    def get_message(self, message_id):
        self.fetched.append(message_id)
        return self.messages[message_id]


class FakeLLM:
    """triage: job-related unless 'newsletter' is in the subject. classify: per-subject answers."""

    def __init__(self):
        self.answers: dict[str, dict] = {}
        self.calls: list[str] = []
        self.fail_on: set[str] = set()

    def __call__(self, system, user, *, max_tokens=1024, label="call"):
        self.calls.append(label)
        if label == "triage":
            import json
            lines = [json.loads(l) for l in user.splitlines() if l.startswith("{")]
            return {"job_related": [l["i"] for l in lines if "newsletter" not in l["subject"].lower()]}
        subject = next(l[len("Subject: "):] for l in user.splitlines() if l.startswith("Subject: "))
        if subject in self.fail_on:
            raise RuntimeError("Inbox classify returned invalid JSON: boom")
        return self.answers[subject]


@pytest.fixture(autouse=True)
def api_classifier_by_default(monkeypatch):
    """Tests expect API mode unless they ask for agent mode, whatever the local .env says."""
    monkeypatch.setattr(sync, "INBOX_CLASSIFIER", "api")


@pytest.fixture
def fake_llm(monkeypatch):
    f = FakeLLM()
    monkeypatch.setattr(llm, "call_json", f)
    return f


def _app_row(app_uuid: str) -> dict:
    with db.database.get_connection() as conn:
        return dict(conn.execute("SELECT * FROM applications WHERE uuid = ?", (app_uuid,)).fetchone())


def _rows() -> dict[str, dict]:
    with db.database.get_connection() as conn:
        return {r["gmail_message_id"]: dict(r) for r in conn.execute("SELECT * FROM inbox_emails").fetchall()}


# ── Parsing ───────────────────────────────────────────────────────────────────

def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def test_parse_message_falls_back_to_html_and_splits_sender():
    raw = {
        "id": "abc", "threadId": "th", "internalDate": "1700000000000", "snippet": "Thanks &amp; regards",
        "payload": {
            "headers": [{"name": "From", "value": "Acme Talent <no-reply@acme.greenhouse-mail.io>"},
                        {"name": "Subject", "value": "Your application"}],
            "mimeType": "multipart/alternative",
            "parts": [{"mimeType": "text/html", "body": {"data": _b64("<p>Hello&nbsp;Matt</p><style>x{}</style><p>Thanks</p>")}}],
        },
    }
    m = parse_message(raw)
    assert (m.sender_name, m.sender_email, m.sender_domain) == ("Acme Talent", "no-reply@acme.greenhouse-mail.io", "acme.greenhouse-mail.io")
    assert m.body == "Hello Matt\nThanks" or m.body.split() == ["Hello", "Matt", "Thanks"]
    assert m.snippet == "Thanks & regards"
    assert m.received_date == "2023-11-14"


# ── Matching and proposals ────────────────────────────────────────────────────

def test_matcher_scores_sender_subject_and_thread():
    apps = [
        {"id": "a1", "uuid": "u1", "company": "Acme Ltd", "role": "Data Engineer", "status": "applied"},
        {"id": "a2", "uuid": "u2", "company": "Globex", "role": "Analyst", "status": "applied"},
    ]
    msg = _msg(1, "Acme Recruiting <jobs@acme.com>", "Your Acme Data Engineer application")
    top = matcher.shortlist(msg, apps)
    assert [c.uuid for c in top] == ["u1"]
    assert set(top[0].reasons) == {"sender", "subject", "role"}

    # A thread link outranks everything else.
    reply = _msg(2, "Someone <x@gmail.com>", "Re: next steps")
    assert matcher.shortlist(reply, apps, thread_app_id="a2")[0].uuid == "u2"
    assert matcher.normalise_company("The Acme Group Holdings Ltd") == "acme"


@pytest.mark.parametrize("current,target,ok", [
    ("applied", "acknowledged", True),
    ("interviewing", "acknowledged", False),   # never backwards
    ("interviewing", "case_study", True),
    ("case_study", "interviewing", True),
    ("interviewing", "rejected", True),
    ("ghosted", "interviewing", True),         # an email revives a ghosted application
    ("rejected", "interviewing", False),
    ("archived", "rejected", False),
    ("offered", "acknowledged", False),
])
def test_status_guard(current, target, ok):
    assert status_allowed(current, target) is ok


def test_propose_dates():
    app = {"status": "applied", "applied_date": None}
    assert propose("acknowledgement", app, "2026-09-01") == {
        "proposed_status": "acknowledged", "proposed_date_type": "applied", "proposed_date": "2026-09-01"}
    assert propose("rejection", {"status": "interviewing", "applied_date": "x"}, "2026-09-02")["proposed_date_type"] == "rejected"
    assert propose("interview_request", app, "2026-09-02", "2026-09-10T14:00")["proposed_date"] == "2026-09-10T14:00"
    assert propose("recruiter_outreach", app, "2026-09-02")["proposed_status"] is None
    assert propose("rejection", None, "2026-09-02")["proposed_status"] is None


# ── Sync ──────────────────────────────────────────────────────────────────────

def _scenario(fake_llm):
    acme    = _make_app(company="Acme", status="applied")
    globex  = _make_app(company="Globex", status="interviewing")
    initech = _make_app(company="Initech", status="applied")
    messages = [
        _msg(1, "Acme Hiring <no-reply@greenhouse-mail.io>", "Thanks for applying to Acme", "We received your application."),
        _msg(2, "Jobs Weekly <news@jobsweekly.com>", "Your weekly newsletter", "10 new jobs"),
        _msg(3, "Globex Talent <talent@globex.com>", "Update on your Globex application", "Unfortunately we will not be moving forward."),
        _msg(4, "Sam <sam@gmail.com>", "Quick chat?", "Would love to talk about Initech's platform role", thread="t1"),
    ]
    fake_llm.answers = {
        "Thanks for applying to Acme": {"classification": "acknowledgement", "application_uuid": acme, "confidence": 0.95,
                                        "evidence": "We received your application."},
        "Update on your Globex application": {"classification": "rejection", "application_uuid": globex, "confidence": 0.9,
                                              "evidence": "Unfortunately we will not be moving forward."},
        "Quick chat?": {"classification": "interview_request", "application_uuid": initech, "confidence": 0.7,
                        "interview_at": "2026-10-01T10:00:00"},
    }
    return FakeGmail(messages), {"acme": acme, "globex": globex, "initech": initech}


def test_sync_queues_job_emails_ignores_the_rest_and_never_changes_applications(fake_llm):
    gmail, apps = _scenario(fake_llm)
    result = sync.run_sync(gmail)
    assert {k: result[k] for k in ("fetched", "queued", "ignored", "failed", "remaining")} == {
        "fetched": 4, "queued": 3, "ignored": 1, "failed": 0, "remaining": 0}
    assert result["llm_calls"] == 0          # the stub doesn't report usage

    rows = _rows()
    assert rows["m2"]["review_status"] == "auto_ignored"
    assert rows["m2"]["body_excerpt"] is None and rows["m2"]["snippet"] is None   # no content kept
    ack, rej, chat = rows["m1"], rows["m3"], rows["m4"]
    assert (ack["classification"], ack["proposed_status"], ack["proposed_date_type"]) == ("acknowledgement", "acknowledged", "applied")
    assert (rej["proposed_status"], rej["proposed_date_type"]) == ("rejected", "rejected")
    assert (chat["proposed_status"], chat["proposed_date"]) == ("interviewing", "2026-10-01T10:00:00")
    assert all(r["review_status"] == "pending" for r in (ack, rej, chat))
    assert chat["match_reason"] == "body"      # Initech named only in the body: too weak to skip triage

    # Only the unhinted emails were triaged: m1 is from an ATS, m3 names Globex.
    assert fake_llm.calls.count("triage") == 1
    assert fake_llm.calls.count("classify") == 3

    # Review queue first: the sync itself changed nothing.
    assert _app_row(apps["globex"])["status"] == "interviewing"

    state = sync.sync_state()
    assert state["last_internal_date"] == T0 + 4 * 60_000
    assert state["pending"] == 3 and state["last_error"] is None


def test_resync_is_idempotent_and_starts_from_the_watermark(fake_llm):
    gmail, _ = _scenario(fake_llm)
    sync.run_sync(gmail)
    fetched = list(gmail.fetched)

    again = sync.run_sync(gmail)
    assert again["fetched"] == 0 and len(_rows()) == 4
    assert gmail.fetched == fetched
    assert gmail.list_calls[-1] == T0 + 4 * 60_000


def test_max_messages_leaves_the_rest_for_next_time(fake_llm):
    gmail, _ = _scenario(fake_llm)
    first = sync.run_sync(gmail, max_messages=2)
    assert (first["fetched"], first["remaining"]) == (2, 2)
    assert sync.sync_state()["last_internal_date"] == T0 + 2 * 60_000
    second = sync.run_sync(gmail, max_messages=2)
    assert (second["fetched"], second["remaining"]) == (2, 0)
    assert set(_rows()) == {"m1", "m2", "m3", "m4"}


def test_a_failed_classification_is_queued_not_lost(fake_llm):
    gmail, apps = _scenario(fake_llm)
    fake_llm.fail_on = {"Update on your Globex application"}
    result = sync.run_sync(gmail)
    assert result["failed"] == 1
    row = _rows()["m3"]
    assert row["review_status"] == "pending" and row["classification"] is None
    assert row["match_reason"].startswith("classification failed")
    # The deterministic match (Globex in sender and subject) is kept as the suggested link.
    assert row["application_id"] == _app_row(apps["globex"])["id"]


def test_thread_replies_inherit_the_link(monkeypatch):
    acme = _make_app(company="Acme", status="applied")
    first = _msg(1, "Acme <hr@acme.com>", "Interview at Acme", "Can you do Tuesday?", thread="T")
    reply = _msg(2, "Pat <pat@gmail.com>", "Re: Tuesday", "See you then", thread="T")
    seen: list[str] = []

    def answer(system, user, *, max_tokens=1024, label="call"):
        if label == "triage":
            return {"job_related": [0]}
        seen.append(user)
        return {"classification": "interview_request", "application_uuid": acme, "confidence": 0.9}

    monkeypatch.setattr(llm, "call_json", answer)
    sync.run_sync(FakeGmail([first, reply]))
    assert "matched_on=thread" in seen[1]
    assert _rows()["m2"]["match_reason"].startswith("thread")


# ── Review ────────────────────────────────────────────────────────────────────

def _email_id(gmail_id: str) -> int:
    return _rows()[gmail_id]["id"]


def test_accept_applies_status_date_and_logs_the_email(fake_llm):
    gmail, apps = _scenario(fake_llm)
    sync.run_sync(gmail)

    out = review.accept(_email_id("m3"))
    assert out["applied"]["status"] == {"from": "interviewing", "to": "rejected"}
    assert out["applied"]["date"]["type"] == "rejected"
    assert out["review_status"] == "accepted"
    app = _app_row(apps["globex"])
    assert app["status"] == "rejected"
    with db.database.get_connection() as conn:
        events = [r["event_type"] for r in conn.execute(
            "SELECT event_type FROM application_events WHERE application_id = ?", (app["id"],))]
    assert sorted(events) == ["email_received", "status_change"]

    with pytest.raises(review.ReviewError):
        review.accept(_email_id("m3"))


def test_accept_skips_a_status_that_has_since_gone_backwards(fake_llm):
    gmail, apps = _scenario(fake_llm)
    sync.run_sync(gmail)
    with db.database.get_connection() as conn:
        conn.execute("UPDATE applications SET status = 'interviewing' WHERE uuid = ?", (apps["acme"],))
    out = review.accept(_email_id("m1"))
    assert out["applied"]["status"] is None and out["applied"]["skipped"]
    assert out["applied"]["date"] == {"type": "applied", "date": _rows()["m1"]["received_at"][:10]}
    assert _app_row(apps["acme"])["status"] == "interviewing"


def test_relink_recomputes_and_accept_can_override(fake_llm):
    gmail, apps = _scenario(fake_llm)
    sync.run_sync(gmail)
    chat = _email_id("m4")

    unlinked = review.relink(chat, None)
    assert unlinked["application_id"] is None and unlinked["proposed_status"] is None
    with pytest.raises(review.ReviewError, match="Link this email"):
        review.accept(chat)

    out = review.accept(chat, application_uuid=apps["acme"])
    assert out["app_uuid"] == apps["acme"] and out["match_reason"] == "manual"
    assert _app_row(apps["acme"])["status"] == "interviewing"


def test_dismiss_leaves_applications_alone(fake_llm):
    gmail, apps = _scenario(fake_llm)
    sync.run_sync(gmail)
    assert review.dismiss(_email_id("m3"))["review_status"] == "dismissed"
    assert _app_row(apps["globex"])["status"] == "interviewing"
    assert [e["gmail_message_id"] for e in review.list_emails("pending")] == ["m4", "m1"]


def test_engagement_keeps_an_application_alive_but_a_receipt_does_not(fake_llm):
    """Auto-ghost measures silence from real engagement, not from a receipt or a review."""
    from api.routes.applications import _do_auto_ghost
    from datetime import date, timedelta

    gmail, apps = _scenario(fake_llm)
    sync.run_sync(gmail)
    review.accept(_email_id("m4"), apply_status=False, apply_date=False)   # link the interview email
    long_ago = (date.today() - timedelta(days=60)).isoformat()
    with db.database.get_connection() as conn:
        # Both applied two months ago; their emails were only reviewed today.
        for uuid in (apps["acme"], apps["initech"]):
            app_id = conn.execute("SELECT id FROM applications WHERE uuid = ?", (uuid,)).fetchone()[0]
            conn.execute("INSERT INTO application_dates (application_id, date_type, date) VALUES (?, 'applied', ?)",
                         (app_id, long_ago))
        ghosted = {_uuid_for(i) for i in _do_auto_ghost(conn, 21)}
    assert apps["initech"] not in ghosted      # its interview email is recent engagement
    assert apps["acme"] in ghosted             # a receipt from today doesn't revive a 60-day silence


def _uuid_for(app_id: str) -> str:
    with db.database.get_connection() as conn:
        return conn.execute("SELECT uuid FROM applications WHERE id = ?", (app_id,)).fetchone()["uuid"]


# ── Job + HTTP ────────────────────────────────────────────────────────────────

def test_sync_job_refuses_when_gmail_is_not_connected(monkeypatch):
    import pipeline.inbox.auth as auth
    monkeypatch.setattr(auth, "connection_status", lambda: {"connected": False, "detail": "no token"})
    from pipeline.jobs.base import JobError
    with pytest.raises(JobError, match="no token"):
        sync.start_sync_job()


def test_sync_runs_as_a_job_and_the_queue_works_over_http(monkeypatch, fake_llm):
    import pipeline.inbox.auth as auth
    import pipeline.inbox.gmail_client as gc

    gmail, apps = _scenario(fake_llm)
    monkeypatch.setattr(auth, "connection_status", lambda: {"connected": True, "detail": None})
    monkeypatch.setattr(gc, "GmailClient", lambda: gmail)

    client = TestClient(api_app)
    res = client.post("/inbox/sync")
    assert res.status_code == 202
    job = service.wait(res.json()["job_id"], timeout=10)
    assert job["status"] == "succeeded", job.get("error")
    assert job["result"]["queued"] == 3

    pending = client.get("/inbox").json()
    assert {e["subject"] for e in pending} == {"Thanks for applying to Acme", "Update on your Globex application", "Quick chat?"}
    assert len(client.get("/inbox?review_status=all").json()) == 4
    assert len(client.get(f"/inbox?review_status=all&application_uuid={apps['globex']}").json()) == 1

    rej = next(e for e in pending if e["classification"] == "rejection")
    assert rej["app_company"] == "Globex"
    res = client.post(f"/inbox/{rej['id']}/accept", json={})
    assert res.status_code == 200 and res.json()["applied"]["status"]["to"] == "rejected"
    assert client.post(f"/inbox/{rej['id']}/accept", json={}).status_code == 409
    assert client.get("/inbox/999999").status_code == 404


# ── Gmail rate limits ─────────────────────────────────────────────────────────

def _http_error(status: int, reason: str = "rateLimitExceeded"):
    import json

    import httplib2
    from googleapiclient.errors import HttpError

    body = {"error": {"code": status, "message": "Quota exceeded", "errors": [{"reason": reason}],
                      "details": [{"reason": reason}]}}
    return HttpError(httplib2.Response({"status": status}), json.dumps(body).encode())


class _Req:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)

    def execute(self):
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def _client():
    from pipeline.inbox.gmail_client import GmailClient

    c = object.__new__(GmailClient)
    c.sleeps = []
    c._sleep, c._clock, c._last = c.sleeps.append, lambda: 1e9, 0.0
    return c


def test_gmail_client_backs_off_and_retries_rate_limits():
    c = _client()
    assert c._execute(_Req([_http_error(403), _http_error(429), {"ok": True}])) == {"ok": True}
    assert [s for s in c.sleeps if s >= 1] == [2.0, 4.0]    # backoff; the rest is 0.2s pacing


def test_gmail_client_does_not_retry_real_errors():
    from googleapiclient.errors import HttpError

    c = _client()
    with pytest.raises(HttpError):
        c._execute(_Req([_http_error(403, reason="insufficientPermissions")]))
    assert c.sleeps == []


def test_a_failure_part_way_keeps_earlier_batches(fake_llm, monkeypatch):
    monkeypatch.setattr(sync, "BATCH_SIZE", 2)
    gmail, _ = _scenario(fake_llm)
    real_get = gmail.get_message

    def flaky(mid):
        if mid == "m3":
            raise RuntimeError("quota")
        return real_get(mid)

    gmail.get_message = flaky
    with pytest.raises(RuntimeError):
        sync.run_sync(gmail)
    assert set(_rows()) == {"m1", "m2"}
    assert sync.sync_state()["last_internal_date"] == T0 + 2 * 60_000
    assert "quota" in sync.sync_state()["last_error"]

    gmail.get_message = real_get
    assert sync.run_sync(gmail)["fetched"] == 2
    assert set(_rows()) == {"m1", "m2", "m3", "m4"}


# ── Token limits ──────────────────────────────────────────────────────────────

def test_common_word_company_names_need_a_cue():
    apps = [{"id": "f", "uuid": "fri", "company": "Friday", "role": "GTM Engineer", "status": "applied"},
            {"id": "s", "uuid": "sam", "company": "Sam", "role": "Account Manager", "status": "applied"}]
    assert matcher.shortlist(_msg(1, "News <n@x.com>", "Your Friday digest", "see you on friday"), apps) == []
    assert matcher.shortlist(_msg(2, "Sam Smith <sam@gmail.com>", "Lunch?"), apps) == []
    assert [c.uuid for c in matcher.shortlist(_msg(3, "Friday Talent <t@ashbyhq.com>", "Thanks"), apps)] == ["fri"]
    assert [c.uuid for c in matcher.shortlist(_msg(4, "HR <h@x.com>", "Thanks for applying to Friday"), apps)] == ["fri"]


def test_clean_body_drops_quotes_signatures_and_footers():
    from pipeline.inbox.classifier import clean_body

    body = ("Hi Matt,\n\nUnfortunately we won't be moving forward.\n\nBest,\nRita\n-- \nRita | Talent\n"
            "On Mon, 1 Sep 2026 at 10:00, Matt <m@x.com> wrote:\n> Any update?\n")
    assert clean_body(body) == "Hi Matt,\n\nUnfortunately we won't be moving forward.\n\nBest,\nRita"
    long = "Real content. " * 40 + "\nTo unsubscribe from these emails click here\nLegal text " * 5
    assert "unsubscribe" not in clean_body(long)
    assert clean_body("x" * 5000).endswith("…") and len(clean_body("x" * 5000)) < 2600


def test_fallback_list_only_when_nothing_matched():
    from pipeline.inbox.classifier import OTHERS_LIMIT, other_open_apps

    apps = [{"uuid": f"u{i}", "status": "applied"} for i in range(40)]
    assert len(other_open_apps(apps, [])) == OTHERS_LIMIT
    assert other_open_apps(apps, [matcher.Candidate(apps[0], score=15)]) == []


def test_sync_reports_token_usage(monkeypatch):
    _make_app(company="Acme", status="applied")
    monkeypatch.setattr(llm, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(llm, "_call_anthropic", lambda s, u, m: ('{"classification": "not_job", "application_uuid": null, "confidence": 0}', 120, 30))
    result = sync.run_sync(FakeGmail([_msg(1, "Acme <a@acme.com>", "Acme news", "hello")]))
    assert (result["llm_calls"], result["input_tokens"], result["output_tokens"]) == (1, 120, 30)


def test_never_reads_further_back_than_the_lookback_window(monkeypatch):
    _make_app(company="Acme", status="applied")
    with db.database.get_connection() as conn:
        conn.execute("UPDATE applications SET created_at = '2020-01-01'")
        assert sync.start_point(conn) >= int((time.time() - 90 * 86400) * 1000) - 5000
        conn.execute("UPDATE gmail_sync_state SET last_internal_date = 1000")   # stale watermark
        assert sync.start_point(conn) >= int((time.time() - 90 * 86400) * 1000) - 5000


# ── Agent mode (classify over MCP) ────────────────────────────────────────────

def test_agent_mode_gathers_without_model_calls(fake_llm):
    gmail, _ = _scenario(fake_llm)
    result = sync.run_sync(gmail, classifier="agent")
    assert (result["untriaged"], result["queued"], result["llm_calls"]) == (4, 0, 0)
    assert fake_llm.calls == []
    rows = _rows()
    assert {r["review_status"] for r in rows.values()} == {"untriaged"}
    assert rows["m1"]["match_reason"] == "ats_sender" and rows["m2"]["match_reason"] is None
    assert sync.sync_state()["untriaged"] == 4


def test_agent_triage_round_trip(fake_llm):
    from pipeline.inbox import agent

    gmail, apps = _scenario(fake_llm)
    sync.run_sync(gmail, classifier="agent")

    batch = agent.untriaged(limit=10)
    assert "classify_inbox_emails" in batch["instructions"] and batch["remaining"] == 0
    by_subject = {e["subject"]: e for e in batch["emails"]}
    assert [e["hint"] for e in batch["emails"]][-1] is None                 # hinted first
    globex = by_subject["Update on your Globex application"]
    assert globex["candidates"][0]["uuid"] == apps["globex"] and "Unfortunately" in globex["body"]

    out = agent.classify([
        {"email_id": globex["email_id"], "classification": "rejection", "application_uuid": apps["globex"],
         "confidence": 0.9, "evidence": "Unfortunately we will not be moving forward.", "summary": "Rejected"},
        {"email_id": by_subject["Your weekly newsletter"]["email_id"], "classification": "not_job"},
        {"email_id": by_subject["Quick chat?"]["email_id"], "classification": "interview_request",
         "application_uuid": "no-such-uuid"},
        {"email_id": by_subject["Thanks for applying to Acme"]["email_id"], "classification": "bogus"},
    ])
    assert [u["review_status"] for u in out["updated"]] == ["pending", "auto_ignored"]
    assert out["updated"][0]["proposed_status"] == "rejected"
    assert len(out["errors"]) == 2 and out["untriaged_remaining"] == 2

    rows = _rows()
    assert rows["m2"]["body_excerpt"] is None and rows["m2"]["snippet"] is None    # ignored → body wiped
    assert rows["m3"]["model"] == "agent"

    # Classified by the agent, still only applied when accepted.
    assert _app_row(apps["globex"])["status"] == "interviewing"
    review.accept(rows["m3"]["id"])
    assert _app_row(apps["globex"])["status"] == "rejected"
    assert agent.classify([{"email_id": rows["m3"]["id"], "classification": "offer"}])["errors"]


def test_inbox_mcp_tools_are_registered():
    import asyncio

    import mcp_server.server  # noqa: F401 — registers tools
    from mcp_server.app import mcp

    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert {"sync_inbox", "list_inbox", "get_untriaged_emails", "classify_inbox_emails",
            "accept_inbox_email", "relink_inbox_email", "dismiss_inbox_email"} <= names


def test_nav_polls_are_kept_out_of_the_access_log():
    import logging

    from api.main import _QuietPolls

    def rec(path, status=200, method="GET"):
        return logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                                 ("127.0.0.1:1", method, path, "1.1", status), None)

    f = _QuietPolls()
    assert not f.filter(rec("/jobs?status=draft&limit=100"))
    assert not f.filter(rec("/inbox/counts"))
    assert f.filter(rec("/inbox/counts", status=500))
    assert f.filter(rec("/inbox?review_status=pending"))
    assert f.filter(rec("/jobs/abc"))
    assert TestClient(api_app).get("/inbox/counts").json() == {"pending": 0, "untriaged": 0}
