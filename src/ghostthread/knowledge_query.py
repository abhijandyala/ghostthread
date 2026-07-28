"""Track A / A2 — the leak-detection query.

The question: which complaints in Slack or Gmail never became tracked work in
Linear or GitHub?

Everything here runs against HydraDB. Complaints are read out of HydraDB rather
than re-fetched from Slack or Gmail, so a source that is out of query scope is
genuinely invisible instead of being filtered after the fact. That is what makes
the kill shot honest.

Owned by Track A. Track B consumes `detect_leaks()` and nothing else in here.

Measured latency, against the live tenant:

* one `POST /query` is ~0.4-1.1s, comfortably inside the 3s budget
* `mode="thinking"` costs ~4.2s against ~0.4s for `"auto"`, so the mode is a
  profile setting rather than a constant
* concurrent queries do not scale linearly - HydraDB appears to throttle per
  key, so a seven-complaint sweep lands around 8s no matter the pool size
* documents are cached after first load, which is what makes a kill-shot
  re-scope ~0.3s instead of a full re-run
"""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Optional

import hydra_db

from . import config
from .contracts import IntentProfile, LeakResult, LeakVerdict, WorkItemRef
from .intent import get_profile
from .resolve import extract_email, slack_emails

COMPLAINT_PROVIDERS = ("slack", "gmail")
WORK_PROVIDERS = ("linear", "github")
ALL_PROVIDERS = COMPLAINT_PROVIDERS + WORK_PROVIDERS

MAX_WORKERS = 12
PAGE_SIZE = 100


class SourceUnavailable(RuntimeError):
    """A HydraDB read failed, as opposed to completing and finding nothing.

    The two are the same empty list to a caller that only gets the contents
    back, and that is how a scoped run ends up asserting "nothing here" about a
    source it never reached.
    """
# Linear identifiers look like ABC-123; GitHub refs like #123 or gh-123.
_LINEAR_REF = re.compile(r"\b([A-Z][A-Z0-9]{1,9}-\d{1,6})\b")
_GITHUB_REF = re.compile(r"(?:#|gh-)(\d{1,6})\b")


# --- connector registry ------------------------------------------------------


@dataclass
class ConnectorInfo:
    provider: str
    connector_id: str
    collection: str


class ConnectorRegistry:
    """provider <-> connector_id, from whatever setup_connectors.py provisioned.

    connector_id is the handle the kill shot filters on, so it is read from
    live state rather than hardcoded anywhere.
    """

    def __init__(self, state: Optional[dict[str, Any]] = None) -> None:
        raw = state
        if raw is None:
            raw = (
                json.loads(config.CONNECTOR_STATE_PATH.read_text())
                if config.CONNECTOR_STATE_PATH.exists()
                else {}
            )
        self._by_provider: dict[str, ConnectorInfo] = {}
        for provider, entry in raw.items():
            connector_id = entry.get("connector_id")
            if not connector_id:
                continue
            self._by_provider[provider] = ConnectorInfo(
                provider=provider,
                connector_id=connector_id,
                collection=entry.get("sub_tenant_id") or provider,
            )

    def connector_id(self, provider: str) -> Optional[str]:
        info = self._by_provider.get(provider)
        return info.connector_id if info else None

    def connector_ids(self, providers: list[str]) -> list[str]:
        return [cid for p in providers if (cid := self.connector_id(p))]

    def providers(self) -> list[str]:
        return sorted(self._by_provider)


# --- documents ---------------------------------------------------------------


@dataclass
class Document:
    """A HydraDB knowledge document, normalised."""

    id: str
    provider: str
    title: str
    text: str
    url: str
    timestamp: float
    actor_email: str
    # The source's own handle for the author -- a Slack member id, a Linear
    # user id. Kept alongside the address rather than in place of it: it is
    # what still identifies the document when no address could be resolved,
    # and it must never be poured into `actor_email`, which downstream joins
    # assume is an address.
    actor_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def blob(self) -> str:
        return f"{self.title}\n{self.text}"

    @property
    def search_text(self) -> str:
        """A distilled query string for retrieval.

        Sending the raw body is both slow and inaccurate: a real email carries
        URLs, image placeholders, quoted replies and footers, which cost several
        seconds per query and dilute the signal. Retrieval wants the topical
        core, so that is what we send.
        """
        return distil(self.title, self.text)


_URL = re.compile(r"https?://\S+|www\.\S+")
_IMAGE_TAG = re.compile(r"\[image:[^\]]*\]", re.IGNORECASE)
_QUOTED = re.compile(r"^\s*(>|On .+ wrote:).*$", re.MULTILINE)
_BOILERPLATE = re.compile(
    r"(unsubscribe|you'?re receiving this|do not reply|sent from my|view it in the browser"
    r"|privacy policy|all rights reserved)",
    re.IGNORECASE,
)
_WHITESPACE = re.compile(r"\s+")
SEARCH_TEXT_LIMIT = 320


