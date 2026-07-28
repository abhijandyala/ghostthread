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

COMPLAINT_PROVIDERS = ("slack", "gmail")
WORK_PROVIDERS = ("linear", "github")
ALL_PROVIDERS = COMPLAINT_PROVIDERS + WORK_PROVIDERS

MAX_WORKERS = 12
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


def _iso_to_epoch(value: Any) -> float:
    if not value:
        return 0.0
    from datetime import datetime

    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return 0.0


def _actor_of(meta: dict[str, Any]) -> str:
    for key in ("author_email", "reporter_email", "from", "sender", "slack_author_id", "actor"):
        value = meta.get(key)
        if value:
            return str(value).lower()
    return ""


_collection_cache: dict[str, set[str]] = {}


def live_collections(
    client: Optional[hydra_db.HydraDB] = None, refresh: bool = False
) -> set[str]:
    """Collections that hold documents. Naming an empty one is a 400.

    Cached: this is consulted before every query and costs ~330ms uncached.
    """
    if not refresh and "value" in _collection_cache:
        return _collection_cache["value"]
    client = client or _client()
    try:
        env = client.databases.collections(database=config.HYDRA_DATABASE)
        found = set(getattr(env.data, "sub_tenant_ids", None) or [])
    except Exception:
        return set()
    _collection_cache["value"] = found
    return found


_document_cache: dict[str, list[Document]] = {}


def refresh_documents() -> None:
    """Drop cached documents. Call after a connector sync or a new ingest."""
    _document_cache.clear()
    _collection_cache.clear()


def load_documents(
    provider: str, client: Optional[hydra_db.HydraDB] = None, refresh: bool = False
) -> list[Document]:
    """Enumerate a collection, then inspect each source for its full content.

    `context.list` only returns ids and titles, and its `include_fields` rejects
    content, so the body has to come from `context.inspect`. Enumerating rather
    than searching matters: a search term would silently drop any complaint that
    did not match it, and a missed complaint is indistinguishable from a leak.
    """
    if not refresh and provider in _document_cache:
        return _document_cache[provider]

    client = client or _client()
    if provider not in live_collections(client):
        return []

    ids: list[str] = []
    page = 1
    while True:
        try:
            resp = client.context.list(
                database=config.HYDRA_DATABASE,
                collection=provider,
                type="knowledge",
                page=page,
                page_size=100,
                include_fields=["title"],
            )
        except Exception:
            break
        sources = getattr(resp.data, "sources", None) or []
        if not sources:
            break
        for src in sources:
            doc_id = src.get("id") if isinstance(src, dict) else getattr(src, "id", None)
            if doc_id:
                ids.append(str(doc_id))
        if len(sources) < 100:
            break
        page += 1

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
        meta = {
            **(payload.get("additional_metadata") or {}),
            **(payload.get("document_metadata") or {}),
            **(payload.get("tenant_metadata") or {}),
        }
        return Document(
            id=str(payload.get("id") or doc_id),
            provider=provider,
            title=str(payload.get("title") or ""),
            text=str(content.get("text") or content.get("markdown") or ""),
            url=str(payload.get("url") or ""),
            timestamp=_iso_to_epoch(payload.get("timestamp")),
            actor_email=_actor_of(meta),
            metadata=meta,
        )

    if not ids:
        _document_cache[provider] = []
        return []
    with ThreadPoolExecutor(max_workers=min(len(ids), MAX_WORKERS)) as pool:
        docs = [d for d in pool.map(fetch, ids) if d is not None]
    _document_cache[provider] = docs
    return docs


# --- retrieval ---------------------------------------------------------------


@dataclass
class WorkCandidate:
    document_id: str
    provider: str
    title: str
    url: str
    score: float
    text: str


