"""
Tests for pipeline/insights/outcomes.py — outcomes derived from history, not status.

The first group runs derive() directly on the shapes of history that motivated it
(screened out, rejected after interviews, ghosted after interviews) and the edge cases
in the plan's Verification section. Company names here are placeholders. The rest go through
the database and the API: cohort exclusions, the outside-inbox flag, and totals that add up.
"""

import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import db.database
from api.main import app as api_app
from pipeline.insights import outcomes as oc
from tests.test_jobs import fresh_db, fresh_runner  # noqa: F401 — fixtures

TODAY    = date(2026, 9, 19)
COVERAGE = date(2026, 4, 27)


def _d(days_ago: int) -> str:
    return (TODAY - timedelta(days=days_ago)).isoformat()


def _derive(status, emails=(), events=(), applied=None, logged=(), **app):
    row = {"uuid": "u", "company": "Co", "role": "R", "status": status, "created_at": _d(60), "updated_at": _d(1), **app}
    return oc.derive(
        row,
        [{"classification": c, "received_at": _d(ago)} for c, ago in emails],
        [{"event_type": "status_change", "to_status": to, "detail": detail, "occurred_at": _d(ago)} for to, detail, ago in events],
        [applied] if applied else [],
        [{"date_type": t, "date": _d(ago), "notes": n} for t, ago, n in logged],
        today=TODAY, coverage_start=COVERAGE,
    )


# ── Known cases ───────────────────────────────────────────────────────────────

def test_receipt_then_rejection_is_screened_out():
    o = _derive("rejected", [("acknowledgement", 2), ("rejection", 0)], applied=_d(2))
    assert (o.outcome, o.engaged, o.answered) == ("screened_out", False, True)


def test_interviews_then_rejection_is_rejected_after_engagement():
    emails = [("acknowledgement", 100), ("interview_request", 85), ("interview_request", 74), ("rejection", 59)]
    o = _derive("rejected", emails, applied=_d(101))
    assert o.outcome == "rejected_after_engagement"
    assert o.furthest_stage == "interview" and o.first_interview == TODAY - timedelta(days=85)


def test_interviews_then_silence_is_ghosted():
    emails = [("acknowledgement", 53)] + [("interview_request", d) for d in (52, 50, 47, 45)]
    assert _derive("ghosted", emails, applied=_d(53)).outcome == "ghosted"
    # Silence past the window counts even before the status is changed.
    assert _derive("interviewing", emails, applied=_d(53)).outcome == "ghosted"


# ── Edge cases ────────────────────────────────────────────────────────────────

def test_no_email_yet_is_excluded_then_unanswered_and_flagged():
    assert _derive("applied", applied=_d(3)).exclusion == "no_receipt_yet"
    o = _derive("applied", applied=_d(40))
    assert o.outcome == "unanswered"
    assert o.flags == ["no_receipt", "suggest_outside_inbox"]


def test_receipt_only_inside_the_window_is_awaiting_reply():
    o = _derive("acknowledged", [("acknowledgement", 5)], applied=_d(5))
    assert (o.outcome, o.answered, o.furthest_stage) == ("awaiting_reply", False, "receipt")


def test_ghosted_status_without_engagement_is_unanswered():
    assert _derive("ghosted", [("acknowledgement", 10)], applied=_d(10)).outcome == "unanswered"


def test_offer_outranks_a_later_rejection():
    o = _derive("rejected", [("interview_request", 30), ("offer", 20), ("rejection", 10)], applied=_d(40))
    assert o.outcome == "offer"


def test_manual_interview_counts_as_engagement():
    o = _derive("interviewing", events=[("interviewing", None, 3)], applied=_d(10))
    assert (o.outcome, o.evidence_source) == ("in_progress", "manual")