def distil(title: str, text: str) -> str:
    """Reduce a message to the part worth searching on."""
    body = _QUOTED.sub(" ", text or "")
    body = _URL.sub(" ", body)
    body = _IMAGE_TAG.sub(" ", body)

    kept: list[str] = []
    for line in body.splitlines():
        line = line.strip()
        if not line or _BOILERPLATE.search(line):
            continue
        # Drop lines that are mostly punctuation or markup scaffolding.
        letters = sum(ch.isalpha() or ch.isspace() for ch in line)
        if letters < max(3, len(line) * 0.6):
            continue
        kept.append(line)
        if sum(len(k) for k in kept) > SEARCH_TEXT_LIMIT:
            break

    title_clean = _WHITESPACE.sub(" ", _URL.sub(" ", title or "")).strip()
    if title_clean.lower().startswith("subject:"):
        title_clean = title_clean[len("subject:") :].strip()

    combined = _WHITESPACE.sub(" ", f"{title_clean}. {' '.join(kept)}").strip(" .")
    return combined[:SEARCH_TEXT_LIMIT] or title_clean[:SEARCH_TEXT_LIMIT]


def _client() -> hydra_db.HydraDB:
    return hydra_db.HydraDB(token=config.HYDRA_TOKEN)


def _to_epoch(value: Any) -> float:
    """Epoch seconds from an ISO-8601 string or from a native epoch value.

    Both shapes are in the tenant: a hand ingest writes ISO, while a connector
    passes the provider's own clock through untouched — Slack's `slack_ts` is
    already epoch seconds carried as a string.
    """
    if not value:
        return 0.0
    from datetime import datetime

    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


# Where an address may be hiding, in preference order. A `from` header arrives
# as `Name <a@b.com>`, so every candidate goes through `extract_email` rather
# than being taken at face value.
_ACTOR_EMAIL_KEYS = (
    "author_email",
    "reporter_email",
    "from",
    "sender",
    "actor",
    "email",
)
# The source's own opaque handle for the author. Deliberately a separate list:
# a member id is a real identifier and never an address.
_ACTOR_ID_KEYS = ("slack_author_id", "author_id", "user_id", "entity_id", "user")


def _actor_of(meta: dict[str, Any]) -> str:
    """The author's address, if the document carries one anywhere.

    Only an address is admitted. A member id in this field would make a Slack
    complaint look attributed while matching nothing — entity resolution and
    `memory_read`'s actor filter both key on email — and a join that silently
    never fires is indistinguishable from a person with no history.
    """
    for key in _ACTOR_EMAIL_KEYS:
        found = extract_email(meta.get(key))
        if found:
            return found
    return ""


def _actor_id_of(meta: dict[str, Any]) -> str:
    for key in _ACTOR_ID_KEYS:
        value = str(meta.get(key) or "").strip()
        if value:
            return value
    return ""


def _metadata_of(payload: dict[str, Any]) -> dict[str, Any]:
    """Flatten every metadata shape `context.inspect` may return into one dict.

    There are two, and neither is a superset of the other. A hand ingest — the
    Gmail OAuth path, and the older Linear fixtures — writes
    `additional_metadata` / `document_metadata` / `tenant_metadata`. A document
    synced by a HydraDB managed connector carries none of those and instead
    holds `app_metadata` (the connector's own handles: connector_id, member id,
    channel) and `app_fields` (the provider's record: author, body, created_at).

    Reading only the first shape is how every connector-synced document came to
    load with no metadata and therefore no actor at all. They are merged rather
    than chosen between, with an explicit ingest's keys winning, because a
    single tenant holds both kinds at once.
    """
    return {
        **(payload.get("app_metadata") or {}),
        **(payload.get("app_fields") or {}),
        **(payload.get("additional_metadata") or {}),
        **(payload.get("document_metadata") or {}),
        **(payload.get("tenant_metadata") or {}),
    }


_collection_cache: dict[str, set[str]] = {}


def probe_collections(
    client: Optional[hydra_db.HydraDB] = None, refresh: bool = False
) -> tuple[set[str], str]:
    """Collections that hold documents, plus why the listing failed if it did.

    Naming an empty collection is a 400, so this is consulted before every
    query. Cached: it costs ~330ms uncached.

    The error comes back rather than being swallowed because an unreachable
    HydraDB and an empty tenant are the same empty set, and every caller that
    reports a result to a human has to be able to tell them apart.
    """
    if not refresh and "value" in _collection_cache:
        return _collection_cache["value"], ""
    client = client or _client()
    try:
        env = client.databases.collections(database=config.HYDRA_DATABASE)
        found = set(getattr(env.data, "sub_tenant_ids", None) or [])
    except Exception as exc:
        return set(), f"collection listing failed: {type(exc).__name__}: {exc}"
    _collection_cache["value"] = found
    return found, ""


