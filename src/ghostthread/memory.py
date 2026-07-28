"""Episodic memory over HydraDB. Pipeline nodes N1 (read) and N7 (write).

Track A owns this file. The two functions are the whole memory story: N1 asks
what we already know about this person and this topic before anything is
decided, N7 writes back one episodic record per resolution so the next
complaint is smarter than this one.

Both talk to the `memories` collection with `type="memory"`, which is a
different index from the knowledge documents `knowledge_query.py` searches.
The client, the relevancy calibration and the collection guard are reused from
there rather than duplicated.

What the live API actually does (measured against the tenant, 2026-07-28)
------------------------------------------------------------------------
* A memory query MUST name `collection="memories"`. Unscoped, it returns zero
  results rather than erroring, so a missing collection argument looks exactly
  like an empty history -- the most dangerous failure this module can have.
* Querying a collection that holds nothing is a 400, not an empty result, hence
  the `live_collections` guard before the first query.
* `metadata_filters={"additional_metadata": {...}}` does work on memory queries,
  contrary to the SDK type hint, which claims `str`. Multiple keys AND together.
* A list value does NOT mean "any of". `{"source": ["slack", "gmail"]}` matches
  nothing at all, while either name alone matches. So a multi-source scope is
  run as one filtered query per source and unioned, never as one query filtered
  afterwards -- scoping the query is what makes the kill shot honest.
* Relevancy sits in a compressed high band: an unrelated memory still scores
  ~0.65 against ~0.83 for a real topical match. `profile.semantic_floor` is the
  cut, applied through the same `_calibrate` the leak query uses.

Scope and the kill shot
-----------------------
Memories are records of complaints, so they inherit the complaint sources.
With no complaint source in scope there is no honest way to recall history, and
the read returns zeros. `MemoryWriteInput` carries no source field, so the
originating source is recovered from the complaint id prefix; when that fails
the memory is written as `unattributed` and is only recalled on a full-scope
run -- it cannot be honestly attributed to a source, so it also cannot be
honestly excluded by removing one.

Never invent history. Zero prior contacts is a real and common answer, and a
`first_contact` reply is the correct output for it. Every HydraDB call here is
wrapped so a failure degrades to a smaller count, never to a plausible one.

Two kinds of zero
-----------------
"We looked and there is no history" and "we cannot look, or nothing we resolve
is ever kept, so there will never be history" are the same number and must not
look the same. `is_operational()` separates them and `MemoryReadResult.stub`
carries the answer out to callers: under `DRY_RUN`, or with no credential, the
write side persists nothing, so the counts are flagged rather than presented as
a measurement. A zero that fell out of a *scoped* run is a real measurement and
is deliberately not flagged -- that zero is the kill shot.
"""

from __future__ import annotations

import json
import re
import warnings
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Optional

from . import config
from .contracts import (
    COMPLAINT_SOURCES,
    ComplaintEvent,
    IntentProfile,
    MemoryReadResult,
    MemoryWriteInput,
    PriorResolution,
    RegressionRef,
)
from .knowledge_query import _calibrate, _client, distil, live_collections

# The real queries have landed. `smoke.py` and `/health` read this, so the
# pipeline now reports itself demo-ready on the memory nodes. It answers only
# "is this code a placeholder"; whether the node can actually accumulate
# history is a separate, run-time question -- see `is_operational()`.
IS_STUB: bool = False

MEMORY_COLLECTION = "memories"
# Written when the complaint id does not carry a recognisable source prefix.
UNATTRIBUTED = "unattributed"
MAX_MEMORY_RESULTS = 25
# `client.query` has no page or offset parameter, so the only way to page is to
# ask for a wider window and re-read. A count taken from a saturated window is a
# lower bound presented as a fact, so the window is widened until it stops
# filling, up to this ceiling.
MAX_MEMORY_WINDOW = 400
WINDOW_GROWTH = 4
MAX_WORKERS = 8
SUMMARY_LIMIT = 240
# Complaint ids are prefixed with their source, both in the fixture corpus and
# in what the live connectors return; any of these separators may follow it.
_ID_SEPARATORS = re.compile(r"[-:/_]")