def test_interview_dates_logged_by_hand_count_as_engagement():
    # A round that happened outside the inbox, logged with log_date.
    o = _derive("rejected", [("acknowledgement", 30), ("rejection", 5)], applied=_d(30),
                logged=[("interview", 10, "Round 2, date approximate")])
    assert (o.outcome, o.evidence_source) == ("rejected_after_engagement", "both")
    # A rejected date written by accepting an email isn't counted twice.
    o = _derive("rejected", [("acknowledgement", 30), ("rejection", 5)], applied=_d(30),
                logged=[("rejected", 5, "From email: Update")])
    assert o.evidence_source == "email"


def test_status_changes_from_inbox_emails_are_not_double_counted():
    o = _derive("interviewing", [("interview_request", 3)], events=[("interviewing", "From email: Interview", 3)], applied=_d(10))
    assert o.evidence_source == "email"


def test_engagement_is_judged_by_content_not_sender():
    # A progress update (e.g. from a no-reply ATS address) is engagement; "other" mail is not.
    assert _derive("applied", [("progress_update", 5)], applied=_d(10)).engaged
    assert not _derive("applied", [("other_job", 5)], applied=_d(10)).engaged


def test_a_rejection_after_only_a_receipt_is_screened_out_not_unanswered():
    assert _derive("rejected", [("acknowledgement", 50), ("rejection", 45)], applied=_d(50)).outcome == "screened_out"


def test_exclusions():
    assert _derive("generated").exclusion == "not_submitted"
    assert _derive("applied", [("acknowledgement", 5)], applied=_d(5), outside_inbox=1).exclusion == "outside_inbox"
    assert _derive("applied", applied="2026-03-01").exclusion == "before_coverage"


def test_archived_counts_only_with_emails():
    assert _derive("archived", applied=_d(40)).exclusion == "archived_no_email"
    o = _derive("archived", [("acknowledgement", 40), ("interview_request", 30)], applied=_d(40))
    assert o.in_scope and o.outcome == "ghosted"


def test_an_applied_date_later_than_the_first_email_is_corrected():
    o = _derive("ghosted", [("interview_request", 60)], applied=_d(30))
    assert o.applied_on == TODAY - timedelta(days=60)
    assert "applied_date_after_first_email" in o.flags


# ── Database, API and MCP ─────────────────────────────────────────────────────

def _app(company: str, status: str, applied_days_ago=None, emails=()) -> str:
    app_id, app_uuid = f"app-{uuid.uuid4().hex[:6]}", str(uuid.uuid4())
    with db.database.get_connection() as conn:
        conn.execute(
            "INSERT INTO applications (id, uuid, company, role, status, output_dir) VALUES (?, ?, ?, 'Engineer', ?, '')",
            (app_id, app_uuid, company, status),
        )
        if applied_days_ago is not None:
            conn.execute("INSERT INTO application_dates (application_id, date_type, date) VALUES (?, 'applied', ?)",
                         (app_id, (date.today() - timedelta(days=applied_days_ago)).isoformat()))
        for i, (cls, ago, review) in enumerate(emails):
            conn.execute(
                """INSERT INTO inbox_emails (gmail_message_id, application_id, classification, received_at, review_status)
                   VALUES (?, ?, ?, ?, ?)""",
                (f"{app_id}-{i}", app_id, cls, (date.today() - timedelta(days=ago)).isoformat(), review),
            )
    return app_uuid


def test_only_accepted_emails_are_evidence():
    # Two interview emails for another company were mismatched and dismissed.
    u = _app("Globex", "rejected", 5, [("interview_request", 20, "dismissed"), ("acknowledgement", 5, "accepted"),
                                     ("rejection", 1, "accepted"), ("interview_request", 2, "pending")])
    with db.database.get_connection() as conn:
        assert oc.outcome_for(conn, u)["outcome"] == "screened_out"


