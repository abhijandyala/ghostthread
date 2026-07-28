#!/usr/bin/env python3
"""One-time Gmail authorisation. Produces a refresh token that does not expire.

Run this once:

    .venv/bin/python scripts/gmail_oauth.py

It opens a browser, you approve, and it prints the line to paste into .env.
Requires GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to already be set there,
from the Desktop-app OAuth client in Google Cloud Console.
"""

from __future__ import annotations

import http.server
import secrets
import socket
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ghostthread import config  # noqa: E402
from ghostthread.google_auth import AUTH_URL, SCOPES, TOKEN_URL  # noqa: E402

_received: dict[str, str] = {}
_done = threading.Event()


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        _received.update({k: v[0] for k, v in params.items()})
        body = (
            b"<html><body style='font-family:sans-serif;padding:40px'>"
            b"<h2>GhostThread is authorised.</h2>"
            b"<p>You can close this tab and go back to the terminal.</p>"
            b"</body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        _done.set()

    def log_message(self, *args: object) -> None:
        pass  # keep the console clean


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> int:
    if not (config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET):
        print("Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env first.")
        return 1

    port = _free_port()
    redirect_uri = f"http://127.0.0.1:{port}"
    state = secrets.token_urlsafe(16)

    params = {
        "client_id": config.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        # Both are required to be handed a refresh token. Without prompt=consent
        # Google silently omits it on any authorisation after the first.
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    server = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    print(f"Opening your browser. If nothing happens, visit:\n\n{url}\n")
    webbrowser.open(url)

    if not _done.wait(timeout=300):
        print("Timed out waiting for the browser redirect.")
        return 1
    server.shutdown()

    if _received.get("state") != state:
        print("State mismatch - aborting rather than trusting this redirect.")
        return 1
    if "error" in _received:
        print(f"Google returned an error: {_received['error']}")
        return 1

    resp = httpx.post(
        TOKEN_URL,
        data={
            "code": _received["code"],
            "client_id": config.GOOGLE_CLIENT_ID,
            "client_secret": config.GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=30.0,
    )
    if resp.status_code >= 400:
        print(f"Token exchange failed: {resp.status_code} {resp.text[:400]}")
        return 1

    payload = resp.json()
    refresh = payload.get("refresh_token")
    if not refresh:
        print(
            "Google did not return a refresh token. Revoke the app's access at\n"
            "https://myaccount.google.com/permissions and run this again."
        )
        return 1

    whoami = httpx.get(
        "https://gmail.googleapis.com/gmail/v1/users/me/profile",
        headers={"Authorization": f"Bearer {payload['access_token']}"},
        timeout=20.0,
    ).json()

    print(f"\nAuthorised as {whoami.get('emailAddress', 'unknown')}.")
    print("Paste this into .env (and leave GMAIL_TOKEN empty):\n")
    print(f"GMAIL_REFRESH_TOKEN={refresh}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