# Complaint id -> the source the ComplaintEvent actually declared, observed at
# read time (N1) and consulted at write time (N7). See `_source_of`.
_observed_sources: dict[str, str] = {}
OBSERVED_SOURCE_LIMIT = 4096


def is_operational() -> bool:
    """True when a memory written now would actually reach HydraDB.

    `IS_STUB` answers "is this code still a placeholder", and it is False: the
    queries here are real. This answers the different and more urgent question
    "can this node accumulate history at all", which is what a caller needs
    before it believes a zero. Under `DRY_RUN`, or with no credential, the
    write side returns a marked id without persisting, so the counts the read
    side reports can never grow no matter how many complaints are resolved.
    """
    return bool(config.HYDRA_TOKEN) and not config.DRY_RUN


_read_truncated = False


def last_read_truncated() -> bool:
    """Whether the most recent `memory_read` hit `MAX_MEMORY_WINDOW`.

    True means the counts in that result are a floor rather than a total. It is
    a module-level accessor rather than a field because `MemoryReadResult` is
    frozen contract and has nowhere to carry it; a `warnings.warn` is raised at
    the same moment so it cannot pass unnoticed.
    """
    return _read_truncated


@dataclass
class _Hit:
    """One memory chunk, normalised."""

    id: str
    score: float
    text: str
    meta: dict[str, Any]


def _fetch(
    client: Any,
    query_text: str,
    source: Optional[str],
    actor: Optional[str],
    limit: int = MAX_MEMORY_RESULTS,
) -> list[_Hit]:
    """One scoped memory query. Returns [] on any failure, never raises.

    `source` and `actor` go into `metadata_filters`, so a memory outside the
    scope is never retrieved in the first place. A filter that matches nothing
    comes back empty rather than erroring, which is why there is no retry
    without the filter here: an empty scoped result is the honest answer.

    `source=None` means "do not constrain the source", which is only ever used
    on a full-scope run.
    """
    filters: dict[str, Any] = {}
    if source:
        filters["source"] = source
    if actor:
        filters["actor"] = actor
    kwargs: dict[str, Any] = dict(
        query=query_text,
        database=config.HYDRA_DATABASE,
        collection=MEMORY_COLLECTION,
        type="memory",
        query_by="hybrid",
        max_results=limit,
    )
    if filters:
        kwargs["metadata_filters"] = {"additional_metadata": filters}
    try:
        envelope = client.query(**kwargs)
    except Exception:
        return []

    hits: list[_Hit] = []
    for chunk in getattr(envelope.data, "chunks", None) or []:
        chunk_id = str(getattr(chunk, "id", "") or "")
        if not chunk_id:
            continue
        hits.append(
            _Hit(
                id=chunk_id,
                score=float(getattr(chunk, "relevancy_score", 0.0) or 0.0),
                text=str(getattr(chunk, "chunk_content", "") or ""),
                meta=dict(getattr(chunk, "additional_metadata", None) or {}),
            )
        )
    return hits


def _fetch_paged(
    client: Any,
    query_text: str,
    source: Optional[str],
    actor: Optional[str],
) -> tuple[list[_Hit], bool]:
    """Everything a pass matches, plus whether the window was still full.

    A result set exactly the size of the window means there may be more, and
    reporting the count from it would under-report prior contacts silently.
    There is no offset parameter to page with, so the window itself is widened
    and the query re-read until it stops filling. Returns `(hits, truncated)`
    where `truncated` is True only if the ceiling was reached still saturated.
    """
    window = MAX_MEMORY_RESULTS
    hits = _fetch(client, query_text, source, actor, window)
    while len(hits) >= window and window < MAX_MEMORY_WINDOW:
        window = min(window * WINDOW_GROWTH, MAX_MEMORY_WINDOW)
        wider = _fetch(client, query_text, source, actor, window)
        # A wider window that returns no more rows has exhausted the matches;
        # one that returns fewer means the query failed, and the narrower
        # result we already hold is the better evidence.
        if len(wider) <= len(hits):
            return hits, False
        hits = wider
    return hits, len(hits) >= window