def search_work(
    complaint_text: str,
    providers: list[str],
    registry: ConnectorRegistry,
    profile: IntentProfile,
    client: Optional[hydra_db.HydraDB] = None,
    limit: int = 5,
    use_connector_filter: bool = True,
) -> list[WorkCandidate]:
    """Retrieve possible matching work items, scoped to `providers`.

    Scoping is applied twice on purpose: `collections` narrows the corpus, and
    `metadata_filters.additional_metadata.connector_id` narrows to the exact
    connectors in scope. The second is the kill-shot handle; belt and braces
    means a stale collection cannot leak a source back into a scoped run.
    """
    client = client or _client()
    scope = [p for p in providers if p in live_collections(client)]
    if not scope:
        return []

    kwargs: dict[str, Any] = dict(
        query=complaint_text,
        database=config.HYDRA_DATABASE,
        collections=scope,
        type="knowledge",
        query_by="hybrid",
        graph_context=True,
        mode=profile.recall_mode,
        max_results=limit,
    )
    connector_ids = registry.connector_ids(scope)
    if use_connector_filter and connector_ids:
        kwargs["metadata_filters"] = {"additional_metadata": {"connector_id": connector_ids}}

    try:
        envelope = client.query(**kwargs)
    except Exception:
        # A connector-id filter can legitimately match nothing (for example a
        # source ingested outside the managed connectors). Retry on collection
        # scope alone rather than reporting a false leak.
        kwargs.pop("metadata_filters", None)
        try:
            envelope = client.query(**kwargs)
        except Exception:
            return []

    out: list[WorkCandidate] = []
    for chunk in getattr(envelope.data, "chunks", None) or []:
        provider = str(getattr(chunk, "sub_tenant_id", "") or "")
        if provider not in scope:
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
                provider=provider,
                title=title,
                url=url,
                score=float(getattr(chunk, "relevancy_score", 0.0) or 0.0),
                text=raw,
            )
        )
    return out


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
) -> LeakVerdict:
    started = time.perf_counter()
    work_scope = [p for p in providers_in_scope if p in WORK_PROVIDERS]
    missing = [p for p in WORK_PROVIDERS if p not in work_scope]

    base = dict(
        issue_cluster_id=f"{complaint.provider}:{complaint.id}",
        complaint_ids=[complaint.id],
        complaint_texts=[complaint.blob[:600]],
        actor_emails=[complaint.actor_email] if complaint.actor_email else [],
        sources_used=registry.connector_ids(providers_in_scope),
        sources_missing=registry.connector_ids(missing),
        providers_used=sorted(providers_in_scope),
        providers_missing=sorted(missing),
        threshold=profile.match_threshold,
    )

    # No work-side source in scope means absence of tracked work cannot be
    # established. Refusing to answer is the correct result, not a leak.
    if not work_scope:
        return LeakVerdict(
            verdict="unknown",
            confidence=None,
            matched_work_items=[],
            best_score=0.0,
            reasons=[
                "no work-side connector in scope: cannot distinguish untracked from unseen"
            ],
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            **base,
        )

    candidates = search_work(complaint.search_text, work_scope, registry, profile, client=client)
    best_score = 0.0
    best: Optional[WorkCandidate] = None
    reasons: list[str] = []
    for candidate in candidates:
        score, why = score_candidate(complaint, candidate, candidates, profile)
        reasons.extend(why)
        if score > best_score:
            best_score, best = score, candidate

    if not candidates:
        reasons.append(f"searched {', '.join(work_scope)} and found no candidate work")

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
        # more independent work sources agree there is nothing there.
        margin = (profile.match_threshold - best_score) / max(profile.match_threshold, 1e-6)
        coverage = len(work_scope) / len(WORK_PROVIDERS)
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


def detect_leaks(
    providers_in_scope: Optional[list[str]] = None,
    profile: Optional[IntentProfile] = None,
    registry: Optional[ConnectorRegistry] = None,
) -> list[LeakVerdict]:
    """The A2 entry point. Track B calls this and nothing else.

    `providers_in_scope` is the kill-shot handle: pass every provider for the
    full answer, or a subset to watch it degrade.
    """
    profile = profile or get_profile()
    registry = registry or ConnectorRegistry()
    scope = list(providers_in_scope) if providers_in_scope is not None else list(ALL_PROVIDERS)
    client = _client()

    complaint_scope = [p for p in scope if p in COMPLAINT_PROVIDERS]
    complaints: list[Document] = []
    if complaint_scope:
        with ThreadPoolExecutor(max_workers=len(complaint_scope)) as pool:
            for batch in pool.map(lambda p: load_documents(p, client=client), complaint_scope):
                complaints.extend(batch)
    if not complaints:
        return []

    with ThreadPoolExecutor(max_workers=min(len(complaints), MAX_WORKERS)) as pool:
        verdicts = list(
            pool.map(lambda c: evaluate(c, scope, registry, profile, client=client), complaints)
        )

    merged = _merge_clusters(verdicts)
    order = {"leak": 0, "unknown": 1, "resolved": 2}
    return sorted(merged, key=lambda v: (order.get(v.verdict, 3), -(v.confidence or 0.0)))


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
