"""Pipeline node N8: the idempotency log, in InsForge Postgres.

One resolution per `complaint_id`, ever. The guarantee is a UNIQUE constraint on
`complaint_id` in the database, not a lock in this process -- that distinction is
the entire point. A retried webhook that lands on a different instance, or on the
same instance after a restart, must not file a second ticket, and an in-memory
dict cannot promise that.

`ResolutionAction`'s field names already map 1:1 onto the columns, which is why
they are named the way they are. The full resolution is also stored as JSON in
`resolution`, because the replay path re-renders the original outcome rather than
recomputing it -- a replay that recomputed would be a second run wearing the
costume of a cache hit.

Degradation
-----------
With no InsForge credentials, or when InsForge is unreachable mid-run, this falls
back to an in-process dict and says so. `backend` becomes `in-process`, `degraded`
becomes True, and `degraded_reason` names the cause; `pipeline.py` puts that in
`RunReport.backends` and the UI renders it. The fallback is genuinely weaker --
it does not survive a restart and does not span instances -- so it is labelled
rather than presented as equivalent.

The read order is InsForge first, then the local mirror. The mirror is always
written, so a run that loses InsForge halfway through still dedupes against what
it already did in this process instead of starting over.
"""

from __future__ import annotations

import threading
from typing import Any, Optional

import httpx

from . import config
from .contracts import ResolutionAction

# Column that carries the whole resolution. Named once here so the writer, the
# reader and the seed script cannot drift apart.
RESOLUTION_COLUMN = "resolution"
KEY_COLUMN = "complaint_id"

# Flat columns mirrored out of the resolution for the memory dashboard and for
# anyone querying the table by hand. The JSON column is the source of truth for
# replay; these exist so `recurring_reporters` and friends can be plain SQL.
_TIMEOUT = 8.0


def _headers() -> dict[str, str]:
    # Same both-headers approach as intent.py: InsForge's docs and its own SDK
    # disagree on which one authenticates an admin call.
    return {
        "X-API-Key": config.INSFORGE_API_KEY,
        "Authorization": f"Bearer {config.INSFORGE_API_KEY}",
        "Content-Type": "application/json",
    }


def _records_url() -> str:
    base = config.INSFORGE_BASE_URL.rstrip("/")
    return f"{base}/api/database/records/{config.INSFORGE_ACTIONS_TABLE}"


def insforge_configured() -> bool:
    return bool(config.INSFORGE_BASE_URL and config.INSFORGE_API_KEY)


def row_from_resolution(complaint_id: str, resolution: ResolutionAction) -> dict[str, Any]:
    """Flatten a ResolutionAction onto the actions_log columns.

    Nothing is invented here. Where the resolution genuinely has no value -- no
    PR was opened, no confidence was reported -- the column is null, because a
    zero in `pr_confidence` would read as "the model was certain it was wrong".
    """
    leak = resolution.leak or {}
    complaint = leak.get("complaint") or {}
    facts = resolution.facts or {}
    routing = resolution.routing or {}

    return {
        KEY_COLUMN: complaint_id,
        "received_at": resolution.timestamp or None,
        "source": complaint.get("source"),
        "actor_resolved": complaint.get("author_email"),
        "complaint_text": complaint.get("text"),
        "category": routing.get("category") or facts.get("category"),
        "confidence": facts.get("confidence"),
        "reply_tone": facts.get("reply_tone"),
        "times_reported_actor": facts.get("times_reported_by_actor"),
        "times_seen_topic": facts.get("times_seen_on_topic"),
        "regression_evidence": facts.get("regression_evidence"),
        "verdict": leak.get("verdict"),
        "verdict_confidence": leak.get("confidence"),
        "actions_taken": list(resolution.actions_taken or []),
        "ticket_url": resolution.ticket_url or resolution.ticket_created_id,
        "pr_url": resolution.fix_pr_url,
        "pr_confidence": resolution.pr_confidence,
        "reply_sent": bool(resolution.reply_sent),
        "escalated": bool(resolution.escalated),
        "cost_usd": resolution.cost_usd,
        "latency_ms": resolution.latency_ms,
        "dry_run": bool(resolution.dry_run),
        RESOLUTION_COLUMN: resolution.to_dict(),
    }


