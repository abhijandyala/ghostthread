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

Why there is a preflight
------------------------
Having credentials is not the same as having a table. An unprovisioned project
answers every read with `404 42P01 relation "public.actions_log" does not exist`,
which used to surface as the generic reason "read failed: HTTPStatusError" *after*
the first complaint had already been processed -- so the run reported the InsForge
backend right up until it silently stopped being true, and the reason it gave was
indistinguishable from a network outage.

So construction probes the table before claiming anything, an absent table is
created rather than treated as an outage (a missing table is a misprovisioned
project, not an unavailable one), and every degradation reason now carries the
HTTP status and PostgREST error code that caused it.
"""

from __future__ import annotations

import threading
from typing import Any, NamedTuple, Optional

import httpx

from . import config
from .contracts import ResolutionAction

# Column that carries the whole resolution. Named once here so the writer, the
# reader and the seed script cannot drift apart.
RESOLUTION_COLUMN = "resolution"
KEY_COLUMN = "complaint_id"

_TIMEOUT = 8.0
# Construction blocks on this, and construction happens at API import time, so it
# is deliberately shorter than the request timeout.
_PREFLIGHT_TIMEOUT = 5.0
_PROVISION_TIMEOUT = 20.0

# PostgREST surfaces Postgres' SQLSTATE. 42P01 is undefined_table -- the one
# failure that means "provision me", not "I am down".
UNDEFINED_TABLE = "42P01"


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


def _tables_url() -> str:
    base = config.INSFORGE_BASE_URL.rstrip("/")
    return f"{base}/api/database/tables"


def insforge_configured() -> bool:
    return bool(config.INSFORGE_BASE_URL and config.INSFORGE_API_KEY)


# --- the schema ---------------------------------------------------------------
# Declared here rather than in scripts/seed_insforge.py because the writer, the
# reader and the provisioner all have to agree on it, and a schema that lives in
# a script is a schema the library cannot check itself against.


def column(name: str, type_: str, nullable: bool = True, unique: bool = False) -> dict[str, Any]:
    """One column, in the shape the live API actually accepts.

    It uses columnName/isNullable/isUnique, not the name/nullable/unique spelling
    the published docs show. All three keys are required.
    """
    return {
        "columnName": name,
        "type": type_,
        "isNullable": nullable,
        "isUnique": unique,
    }


# Mirrors the PRD schema and maps 1:1 onto ResolutionAction's field names -- see
# row_from_resolution below. `complaint_id` UNIQUE is the idempotency key and the
# only column the guarantee depends on; `resolution` carries the full outcome so a
# replay re-renders the original rather than recomputing it. The rest are flat
# mirrors so the memory dashboard can be plain SQL.
ACTIONS_COLUMNS: list[dict[str, Any]] = [
    column(KEY_COLUMN, "string", nullable=False, unique=True),
    column("received_at", "string"),
    column("source", "string"),
    column("actor_resolved", "string"),
    column("complaint_text", "string"),
    column("category", "string"),
    column("confidence", "float"),
    column("reply_tone", "string"),
    column("times_reported_actor", "integer"),
    column("times_seen_topic", "integer"),
    column("regression_evidence", "string"),
    column("verdict", "string"),
    column("verdict_confidence", "float"),
    column("actions_taken", "json"),
    column("ticket_url", "string"),
    column("pr_url", "string"),
    column("pr_confidence", "float"),
    column("reply_sent", "boolean"),
    column("escalated", "boolean"),
    column("cost_usd", "float"),
    column("latency_ms", "float"),
    column("dry_run", "boolean"),
    column(RESOLUTION_COLUMN, "json"),
]

# If the extended column types are ever rejected, this is the smallest table that
# still delivers the guarantee. `_insert` writes the reduced shape on a schema
# rejection, so a project provisioned this way still dedupes correctly -- it just
# loses the flat dashboard columns.
MINIMAL_ACTIONS_COLUMNS: list[dict[str, Any]] = [
    column(KEY_COLUMN, "string", nullable=False, unique=True),
    column(RESOLUTION_COLUMN, "json"),
]


# --- talking to InsForge without losing the reason ----------------------------


def describe(resp: httpx.Response) -> str:
    """The status plus whatever PostgREST said, instead of an exception class.

    "read failed: HTTPStatusError" and "HTTP 404: 42P01 relation
    "public.actions_log" does not exist" are the same event; only one of them
    tells you what to do about it.
    """
    try:
        body = resp.json()
    except Exception:
        body = None
    if isinstance(body, dict):
        parts = [str(body[k]) for k in ("code", "message", "details") if body.get(k)]
        if parts:
            return f"HTTP {resp.status_code}: {' '.join(parts)[:200]}"
    return f"HTTP {resp.status_code}: {resp.text[:160]}"


def _is_missing_table(resp: httpx.Response) -> bool:
    if resp.status_code not in (400, 404):
        return False
    body = resp.text
    return UNDEFINED_TABLE in body or "does not exist" in body.lower()


class Probe(NamedTuple):
    """Whether the table is genuinely usable, and if not, precisely why."""

    ok: bool
    reason: str = ""
    missing_table: bool = False


def probe() -> Probe:
    """One cheap read against the real table. No side effects."""
    if not insforge_configured():
        return Probe(False, "InsForge not configured; idempotency is in-process only")
    try:
        resp = httpx.get(
            _records_url(),
            params={"limit": "1"},
            headers=_headers(),
            timeout=_PREFLIGHT_TIMEOUT,
        )
    except Exception as exc:
        return Probe(False, f"InsForge unreachable ({type(exc).__name__})")
    if resp.is_success:
        return Probe(True)
    if _is_missing_table(resp):
        return Probe(
            False,
            f"table {config.INSFORGE_ACTIONS_TABLE!r} does not exist ({describe(resp)})",
            missing_table=True,
        )
    if resp.status_code in (401, 403):
        return Probe(False, f"InsForge rejected the credential ({describe(resp)})")
    return Probe(False, f"InsForge read failed ({describe(resp)})")


def provision() -> tuple[bool, str]:
    """Create the table. Full schema first, then the two columns that matter.

    Idempotent: an existing table is reported as existing, never recreated.
    Returns (table exists afterwards, what happened).
    """
    if not insforge_configured():
        return False, "InsForge not configured"
    detail = "no attempt made"
    for columns, label in ((ACTIONS_COLUMNS, "full"), (MINIMAL_ACTIONS_COLUMNS, "minimal")):
        try:
            resp = httpx.post(
                _tables_url(),
                headers=_headers(),
                json={"tableName": config.INSFORGE_ACTIONS_TABLE, "columns": columns},
                timeout=_PROVISION_TIMEOUT,
            )
        except Exception as exc:
            return False, f"create failed: {type(exc).__name__}"
        if resp.is_success:
            return True, f"created with the {label} schema ({len(columns)} columns)"
        if "already exists" in resp.text:
            return True, "already existed"
        detail = describe(resp)
    return False, detail


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

    def __init__(self, auto_provision: Optional[bool] = None) -> None:
        self._lock = threading.Lock()
        self._mirror: dict[str, dict[str, Any]] = {}
        if auto_provision is None:
            auto_provision = config.INSFORGE_AUTO_PROVISION

        result = probe()
        if not result.ok and result.missing_table and auto_provision:
            # A table that was never created is a misprovisioned project, not an
            # unavailable one, and silently downgrading the guarantee because
            # nobody remembered to run the seed script is exactly the failure
            # this class exists to make impossible.
            created, detail = provision()
            result = (
                probe()
                if created
                else Probe(
                    False,
                    f"{result.reason} and could not be created ({detail}); "
                    f"run scripts/seed_insforge.py",
                )
            )

        self._live = result.ok
        self.degraded_reason = result.reason if not result.ok else ""

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
        except Exception as exc:
            self._fall_back(f"read failed: {type(exc).__name__}")
            return None

        if not resp.is_success:
            # The preflight passed, so a missing table now means it was dropped
            # underneath a running process. Say that, rather than reporting the
            # same "read failed" a timeout would produce.
            if _is_missing_table(resp):
                self._fall_back(f"table disappeared mid-run ({describe(resp)})")
            else:
                self._fall_back(f"read rejected ({describe(resp)})")
            return None

        try:
            rows = resp.json()
        except Exception as exc:
            self._fall_back(f"read returned unparseable body: {type(exc).__name__}")
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

        self._fall_back(f"write rejected ({describe(resp)})")
        return {"written": False, "error": resp.text[:200]}


def _is_unique_violation(resp: httpx.Response) -> bool:
    body = resp.text.lower()
    return resp.status_code == 409 or "duplicate key" in body or "unique constraint" in body
