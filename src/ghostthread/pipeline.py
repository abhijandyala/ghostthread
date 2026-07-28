"""Orchestration. One run = ingest -> resolve -> join -> extract -> act.

`GhostThread.run()` takes an explicit source scope. That single parameter is
what the kill shot varies, and it is threaded all the way down rather than being
applied as a filter at the end, so a scoped run genuinely never sees the data it
is not allowed to see.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from . import act, config, connectors
from .contracts import ComplaintEvent, IntentProfile, LeakResult, WorkEvent
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

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


QUESTION = (
    "Which customer complaints in Slack and Gmail never became tracked work in "
    "Linear or GitHub, and who is still waiting on an answer?"
)


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

    def run(
        self,
        sources: Optional[list[str]] = None,
        act_on_leaks: bool = True,
        now: Optional[float] = None,
        only_complaint_id: Optional[str] = None,
    ) -> RunReport:
        started = time.perf_counter()
        profile = get_profile()
        scope = list(sources) if sources is not None else list(profile.watch_sources) + list(profile.join_sources)
        now = now or time.time()

        self.load(now)

        pool = self.complaints
        if only_complaint_id:
            pool = [c for c in pool if c.id == only_complaint_id]

        results = find_leaks(pool, self.grounding, self.identities, profile, scope, now)

        actions: list[dict[str, Any]] = []
        if act_on_leaks:
            for result in results:
                if result.verdict != "leaked":
                    continue
                complaint = next(c for c in pool if c.id == result.complaint["id"])
                facts = self.extractor.extract(complaint)
                actions.append(act.resolve(result, facts, profile).to_dict())

        return RunReport(
            question=QUESTION,
            sources_requested=scope,
            sources_loaded={k: v for k, v in self.loaded_counts.items() if k in scope},
            profile=profile.to_dict(),
            backends={
                "grounding": self.grounding.backend,
                "extraction": self.extractor.backend,
                "intent_profile": profile.origin,
            },
            identities=self.identities.summary(),
            results=[r.to_dict() for r in results],
            actions=actions,
            summary=summarise(results),
            elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
            capabilities=config.capability_report(),
        )
