"""The Gmail connect command's flags and credential lookup, and the CV-email comparison."""

import pytest

import pipeline.config as config
import pipeline.inbox.auth as auth
from pipeline.meta import emails_match


def test_defaults_are_unchanged():
    args = auth.parse_args([])
    assert (args.no_browser, args.port, args.host) == (False, 0, "localhost")


def test_headless_flags_are_parsed():
    args = auth.parse_args(["--no-browser", "--port", "8765", "--host", "0.0.0.0"])
    assert (args.no_browser, args.port, args.host) == (True, 8765, "0.0.0.0")


def test_a_client_file_dropped_in_data_is_found(tmp_path, monkeypatch):
    home = tmp_path
    (home / "data").mkdir()
    (home / "data" / "client_secret.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(auth, "ATAT_HOME", home)
    monkeypatch.setattr(auth, "GMAIL_CREDENTIALS_PATH", home / "data" / "gmail" / "client_secret.json")
    assert auth.find_credentials() == home / "data" / "client_secret.json"


def test_the_configured_path_wins_when_both_exist(tmp_path, monkeypatch):
    (tmp_path / "data" / "gmail").mkdir(parents=True)
    (tmp_path / "data" / "client_secret.json").write_text("{}", encoding="utf-8")
    (tmp_path / "data" / "gmail" / "client_secret.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(auth, "ATAT_HOME", tmp_path)
    monkeypatch.setattr(auth, "GMAIL_CREDENTIALS_PATH", tmp_path / "data" / "gmail" / "client_secret.json")
    assert auth.find_credentials() == tmp_path / "data" / "gmail" / "client_secret.json"


def test_the_error_names_every_place_it_looked(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(auth, "ATAT_HOME", tmp_path)
    monkeypatch.setattr(auth, "GMAIL_CREDENTIALS_PATH", tmp_path / "data" / "gmail" / "client_secret.json")
    assert auth.main([]) == 1
    err = capsys.readouterr().err
    assert str(tmp_path / "data" / "gmail" / "client_secret.json") in err
    assert str(tmp_path / "data" / "client_secret.json") in err


def test_connection_status_counts_the_alternate_location(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "client_secret.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(auth, "ATAT_HOME", tmp_path)
    monkeypatch.setattr(auth, "GMAIL_CREDENTIALS_PATH", tmp_path / "data" / "gmail" / "client_secret.json")
    assert auth.connection_status()["credentials_present"] is True


@pytest.mark.parametrize("a,b,same", [
    ("kerri@example.com", "KERRI@example.com", True),
    ("kerri+jobs@example.com", "kerri@example.com", True),
    ("kerri.jones@gmail.com", "kerrijones@gmail.com", True),
    ("kerri.jones@googlemail.com", "kerrijones@gmail.com", True),
    ("kerri.jones@example.com", "kerrijones@example.com", False),      # dots only ignored for Gmail
    ("kerri@example.com", "other@example.com", False),
    ("kerrijones@gmail.com", "kerrijones.career@gmail.com", False),
])
def test_same_mailbox_rules(a, b, same):
    assert emails_match(a, b) is same


def test_the_inbox_status_flags_a_cv_email_that_differs_from_the_account(tmp_path, monkeypatch):
    from api.routes.inbox import _cv_email_check

    meta = tmp_path / "meta.md"
    meta.write_text("# Kerri\n**Email:** kerri.personal@gmail.com\n**Location:** Leeds\n", encoding="utf-8")
    monkeypatch.setattr(config, "META_PATH", meta)

    assert _cv_email_check("kerrijones.career@gmail.com") == {
        "cv_email": "kerri.personal@gmail.com", "email_mismatch": True,
    }
    assert _cv_email_check("kerripersonal@gmail.com")["email_mismatch"] is False     # same mailbox
    assert _cv_email_check(None)["email_mismatch"] is False                          # nothing connected yet


@pytest.mark.parametrize("host,bind", [
    ("localhost", None), ("127.0.0.1", None), ("", None), ("0.0.0.0", "0.0.0.0"), ("192.168.1.5", "192.168.1.5"),
])
def test_only_the_listening_address_changes_never_the_redirect_host(host, bind):
    # Google only accepts a localhost redirect for a Desktop client, so --host maps to bind_addr.
    assert auth.bind_address(host) == bind


def test_the_flow_is_told_localhost_for_the_redirect_and_the_bind_address_separately(tmp_path, monkeypatch):
    import sys, types

    calls = {}

    class FakeFlow:
        @classmethod
        def from_client_secrets_file(cls, path, scopes):
            return cls()

        def run_local_server(self, **kw):
            calls.update(kw)
            raise SystemExit(0)                       # stop before the token is written

    fake_mod = types.ModuleType("google_auth_oauthlib.flow")
    fake_mod.InstalledAppFlow = FakeFlow
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", fake_mod)
    (tmp_path / "client_secret.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(auth, "ATAT_HOME", tmp_path)
    monkeypatch.setattr(auth, "GMAIL_CREDENTIALS_PATH", tmp_path / "client_secret.json")

    with pytest.raises(SystemExit):
        auth.main(["--no-browser", "--port", "8765", "--host", "0.0.0.0"])
    assert calls["host"] == "localhost" and calls["bind_addr"] == "0.0.0.0" and calls["port"] == 8765