def live_collections(
    client: Optional[hydra_db.HydraDB] = None, refresh: bool = False
) -> set[str]:
    """Membership-test view of `probe_collections`. Lossy on purpose.

    A failed listing is an empty set here, which is fine for "may I name this
    collection in a query" and wrong for anything that reports a finding. Use
    `probe_collections` when the difference reaches a human.
    """
    return probe_collections(client, refresh)[0]


_document_cache: dict[str, list[Document]] = {}


def refresh_documents() -> None:
    """Drop cached documents. Call after a connector sync or a new ingest."""
    _document_cache.clear()
    _collection_cache.clear()


@dataclass
class SourceLoad:
    """The outcome of reading one collection, not merely its contents.

    `documents == []` is ambiguous on its own. `ok` says whether the read
    completed, `present` says whether the collection exists at all, and
    `unreadable` counts documents that were enumerated but could not be
    fetched — a partial read that would otherwise look like a short source.
    """

    provider: str
    documents: list[Document] = field(default_factory=list)
    error: str = ""
    present: bool = True
    unreadable: int = 0

    @property
    def ok(self) -> bool:
        return not self.error


def load_documents_result(
    provider: str, client: Optional[hydra_db.HydraDB] = None, refresh: bool = False
) -> SourceLoad:
    """Enumerate a collection, then inspect each source for its full content.

    `context.list` only returns ids and titles, and its `include_fields` rejects
    content, so the body has to come from `context.inspect`. Enumerating rather
    than searching matters: a search term would silently drop any complaint that
    did not match it, and a missed complaint is indistinguishable from a leak.

    Every failure below used to be a `break` or a `None` and therefore arrived
    at the caller as a short list. They are reported instead, because a leak
    report built on a source that failed to load is a fabricated one.
    """
    if not refresh and provider in _document_cache:
        return SourceLoad(provider=provider, documents=_document_cache[provider])

    client = client or _client()
    found, probe_error = probe_collections(client)
    if probe_error:
        return SourceLoad(provider=provider, error=probe_error)
    if provider not in found:
        # A real answer: the tenant holds no such collection. Distinct from
        # not having been able to look, which is the branch above.
        return SourceLoad(provider=provider, present=False)

    ids: list[str] = []
    page = 1
    listing_error = ""
    while True:
        try:
            resp = client.context.list(
                database=config.HYDRA_DATABASE,
                collection=provider,
                type="knowledge",
                page=page,
                page_size=PAGE_SIZE,
                include_fields=["title"],
            )
        except Exception as exc:
            listing_error = (
                f"listing failed at page {page}: {type(exc).__name__}: {exc}"
            )
            break
        sources = getattr(resp.data, "sources", None) or []
        if not sources:
            break
        for src in sources:
            doc_id = src.get("id") if isinstance(src, dict) else getattr(src, "id", None)
            if doc_id:
                ids.append(str(doc_id))
        if len(sources) < PAGE_SIZE:
            break
        page += 1

    if listing_error:
        return SourceLoad(provider=provider, error=listing_error)

    def fetch(doc_id: str) -> Optional[Document]:
        try:
            env = client.context.inspect(
                database=config.HYDRA_DATABASE, collection=provider, id=doc_id
            )
        except Exception:
            return None
        raw = getattr(env.data, "content", None) or getattr(env, "content", None)
        if not raw:
            return None
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            return None
        content = payload.get("content") or {}
        meta = _metadata_of(payload)
        # A connector document has no top-level `timestamp` and may carry its
        # body only under the provider's own field name, so both fall back to
        # the flattened metadata instead of coming out empty.
        return Document(
            id=str(payload.get("id") or doc_id),
            provider=provider,
            title=str(payload.get("title") or meta.get("title") or ""),
            text=str(
                content.get("text")
                or content.get("markdown")
                or meta.get("body")
                or ""
            ),
            url=str(payload.get("url") or meta.get("url") or ""),
            timestamp=(
                _to_epoch(payload.get("timestamp"))
                or _to_epoch(meta.get("created_at"))
                or _to_epoch(meta.get("slack_ts"))
            ),
            actor_email=_actor_of(meta),
            actor_id=_actor_id_of(meta),
            metadata=meta,
        )

    if not ids:
        _document_cache[provider] = []
        return SourceLoad(provider=provider)

    with ThreadPoolExecutor(max_workers=min(len(ids), MAX_WORKERS)) as pool:
        docs = [d for d in pool.map(fetch, ids) if d is not None]
    unreadable = len(ids) - len(docs)
    _resolve_slack_actors(docs)

    if not docs:
        return SourceLoad(
            provider=provider,
            error=f"all {len(ids)} enumerated document(s) failed to inspect",
            unreadable=unreadable,
        )
    # Only a clean read is cached. Caching a partial one would make the
    # degradation permanent for the rest of the process.
    if not unreadable:
        _document_cache[provider] = docs
    return SourceLoad(provider=provider, documents=docs, unreadable=unreadable)