def test_api_totals_add_up_and_the_flag_excludes():
    engaged = _app("Acme", "interviewing", 10, [("acknowledgement", 10, "accepted"), ("interview_request", 4, "accepted")])
    _app("Globex", "applied", 30, [("acknowledgement", 30, "accepted")])
    _app("Initech", "generated")
    client = TestClient(api_app)

    body = client.get("/insights/outcomes").json()
    s = body["summary"]
    assert s["total"] == 3 and s["in_scope"] == 2 and s["engaged"] == 1
    assert sum(s["by_outcome"].values()) + sum(s["by_exclusion"].values()) == s["total"]
    assert {a["outcome"] for a in body["applications"]} == {"in_progress", "unanswered"}
    assert len(client.get("/insights/outcomes?include_excluded=true").json()["applications"]) == 3

    assert client.patch(f"/applications/{engaged}", json={"outside_inbox": True}).status_code == 200
    one = client.get(f"/insights/outcomes/{engaged}").json()
    assert (one["in_scope"], one["exclusion"]) == (False, "outside_inbox")
    assert client.get("/insights/outcomes/nope").status_code == 404
    assert client.get("/insights/outcomes?outcome=bogus").status_code == 422


def test_mcp_outcome_tools():
    import asyncio

    import mcp_server.server  # noqa: F401 — registers tools
    from mcp_server.app import mcp
    from mcp_server.tools_applications import get_application, update_application
    from mcp_server.tools_insights import get_outcomes

    assert "get_outcomes" in {t.name for t in asyncio.run(mcp.list_tools())}
    u = _app("Acme", "rejected", 10, [("acknowledgement", 10, "accepted"), ("rejection", 2, "accepted")])
    assert get_application(u)["outcome"]["outcome"] == "screened_out"
    assert get_outcomes(outcome="screened_out")["summary"]["by_outcome"]["screened_out"] == 1
    update_application(u, outside_inbox=True)
    assert get_application(u)["outcome"]["exclusion"] == "outside_inbox"


# ── Phase 2: rounds, progress updates, re-classification, statuses ────────────

def test_count_rounds_merges_dates_within_a_day():
    d = lambda s: date.fromisoformat(s)  # noqa: E731
    # Screen, hiring manager, technical, then a panel and an exec interview each moved by two days
    dates = ["2026-03-02", "2026-03-02", "2026-03-10", "2026-03-15", "2026-03-29", "2026-03-30", "2026-03-31", "2026-04-01"]
    assert oc.count_rounds(d(x) for x in dates) == 4
    assert oc.count_rounds([]) == 0
    assert oc.count_rounds([d("2026-05-06"), d("2026-05-06")]) == 1


def test_rounds_combine_booked_email_dates_and_logged_rounds():
    row = {"uuid": "u", "company": "Hooli", "role": "R", "status": "rejected", "created_at": _d(60), "updated_at": _d(1)}
    emails = [
        {"classification": "interview_request", "received_at": _d(40), "extracted_json": '{"interview_at": "%s"}' % _d(36)},
        {"classification": "rejection", "received_at": _d(30)},
    ]
    logged = [{"date_type": "interview", "date": _d(34), "notes": "approx"}, {"date_type": "interview", "date": _d(32), "notes": "approx"}]
    o = oc.derive(row, emails, [], [_d(45)], logged, today=TODAY, coverage_start=COVERAGE)
    assert o.rounds == 3 and o.outcome == "rejected_after_engagement"
    # Logged rounds on consecutive days are still separate rounds; one logged on the day of an
    # emailed booking is the same round.
    logged = [{"date_type": "interview", "date": _d(35), "notes": ""}, {"date_type": "interview", "date": _d(34), "notes": ""},
              {"date_type": "interview", "date": _d(36), "notes": "same as the emailed booking"}]
    assert oc.derive(row, emails, [], [_d(45)], logged, today=TODAY, coverage_start=COVERAGE).rounds == 3
    # An interview email with no booked date still counts as one round.
    assert _derive("interviewing", [("interview_request", 3)], applied=_d(10)).rounds == 1


