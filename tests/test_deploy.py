"""
Deployment-facing behaviour: the access password, locked instances, the scrape guard,
per-instance paths (ATAT_HOME), and the web/Python status lists staying in step.

None of these touch the database, and the app is built without running its startup
hooks, so no migrations or scheduler start.
"""

import asyncio
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import api.auth as auth
import pipeline.config as config
from api.main import create_app
from api.routes.scrape import assert_public_url

REPO = Path(__file__).resolve().parent.parent
PASSWORD = "correct horse battery"


def _client(monkeypatch, password: str = PASSWORD) -> TestClient:
    monkeypatch.setattr(config, "ATAT_PASSWORD", password)
    auth._fails.clear()
    return TestClient(create_app())


# ── Access password ───────────────────────────────────────────────────────────

def test_no_password_means_no_login(monkeypatch):
    client = _client(monkeypatch, password="")
    assert client.get("/settings").status_code == 200
    assert client.get("/auth/status").json() == {"auth_required": False, "authenticated": True}


def test_routes_need_a_session_when_a_password_is_set(monkeypatch):
    client = _client(monkeypatch)
    assert client.get("/settings").status_code == 401
    assert client.get("/counts").status_code == 401
    assert client.get("/health").status_code == 200          # container health check stays open
    assert client.get("/auth/status").json() == {"auth_required": True, "authenticated": False}


def test_api_docs_are_hidden_when_protected(monkeypatch):
    client = _client(monkeypatch)
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404


def test_login_sets_a_cookie_that_unlocks_the_api_and_logout_removes_it(monkeypatch):
    client = _client(monkeypatch)
    assert client.post("/auth/login", json={"password": "nope"}).status_code == 401
    assert client.get("/settings").status_code == 401

    ok = client.post("/auth/login", json={"password": PASSWORD})
    assert ok.status_code == 200
    assert "httponly" in ok.headers["set-cookie"].lower()
    assert client.get("/settings").status_code == 200
    assert client.get("/auth/status").json()["authenticated"] is True

    client.post("/auth/logout")
    assert client.get("/settings").status_code == 401


def test_cookie_is_marked_secure_behind_https(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/auth/login", json={"password": PASSWORD}, headers={"x-forwarded-proto": "https"})
    assert "secure" in resp.headers["set-cookie"].lower()


def test_bearer_password_works_for_scripts(monkeypatch):
    client = _client(monkeypatch)
    assert client.get("/settings", headers={"authorization": f"Bearer {PASSWORD}"}).status_code == 200
    assert client.get("/settings", headers={"authorization": "Bearer wrong"}).status_code == 401


def test_failed_logins_are_throttled(monkeypatch):
    client = _client(monkeypatch)
    for _ in range(auth.MAX_FAILS):
        assert client.post("/auth/login", json={"password": "nope"}).status_code == 401
    # Even the right password is refused while throttled
    assert client.post("/auth/login", json={"password": PASSWORD}).status_code == 429


def test_tokens_are_signed_and_expire(monkeypatch):
    monkeypatch.setattr(config, "ATAT_PASSWORD", PASSWORD)
    token = auth.make_token()
    assert auth.valid_token(token)

    expiry, sig = token.split(".")
    assert not auth.valid_token(f"{int(expiry) + 999}.{sig}")        # tampered expiry
    assert not auth.valid_token(f"{expiry}.{'0' * len(sig)}")        # bad signature
    assert not auth.valid_token(None) and not auth.valid_token("garbage")
    assert not auth.valid_token(token, now=time.time() + auth.SESSION_TTL_S + 10)  # expired


def test_changing_the_password_signs_everyone_out(monkeypatch):
    monkeypatch.setattr(config, "ATAT_PASSWORD", PASSWORD)
    token = auth.make_token()
    monkeypatch.setattr(config, "ATAT_PASSWORD", "a different password")
    assert not auth.valid_token(token)


# ── Locked instance ───────────────────────────────────────────────────────────

def test_locked_instance_refuses_advanced_settings(monkeypatch):
    import api.routes.settings as settings_route
    monkeypatch.setattr(settings_route, "ATAT_LOCK_ADVANCED", True)
    client = _client(monkeypatch, password="")
    for body in ({"llm_model": "x"}, {"anthropic_api_key": "placeholder-not-a-real-key"}, {"output_path": "/tmp"}):
        assert client.patch("/settings", json=body).status_code == 403
    assert client.get("/settings").json()["advanced_locked"] is True


def test_locked_instance_refuses_shared_prompt_edits(monkeypatch):
    import api.routes.prompts as prompts_route
    monkeypatch.setattr(prompts_route, "ATAT_LOCK_ADVANCED", True)
    client = _client(monkeypatch, password="")
    resp = client.put("/prompts/cv_generation", json={"content": "x"})
    assert resp.status_code == 403


# ── Scrape guard ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://localhost/", "http://192.168.1.10/admin",
    "http://10.0.0.5/", "http://169.254.169.254/latest/meta-data", "http://100.64.1.2/",
    "http://[::1]/",
])
def test_scrape_refuses_private_addresses(url):
    with pytest.raises(HTTPException) as e:
        asyncio.run(assert_public_url(url))
    assert e.value.status_code == 422