SLACK_PROVIDER = "slack"
SLACK_AUTHOR_ID_KEY = "slack_author_id"


def _slack_member_id(doc: Document) -> str:
    """The member id a document names its Slack author by, if it names one.

    A connector-synced message puts it in `slack_author_id`. A hand ingest of
    the same channel puts it in the generic `entity_id`, which for every other
    provider holds an address — so the generic field is only read as a member
    id when the document came from Slack in the first place.
    """
    found = str(doc.metadata.get(SLACK_AUTHOR_ID_KEY) or "").strip()
    if found:
        return found
    return doc.actor_id if doc.provider == SLACK_PROVIDER else ""


def _resolve_slack_actors(documents: list[Document]) -> None:
    """Give Slack documents an address, where Slack will supply one.

    Slack puts no address on a message — only a member id and a display name —
    so this is the only point at which a Slack complaint can be recognised as
    the same human as a Gmail complaint or an existing memory. Both of those
    key on email.

    Run once over the whole load rather than per document, so a channel of a
    hundred messages from two people costs two `users.info` calls, and only for
    the ids actually present. A document whose id does not resolve keeps its
    empty `actor_email` and its `actor_id`: no token, an API error and a bot
    author are all honest reasons to have no address, and inventing one would
    make `memory_read` answer about somebody else.
    """
    pending: set[str] = set()
    for doc in documents:
        if not doc.actor_email:
            pending.add(_slack_member_id(doc))
    pending.discard("")
    if not pending:
        return

    emails = slack_emails(sorted(pending))
    if not emails:
        return
    for doc in documents:
        if doc.actor_email:
            continue
        found = emails.get(_slack_member_id(doc), "")
        if found:
            doc.actor_email = found


def load_documents(
    provider: str, client: Optional[hydra_db.HydraDB] = None, refresh: bool = False
) -> list[Document]:
    """Contents-only view of `load_documents_result`, for callers that cannot
    act on the difference between an empty source and an unreachable one."""
    return load_documents_result(provider, client=client, refresh=refresh).documents


# --- retrieval ---------------------------------------------------------------


@dataclass
class WorkCandidate:
    document_id: str
    provider: str
    title: str
    url: str
    score: float
    text: str


@dataclass
class WorkSearch:
    """The outcome of a work-side retrieval, not merely its hits.

    Three different things used to arrive as an empty list: nothing in scope
    was live, every query raised, and a genuine no-match. Only the last of
    those licenses a leak verdict, so they are kept apart here.
    """

    candidates: list[WorkCandidate] = field(default_factory=list)
    # Providers that were queried and answered. This, not the requested scope,
    # is what the verdict may claim to have looked at.
    searched: list[str] = field(default_factory=list)
    # Requested but holding no live collection, so never queried at all.
    unavailable: list[str] = field(default_factory=list)
    # Queried and raised. Their silence carries no information.
    failed: dict[str, str] = field(default_factory=dict)
    # Chunks dropped for coming from a collection outside the request.
    out_of_scope_chunks: dict[str, int] = field(default_factory=dict)

    @property
    def grounded(self) -> bool:
        """True when at least one work provider actually answered."""
        return bool(self.searched)


def search_work(
    complaint_text: str,
    providers: list[str],
    registry: ConnectorRegistry,
    profile: IntentProfile,
    client: Optional[hydra_db.HydraDB] = None,
    limit: int = 5,
    use_connector_filter: bool = True,
) -> WorkSearch:
    """Retrieve possible matching work items, scoped to `providers`.

    Scoping is applied twice on purpose: `collections` narrows the corpus, and
    `metadata_filters.additional_metadata.connector_id` narrows to the exact
    connector. The second is the kill-shot handle; belt and braces means a stale
    collection cannot leak a source back into a scoped run.

    One query is issued per connector rather than one query for all of them.
    That is not a style choice: a *list* of connector ids ANDs rather than ORs,
    so `{"connector_id": [linear_id, github_id]}` matches zero documents. A
    single query would therefore have returned nothing whenever both work
    sources were in scope, and the retry-without-filter fallback would have
    quietly turned it into a collection-scoped search - leaving the connector
    filter dead while appearing to work.

    Returns a `WorkSearch` rather than a list so the caller can tell a real
    no-match from a scope that was never queried or a query that failed.
    """
    client = client or _client()
    found, probe_error = probe_collections(client)
    if probe_error:
        return WorkSearch(failed={p: probe_error for p in providers})

    scope = [p for p in providers if p in found]
    unavailable = [p for p in providers if p not in found]
    result = WorkSearch(unavailable=unavailable)
    if not scope:
        return result

    allowed = set(scope)

    def one(provider: str) -> tuple[str, list[WorkCandidate], dict[str, int], str]:
        kwargs: dict[str, Any] = dict(
            query=complaint_text,
            database=config.HYDRA_DATABASE,
            collections=[provider],
            type="knowledge",
            query_by="hybrid",
            graph_context=True,
            mode=profile.recall_mode,
            max_results=limit,
        )
        connector_id = registry.connector_id(provider)
        if use_connector_filter and connector_id:
            kwargs["metadata_filters"] = {
                "additional_metadata": {"connector_id": connector_id}
            }
        try:
            envelope = client.query(**kwargs)
        except Exception as exc:
            return provider, [], {}, f"{type(exc).__name__}: {exc}"
        candidates, dropped = _parse_chunks(envelope, provider, allowed)
        return provider, candidates, dropped, ""

    if len(scope) == 1:
        batches = [one(scope[0])]
    else:
        with ThreadPoolExecutor(max_workers=len(scope)) as pool:
            batches = list(pool.map(one, scope))

    merged: list[WorkCandidate] = []
    for provider, candidates, dropped, error in batches:
        if error:
            result.failed[provider] = error
            continue
        result.searched.append(provider)
        merged.extend(candidates)
        for origin, count in dropped.items():
            result.out_of_scope_chunks[origin] = (
                result.out_of_scope_chunks.get(origin, 0) + count
            )

    # Relevancy is comparable across collections, so a single sort is a fair
    # merge of the per-connector result sets.
    merged.sort(key=lambda c: c.score, reverse=True)
    result.candidates = merged[:limit]
    result.searched.sort()
    return result


