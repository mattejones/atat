"""
auth.py — connect ATAT to Gmail (read-only), once, from the command line.

    python -m pipeline.inbox.auth

Needs an OAuth client of type "Desktop app" from a Google Cloud project with the Gmail
API enabled, saved as GMAIL_CREDENTIALS_PATH (default data/gmail/client_secret.json).
Opens a browser for consent on a loopback port (or, where there's no browser to open,
as under WSL, prints the URL to open by hand — or force that with --no-browser), then
writes the token to
GMAIL_TOKEN_PATH. Both live under data/, which is gitignored.

The only scope requested is gmail.readonly: ATAT can't send, delete, or label mail.
"""

import json
import sys

from pipeline.config import GMAIL_CREDENTIALS_PATH, GMAIL_TOKEN_PATH
from pipeline.inbox.gmail_client import SCOPES, GmailNotConnected, load_credentials

_ACCOUNT_PATH = GMAIL_TOKEN_PATH.with_name("account.json")


def connection_status() -> dict:
    """Whether a usable token exists. Doesn't call Google unless the token needs a refresh."""
    status = {
        "credentials_present": GMAIL_CREDENTIALS_PATH.exists(),
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


def main() -> int:
    if not GMAIL_CREDENTIALS_PATH.exists():
        print(
            f"No OAuth client at {GMAIL_CREDENTIALS_PATH}.\n"
            "Create one in Google Cloud Console (APIs & Services > Credentials > OAuth client ID,\n"
            "type 'Desktop app'), enable the Gmail API for the project, and save the JSON there.",
            file=sys.stderr,
        )
        return 1

    from google_auth_oauthlib.flow import InstalledAppFlow

    from pipeline.inbox.gmail_client import GmailClient

    # Under WSL (or over SSH) there's no browser to launch: print the URL instead and
    # let the user open it on the host. The redirect goes to http://localhost:<port>/,
    # which WSL2 forwards back to this process, so the flow completes the same way.
    open_browser = "--no-browser" not in sys.argv and _has_browser()
    flow  = InstalledAppFlow.from_client_secrets_file(str(GMAIL_CREDENTIALS_PATH), SCOPES)
    creds = flow.run_local_server(
        port=0,
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
