"""
auth.py — connect ATAT to Gmail (read-only), once, from the command line.

    python -m pipeline.inbox.auth [--no-browser] [--port N] [--host H]

Needs an OAuth client of type "Desktop app" from a Google Cloud project with the Gmail
API enabled, saved as GMAIL_CREDENTIALS_PATH (default data/gmail/client_secret.json; a file
dropped in data/ is found too). Opens a browser for consent on a loopback port (or, where
there's no browser to open, as under WSL, prints the URL to open by hand — or force that with
--no-browser), then writes the token to GMAIL_TOKEN_PATH. Both live under data/, which is
gitignored.

On a headless server pick a fixed port so it can be tunnelled, rather than reading a random one
out of the URL:

    python -m pipeline.inbox.auth --no-browser --port 8765 --host 0.0.0.0     (on the server)
    ssh -L 8765:localhost:8765 <server>                                          (on your laptop)

then open the printed URL on the laptop. Google redirects back to http://localhost:8765/, which
the tunnel carries to the server. (--host 0.0.0.0 is needed when the command runs in a
container.)

The only scope requested is gmail.readonly: ATAT can't send, delete, or label mail.
"""

import argparse
import json
import sys
from pathlib import Path

from pipeline.config import ATAT_HOME, GMAIL_CREDENTIALS_PATH, GMAIL_TOKEN_PATH
from pipeline.inbox.gmail_client import SCOPES, GmailNotConnected, load_credentials

_ACCOUNT_PATH = GMAIL_TOKEN_PATH.with_name("account.json")


def credential_candidates() -> list:
    """Where client_secret.json is looked for: the configured path, then data/ beside it."""
    out = [GMAIL_CREDENTIALS_PATH, ATAT_HOME / "data" / "client_secret.json"]
    return list(dict.fromkeys(out))               # no duplicates, order kept


def find_credentials() -> Path | None:
    return next((p for p in credential_candidates() if p.exists()), None)


def connection_status() -> dict:
    """Whether a usable token exists. Doesn't call Google unless the token needs a refresh."""
    status = {
        "credentials_present": find_credentials() is not None,
        "connected":           False,
        "email":               None,
        "detail":              None,
        "connect_command":     "python -m pipeline.inbox.auth",
    }
    try:
        load_credentials()
        status["connected"] = True
    except GmailNotConnected as e:
        status["detail"] = str(e)
    except Exception as e:  # malformed token file, missing library, ...
        status["detail"] = f"Gmail token unusable: {e}"
    if _ACCOUNT_PATH.exists():
        try:
            status["email"] = json.loads(_ACCOUNT_PATH.read_text(encoding="utf-8")).get("email")
        except (OSError, ValueError):
            pass
    return status


def _has_browser() -> bool:
    import webbrowser

    try:
        webbrowser.get()
        return True
    except webbrowser.Error:
        return False


def parse_args(argv: list) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m pipeline.inbox.auth", description=__doc__.split("\n\n")[0])
    p.add_argument("--no-browser", action="store_true", help="print the sign-in URL instead of opening a browser")
    p.add_argument("--port", type=int, default=0, help="loopback port for the redirect (default: any free port)")
    p.add_argument("--host", default="localhost", help="address to listen on (use 0.0.0.0 inside a container)")
    return p.parse_args(argv)


def bind_address(host: str) -> str | None:
    """
    The address the redirect server listens on, when it is not just this machine.

    Google redirects to http://localhost:<port>/ whatever is listening, and rejects any other
    host for a Desktop client, so the redirect host always stays "localhost". Only the listening
    address changes (0.0.0.0 inside a container, so a published or tunnelled port reaches it).
    """
    return None if host in ("localhost", "127.0.0.1", "") else host


def main(argv: list | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    credentials = find_credentials()
    if credentials is None:
        looked = "\n  ".join(str(p) for p in credential_candidates())
        print(
            "No OAuth client file (client_secret.json) found. Looked in:\n  " + looked + "\n"
            "Create one in Google Cloud Console (APIs & Services > Credentials > OAuth client ID,\n"
            "type 'Desktop app'), enable the Gmail API for the project, and save the JSON in one of\n"
            "those places.",
            file=sys.stderr,
        )
        return 1

    from google_auth_oauthlib.flow import InstalledAppFlow

    from pipeline.inbox.gmail_client import GmailClient

    # Under WSL (or over SSH) there's no browser to launch: print the URL instead and
    # let the user open it on the host. The redirect goes to http://localhost:<port>/,
    # which WSL2 forwards back to this process, so the flow completes the same way.
    open_browser = not args.no_browser and _has_browser()
    flow  = InstalledAppFlow.from_client_secrets_file(str(credentials), SCOPES)
    creds = flow.run_local_server(
        host="localhost",                       # the redirect address Google is told about
        bind_addr=bind_address(args.host),      # where the redirect server actually listens
        port=args.port,
        open_browser=open_browser,
        authorization_prompt_message=(
            "Opening your browser to sign in to Google: {url}" if open_browser else
            "Open this URL in your browser (on Windows, if you're in WSL) and approve read-only access:\n\n{url}\n"
        ),
        prompt="consent",
        access_type="offline",
    )
    GMAIL_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GMAIL_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")

    email = GmailClient(creds).profile_email()
    _ACCOUNT_PATH.write_text(json.dumps({"email": email}), encoding="utf-8")
    print(f"Connected Gmail account {email} (read-only). Token saved to {GMAIL_TOKEN_PATH}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