def _parse_chunks(
    envelope: Any, provider: str, allowed: set[str]
) -> tuple[list[WorkCandidate], dict[str, int]]:
    """Turn chunks into candidates, dropping any from outside `allowed`.

    The guard is not redundant with naming one collection in the query.
    `graph_context=True` lets HydraDB pull in neighbours linked through
    `relations`, and those links deliberately span collections — a document is
    joined to a person, and that person appears in every source. A neighbour
    from a collection this run was not allowed to read could resolve a
    complaint the scope is supposed to be blind to, which is precisely the
    claim the kill shot makes about itself.

    Measured against this tenant, expansion never crossed a named collection
    (36 probes, both recall modes, both graph settings). The guard stays
    because that is a property of HydraDB's current behaviour and this tenant's
    data, not a guarantee, and because dropping it costs a silent wrong answer.
    """
    out: list[WorkCandidate] = []
    dropped: dict[str, int] = {}
    for chunk in getattr(envelope.data, "chunks", None) or []:
        origin = str(getattr(chunk, "sub_tenant_id", "") or provider)
        if origin not in allowed:
            dropped[origin] = dropped.get(origin, 0) + 1
            continue
        raw = str(getattr(chunk, "chunk_content", "") or "")
        title = str(getattr(chunk, "source_title", "") or "")
        url = ""
        try:
            parsed = json.loads(raw)
            url = str(parsed.get("url") or "")
            title = title or str(parsed.get("title") or "")
            raw = str((parsed.get("content") or {}).get("text") or raw)
        except (TypeError, ValueError):
            pass
        out.append(
            WorkCandidate(
                document_id=str(getattr(chunk, "id", "") or ""),
                provider=origin,
                title=title,
                url=url,
                score=float(getattr(chunk, "relevancy_score", 0.0) or 0.0),
                text=raw,
            )
        )
    return out, dropped


# --- scoring -----------------------------------------------------------------


def _calibrate(raw: float, floor: float) -> float:
    """Retrieval relevancy sits in a compressed high band; stretch the usable part."""
    if raw <= floor:
        return 0.0
    headroom = 1.0 - floor
    return 1.0 if headroom <= 0 else min(1.0, (raw - floor) / headroom)


def _anchor_ok(
    candidate: WorkCandidate, candidates: list[WorkCandidate], profile: IntentProfile
) -> tuple[bool, str]:
    """A2 cut rule: optionally require GitHub matches to be anchored via Linear.

    Customers are not GitHub users, so a complaint rarely resembles a commit
    directly. When this is enabled a GitHub candidate only counts if it cites a
    Linear issue that also came back for the same complaint.
    """
    if candidate.provider != "github" or not getattr(profile, "anchor_github_via_linear", False):
        return True, ""

    linear_ids = {
        c.document_id.upper() for c in candidates if c.provider == "linear"
    } | {
        ref.upper()
        for c in candidates
        if c.provider == "linear"
        for ref in _LINEAR_REF.findall(f"{c.title} {c.text}")
    }
    cited = {ref.upper() for ref in _LINEAR_REF.findall(f"{candidate.title} {candidate.text}")}
    overlap = cited & linear_ids
    if overlap:
        return True, f"anchored via Linear {sorted(overlap)[0]}"
    return False, "github match rejected: no Linear anchor"