def test_reclassifying_an_accepted_email_changes_the_outcome_not_the_status():
    from pipeline.inbox import agent, review

    u = _app("Acme", "applied", 30, [("acknowledgement", 30, "accepted"), ("other_job", 10, "accepted")])
    with db.database.get_connection() as conn:
        assert oc.outcome_for(conn, u)["engaged"] is False
    other = review.list_emails("accepted", classification="other_job")
    assert len(other) == 1
    out = agent.reclassify_accepted([
        {"email_id": other[0]["id"], "classification": "progress_update", "summary": "Shortlisted"},
        {"email_id": other[0]["id"], "classification": "not_job"},
        {"email_id": 99999, "classification": "offer"},
    ])
    assert out["updated"] == [{"email_id": other[0]["id"], "from": "other_job", "to": "progress_update"}]
    assert len(out["errors"]) == 2
    email = review.get_email(other[0]["id"])
    assert email["review_status"] == "accepted" and email["extracted"]["previous_classification"] == "other_job"
    with db.database.get_connection() as conn:
        o = oc.outcome_for(conn, u)
        status = conn.execute("SELECT status FROM applications WHERE uuid = ?", (u,)).fetchone()[0]
    assert o["engaged"] and o["furthest_stage"] == "update" and status == "applied"


def test_reclassify_refuses_emails_not_yet_accepted():
    from pipeline.inbox import agent, review

    _app("Acme", "applied", 5, [("other_job", 2, "pending")])
    pending = review.list_emails("pending")[0]
    assert agent.reclassify_accepted([{"email_id": pending["id"], "classification": "progress_update"}])["errors"]


def test_progress_update_is_a_valid_classification_with_no_status_change():
    from pipeline.inbox.proposals import CLASSIFICATIONS, propose

    assert "progress_update" in CLASSIFICATIONS
    assert propose("progress_update", {"status": "applied", "applied_date": "x"}, "2026-09-01")["proposed_status"] is None


def test_one_status_list_shared_by_api_and_mcp():
    import api.routes.applications as api_apps
    import mcp_server.tools_applications as mcp_apps
    from pipeline import statuses

    assert api_apps.VALID_STATUSES is statuses.VALID_STATUSES is mcp_apps.VALID_STATUSES
    assert "analysed" in statuses.VALID_STATUSES


# ── Phase 3: report ───────────────────────────────────────────────────────────

def test_wilson_interval():
    from pipeline.insights.report import wilson

    lo, hi = wilson(81, 134)
    assert 0.51 < lo < 0.53 and 0.68 < hi < 0.69
    assert wilson(0, 10)[0] == 0 and wilson(0, 0) is None


def test_response_window_is_data_driven_with_a_floor_and_a_fallback(monkeypatch):
    def answered(days):
        o = oc.Outcome(uuid="u", company="C", role="R", status="rejected", in_scope=True)
        o.applied_on, o.first_answer = TODAY - timedelta(days=60), TODAY - timedelta(days=60 - days)
        return o

    assert oc.response_window([answered(5)] * 3)["basis"] == "default"
    w = oc.response_window([answered(d) for d in (2, 3, 4, 5, 6, 7, 8, 9, 30, 40)])
    assert (w["basis"], w["p90"], w["days"]) == ("data", 30, 30)
    assert oc.response_window([answered(3)] * 12)["days"] == oc.WINDOW_FLOOR_DAYS
    monkeypatch.setattr(oc, "_WINDOW_OVERRIDE", "45")
    monkeypatch.setattr(oc, "RESPONSE_WINDOW_DAYS", 45)
    assert oc.response_window([answered(3)] * 12) == {"days": 45, "basis": "setting", "sample": 12, "p90": None}


def test_ats_is_named_from_sender_domains():
    assert oc.ats_of(["us.greenhouse-mail.io", "acme.com", "greenhouse-mail.io"]) == "greenhouse"
    assert oc.ats_of(["jobs.ashbyhq.com"]) == "ashby"
    assert oc.ats_of(["acme.myworkdayjobs.com"]) == "workday"
    assert oc.ats_of(["acme.com", None]) is None