def _scope_sources(sources: list[str]) -> list[Optional[str]]:
    """The source filters to query under, one per pass.

    A full run appends `None`, an unconstrained pass, so that memories carrying
    no source metadata -- ones written before this module, or whose complaint
    id gave up no prefix -- are still recalled. They are deliberately absent
    from a partial run: a memory that cannot be attributed to a source cannot
    honestly be shown as surviving the removal of one either.
    """
    scope: list[Optional[str]] = sorted({s for s in sources if s in COMPLAINT_SOURCES})
    if set(COMPLAINT_SOURCES).issubset(set(sources)):
        scope.append(None)
    return scope


def memory_read(
    complaint: ComplaintEvent,
    sources: list[str],
    profile: IntentProfile,
    grounding: Any = None,
) -> MemoryReadResult:
    """Prior history for this actor and this topic, scoped to `sources`.

    Two families of query run per source in scope: one filtered to this actor,
    which answers "how often has this person come to us", and one open across
    actors, which answers "how often have we seen this topic". The actor query
    is an exact metadata match so every hit counts; the topic query is
    retrieval, so hits must clear `profile.semantic_floor` to count.
    """
    global _read_truncated
    _read_truncated = False
    _remember_source(complaint)

    actor = (complaint.author_email or complaint.entity_id or "").strip()
    # `stub` means "do not read these counts as history". A dry run earns that
    # flag: the write side returns a marked id without persisting, so the zero
    # this can only ever return is an artefact of the configuration and not a
    # measurement of the world -- exactly the confusion the field exists to
    # prevent. It is deliberately NOT tied to scope: a count that fell to zero
    # because a source was scoped away is a real, measured zero and the whole
    # point of the kill shot, so those results stay stub=False.
    stub = not is_operational()
    empty = MemoryReadResult(
        actor=actor,
        sources_used=sorted(sources),
        stub=stub,
    )

    scope = _scope_sources(sources)
    # No credential or no complaint source in scope: history cannot be
    # grounded, and zeros are the honest answer rather than a degraded guess.
    if not actor or not scope or not config.HYDRA_TOKEN:
        return empty

    try:
        client = _client()
    except Exception:
        return empty
    # Nothing has ever been written, so there is no collection to query and
    # asking would be a 400 rather than an empty result.
    #
    # The miss is re-checked against the API before it is believed. The cache
    # behind `live_collections` lives for the whole process and nothing else
    # clears it, so a set cached before the first memory was ever written would
    # make this function return zeros for the rest of that process -- failing
    # in the one way that looks exactly like "no history". The cached hit stays
    # cheap; only a miss pays the ~330ms re-read, and a miss is rare.
    if MEMORY_COLLECTION not in live_collections(client):
        if MEMORY_COLLECTION not in live_collections(client, refresh=True):
            return empty

    topic = distil("", complaint.text)
    passes: list[tuple[str, Optional[str], Optional[str]]] = []
    for source in scope:
        passes.append((f"{actor} {topic}".strip(), source, actor))
        passes.append((topic, source, None))

    with ThreadPoolExecutor(max_workers=min(len(passes), MAX_WORKERS)) as pool:
        results = list(pool.map(lambda p: _fetch_paged(client, *p), passes))

    truncated = any(flag for _, flag in results)
    if truncated:
        _read_truncated = True
        warnings.warn(
            f"memory_read hit the {MAX_MEMORY_WINDOW}-result ceiling for {actor!r}: "
            "the reported counts are a lower bound, not a total",
            stacklevel=2,
        )

    # The current complaint's own memory is upserted under a derived id, so a
    # re-run would otherwise recall it and count this very complaint as prior
    # history of itself.
    self_id = complaint.id.strip()
    self_memory_id = f"mem-{self_id}"

    by_actor: dict[str, _Hit] = {}
    on_topic: dict[str, _Hit] = {}
    for (_, _, filtered_actor), (hits, _) in zip(passes, results):
        for hit in hits:
            key = _memory_key(hit)
            if self_id and (key == self_id or hit.id.startswith(self_memory_id)):
                continue
            if filtered_actor:
                _keep_best(by_actor, key, hit)
            elif _calibrate(hit.score, profile.semantic_floor) > 0.0:
                _keep_best(on_topic, key, hit)

    recalled = sorted(
        {**on_topic, **by_actor}.values(), key=lambda h: h.score, reverse=True
    )

    prior: list[PriorResolution] = []
    regression: Optional[RegressionRef] = None
    for hit in recalled:
        ticket_url = str(hit.meta.get("ticket_url") or "")
        resolved_at = str(hit.meta.get("resolved_at") or "")
        # A memory with neither is a prior contact but not a prior resolution,
        # and reporting it as one would overstate what was done last time.
        if ticket_url or resolved_at:
            prior.append(
                PriorResolution(
                    ticket_url=ticket_url,
                    resolved_at=resolved_at,
                    summary=str(hit.meta.get("summary") or hit.text)[:SUMMARY_LIMIT],
                )
            )
        ref = str(hit.meta.get("regression_ref") or "")
        if regression is None and ref:
            regression = RegressionRef(
                source=str(hit.meta.get("regression_source") or ""),
                ref=ref,
                url=str(hit.meta.get("regression_url") or ""),
            )

    return MemoryReadResult(
        actor=actor,
        times_reported_by_actor=len(by_actor),
        times_seen_on_topic=len(on_topic),
        prior_resolutions=prior,
        likely_regression=regression,
        sources_used=sorted(sources),
        stub=stub,
    )


