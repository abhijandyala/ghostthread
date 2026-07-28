"""Read-side connectors for the four synced sources.

Each connector normalises its provider's payload into the frozen contract and
nothing else. If a provider token is present we hit the real API; otherwise we
read the corpus file. Both paths produce identical shapes, so every downstream
stage is oblivious to which one ran.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Optional

import httpx

from . import config, google_auth
from .contracts import ComplaintEvent, WorkEvent


def _corpus() -> dict[str, Any]:
    with open(config.FIXTURES_DIR / "corpus.json", "r", encoding="utf-8") as fh:
        return json.load(fh)


def _abs_time(offset_hours: float, now: float) -> float:
    return now - (offset_hours * 3600.0)


def identities() -> list[dict[str, Any]]:
    """Provider-native handles per human. Feeds the entity resolution step."""
    return _corpus().get("identities", [])


# --- complaint sources -------------------------------------------------------


def fetch_slack(now: Optional[float] = None) -> list[ComplaintEvent]:
    now = now or time.time()
    if config.SLACK_TOKEN:
        return _fetch_slack_live(now)
    return [
        ComplaintEvent(
            id=c["id"],
            source="slack",
            entity_id=c["entity_id"],
            text=c["text"],
            t=_abs_time(c["t_offset_hours"], now),
            channel_or_thread=c["channel_or_thread"],
            author_email=c["author_email"],
        )
        for c in _corpus()["complaints"]
        if c["source"] == "slack"
    ]


def _fetch_slack_live(now: float) -> list[ComplaintEvent]:
    out: list[ComplaintEvent] = []
    with httpx.Client(timeout=15.0) as client:
        headers = {"Authorization": f"Bearer {config.SLACK_TOKEN}"}
        channels = client.get(
            "https://slack.com/api/conversations.list",
            headers=headers,
            params={"types": "public_channel", "limit": 50},
        ).json()
        for ch in channels.get("channels", []):
            hist = client.get(
                "https://slack.com/api/conversations.history",
                headers=headers,
                params={"channel": ch["id"], "limit": 50},
            ).json()
            for msg in hist.get("messages", []):
                if msg.get("subtype") or not msg.get("text"):
                    continue
                out.append(
                    ComplaintEvent(
                        id=f"slack-{msg['ts']}",
                        source="slack",
                        entity_id=msg.get("user", ""),
                        text=msg["text"],
                        t=float(msg["ts"]),
                        channel_or_thread=f"#{ch.get('name', ch['id'])}",
                        author_email=msg.get("user_profile", {}).get("email", ""),
                    )
                )
    return out


def fetch_gmail(now: Optional[float] = None) -> list[ComplaintEvent]:
    now = now or time.time()
    if google_auth.is_configured():
        return _fetch_gmail_live(now)
    return [
        ComplaintEvent(
            id=c["id"],
            source="gmail",
            entity_id=c["entity_id"],
            text=c["text"],
            t=_abs_time(c["t_offset_hours"], now),
            channel_or_thread=c["channel_or_thread"],
            author_email=c["author_email"],
        )
        for c in _corpus()["complaints"]
        if c["source"] == "gmail"
    ]


def _fetch_gmail_live(now: float) -> list[ComplaintEvent]:
    out: list[ComplaintEvent] = []
    headers = {"Authorization": f"Bearer {google_auth.access_token()}"}
    base = f"https://gmail.googleapis.com/gmail/v1/users/{config.GMAIL_USER}"
    with httpx.Client(timeout=15.0, headers=headers) as client:
        listing = client.get(f"{base}/messages", params={"maxResults": 50}).json()
        for ref in listing.get("messages", []):
            msg = client.get(f"{base}/messages/{ref['id']}", params={"format": "metadata"}).json()
            hdrs = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
            sender = hdrs.get("from", "")
            email = sender.split("<")[-1].strip(">").strip() if "<" in sender else sender
            out.append(
                ComplaintEvent(
                    id=f"gmail-{ref['id']}",
                    source="gmail",
                    entity_id=email,
                    text=f"Subject: {hdrs.get('subject', '')}. {msg.get('snippet', '')}",
                    t=float(msg.get("internalDate", 0)) / 1000.0,
                    channel_or_thread=msg.get("threadId", ""),
                    author_email=email,
                )
            )
    return out


# --- work sources ------------------------------------------------------------


def fetch_linear(now: Optional[float] = None) -> list[WorkEvent]:
    now = now or time.time()
    if config.LINEAR_TOKEN:
        return _fetch_linear_live(now)
    return [
        WorkEvent(
            id=w["id"],
            source="linear",
            entity_id=w["entity_id"],
            title=w["title"],
            description=w["description"],
            t_created=_abs_time(w["t_offset_hours"], now),
            status=w["status"],
            reporter_email=w["reporter_email"],
        )
        for w in _corpus()["work"]
        if w["source"] == "linear"
    ]


_LINEAR_ISSUES_QUERY = """
query { issues(first: 100, orderBy: createdAt) { nodes {
  id identifier title description createdAt
  state { name } creator { email }
} } }
"""


def _fetch_linear_live(now: float) -> list[WorkEvent]:
    from datetime import datetime

    resp = httpx.post(
        "https://api.linear.app/graphql",
        headers={"Authorization": config.LINEAR_TOKEN, "Content-Type": "application/json"},
        json={"query": _LINEAR_ISSUES_QUERY},
        timeout=20.0,
    )
    resp.raise_for_status()
    nodes = resp.json()["data"]["issues"]["nodes"]
    out: list[WorkEvent] = []
    for n in nodes:
        created = datetime.fromisoformat(n["createdAt"].replace("Z", "+00:00")).timestamp()
        out.append(
            WorkEvent(
                id=n["identifier"],
                source="linear",
                entity_id=n["identifier"],
                title=n["title"] or "",
                description=n.get("description") or "",
                t_created=created,
                status=(n.get("state") or {}).get("name", ""),
                reporter_email=(n.get("creator") or {}).get("email", "") or "",
            )
        )
    return out


def fetch_github(now: Optional[float] = None) -> list[WorkEvent]:
    now = now or time.time()
    if config.GITHUB_TOKEN and config.GITHUB_REPO:
        return _fetch_github_live(now)
    return [
        WorkEvent(
            id=w["id"],
            source="github",
            entity_id=w["entity_id"],
            title=w["title"],
            description=w["description"],
            t_created=_abs_time(w["t_offset_hours"], now),
            status=w["status"],
            reporter_email=w["reporter_email"],
        )
        for w in _corpus()["work"]
        if w["source"] == "github"
    ]


def _fetch_github_live(now: float) -> list[WorkEvent]:
    from datetime import datetime

    url = f"https://api.github.com/repos/{config.GITHUB_REPO}/issues"
    resp = httpx.get(
        url,
        headers={
            "Authorization": f"Bearer {config.GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
        },
        params={"state": "all", "per_page": 100},
        timeout=20.0,
    )
    resp.raise_for_status()
    out: list[WorkEvent] = []
    for item in resp.json():
        created = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00")).timestamp()
        out.append(
            WorkEvent(
                id=f"gh-{item['number']}",
                source="github",
                entity_id=str(item["number"]),
                title=item.get("title") or "",
                description=item.get("body") or "",
                t_created=created,
                status=item.get("state", ""),
                reporter_email=(item.get("user") or {}).get("login", ""),
            )
        )
    return out


COMPLAINT_FETCHERS: dict[str, Callable[..., list[ComplaintEvent]]] = {
    "slack": fetch_slack,
    "gmail": fetch_gmail,
}

WORK_FETCHERS: dict[str, Callable[..., list[WorkEvent]]] = {
    "linear": fetch_linear,
    "github": fetch_github,
}