def score_candidate(
    complaint: Document,
    candidate: WorkCandidate,
    candidates: list[WorkCandidate],
    profile: IntentProfile,
) -> tuple[float, list[str]]:
    reasons: list[str] = []
    semantic = _calibrate(candidate.score, profile.semantic_floor)
    reasons.append(
        f"{candidate.provider} {candidate.document_id}: relevancy {candidate.score:.2f}"
        f" -> {semantic:.2f} above floor {profile.semantic_floor}"
    )

    weights = profile.match_weights
    identity = 0.0
    if complaint.actor_email and complaint.actor_email in candidate.text.lower():
        identity = 1.0
        reasons.append(f"reporter {complaint.actor_email} named in the work item")

    explicit = 0.0
    if complaint.id.lower() in candidate.text.lower():
        explicit = 1.0
        reasons.append(f"work item cites {complaint.id}")

    core = (
        semantic * weights.get("semantic", 0.0) + identity * weights.get("identity", 0.0)
    )
    denominator = weights.get("semantic", 0.0) + weights.get("identity", 0.0)
    score = core / denominator if denominator else semantic
    score = min(1.0, score + explicit * profile.explicit_reference_bonus)

    ok, why = _anchor_ok(candidate, candidates, profile)
    if not ok:
        reasons.append(why)
        return 0.0, reasons
    if why:
        reasons.append(why)
    return score, reasons


# --- the verdict -------------------------------------------------------------


def evaluate(
    complaint: Document,
    providers_in_scope: list[str],
    registry: ConnectorRegistry,
    profile: IntentProfile,
    client: Optional[hydra_db.HydraDB] = None,
    complaint_providers_read: Optional[list[str]] = None,
) -> LeakVerdict:
    """Decide whether one complaint became tracked work.

    Everything reported here — coverage, `sources_used`, the reason strings —
    is derived from the providers that actually answered, never from the ones
    that were asked for. A requested provider with no live collection is
    reported as requested-and-unavailable in `reasons`, because "we did not
    look there" is information, but it may not inflate confidence.

    `complaint_providers_read` is the complaint-side half of the same rule.
    `detect_leaks` knows which loads succeeded; a direct caller does not, so
    the declared scope is the fallback.
    """
    started = time.perf_counter()
    work_scope = [p for p in providers_in_scope if p in WORK_PROVIDERS]
    declared_complaints = (
        complaint_providers_read
        if complaint_providers_read is not None
        else providers_in_scope
    )
    complaint_used = [
        p
        for p in providers_in_scope
        if p in COMPLAINT_PROVIDERS and p in set(declared_complaints)
    ]

    search = (
        search_work(complaint.search_text, work_scope, registry, profile, client=client)
        if work_scope
        else WorkSearch()
    )

    # Only providers that answered count as used. GitHub with no collection is
    # dropped inside `search_work`, and used to remain in the coverage figure
    # and in the provenance line the demo points at.
    used = sorted(set(complaint_used) | set(search.searched))
    missing = [p for p in ALL_PROVIDERS if p not in used]

    base = dict(
        issue_cluster_id=f"{complaint.provider}:{complaint.id}",
        complaint_ids=[complaint.id],
        complaint_texts=[complaint.blob[:600]],
        actor_emails=[complaint.actor_email] if complaint.actor_email else [],
        sources_used=registry.connector_ids(used),
        sources_missing=registry.connector_ids(missing),
        providers_used=used,
        providers_missing=missing,
        threshold=profile.match_threshold,
    )

    # Requested-but-not-searched is kept rather than dropped: which source went
    # unread is as interesting as what the read ones said.
    notes: list[str] = []
    for provider in search.unavailable:
        notes.append(
            f"{provider} was in scope but holds no live collection, so it was never queried"
        )
    for provider, error in sorted(search.failed.items()):
        notes.append(f"{provider} query failed ({error}); its silence is not evidence")
    for origin, count in sorted(search.out_of_scope_chunks.items()):
        notes.append(
            f"dropped {count} chunk(s) from out-of-scope collection {origin} "
            f"returned by graph expansion"
        )

    # Nothing on the work side answered, so absence of tracked work cannot be
    # established. Refusing is the correct result, not a leak.
    if not search.grounded:
        if not work_scope:
            why = "no work-side connector in scope"
        elif search.failed:
            why = "every work-side query failed"
        else:
            why = "no work-side connector in scope holds a live collection"
        return LeakVerdict(
            verdict="unknown",
            confidence=None,
            matched_work_items=[],
            best_score=0.0,
            reasons=[
                f"{why}: cannot distinguish untracked from unseen",
                *notes,
            ],
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            **base,
        )

    candidates = search.candidates
    best_score = 0.0
    best: Optional[WorkCandidate] = None
    reasons: list[str] = []
    for candidate in candidates:
        score, why = score_candidate(complaint, candidate, candidates, profile)
        reasons.extend(why)
        if score > best_score:
            best_score, best = score, candidate

    if not candidates:
        reasons.append(
            f"searched {', '.join(search.searched)} and found no candidate work"
        )
    reasons.extend(notes)

    resolved = best is not None and best_score >= profile.match_threshold
    matched = (
        [WorkItemRef(best.provider, best.document_id, best.url, best.title, round(best_score, 4)).to_dict()]
        if resolved and best
        else []
    )

    if resolved:
        confidence = round(min(1.0, best_score), 4)
    else:
        # Confidence that it leaked rises as the best candidate weakens and as
        # more independent work sources agree there is nothing there. Coverage
        # counts the sources that spoke, not the ones that were asked.
        margin = (profile.match_threshold - best_score) / max(profile.match_threshold, 1e-6)
        coverage = len(search.searched) / len(WORK_PROVIDERS)
        confidence = round(max(0.0, min(1.0, margin * coverage)), 4)

    return LeakVerdict(
        verdict="resolved" if resolved else "leak",
        confidence=confidence,
        matched_work_items=matched,
        best_score=round(best_score, 4),
        reasons=reasons[:12],
        latency_ms=round((time.perf_counter() - started) * 1000, 1),
        **base,
    )