def test_funnel_breakdown_and_timing():
    from pipeline.insights import report

    _app("Acme", "interviewing", 20, [("acknowledgement", 20, "accepted"), ("interview_request", 15, "accepted")])
    _app("Globex", "rejected", 30, [("acknowledgement", 30, "accepted"), ("rejection", 25, "accepted")])
    _app("Initech", "applied", 60, [("acknowledgement", 60, "accepted")])
    with db.database.get_connection() as conn:
        r = report.build(conn)

    steps = {s["key"]: s for s in r["funnel"]["steps"]}
    assert steps["in_scope"]["of_applied"]["count"] == 3
    assert steps["answered"]["of_applied"]["count"] == 2
    assert steps["interviewed"]["of_applied"]["count"] == 1
    assert steps["interviewed"]["from_previous"]["of"] == 1          # of the engaged
    assert r["funnel"]["receipts"]["count"] == 3
    tiers = r["breakdowns"]["tier"]
    assert tiers[0]["group"] == "(unset)" and tiers[0]["applications"] == 3 and tiers[0]["small"]
    timing = {t["key"]: t for t in r["timing"]}
    assert timing["first_interview"]["median"] == 5 and timing["screened_out"]["n"] == 1
    assert len(r["applications"]) == 3 and r["excluded"] == []


def test_insights_report_api_and_success_stats():
    from mcp_server.tools_insights import get_success_stats

    _app("Acme", "rejected", 30, [("acknowledgement", 30, "accepted"), ("rejection", 25, "accepted")])
    client = TestClient(api_app)
    body = client.get("/insights/report").json()
    assert {"summary", "funnel", "breakdowns", "timing", "applications", "excluded"} <= set(body)
    assert client.get("/insights/funnel?group_by=month").status_code == 200
    assert client.get("/insights/funnel?group_by=bogus").status_code == 422
    assert "response_window" in client.get("/insights/timing").json()

    stats = get_success_stats(group_by="company")
    assert stats[0]["group"] == "Acme" and stats[0]["by_outcome"]["screened_out"] == 1
    with pytest.raises(ValueError):
        get_success_stats(group_by="status")


# ── Phase 4: forecast ─────────────────────────────────────────────────────────

def test_survival_and_bayes_update():
    from pipeline.insights import forecast as fc

    g = fc.Survival([0, 2, 4, 6, 30], window=34)
    assert g(0) > 0.9 and g(5) < g(3) < g(0) and g(100) > 0            # never exactly zero
    assert fc.Survival([1], window=21)(10) == 1.0 and fc.Survival([1], window=21)(40) == 0.1
    assert fc.still_to_come(0.2, 1.0) == pytest.approx(0.2)            # day 0 = base rate
    assert fc.still_to_come(0.2, 0.1) < 0.03
    assert fc.still_to_come(0, 1.0) == 0


def _history():
    """20 settled unengaged, 5 that screened within a week (3 went to round 2), plus open ones."""
    for i in range(20):
        _app(f"Co{i}", "rejected", 60, [("acknowledgement", 60, "accepted"), ("rejection", 50, "accepted")])
    for i in range(5):
        emails = [("acknowledgement", 60, "accepted"), ("interview_request", 57 - i, "accepted"), ("rejection", 30, "accepted")]
        u = _app(f"Eng{i}", "rejected", 60, emails)
        if i < 3:
            with db.database.get_connection() as conn:
                app_id = conn.execute("SELECT id FROM applications WHERE uuid = ?", (u,)).fetchone()[0]
                conn.execute("INSERT INTO application_dates (application_id, date_type, date) VALUES (?, 'interview', ?)",
                             (app_id, (date.today() - timedelta(days=45)).isoformat()))
    return {
        "fresh": _app("Fresh", "applied", 1),                                           # no receipt yet
        "week":  _app("Week", "acknowledged", 7, [("acknowledgement", 7, "accepted")]),
        "stale": _app("Stale", "acknowledged", 40, [("acknowledgement", 40, "accepted")]),
        "live":  _app("Live", "interviewing", 10, [("acknowledgement", 10, "accepted"), ("interview_request", 3, "accepted")]),
    }