def _remember_source(complaint: ComplaintEvent) -> None:
    """Note the source the complaint itself declared, for the write side.

    `MemoryWriteInput` carries no source field and `contracts.py` is frozen, so
    N7 has only the complaint id to go on -- and a live-typed complaint gets an
    id with no source prefix, which would store the judge's own complaint as
    `unattributed` and make it invisible to every scoped run. N1 does have the
    `ComplaintEvent`, and N1 always runs immediately before N7 for the same
    complaint, so the source is recorded here rather than guessed there. This
    is an observation, not an inference: it is the source the event declared.
    """
    source = (complaint.source or "").strip().lower()
    key = complaint.id.strip()
    if not key or source not in COMPLAINT_SOURCES:
        return
    if len(_observed_sources) >= OBSERVED_SOURCE_LIMIT:
        _observed_sources.clear()
    _observed_sources[key] = source


def _memory_key(hit: _Hit) -> str:
    """The identity of the prior contact a chunk belongs to.

    One prior contact is one complaint, not one chunk. HydraDB may split a
    memory across chunks, and counting chunks would report a single previous
    complaint several times -- a plausible wrong number, stamped onto
    `ExtractedFacts` and spoken as fact in the reply. Every memory this module
    writes carries `complaint_id`; the chunk id is only the fallback for rows
    written before that was true.
    """
    return str(hit.meta.get("complaint_id") or "").strip() or hit.id


def _keep_best(bucket: dict[str, _Hit], key: str, hit: _Hit) -> None:
    """Keep the strongest chunk per prior contact, so ranking survives dedup."""
    existing = bucket.get(key)
    if existing is None or hit.score > existing.score:
        bucket[key] = hit


def _source_of(complaint_id: str) -> str:
    """Recover the originating source from the complaint id, or admit we cannot."""
    key = complaint_id.strip()
    observed = _observed_sources.get(key)
    if observed:
        return observed
    head = _ID_SEPARATORS.split(key.lower(), maxsplit=1)[0]
    return head if head in COMPLAINT_SOURCES else UNATTRIBUTED


def _regression_of(record: MemoryWriteInput) -> dict[str, str]:
    """A regression ref, only when the resolution actually names a code change.

    `MemoryWriteInput` has no field for the PR a fix landed in, so the only
    place one can appear today is a ticket url that already points at a GitHub
    pull request. Anything else stays absent rather than being guessed at.
    """
    url = str(record.ticket_url or "")
    match = re.search(r"github\.com/[^\s]+/pull/(\d+)", url)
    if not match:
        return {}
    return {
        "regression_source": "github",
        "regression_ref": f"#{match.group(1)}",
        "regression_url": url,
    }