def _merge_clusters(verdicts: list[LeakVerdict]) -> list[LeakVerdict]:
    """Fold complaints that resolved to the same work item into one cluster.

    Two people reporting one bug is one issue, and reporting it twice should not
    read as two separate resolutions. Complaints with no match stay separate:
    merging unmatched complaints would need topical clustering between
    complaints, which A2 does not attempt.
    """
    by_work: dict[str, LeakVerdict] = {}
    out: list[LeakVerdict] = []
    for verdict in verdicts:
        key = (
            verdict.matched_work_items[0]["id"]
            if verdict.verdict == "resolved" and verdict.matched_work_items
            else ""
        )
        if not key:
            out.append(verdict)
            continue
        existing = by_work.get(key)
        if existing is None:
            by_work[key] = verdict
            out.append(verdict)
            continue
        existing.complaint_ids.extend(verdict.complaint_ids)
        existing.complaint_texts.extend(verdict.complaint_texts)
        existing.actor_emails.extend(a for a in verdict.actor_emails if a)
        existing.confidence = max(existing.confidence or 0.0, verdict.confidence or 0.0)
    return out


@dataclass
class LeakRun:
    """A `detect_leaks` run together with what it was actually able to read.

    An empty verdict list cannot say why it is empty. This can, which is the
    difference between "this tenant holds no complaints" and "we reached
    nothing". A caller that reports to a human must branch on `grounded`.
    """

    verdicts: list[LeakVerdict] = field(default_factory=list)
    providers_requested: list[str] = field(default_factory=list)
    # Complaint sources whose read completed, whether or not they held anything.
    complaint_providers_read: list[str] = field(default_factory=list)
    # Read successfully and genuinely holding nothing.
    complaint_providers_empty: list[str] = field(default_factory=list)
    # Requested, reachable, but no such collection exists in the tenant. An
    # honest absence rather than a failure, and kept apart from both.
    complaint_providers_absent: list[str] = field(default_factory=list)
    # provider -> what went wrong. A provider can appear here *and* in
    # `complaint_providers_read` when the read completed only in part.
    errors: dict[str, str] = field(default_factory=dict)
    complaints_examined: int = 0

    @property
    def grounded(self) -> bool:
        """True when at least one complaint source was actually read."""
        return bool(self.complaint_providers_read)

    @property
    def degraded(self) -> bool:
        return bool(self.errors)


def detect_leaks_run(
    providers_in_scope: Optional[list[str]] = None,
    profile: Optional[IntentProfile] = None,
    registry: Optional[ConnectorRegistry] = None,
) -> LeakRun:
    """`detect_leaks` with the read status attached.

    Use this anywhere the difference between "found no leaks" and "could not
    look" changes what should be shown.
    """
    profile = profile or get_profile()
    registry = registry or ConnectorRegistry()
    scope = list(providers_in_scope) if providers_in_scope is not None else list(ALL_PROVIDERS)
    client = _client()

    complaint_scope = [p for p in scope if p in COMPLAINT_PROVIDERS]
    run = LeakRun(providers_requested=scope)
    if not complaint_scope:
        run.errors["*"] = "no complaint-side provider in scope: nothing to examine"
        return run

    with ThreadPoolExecutor(max_workers=len(complaint_scope)) as pool:
        loads = list(
            pool.map(lambda p: load_documents_result(p, client=client), complaint_scope)
        )

    complaints: list[Document] = []
    for load in loads:
        if not load.ok:
            run.errors[load.provider] = load.error
            continue
        run.complaint_providers_read.append(load.provider)
        if load.unreadable:
            run.errors[load.provider] = (
                f"{load.unreadable} document(s) enumerated but could not be inspected"
            )
        if not load.present:
            run.complaint_providers_absent.append(load.provider)
        elif not load.documents:
            run.complaint_providers_empty.append(load.provider)
        complaints.extend(load.documents)

    run.complaints_examined = len(complaints)
    if not complaints:
        return run

    with ThreadPoolExecutor(max_workers=min(len(complaints), MAX_WORKERS)) as pool:
        verdicts = list(
            pool.map(
                lambda c: evaluate(
                    c,
                    scope,
                    registry,
                    profile,
                    client=client,
                    complaint_providers_read=run.complaint_providers_read,
                ),
                complaints,
            )
        )

    merged = _merge_clusters(verdicts)
    order = {"leak": 0, "unknown": 1, "resolved": 2}
    run.verdicts = sorted(
        merged, key=lambda v: (order.get(v.verdict, 3), -(v.confidence or 0.0))
    )
    return run


