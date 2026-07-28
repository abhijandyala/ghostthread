"""Ingest Gmail into HydraDB over OAuth instead of the managed connector.

HydraDB's gmail connector authenticates over IMAP with an app password. When
that credential is unavailable, this path reads the same mailbox through the
Gmail REST API using the OAuth token we already hold and writes the messages
into the same `gmail` collection.

The documents are shaped to match what a managed connector produces, so
everything downstream (scoping, retrieval, entity resolution) is identical.
The one difference is `connector_id`, which is marked as OAuth-sourced rather
than being a HydraDB connector UUID - flagged honestly rather than faked.
"""

from __future__ import annotations

import base64
import re
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from . import config, google_auth

API_BASE = "https://gmail.googleapis.com/gmail/v1/users"
CONNECTOR_ID = "oauth:gmail"
_ADDRESS = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {google_auth.access_token()}"}


def _address_of(raw: str) -> str:
    match = _ADDRESS.search(raw or "")
    return match.group(0).lower() if match else ""


def _body_text(payload: dict[str, Any]) -> str:
    """Depth-first search for the first text/plain part."""
    if payload.get("mimeType") == "text/plain":
        data = (payload.get("body") or {}).get("data")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    for part in payload.get("parts") or []:
        found = _body_text(part)
        if found:
            return found
    return ""


def fetch_messages(limit: int = 100, query: Optional[str] = None) -> list[dict[str, Any]]:
    """Read the mailbox and shape each message as a HydraDB app-knowledge doc."""
    if not google_auth.is_configured():
        return []

    base = f"{API_BASE}/{config.GMAIL_USER}"
    docs: list[dict[str, Any]] = []
    with httpx.Client(timeout=30.0, headers=_headers()) as client:
        params: dict[str, Any] = {"maxResults": min(limit, 100)}
        if query:
            params["q"] = query
        listing = client.get(f"{base}/messages", params=params).json()

        for ref in listing.get("messages", [])[:limit]:
            msg = client.get(f"{base}/messages/{ref['id']}", params={"format": "full"}).json()
            payload = msg.get("payload", {})
            headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}

            sender = _address_of(headers.get("from", ""))
            subject = headers.get("subject", "(no subject)")
            body = _body_text(payload) or msg.get("snippet", "")
            sent_ms = int(msg.get("internalDate", 0))
            timestamp = datetime.fromtimestamp(sent_ms / 1000.0, tz=timezone.utc)

            docs.append(
                {
                    "id": f"gmail-{msg['id']}",
                    "title": subject[:120],
                    "type": "gmail",
                    "content": {"text": f"Subject: {subject}\n\n{body}".strip()},
                    "url": f"https://mail.google.com/mail/u/0/#inbox/{msg['id']}",
                    "timestamp": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "tenant_metadata": {
                        "kind": "complaint",
                        "source": "gmail",
                        "thread_id": msg.get("threadId", ""),
                    },
                    "additional_metadata": {
                        "app_provider": "gmail",
                        "app_kind": "message",
                        "app_external_id": msg["id"],
                        "connector_id": CONNECTOR_ID,
                        "provider_account_scope": config.GMAIL_ADDRESS or config.GMAIL_USER,
                        "author_email": sender,
                        "to": headers.get("to", ""),
                        "thread_id": msg.get("threadId", ""),
                    },
                    # Ties the message to its sender in the context graph, which
                    # is what lets a person be followed across tools.
                    "relations": {"ids": [f"person:{sender}"] if sender else []},
                }
            )
    return docs


def ingest(limit: int = 100, query: Optional[str] = None) -> dict[str, Any]:
    import json

    import hydra_db

    docs = fetch_messages(limit=limit, query=query)
    if not docs:
        return {"ingested": 0, "reason": "no messages or Gmail not configured"}

    client = hydra_db.HydraDB(token=config.HYDRA_TOKEN)
    client.context.ingest(
        database=config.HYDRA_DATABASE,
        collection="gmail",
        type="knowledge",
        app_knowledge=json.dumps(docs),
        upsert="true",
    )
    return {"ingested": len(docs), "ids": [d["id"] for d in docs]}
