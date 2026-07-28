"""Google OAuth token management.

Google access tokens expire after roughly an hour, which is shorter than the
gap between setting up in the morning and demoing in the afternoon. A static
token in `.env` will be dead by the time it matters.

So we hold the refresh token instead and mint access tokens on demand, cached
until just before they expire. `scripts/gmail_oauth.py` obtains the refresh
token once; after that this runs unattended.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

import httpx

from . import config

TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

# Refresh a little early so a token cannot expire mid-request.
_EXPIRY_MARGIN_SECONDS = 120.0

_lock = threading.Lock()
_cache: dict[str, object] = {"token": None, "expires_at": 0.0}


class GoogleAuthError(RuntimeError):
    pass


def exchange_refresh_token(refresh_token: str) -> tuple[str, float]:
    """Trade a refresh token for a fresh access token. Returns (token, ttl)."""
    resp = httpx.post(
        TOKEN_URL,
        data={
            "client_id": config.GOOGLE_CLIENT_ID,
            "client_secret": config.GOOGLE_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=20.0,
    )
    if resp.status_code >= 400:
        raise GoogleAuthError(f"refresh failed: {resp.status_code} {resp.text[:300]}")
    payload = resp.json()
    return payload["access_token"], float(payload.get("expires_in", 3600))


def access_token() -> Optional[str]:
    """A currently-valid Gmail access token, or None if Gmail is not configured.

    A static `GMAIL_TOKEN` still wins if one is set, so a hand-pasted token from
    the OAuth playground keeps working for quick tests.
    """
    if config.GMAIL_TOKEN:
        return config.GMAIL_TOKEN

    if not (config.GMAIL_REFRESH_TOKEN and config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET):
        return None

    now = time.time()
    with _lock:
        cached = _cache["token"]
        if cached and now < float(_cache["expires_at"]):
            return str(cached)

        token, ttl = exchange_refresh_token(config.GMAIL_REFRESH_TOKEN)
        _cache["token"] = token
        _cache["expires_at"] = now + max(ttl - _EXPIRY_MARGIN_SECONDS, 0.0)
        return token


def is_configured() -> bool:
    return bool(
        config.GMAIL_TOKEN
        or (config.GMAIL_REFRESH_TOKEN and config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET)
    )