def test_forecast_decays_with_silence_and_ranks_live_processes_first():
    from pipeline.insights import forecast as fc

    apps = _history()
    with db.database.get_connection() as conn:
        f = fc.load(conn)
    m = f["model"]
    # 25 settled by rejection, plus the stale one: past the window it counts as Unanswered
    assert (m["screen_observed"]["count"], m["screen_observed"]["of"]) == (5, 26)
    assert m["round_observed"]["1"] == {"count": 3, "of": 5}

    by = {s["uuid"]: s for s in f["applications"]}
    assert set(by) == set(apps.values())                          # closed ones aren't scored
    fresh, week, stale, live = (by[apps[k]] for k in ("fresh", "week", "stale", "live"))
    assert fresh["p_screen"] > week["p_screen"] > stale["p_screen"]
    assert stale["p_screen"] < 0.02                               # a month-old receipt is worth ~nothing
    assert live["p_screen"] == 1 and live["waiting_for"] == "round 2"
    assert f["applications"][0]["uuid"] == apps["live"]
    assert f["per_application"]["offer"] is None
    assert f["pipeline"]["expected"]["screens"] == pytest.approx(
        sum(s["p_screen"] for s in f["applications"] if s["waiting_for"] == "screen"), abs=0.01)


def test_forecast_api_mcp_and_application_score():
    from mcp_server.tools_applications import get_application
    from mcp_server.tools_insights import get_pipeline_health

    apps = _history()
    client = TestClient(api_app)
    body = client.get("/insights/forecast").json()
    assert {"model", "pipeline", "per_application", "applications", "decay"} <= set(body)
    one = client.get(f"/insights/outcomes/{apps['week']}").json()
    assert one["score"]["waiting_for"] == "screen" and 0 < one["score"]["p_screen"] < 1
    assert len(get_pipeline_health(top=2)["applications"]) == 2
    assert get_application(apps["live"])["outcome"]["score"]["waiting_for"] == "round 2"


def test_weekly_target_and_data_driven_ghost_threshold(monkeypatch):
    import pipeline.config as config
    from api.routes.applications import ghost_threshold_days
    from pipeline.insights import forecast as fc

    _history()
    monkeypatch.setattr(config, "WEEKLY_SCREEN_TARGET", 2.0)
    with db.database.get_connection() as conn:
        plan = fc.load(conn)["plan"]
        assert plan["weekly_screen_target"] == 2.0 and plan["applications_needed"] > 2
        monkeypatch.setattr(config, "AUTO_GHOST_USE_WINDOW", False)
        assert ghost_threshold_days(conn) == config.AUTO_GHOST_DAYS
        monkeypatch.setattr(config, "AUTO_GHOST_USE_WINDOW", True)
        _, window = oc.load_with_window(conn)
        assert ghost_threshold_days(conn) == (window["days"] if window["basis"] == "data" else config.AUTO_GHOST_DAYS)


def test_an_undated_invite_well_before_a_dated_round_is_its_own_round():
    row = {"uuid": "u", "company": "Co", "role": "R", "status": "rejected", "created_at": _d(60), "updated_at": _d(1)}
    screening = {"classification": "interview_request", "received_at": _d(40)}                      # no date
    booked    = {"classification": "interview_request", "received_at": _d(28), "extracted_json": '{"interview_at": "%s"}' % _d(23)}
    assert oc.derive(row, [screening, booked], [], [_d(45)], today=TODAY, coverage_start=COVERAGE).rounds == 2
    # The scheduling email a few days before its booking is the same round.
    scheduling = {"classification": "interview_request", "received_at": _d(26)}
    assert oc.derive(row, [scheduling, booked], [], [_d(45)], today=TODAY, coverage_start=COVERAGE).rounds == 1