def _compose_text(record: MemoryWriteInput) -> str:
    """The searchable body. Must contain both the actor and the topic.

    The read side finds this two ways -- an actor-filtered query and an open
    topical one -- so both handles have to be present in the text itself, not
    only in the metadata.
    """
    parts = [f"{record.actor} reported {record.complaint_summary}".strip()]
    if record.category:
        parts.append(f"Category: {record.category}.")
    if record.action_taken:
        parts.append(f"Action taken: {', '.join(record.action_taken)}.")
    if record.ticket_url:
        parts.append(f"Ticket: {record.ticket_url}.")
    if record.resolved_at:
        parts.append(f"Resolved at {record.resolved_at}.")
    return " ".join(p for p in parts if p)


def memory_write(
    record: MemoryWriteInput,
    grounding: Any = None,
) -> Optional[str]:
    """Persist one episodic memory. Returns the written id, or None.

    The id is derived from the complaint id and ingested with `upsert`, so a
    replayed resolution updates one row instead of inflating the very counts
    the read side reports.

    A `DRY-` prefixed id means nothing was persisted. That is the correct
    behaviour for a dry run, but it also means history can never accumulate,
    so `is_operational()` reports it and every `MemoryReadResult` produced in
    that state comes back `stub=True` rather than posing as a measured zero.
    """
    memory_id = f"mem-{record.complaint_id}"

    # Same gate as every other side effect in the pipeline: in a dry run we
    # compute the exact payload and return a marked id, so the demo shows real
    # intent without writing into the shared tenant.
    if config.DRY_RUN:
        return f"DRY-{memory_id}"
    if not config.HYDRA_TOKEN:
        return None

    metadata: dict[str, Any] = {
        "actor": record.actor,
        "complaint_id": record.complaint_id,
        "category": record.category,
        "source": _source_of(record.complaint_id),
        "ticket_url": str(record.ticket_url or ""),
        "resolved_at": record.resolved_at,
        "action_taken": ", ".join(record.action_taken),
        "summary": record.complaint_summary[:SUMMARY_LIMIT],
        **_regression_of(record),
    }
    item = {
        "id": memory_id,
        "text": _compose_text(record),
        "additional_metadata": metadata,
    }

    try:
        client = _client()
        response = client.context.ingest(
            database=config.HYDRA_DATABASE,
            collection=MEMORY_COLLECTION,
            type="memory",
            memories=json.dumps([item]),
            upsert="true",
        )
    except Exception:
        return None

    data = getattr(response, "data", None)
    if getattr(data, "success", True) is False:
        return None

    # This is the moment the collection can come into existence, and the
    # collection set is cached for the life of the process. Repopulate it now
    # so the next read sees what was just written instead of guarding itself
    # into a permanent zero. A failure here costs a re-check on the read side,
    # never the write.
    try:
        live_collections(client, refresh=True)
    except Exception:
        pass

    for result in getattr(data, "results", None) or []:
        written = getattr(result, "id", None)
        if written:
            return str(written)
    return memory_id


def derive_reply_tone(memory: MemoryReadResult, profile: IntentProfile) -> str:
    """Reply tone as a pure function of what the memory returned.

    Deliberately NOT asked of the language model. It is a count comparison, and
    a counted fact should never be a generated one -- if the model hallucinates
    "third complaint" warmth at a first contact, the memory pitch inverts on
    stage. Both step points come from the profile so the ladder is retunable
    without a deploy.

    This lives here rather than in the router because it is a property of the
    memory read, and it must give the same answer wherever it is asked.
    """
    thresholds = profile.reply_tone_thresholds or {}
    seen = max(memory.times_reported_by_actor, memory.times_seen_on_topic)

    escalation_at = thresholds.get("escalation")
    if escalation_at is not None and seen >= escalation_at:
        return "escalation"

    returning_at = thresholds.get("returning")
    if returning_at is not None and seen >= returning_at:
        return "returning"

    return "first_contact"