def detect_leaks(
    providers_in_scope: Optional[list[str]] = None,
    profile: Optional[IntentProfile] = None,
    registry: Optional[ConnectorRegistry] = None,
) -> list[LeakVerdict]:
    """The A2 entry point. Track B calls this and nothing else.

    `providers_in_scope` is the kill-shot handle: pass every provider for the
    full answer, or a subset to watch it degrade.

    Raises `SourceUnavailable` when no complaint source could be read at all,
    so that an empty list only ever means "read them, found nothing". Callers
    that would rather handle the difference than catch it should use
    `detect_leaks_run`.
    """
    run = detect_leaks_run(providers_in_scope, profile=profile, registry=registry)
    if not run.grounded:
        detail = "; ".join(f"{p}: {e}" for p, e in sorted(run.errors.items()))
        raise SourceUnavailable(
            f"no complaint source could be read for scope "
            f"{run.providers_requested}: {detail or 'no reason reported'}"
        )
    return run.verdicts


# --- interop with the 8-node pipeline ----------------------------------------
# `baseline` wires node N2 to `leaks.find_leaks`, which speaks LeakResult, while
# the PRD specifies LeakVerdict for Track A's own surface. Rather than pick one
# and break the other, A2 keeps LeakVerdict and offers this adapter, so N2 can
# move onto the HydraDB-native path in a single line when Track B is ready.

_VERDICT_TO_LEAK_RESULT = {
    "leak": "leaked",
    "resolved": "actioned",
    "unknown": "unknown_insufficient_sources",
}


def to_leak_result(verdict: LeakVerdict) -> LeakResult:
    """Express a LeakVerdict in the pipeline's LeakResult shape.

    One detail is lossy and worth knowing: LeakResult.confidence is a float, so
    the PRD's "confidence is null whenever the verdict is unknown" becomes 0.0.
    The distinction survives in the verdict string itself, which is what the UI
    and the kill-shot panel key on.
    """
    complaint = {
        "id": verdict.complaint_ids[0] if verdict.complaint_ids else verdict.issue_cluster_id,
        "source": verdict.issue_cluster_id.split(":", 1)[0],
        "text": verdict.complaint_texts[0] if verdict.complaint_texts else "",
        "author_email": verdict.actor_emails[0] if verdict.actor_emails else "",
        "channel_or_thread": "",
        "entity_id": "",
        "t": 0.0,
    }
    matched = verdict.matched_work_items[0] if verdict.matched_work_items else None
    return LeakResult(
        canonical_id=verdict.issue_cluster_id,
        complaint=complaint,
        matched_work=matched,
        verdict=_VERDICT_TO_LEAK_RESULT.get(verdict.verdict, verdict.verdict),
        sources_used=verdict.providers_used,
        confidence=verdict.confidence or 0.0,
        score=verdict.best_score,
        threshold=verdict.threshold,
        signals=[{"name": "reason", "value": 0.0, "weight": 0.0, "source": "hydradb", "detail": r} for r in verdict.reasons],
        unanswered=verdict.verdict == "leak",
        evidence_sources=[w["source"] for w in verdict.matched_work_items],
        sources_missing=verdict.providers_missing,
    )


def detect_leaks_as_results(
    providers_in_scope: Optional[list[str]] = None,
    profile: Optional[IntentProfile] = None,
) -> list[LeakResult]:
    """Drop-in replacement for `leaks.find_leaks` backed by the A2 query."""
    return [to_leak_result(v) for v in detect_leaks(providers_in_scope, profile)]


def summarise(verdicts: list[LeakVerdict]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for v in verdicts:
        counts[v.verdict] = counts.get(v.verdict, 0) + 1
    answerable = len(verdicts) - counts.get("unknown", 0)
    scored = [v.confidence for v in verdicts if v.confidence is not None]
    return {
        "clusters": len(verdicts),
        "by_verdict": counts,
        "leaks": counts.get("leak", 0),
        "answerable": answerable,
        "answerable_rate": round(answerable / len(verdicts), 4) if verdicts else 0.0,
        "mean_confidence": round(sum(scored) / len(scored), 4) if scored else 0.0,
    }