class ActionsLog:
    """N8. InsForge-backed when it can be, in-process when it cannot.

    Constructed once per engine. Thread-safe because the API surface is served
    by FastAPI and two concurrent webhook retries for the same complaint is the
    exact case this exists to handle.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mirror: dict[str, dict[str, Any]] = {}
        self._live = insforge_configured()
        self.degraded_reason = (
            "" if self._live else "InsForge not configured; idempotency is in-process only"
        )

    # -- status ---------------------------------------------------------------

    @property
    def backend(self) -> str:
        return "insforge" if self._live else "in-process"

    @property
    def degraded(self) -> bool:
        return not self._live

    def status(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "degraded": self.degraded,
            "reason": self.degraded_reason,
            "table": config.INSFORGE_ACTIONS_TABLE,
            "survives_restart": self._live,
            "spans_instances": self._live,
        }

    def _fall_back(self, reason: str) -> None:
        """Drop to the mirror for the rest of this process, loudly.

        Deliberately sticky: flapping between backends mid-run would make the
        dedupe guarantee depend on which request got lucky.
        """
        if self._live:
            self._live = False
            self.degraded_reason = f"InsForge unavailable ({reason}); fell back to in-process"

    # -- the contract pipeline.py depends on ----------------------------------

    def seen(self, complaint_id: str) -> Optional[dict[str, Any]]:
        """The resolution already recorded for this complaint, or None."""
        if self._live:
            row = self._fetch(complaint_id)
            if row is not None:
                stored = row.get(RESOLUTION_COLUMN)
                # A row with no JSON payload still proves the complaint was
                # handled. Return what we have rather than re-acting on it.
                return stored if isinstance(stored, dict) else {KEY_COLUMN: complaint_id}
        with self._lock:
            return self._mirror.get(complaint_id)

    def record(self, complaint_id: str, resolution: ResolutionAction) -> dict[str, Any]:
        """Write once. A UNIQUE violation is success, not an error."""
        outcome: dict[str, Any] = {"backend": self.backend, "written": False, "duplicate": False}

        with self._lock:
            first_locally = complaint_id not in self._mirror
            self._mirror.setdefault(complaint_id, resolution.to_dict())
        outcome["first_in_process"] = first_locally

        if self._live:
            outcome.update(self._insert(complaint_id, resolution))
        return outcome

    def clear(self) -> None:
        """Drops the in-process mirror only.

        It does NOT delete rows from InsForge. A log you can clear from the app
        is not an idempotency log; to reset the demo, truncate the table.
        """
        with self._lock:
            self._mirror.clear()

    # -- InsForge transport ---------------------------------------------------

    def _fetch(self, complaint_id: str) -> Optional[dict[str, Any]]:
        try:
            resp = httpx.get(
                _records_url(),
                params={KEY_COLUMN: f"eq.{complaint_id}", "limit": "1"},
                headers=_headers(),
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            rows = resp.json()
        except Exception as exc:
            self._fall_back(f"read failed: {type(exc).__name__}")
            return None
        if not rows:
            return None
        return rows[0] if isinstance(rows[0], dict) else None

    def _insert(self, complaint_id: str, resolution: ResolutionAction) -> dict[str, Any]:
        payload = row_from_resolution(complaint_id, resolution)
        try:
            resp = httpx.post(
                _records_url(),
                headers={**_headers(), "Prefer": "return=representation"},
                json=[payload],
                timeout=_TIMEOUT,
            )
        except Exception as exc:
            self._fall_back(f"write failed: {type(exc).__name__}")
            return {"written": False, "error": str(exc)[:200]}

        if resp.is_success:
            return {"written": True, "duplicate": False}

        # The UNIQUE constraint doing its job. This is the cross-instance
        # guarantee firing, so it is reported as a successful dedupe.
        if _is_unique_violation(resp):
            return {"written": False, "duplicate": True}

        # A schema mismatch would otherwise silently drop every row. Retry once
        # with only the two columns the guarantee actually needs, so a partially
        # provisioned table still dedupes.
        minimal = {KEY_COLUMN: complaint_id, RESOLUTION_COLUMN: resolution.to_dict()}
        try:
            retry = httpx.post(
                _records_url(), headers=_headers(), json=[minimal], timeout=_TIMEOUT
            )
        except Exception as exc:
            self._fall_back(f"write failed: {type(exc).__name__}")
            return {"written": False, "error": str(exc)[:200]}

        if retry.is_success:
            return {"written": True, "duplicate": False, "reduced_columns": True}
        if _is_unique_violation(retry):
            return {"written": False, "duplicate": True}

        self._fall_back(f"write rejected: HTTP {resp.status_code}")
        return {"written": False, "error": resp.text[:200]}


def _is_unique_violation(resp: httpx.Response) -> bool:
    body = resp.text.lower()
    return resp.status_code == 409 or "duplicate key" in body or "unique constraint" in body
