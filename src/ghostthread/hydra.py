"""HydraDB grounding layer.

All four connectors land in one HydraDB database, one `sub_tenant_id` per
source. Retrieval is always scoped by an explicit source list, which is the
single mechanism the kill shot uses: run the same query with fewer
`sub_tenant_ids` and watch the join starve.

When no HydraDB token is configured we fall back to a local TF-IDF index that
implements the same interface. It is genuinely worse at retrieval, which is the
honest outcome, and it is clearly labelled as degraded in the API response. It
never contains precomputed answers.
"""

from __future__ import annotations

import math
import re
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from . import config
from .contracts import ComplaintEvent, WorkEvent

_POLL_INTERVAL_SECONDS = 2.0

_TOKEN = re.compile(r"[a-z0-9_]+")
_STOP = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "to", "of", "and",
    "or", "in", "on", "for", "with", "that", "this", "it", "we", "our", "us", "you",
    "your", "at", "by", "from", "as", "but", "not", "no", "so", "if", "then", "than",
    "there", "their", "has", "have", "had", "do", "does", "did", "can", "could",
    "will", "would", "should", "i", "me", "my", "just", "get", "got", "one", "two",
}


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 2]


@dataclass
class Candidate:
    work: WorkEvent
    semantic: float
    graph_hits: list[str]


class _LocalIndex:
    """Degraded stand-in for HydraDB retrieval. Same interface, worse recall."""

    name = "local-tfidf"

    def __init__(self) -> None:
        self._docs: dict[str, WorkEvent] = {}
        self._tf: dict[str, Counter] = {}
        self._df: Counter = Counter()

    def ingest(self, work: Iterable[WorkEvent]) -> int:
        for w in work:
            toks = _tokens(f"{w.title} {w.description}")
            self._docs[w.id] = w
            self._tf[w.id] = Counter(toks)
            for t in set(toks):
                self._df[t] += 1
        return len(self._docs)

    def _idf(self, term: str) -> float:
        n = max(len(self._docs), 1)
        return math.log(1.0 + (n / (1.0 + self._df.get(term, 0))))

    def search(self, query: str, sources: list[str], limit: int) -> list[Candidate]:
        q = Counter(_tokens(query))
        if not q:
            return []
        qnorm = math.sqrt(sum((v * self._idf(t)) ** 2 for t, v in q.items())) or 1.0
        scored: list[Candidate] = []
        for doc_id, tf in self._tf.items():
            work = self._docs[doc_id]
            if work.source not in sources:
                continue
            dot = sum(q[t] * tf[t] * (self._idf(t) ** 2) for t in q if t in tf)
            if dot <= 0:
                continue
            dnorm = math.sqrt(sum((v * self._idf(t)) ** 2 for t, v in tf.items())) or 1.0
            scored.append(Candidate(work=work, semantic=dot / (qnorm * dnorm), graph_hits=[]))
        scored.sort(key=lambda c: c.semantic, reverse=True)
        return scored[:limit]


