"""
gmail_client.py — the thin Gmail API layer: list message ids, fetch one as EmailMessage.

Read-only by construction: the token is only ever granted gmail.readonly, and nothing
here calls a mutating endpoint. The Google client libraries are imported lazily so the
rest of ATAT (and the tests, which use a fake client) doesn't need them installed.
"""

from __future__ import annotations

import base64
import html
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parseaddr
from typing import Optional, Protocol

from pipeline.config import GMAIL_TOKEN_PATH

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Mail that is never about an application. Promotions/social catch most job-board
# newsletters; the triage step handles what gets through.
BASE_QUERY = "-category:promotions -category:social -in:chats -in:sent -in:drafts"

BODY_LIMIT = 4000


class GmailNotConnected(RuntimeError):
    """No usable token — run `python -m pipeline.inbox.auth`."""


@dataclass
class EmailMessage:
    id:            str
    thread_id:     str
    internal_date: int          # ms since epoch, Gmail's receive time
    sender:        str          # "Name <addr>" as sent
    sender_name:   str
    sender_email:  str
    sender_domain: str
    subject:       str
    snippet:       str
    body:          str          # plain text, trimmed to BODY_LIMIT

    @property
    def received_at(self) -> str:
        return datetime.fromtimestamp(self.internal_date / 1000, tz=timezone.utc).isoformat(timespec="seconds")

    @property
    def received_date(self) -> str:
        return self.received_at[:10]


class MailSource(Protocol):
    def list_message_ids(self, after_ms: int) -> list[str]: ...
    def get_message(self, message_id: str) -> EmailMessage: ...


# ── Credentials ───────────────────────────────────────────────────────────────

def load_credentials():
    """Load the saved token, refreshing it if expired. Raises GmailNotConnected."""
    if not GMAIL_TOKEN_PATH.exists():
        raise GmailNotConnected(
            f"Gmail isn't connected (no token at {GMAIL_TOKEN_PATH}). Run: python -m pipeline.inbox.auth"
        )
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = Credentials.from_authorized_user_file(str(GMAIL_TOKEN_PATH), SCOPES)
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as e:
            raise GmailNotConnected(f"Gmail token refresh failed ({e}). Re-run: python -m pipeline.inbox.auth") from e
        GMAIL_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
        return creds
    raise GmailNotConnected("Gmail token is invalid. Re-run: python -m pipeline.inbox.auth")


# ── Parsing ───────────────────────────────────────────────────────────────────

def _b64(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def html_to_text(raw: str) -> str:
    text = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", raw)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>|</h\d>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    return text


def _normalise_ws(text: str) -> str:
    text = re.sub(r"[ \t ‌​]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _find_part(payload: dict, mime: str) -> Optional[str]:
    if payload.get("mimeType") == mime and payload.get("body", {}).get("data"):
        return _b64(payload["body"]["data"])
    for part in payload.get("parts", []) or []:
        found = _find_part(part, mime)
        if found:
            return found
    return None


def extract_body(payload: dict) -> str:
    plain = _find_part(payload, "text/plain")
    if plain and plain.strip():
        return _normalise_ws(plain)[:BODY_LIMIT]
    rich = _find_part(payload, "text/html")
    return _normalise_ws(html_to_text(rich))[:BODY_LIMIT] if rich else ""


def parse_message(raw: dict) -> EmailMessage:
    headers = {h["name"].lower(): h["value"] for h in raw.get("payload", {}).get("headers", [])}
    sender  = headers.get("from", "")
    name, addr = parseaddr(sender)
    addr = addr.lower()
    return EmailMessage(
        id            = raw["id"],
        thread_id     = raw.get("threadId", raw["id"]),
        internal_date = int(raw.get("internalDate", 0)),
        sender        = sender,
        sender_name   = name,
        sender_email  = addr,
        sender_domain = addr.rsplit("@", 1)[-1] if "@" in addr else "",
        subject       = headers.get("subject", ""),
        snippet       = html.unescape(raw.get("snippet", "")),
        body          = extract_body(raw.get("payload", {})),
    )


# ── Client ────────────────────────────────────────────────────────────────────

# Gmail meters each user in quota units (messages.get and messages.list cost 5 each) per
# minute, and a new Cloud project can have a lower ceiling than the documented default.
# Pace calls well under it, and back off and retry when Google still says slow down.
MIN_INTERVAL_S  = 0.2        # at most ~5 calls/s, ~1,500 units/min
RETRIES         = 6
MAX_BACKOFF_S   = 64
_RETRY_REASONS  = {"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded", "backendError"}


def _retryable(err) -> Optional[float]:
    """Seconds to wait before retrying this HttpError, or None if it shouldn't be retried."""
    resp    = getattr(err, "resp", None) or {}          # httplib2.Response is a dict of headers
    status  = getattr(resp, "status", None)
    details = getattr(err, "error_details", None) or []
    reasons = {d.get("reason") for d in details if isinstance(d, dict)} if isinstance(details, list) else set()
    if not (status == 429 or (status == 403 and reasons & _RETRY_REASONS) or (status and status >= 500)):
        return None
    try:
        return float(resp.get("retry-after") or 0)
    except (TypeError, ValueError):
        return 0.0


class GmailClient:
    def __init__(self, creds=None, sleep=time.sleep, clock=time.monotonic):
        from googleapiclient.discovery import build

        self._svc   = build("gmail", "v1", credentials=creds or load_credentials(), cache_discovery=False)
        self._sleep = sleep
        self._clock = clock
        self._last  = 0.0

    def _execute(self, request):
        """Run a request with pacing, retrying rate-limit and server errors with backoff."""
        from googleapiclient.errors import HttpError

        backoff = 2.0
        for attempt in range(RETRIES + 1):
            wait = MIN_INTERVAL_S - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last = self._clock()
            try:
                return request.execute()
            except HttpError as e:
                hint = _retryable(e)
                if hint is None or attempt == RETRIES:
                    raise
                delay = min(max(hint, backoff), MAX_BACKOFF_S)
                log.warning("Gmail rate-limited (%s); retrying in %.0fs", getattr(e.resp, "status", "?"), delay)
                self._sleep(delay)
                backoff = min(backoff * 2, MAX_BACKOFF_S)

    def list_message_ids(self, after_ms: int) -> list[str]:
        """Every message id newer than after_ms, oldest first (Gmail lists newest first)."""
        query = f"{BASE_QUERY} after:{max(after_ms // 1000, 0)}"
        ids: list[str] = []
        token = None
        while True:
            resp = self._execute(self._svc.users().messages().list(
                userId="me", q=query, maxResults=500, pageToken=token,
            ))
            ids.extend(m["id"] for m in resp.get("messages", []))
            token = resp.get("nextPageToken")
            if not token:
                break
        ids.reverse()
        return ids

    def get_message(self, message_id: str) -> EmailMessage:
        raw = self._execute(self._svc.users().messages().get(userId="me", id=message_id, format="full"))
        return parse_message(raw)

    def profile_email(self) -> str:
        return self._execute(self._svc.users().getProfile(userId="me")).get("emailAddress", "")
