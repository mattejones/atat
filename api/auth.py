"""
auth.py — a single shared password for one ATAT instance.

When ATAT_PASSWORD is set, every API route except /auth/* and /health needs either a valid
session cookie (set by POST /auth/login) or `Authorization: Bearer <password>` (for scripts).
Unset, nothing is enforced — fine on localhost, not on a network.

This is deliberately simple: one instance = one person = one password. It is meant to sit
behind a private network layer (Tailscale, Cloudflare Access, a VPN) and HTTPS, not replace
them. The session cookie is a stateless HMAC-signed expiry, so changing the password (or
ATAT_SECRET) signs everyone out.
"""

import hashlib
import hmac
import os
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

import pipeline.config as config

SESSION_COOKIE = "atat_session"
SESSION_TTL_S  = 30 * 24 * 3600

# Never gated: the login flow itself and the container health check.
_OPEN_PREFIXES = ("/auth/", "/health")

# Failed-login throttle: MAX_FAILS attempts per WINDOW_S, per client address.
MAX_FAILS = 5
WINDOW_S  = 60
_fails: dict[str, list[float]] = {}


def auth_required() -> bool:
    return bool(config.ATAT_PASSWORD)


def _key() -> bytes:
    secret = os.getenv("ATAT_SECRET") or config.ATAT_PASSWORD
    return hashlib.sha256(f"atat-session-v1:{secret}".encode()).digest()


def make_token(now: Optional[float] = None) -> str:
    expiry = int((now if now is not None else time.time()) + SESSION_TTL_S)
    sig = hmac.new(_key(), str(expiry).encode(), hashlib.sha256).hexdigest()
    return f"{expiry}.{sig}"


def valid_token(token: Optional[str], now: Optional[float] = None) -> bool:
    if not token or "." not in token:
        return False
    expiry, _, sig = token.partition(".")
    if not expiry.isdigit():
        return False
    good = hmac.new(_key(), expiry.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, good):
        return False
    return int(expiry) > (now if now is not None else time.time())


def _password_ok(candidate: str) -> bool:
    return hmac.compare_digest(candidate.encode(), config.ATAT_PASSWORD.encode())


def _client(request: Request) -> str:
    # Behind the web container's proxy every request shares an address, so prefer the
    # forwarded one when present.
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def _throttled(client: str, now: float) -> bool:
    recent = [t for t in _fails.get(client, []) if now - t < WINDOW_S]
    _fails[client] = recent
    return len(recent) >= MAX_FAILS


def is_authenticated(request: Request) -> bool:
    if not auth_required():
        return True
    if valid_token(request.cookies.get(SESSION_COOKIE)):
        return True
    header = request.headers.get("authorization", "")
    return header.lower().startswith("bearer ") and _password_ok(header[7:].strip())


async def require_auth(request: Request) -> None:
    """App-wide dependency: 401 unless signed in (or the route is open)."""
    if not auth_required() or request.method == "OPTIONS":
        return
    if request.url.path.startswith(_OPEN_PREFIXES):
        return
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Not signed in")


router = APIRouter(prefix="/auth", tags=["auth"])


class Login(BaseModel):
    password: str


@router.get("/status")
def status(request: Request):
    return {"auth_required": auth_required(), "authenticated": is_authenticated(request)}


@router.post("/login")
def login(body: Login, request: Request, response: Response):
    if not auth_required():
        return {"authenticated": True}

    client, now = _client(request), time.time()
    if _throttled(client, now):
        raise HTTPException(status_code=429, detail="Too many attempts. Wait a minute and try again.")
    if not _password_ok(body.password):
        _fails.setdefault(client, []).append(now)
        raise HTTPException(status_code=401, detail="That password isn't right.")

    _fails.pop(client, None)
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(
        SESSION_COOKIE, make_token(now),
        max_age=SESSION_TTL_S, httponly=True, samesite="lax", secure=secure, path="/",
    )
    return {"authenticated": True}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"authenticated": False}