def test_scrape_allows_public_addresses():
    asyncio.run(assert_public_url("http://8.8.8.8/"))     # IP literal: no DNS needed


# ── Instance home ─────────────────────────────────────────────────────────────

def _config_paths(home: str | None, tmp_path: Path) -> dict:
    code = (
        "import pipeline.config as c;"
        "print(c.ATAT_HOME);print(c.PERSONAL_PROMPTS_PATH);print(c.LOGS_PATH);"
        "print(c.DB_PATH);print(c.OUTPUT_PATH);print(c.GMAIL_TOKEN_PATH)"
    )
    env = {"PATH": "", "SYSTEMROOT": "", "PYTHONPATH": str(REPO)}
    env = {k: v for k, v in env.items() if v}
    if home:
        env["ATAT_HOME"] = home
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True, text=True, check=True,
    ).stdout.split("\n")
    return {
        "home": Path(out[0]), "prompts": Path(out[1]), "logs": Path(out[2]),
        "db": Path(out[3]), "output": Path(out[4]), "gmail_token": Path(out[5]),
    }


def test_atat_home_moves_personal_files_and_logs(tmp_path):
    home = tmp_path / "kerri"
    home.mkdir()
    p = _config_paths(str(home), tmp_path)
    assert p["home"] == home.resolve()
    assert p["prompts"] == home.resolve() / "prompts"      # her own personal_additions.md
    assert p["logs"] == home.resolve() / "logs"


def test_atat_home_wins_over_a_repo_dotenv(tmp_path):
    # The repo's own .env (if one exists, as on a developer machine) pins DB_PATH and
    # friends. An instance with its own ATAT_HOME must not pick those up — that would point
    # it at the default instance's database. On a machine with no .env this holds trivially.
    home = tmp_path / "kerri"
    home.mkdir()
    p = _config_paths(str(home), tmp_path)
    assert p["db"] == home.resolve() / "data" / "atat.db"
    assert p["output"] == home.resolve() / "output"
    assert p["gmail_token"] == home.resolve() / "data" / "gmail" / "token.json"


def test_without_atat_home_nothing_moves(tmp_path):
    p = _config_paths(None, tmp_path)
    assert p["home"] == REPO.resolve()
    assert p["prompts"] == REPO.resolve() / "prompts"


# ── Status lists ──────────────────────────────────────────────────────────────

def _ts_block(name: str) -> str:
    src = (REPO / "web" / "src" / "lib" / "statuses.ts").read_text(encoding="utf-8")
    m = re.search(rf"export const {name}[^=]*=\s*(\{{.*?\n\}});", src, re.S)
    assert m, f"{name} not found in statuses.ts"
    return m.group(1)


def test_web_and_python_status_lists_agree():
    from pipeline.statuses import VALID_STATUSES
    labels = set(re.findall(r"^\s*(\w+):\s*\"", _ts_block("STATUS_LABELS"), re.M))
    flow   = set(re.findall(r"^\s*(\w+):\s*\[", _ts_block("STATUS_FLOW"), re.M))
    assert labels == set(VALID_STATUSES)
    assert flow == set(VALID_STATUSES)


def test_suggested_next_steps_are_allowed_transitions():
    flow = {
        k: re.findall(r'"(\w+)"', v)
        for k, v in re.findall(r"^\s*(\w+):\s*\[(.*?)\]", _ts_block("STATUS_FLOW"), re.M)
    }
    nxt = {
        k: re.findall(r'"(\w+)"', v)
        for k, v in re.findall(r"^\s*(\w+):\s*\[(.*?)\]", _ts_block("NEXT_STEPS"), re.M)
    }
    assert nxt.keys() == flow.keys()
    for status, steps in nxt.items():
        assert set(steps) <= set(flow[status]), f"{status}: suggested step not in STATUS_FLOW"