def test_pipeline_separates_live_from_stalled_and_says_where_expectations_come_from():
    from pipeline.insights import forecast as fc

    apps = _history()
    with db.database.get_connection() as conn:
        # An interviewed application that went quiet long ago is stalled, not in process.
        conn.execute("UPDATE applications SET status = 'ghosted' WHERE uuid = ?", (apps["live"],))
        f = fc.load(conn)
    p = f["pipeline"]
    assert (p["in_process"], p["stalled"]) == (0, 1)
    assert p["awaiting_screen"] == 3 and p["open"] == 4
    ef = p["expected_from"]
    assert ef["stalled"]["second_rounds"] > 0 and ef["in_process"]["second_rounds"] == 0
    # awaiting_screen is the live + cold pair, so sum the disjoint groups only.
    disjoint = ("awaiting_screen", "in_process", "stalled")
    assert p["expected"]["second_rounds"] == pytest.approx(
        sum(ef[k]["second_rounds"] for k in disjoint), abs=0.02)
    assert ef["awaiting_screen"]["screens"] == pytest.approx(
        ef["awaiting_screen_live"]["screens"] + ef["awaiting_screen_cold"]["screens"], abs=0.02)
    assert p["expected"]["screens"] == pytest.approx(ef["awaiting_screen"]["screens"], abs=0.02)


# ── Auto-ghost: silence measured from meaningful activity ─────────────────────

def test_a_receipt_does_not_keep_an_application_alive():
    from api.routes.applications import _do_auto_ghost

    # Acknowledged 60 days ago; the receipt was only reviewed (accepted) today.
    quiet = _app("Acme", "acknowledged", 60, [("acknowledgement", 58, "accepted"), ("other_job", 2, "accepted")])
    fresh = _app("Globex", "acknowledged", 10, [("acknowledgement", 10, "accepted")])
    engaged = _app("Initech", "interviewing", 60, [("acknowledgement", 58, "accepted"), ("interview_request", 5, "accepted")])
    with db.database.get_connection() as conn:
        ghosted = _do_auto_ghost(conn, 21)
        statuses = {r[0]: r[1] for r in conn.execute("SELECT uuid, status FROM applications")}
    assert len(ghosted) == 1
    assert statuses[quiet] == "ghosted"                 # a survey and a stale receipt aren't signs of life
    assert statuses[fresh] == "acknowledged"            # still inside the threshold
    assert statuses[engaged] == "interviewing"          # a real interview 5 days ago counts

    with db.database.get_connection() as conn:
        detail = conn.execute(
            "SELECT detail FROM application_events WHERE event_type = 'status_change' AND to_status = 'ghosted'"
        ).fetchone()[0]
    assert "60 days" in detail and "threshold 21" in detail


def test_headline_infers_second_rounds_from_expected_screens():
    """Screens from every open application yet to screen; second rounds inferred from those."""
    from pipeline.insights import forecast as fc

    _history()
    _app("Cold", "acknowledged", 120, [("acknowledgement", 120, "accepted")])
    with db.database.get_connection() as conn:
        f = fc.load(conn)
    p, h = f["pipeline"], f["pipeline"]["headline"]
    assert p["cold"] >= 1 and p["live"] + p["cold"] == p["awaiting_screen"]
    assert h["screens"] == p["expected_from"]["awaiting_screen"]["screens"]
    assert h["second_rounds_from_screens"] == pytest.approx(h["screens"] * h["second_round_rate"], abs=0.01)
    assert h["second_rounds"] == pytest.approx(
        h["second_rounds_from_screens"] + h["second_rounds_in_process"], abs=0.01)
    # Stalled applications are reported, but never folded into the headline.
    assert h["second_rounds_in_process"] == p["expected_from"]["in_process"]["second_rounds"]
    assert "stalled" in p["expected_from"]
