"""Orchestration. The 8-node pipeline, as named functions.

Each node is a module-level `node_*` function with an explicit signature. That
is the ownership boundary made mechanical: Track A replaces the body of
`node_memory_read` and `node_memory_write` without touching anything else, and
Track B owns the rest. Nobody has to edit the same lines.

    N1  node_memory_read     HydraDB memory  -> MemoryReadResult   [Track A]
    N2  find_leaks           HydraDB knowledge -> LeakResult[]     [Track A]
    N3  node_classify        Pipeshift       -> ExtractedFacts     [Track B]
    N4  (policy lookup)      InsForge profile                      [Track B]
    N5  node_route           pure function   -> RoutingDecision    [Track B]
    N6  node_act             Linear/GitHub/reply -> ResolutionAction [Track B]
    N7  node_memory_write    HydraDB memory  -> memory id          [Track A]
    N8  node_log             idempotency log                       [Track B]

N4 and N5 are one function because a policy lookup with nothing to decide is not
a step; `route()` does the lookup and the decision together, purely.

Ordering note: for a single live complaint the order is exactly N1 -> N2 -> N3,
as specified. For a batch run N2 is hoisted and fanned out across every
complaint first, because it is one round trip for all of them rather than one
each. Same nodes, same data, materially faster.

`run()` takes an explicit source scope. That single parameter is what the kill
shot varies, and it is threaded all the way down rather than being applied as a
filter at the end, so a scoped run genuinely never sees the data it is not
allowed to see.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from . import act, config, connectors, memory, router
from .contracts import (
    ALL_SOURCES,
    ComplaintEvent,
    ExtractedFacts,
    IntentProfile,
    LeakResult,
    MemoryReadResult,
    MemoryWriteInput,
    ResolutionAction,
    RoutingDecision,
    WorkEvent,
)
from .extract import Extractor
from .hydra import HydraGrounding
from .intent import get_profile
from .leaks import find_leaks, summarise
from .resolve import IdentityGraph


@dataclass
class RunReport:
    question: str
    sources_requested: list[str]
    sources_loaded: dict[str, int]
    profile: dict[str, Any]
    backends: dict[str, str]
    identities: list[dict[str, Any]]
    results: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    summary: dict[str, Any]
    elapsed_ms: float
    capabilities: dict[str, bool] = field(default_factory=dict)
    # Which nodes are still running on placeholder implementations. Empty is the
    # only demo-ready state; the UI badges this and smoke.py fails on it.
    stubs: list[str] = field(default_factory=list)
    # Profile problems that would cause an undefined routing fallthrough.
    policy_problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


QUESTION = (
    "Which customer complaints in Slack and Gmail never became tracked work in "
    "Linear or GitHub, and who is still waiting on an answer?"
)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- the nodes ----------------------------------------------------------------


def node_memory_read(
    complaint: ComplaintEvent,
    sources: list[str],
    profile: IntentProfile,
    grounding: HydraGrounding,
) -> MemoryReadResult:
    """N1. What we already know about this actor and this topic."""
    return memory.memory_read(complaint, sources, profile, grounding)


def node_classify(
    complaint: ComplaintEvent,
    recalled: MemoryReadResult,
    profile: IntentProfile,
    extractor: Extractor,
) -> ExtractedFacts:
    """N3. Pipeshift extraction, then stamp the memory-derived fields.

    The model is asked what the complaint *says*. It is not asked how many times
    this person has written in -- that is counted, not inferred, so it is
    stamped on afterwards from the memory read. A generated count would be a
    hallucination wearing the costume of evidence.
    """
    facts = extractor.extract(complaint)

    facts.times_reported_by_actor = recalled.times_reported_by_actor
    facts.times_seen_on_topic = recalled.times_seen_on_topic
    facts.reply_tone = memory.derive_reply_tone(recalled, profile)
    facts.regression_evidence = recalled.likely_regression.ref if recalled.likely_regression else None
    return facts


def node_route(facts: ExtractedFacts, profile: IntentProfile) -> RoutingDecision:
    """N4 + N5. Policy lookup and the pure routing decision."""
    return router.route(facts, profile)


def node_act(
    leak: LeakResult,
    facts: ExtractedFacts,
    decision: RoutingDecision,
    profile: IntentProfile,
) -> ResolutionAction:
    """N6. Ticket, sandboxed fix, reply, escalate -- all behind DRY_RUN."""
    resolution = act.resolve(leak, facts, profile)
    resolution.routing = decision.to_dict()
    resolution.actions_taken = list(decision.actions)
    resolution.escalated = router.ESCALATE_ACTION in decision.actions
    resolution.timestamp = _now_iso()
    return resolution


def node_memory_write(
    complaint: ComplaintEvent,
    facts: ExtractedFacts,
    decision: RoutingDecision,
    resolution: ResolutionAction,
    grounding: HydraGrounding,
) -> Optional[str]:
    """N7. One episodic memory per resolution. Makes the next run smarter."""
    record = MemoryWriteInput(
        actor=complaint.author_email or complaint.entity_id,
        complaint_id=complaint.id,
        complaint_summary=facts.what_broke,
        category=decision.category,
        action_taken=list(decision.actions),
        ticket_url=resolution.ticket_url or resolution.ticket_created_id,
        resolved_at=resolution.timestamp or _now_iso(),
    )
    return memory.memory_write(record, grounding)


class _IdempotencyLog:
    """N8. One resolution per complaint id, ever.

    In-process for now, which is enough to satisfy "file the same complaint
    twice, get one ticket" on a single instance. Track B swaps the body for the
    InsForge `actions_log` table with a UNIQUE constraint on `complaint_id`,
    which is what makes it survive a retried webhook across instances.
    """

    def __init__(self) -> None:
        self._seen: dict[str, dict[str, Any]] = {}

    def seen(self, complaint_id: str) -> Optional[dict[str, Any]]:
        return self._seen.get(complaint_id)

    def record(self, complaint_id: str, resolution: ResolutionAction) -> None:
        self._seen.setdefault(complaint_id, resolution.to_dict())

    def clear(self) -> None:
        self._seen.clear()


# --- the engine ---------------------------------------------------------------


class GhostThread:
    """Holds the loaded corpus and grounding index across runs.

    Reused between requests so the kill shot can re-query the same index with a
    different scope instead of re-ingesting, which is both faster and a fairer
    comparison.
    """

    def __init__(self) -> None:
        self.grounding = HydraGrounding()
        self.extractor = Extractor()
        self.identities = IdentityGraph()
        self.complaints: list[ComplaintEvent] = []
        self.work: list[WorkEvent] = []
        self.loaded_counts: dict[str, int] = {}
        self.action_log = _IdempotencyLog()
        self._loaded = False

    def load(self, now: Optional[float] = None, force: bool = False) -> dict[str, int]:
        if self._loaded and not force:
            return self.loaded_counts
        now = now or time.time()

        self.complaints = []
        for name, fetch in connectors.COMPLAINT_FETCHERS.items():
            events = fetch(now)
            self.complaints.extend(events)
            self.loaded_counts[name] = len(events)

        self.work = []
        for name, fetch in connectors.WORK_FETCHERS.items():
            events = fetch(now)
            self.work.extend(events)
            self.loaded_counts[name] = len(events)

        self.identities = IdentityGraph().build(
            self.complaints, self.work, declared=connectors.identities()
        )
        self.grounding.ingest(self.complaints, self.work)
        self.grounding.wait_until_indexed(len(self.complaints) + len(self.work))
        self._loaded = True
        return self.loaded_counts

    def add_complaint(self, complaint: ComplaintEvent) -> None:
        """Live-typed complaint from a judge. Goes through the identical path."""
        self.load()
        self.complaints.append(complaint)
        self.identities = IdentityGraph().build(
            self.complaints, self.work, declared=connectors.identities()
        )
        before = self.grounding.row_count()
        self.grounding.ingest([complaint], [])
        self.grounding.wait_until_indexed(before + 1)

    def stubs(self) -> list[str]:
        """Nodes still running on placeholders. Empty is the demo-ready state."""
        live: list[str] = []
        if memory.IS_STUB:
            live.append("memory_read/memory_write (N1/N7)")
        return live

    def resolve_one(
        self,
        leak: LeakResult,
        complaint: ComplaintEvent,
        profile: IntentProfile,
        scope: list[str],
    ) -> ResolutionAction:
        """N1 -> N7 for a single complaint, in order."""
        recalled = node_memory_read(complaint, scope, profile, self.grounding)
        facts = node_classify(complaint, recalled, profile, self.extractor)
        decision = node_route(facts, profile)
        resolution = node_act(leak, facts, decision, profile)
        resolution.memory = recalled.to_dict()
        resolution.memory_write_id = node_memory_write(
            complaint, facts, decision, resolution, self.grounding
        )
        return resolution

    def run(
        self,
        sources: Optional[list[str]] = None,
        act_on_leaks: bool = True,
        now: Optional[float] = None,
        only_complaint_id: Optional[str] = None,
    ) -> RunReport:
        started = time.perf_counter()
        profile = get_profile()
        scope = (
            list(sources)
            if sources is not None
            else list(profile.watch_sources) + list(profile.join_sources)
        )
        now = now or time.time()

        self.load(now)

        pool = self.complaints
        if only_complaint_id:
            pool = [c for c in pool if c.id == only_complaint_id]

        # N2, fanned out across every complaint at once.
        results = find_leaks(pool, self.grounding, self.identities, profile, scope, now)

        missing = [s for s in ALL_SOURCES if s not in scope]
        for result in results:
            result.sources_missing = missing

        actions: list[dict[str, Any]] = []
        if act_on_leaks:
            by_id = {c.id: c for c in pool}
            for result in results:
                if result.verdict != "leaked":
                    continue
                complaint = by_id.get(result.complaint["id"])
                if complaint is None:
                    continue

                # N8, checked before acting rather than after: a retried webhook
                # must not file a second ticket on its way to being deduped.
                already = self.action_log.seen(complaint.id)
                if already is not None:
                    actions.append({**already, "idempotent_replay": True})
                    continue

                resolution = self.resolve_one(result, complaint, profile, scope)
                self.action_log.record(complaint.id, resolution)
                actions.append(resolution.to_dict())

        return RunReport(
            question=QUESTION,
            sources_requested=scope,
            sources_loaded={k: v for k, v in self.loaded_counts.items() if k in scope},
            profile=profile.to_dict(),
            backends={
                "grounding": self.grounding.backend,
                "extraction": self.extractor.backend,
                "intent_profile": profile.origin,
                "memory": "stub" if memory.IS_STUB else self.grounding.backend,
            },
            identities=self.identities.summary(),
            results=[r.to_dict() for r in results],
            actions=actions,
            summary=summarise(results),
            elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
            capabilities=config.capability_report(),
            stubs=self.stubs(),
            policy_problems=router.validate_policy(profile),
        )