class HydraGrounding:
    """Thin wrapper over the HydraDB v2 SDK with a local fallback."""

    def __init__(self) -> None:
        self.live = bool(config.HYDRA_TOKEN)
        self.backend = "hydradb" if self.live else "local-tfidf"
        self._local = _LocalIndex()
        self._work_by_id: dict[str, WorkEvent] = {}
        self._candidate_cache: dict[tuple, list[Candidate]] = {}
        self._collections: Optional[set[str]] = None
        self._cache_lock = threading.Lock()
        self._client = None
        if self.live:
            import hydra_db

            kwargs: dict[str, Any] = {"token": config.HYDRA_TOKEN}
            if config.HYDRA_BASE_URL:
                kwargs["base_url"] = config.HYDRA_BASE_URL
            self._client = hydra_db.HydraDB(**kwargs)

    # -- ingestion ------------------------------------------------------------

    @staticmethod
    def _ts(epoch: float) -> str:
        """HydraDB rejects numeric timestamps; it wants RFC3339 strings."""
        return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def ingest(
        self, complaints: list[ComplaintEvent], work: list[WorkEvent]
    ) -> dict[str, Any]:
        """Push every event into HydraDB, one sub-tenant per source.

        `relations.ids` is what builds the context graph: we link each work item
        to the reporter's identity node so HydraDB can traverse from a person to
        everything they touched across tools.
        """
        for w in work:
            self._work_by_id[w.id] = w
        self._local.ingest(work)
        with self._cache_lock:
            self._candidate_cache.clear()

        if not self.live:
            return {"backend": self.backend, "ingested": len(complaints) + len(work)}

        import json

        by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for c in complaints:
            by_source[c.source].append(
                {
                    "id": c.id,
                    "title": c.text[:80],
                    "type": c.source,
                    "content": {"text": c.text},
                    "url": f"{c.source}://{c.channel_or_thread}/{c.id}",
                    "timestamp": self._ts(c.t),
                    "tenant_metadata": {
                        "kind": "complaint",
                        "source": c.source,
                        "channel": c.channel_or_thread,
                    },
                    "additional_metadata": {
                        "author_email": c.author_email,
                        "entity_id": c.entity_id,
                    },
                    "relations": {"ids": [f"person:{c.author_email}"]},
                }
            )
        for w in work:
            by_source[w.source].append(
                {
                    "id": w.id,
                    "title": w.title,
                    "type": w.source,
                    "content": {"text": f"{w.title}\n\n{w.description}"},
                    "url": f"{w.source}://{w.entity_id}",
                    "timestamp": self._ts(w.t_created),
                    "tenant_metadata": {
                        "kind": "work",
                        "source": w.source,
                        "status": w.status,
                    },
                    "additional_metadata": {
                        "reporter_email": w.reporter_email,
                        "entity_id": w.entity_id,
                    },
                    "relations": {"ids": [f"person:{w.reporter_email}"]},
                }
            )

        counts: dict[str, int] = {}
        for source, items in by_source.items():
            self._client.context.ingest(
                database=config.HYDRA_DATABASE,
                tenant_id=config.HYDRA_TENANT_ID,
                sub_tenant_id=source,
                type="knowledge",
                app_knowledge=json.dumps(items),
                upsert="true",
            )
            counts[source] = len(items)
        return {
            "backend": self.backend,
            "ingested_by_source": counts,
            "ids": [item["id"] for items in by_source.values() for item in items],
        }

    # -- retrieval ------------------------------------------------------------

    def candidates(
        self, complaint: ComplaintEvent, sources: list[str], limit: int = 5
    ) -> list[Candidate]:
        """Work items that might correspond to this complaint, scoped to `sources`.

        An empty `sources` list means there is nothing to join against, which is
        exactly what the single-source kill shot produces.
        """
        if not sources:
            return []
        if not self.live:
            return self._local.search(complaint.text, sources, limit)

        # The kill shot asks the same (complaint, scope) pair more than once, and
        # a round trip is ~2s. Memoise so re-scoping is instant.
        cache_key = (complaint.id, tuple(sorted(sources)), limit)
        with self._cache_lock:
            hit = self._candidate_cache.get(cache_key)
        if hit is not None:
            return hit

        # Naming a collection that holds no documents is a 400, not an empty
        # result, so an unpopulated connector would take down the whole query.
        # An empty intersection means there is genuinely nothing to join against,
        # which is a real "no candidates" answer rather than an error.
        scope = [s for s in sources if s in self.existing_collections()]
        if not scope:
            return []

        try:
            envelope = self._client.query(
                query=complaint.text,
                database=config.HYDRA_DATABASE,
                collections=scope,
                type="knowledge",
                query_by="hybrid",
                graph_context=True,
                max_results=limit,
                recency_bias=0.2,
            )
        except Exception:
            return self._local.search(complaint.text, sources, limit)

        parsed = self._parse_results(envelope, sources, limit)
        with self._cache_lock:
            self._candidate_cache[cache_key] = parsed
        return parsed

    def _parse_results(self, envelope: Any, sources: list[str], limit: int) -> list[Candidate]:
        data = getattr(envelope, "data", envelope)
        chunks = getattr(data, "chunks", None) or []
        graph = getattr(data, "graph_context", None) or {}

        raw: list[tuple[WorkEvent, float, list[str]]] = []
        for chunk in chunks:
            doc_id = str(_attr(chunk, "id") or "")
            work = self._work_by_id.get(doc_id)
            if work is None or work.source not in sources:
                continue
            raw.append(
                (work, float(_attr(chunk, "relevancy_score") or 0.0), _graph_hits(graph, doc_id))
            )

        # Raw relevancy is returned as-is. HydraDB compresses scores into a high,
        # narrow band (an unrelated document still scores ~0.7), but deciding
        # where "unrelated" ends is a policy call, so calibration happens in
        # leaks.py against the intent profile rather than being baked in here.
        return [
            Candidate(work=work, semantic=score, graph_hits=hits)
            for work, score, hits in raw[:limit]
        ]

    def existing_collections(self, refresh: bool = False) -> set[str]:
        """Collections that currently hold documents.

        HydraDB rejects a query naming a collection with nothing in it, so this
        is consulted before every scoped query. Cached, because it changes only
        when a connector syncs.
        """
        if not self.live:
            return {w.source for w in self._work_by_id.values()}
        with self._cache_lock:
            if self._collections is not None and not refresh:
                return self._collections
        try:
            env = self._client.databases.collections(database=config.HYDRA_DATABASE)
            found = set(getattr(env.data, "sub_tenant_ids", None) or [])
        except Exception:
            found = set()
        with self._cache_lock:
            self._collections = found
        return found

    def row_count(self) -> int:
        """Indexed knowledge rows in the database, or -1 if unavailable."""
        if not self.live:
            return len(self._work_by_id)
        try:
            env = self._client.databases.stats(database=config.HYDRA_DATABASE)
            data = getattr(env, "data", env)
            collection = getattr(data, "knowledge_collection", None)
            return int(_attr(collection, "row_count") or 0)
        except Exception:
            return -1

    def wait_until_indexed(self, expected_rows: int, timeout: float = 60.0) -> dict[str, Any]:
        """Block until the index actually contains what we just pushed.

        Ingest returns as soon as documents are accepted, but they are not
        retrievable until embedding and graph linking finish. Querying too early
        silently searches a stale index, which looks exactly like a leak.

        Per-document status is not usable for this: `context.status` tracks
        upload jobs rather than document ids, and the `ids` filter on
        `context.list` returns nothing for app-knowledge sources. The collection
        row count is the signal that actually moves.
        """
        if not self.live or expected_rows <= 0:
            return {"waited": False}

        deadline = time.time() + timeout
        seen = -1
        while time.time() < deadline:
            seen = self.row_count()
            if seen < 0 or seen >= expected_rows:
                break
            time.sleep(_POLL_INTERVAL_SECONDS)

        with self._cache_lock:
            self._candidate_cache.clear()
            self._collections = None
        return {"waited": True, "rows": seen, "expected": expected_rows}

    def relations(self, source_id: str) -> list[dict[str, Any]]:
        """Context graph edges for one node. Rendered in the UI as provenance."""
        if not self.live:
            return []
        try:
            env = self._client.context.relations(
                database=config.HYDRA_DATABASE,
                tenant_id=config.HYDRA_TENANT_ID,
                id=source_id,
                type="knowledge",
                limit=10,
            )
        except Exception:
            return []
        data = getattr(env, "data", env)
        triplets = getattr(data, "triplets", None) or []
        return [
            {
                "subject": str(_attr(t, "subject") or ""),
                "predicate": str(_attr(t, "predicate") or ""),
                "object": str(_attr(t, "object") or ""),
            }
            for t in triplets
        ]


def _graph_hits(graph: Any, doc_id: str) -> list[str]:
    """Entity names the context graph associates with this document."""
    entries = graph.get(doc_id) if isinstance(graph, dict) else None
    if not entries:
        return []
    hits: list[str] = []
    for entry in entries if isinstance(entries, list) else [entries]:
        label = _attr(entry, "entity") or _attr(entry, "name") or _attr(entry, "object")
        if label:
            hits.append(str(label))
    return hits[:4]


def _attr(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)
