"""The join: did this complaint ever become tracked work?

Every number in this module comes from the InsForge intent profile that is
passed in. There are no literal thresholds, no weights, and no lookup tables
keyed on entity names. Change the profile and the verdicts change.
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

# Concurrency for retrieval fan-out. Not a decision threshold - purely how many
# sockets we are willing to open at once.
MAX_RETRIEVAL_WORKERS = 12

from .contracts import (
    COMPLAINT_SOURCES,
    WORK_SOURCES,
    ComplaintEvent,
    IntentProfile,
    LeakResult,
    MatchSignal,
    WorkEvent,
)
from .hydra import Candidate, HydraGrounding
from .resolve import IdentityGraph

_ID_LIKE = re.compile(r"\b([A-Z]{2,5}-\d{1,6}|#\d{1,6}|gh-\d{1,6})\b")


def _explicit_reference(complaint: ComplaintEvent, work: WorkEvent) -> tuple[float, str]:
    """Either side naming the other outright is the strongest possible signal."""
    work_blob = f"{work.title} {work.description}"
    if complaint.id.lower() in work_blob.lower():
        return 1.0, f"{work.source} {work.id} cites {complaint.id}"
    if complaint.channel_or_thread and complaint.channel_or_thread.lower() in work_blob.lower():
        return 0.7, f"{work.source} {work.id} cites {complaint.channel_or_thread}"
    for token in _ID_LIKE.findall(complaint.text):
        if token.strip("#").lower() in (work.id.lower(), work.entity_id.lower()):
            return 1.0, f"complaint names {token}"
    return 0.0, "neither side references the other"


def _calibrate(raw: float, floor: float) -> float:
    """Stretch a retrieval score's usable band back across 0..1.

    Retrieval engines return relevancy in a compressed high range, so an
    unrelated document can still score 0.7. Everything at or below `floor` is
    treated as noise; the remainder is rescaled linearly.
    """
    if raw <= floor:
        return 0.0
    headroom = 1.0 - floor
    if headroom <= 0:
        return 1.0
    return min(1.0, (raw - floor) / headroom)


def _temporal(complaint: ComplaintEvent, work: WorkEvent, window_hours: float) -> tuple[float, str]:
    """Work that predates the complaint cannot be a response to it.

    Decays linearly across the correlation window from the profile.
    """
    delta_hours = (work.t_created - complaint.t) / 3600.0
    if delta_hours < 0:
        return 0.0, f"{work.source} {work.id} predates the complaint by {abs(delta_hours):.0f}h"
    if delta_hours > window_hours:
        return 0.0, f"filed {delta_hours:.0f}h later, outside the {window_hours:.0f}h window"
    return 1.0 - (delta_hours / window_hours), f"filed {delta_hours:.0f}h after the complaint"


def score_pair(
    complaint: ComplaintEvent,
    candidate: Candidate,
    identities: IdentityGraph,
    profile: IntentProfile,
) -> tuple[float, list[MatchSignal]]:
    weights = profile.match_weights
    work = candidate.work

    identity_value, identity_why = identities.same_person(complaint, work)
    ref_value, ref_why = _explicit_reference(complaint, work)
    temporal_value, temporal_why = _temporal(complaint, work, profile.correlation_window_hours)
    semantic_value = _calibrate(candidate.semantic, profile.semantic_floor)
    semantic_why = (
        f"retrieval {candidate.semantic:.2f} -> {semantic_value:.2f} after floor "
        f"{profile.semantic_floor} against {work.source} {work.id}"
    )
    if candidate.graph_hits:
        semantic_why += f" (graph: {', '.join(candidate.graph_hits)})"

    # Identity, semantics and timing are always applicable, so they are averaged.
    # An explicit cross-reference is a bonus on top: most genuine matches never
    # name each other, so folding it into the denominator would cap every honest
    # match below threshold.
    core = [
        MatchSignal("identity", identity_value, weights.get("identity", 0.0), work.source, identity_why),
        MatchSignal("semantic", semantic_value, weights.get("semantic", 0.0), work.source, semantic_why),
        MatchSignal("temporal", temporal_value, weights.get("temporal", 0.0), work.source, temporal_why),
    ]
    total_weight = sum(s.weight for s in core) or 1.0
    score = sum(s.contribution for s in core) / total_weight

    bonus = MatchSignal(
        "explicit_reference", ref_value, profile.explicit_reference_bonus, work.source, ref_why
    )
    score = min(1.0, score + bonus.contribution)
    return score, core + [bonus]


def evaluate_complaint(
    complaint: ComplaintEvent,
    grounding: HydraGrounding,
    identities: IdentityGraph,
    profile: IntentProfile,
    active_sources: list[str],
    now: Optional[float] = None,
) -> LeakResult:
    now = now or time.time()
    work_sources = [s for s in active_sources if s in WORK_SOURCES]
    candidates = grounding.candidates(complaint, work_sources)

    best_score = 0.0
    best: Optional[Candidate] = None
    best_signals: list[MatchSignal] = []
    for cand in candidates:
        score, signals = score_pair(complaint, cand, identities, profile)
        if score > best_score:
            best_score, best, best_signals = score, cand, signals

    if not best_signals:
        # Retrieval returned nothing at all. That is the strongest possible leak
        # signal, so say so explicitly rather than rendering an empty card.
        best_signals = [
            MatchSignal(
                name="no_candidate",
                value=0.0,
                weight=0.0,
                source=source,
                detail=f"searched {source} and found no plausible matching work",
            )
            for source in work_sources
        ]

    # Sources the answer is grounded in: the complaint's own source plus every
    # work source we were actually allowed to consult. This is deliberately
    # *consulted*, not *matched* - finding nothing in Linear and GitHub is a
    # two-source finding, and is precisely what makes a leak verdict trustworthy.
    sources_used = sorted({complaint.source} | set(work_sources))
    evidence_sources = sorted(
        {complaint.source} | {s.source for s in best_signals if s.value > 0}
    )

    age_hours = (now - complaint.t) / 3600.0

    if len(sources_used) < profile.min_sources_required:
        verdict = "unknown_insufficient_sources"
        confidence = 0.0
    elif best_score >= profile.match_threshold:
        verdict = "actioned"
        confidence = min(1.0, best_score)
    else:
        verdict = "leaked"
        # Confidence that it leaked grows as the best candidate gets weaker, and
        # as more independent work sources agree there is nothing there.
        margin = (profile.match_threshold - best_score) / max(profile.match_threshold, 1e-6)
        coverage = len(work_sources) / max(len(WORK_SOURCES), 1)
        confidence = round(min(1.0, margin * coverage), 4)

    return LeakResult(
        canonical_id=f"{complaint.source}:{complaint.id}",
        complaint=complaint.to_dict(),
        matched_work=best.work.to_dict() if (best and verdict == "actioned") else None,
        verdict=verdict,
        sources_used=sources_used,
        confidence=round(confidence, 4),
        score=round(best_score, 4),
        threshold=profile.match_threshold,
        signals=[s.to_dict() for s in best_signals],
        age_hours=round(age_hours, 1),
        unanswered=verdict == "leaked",
        evidence_sources=evidence_sources,
    )


def find_leaks(
    complaints: list[ComplaintEvent],
    grounding: HydraGrounding,
    identities: IdentityGraph,
    profile: IntentProfile,
    active_sources: list[str],
    now: Optional[float] = None,
) -> list[LeakResult]:
    watched = [s for s in active_sources if s in COMPLAINT_SOURCES]
    pending = [c for c in complaints if c.source in watched]
    if not pending:
        return []

    # Each complaint costs one retrieval round trip, and they are independent.
    # Serially this dominates the whole run; fanned out it is one round trip.
    with ThreadPoolExecutor(max_workers=min(len(pending), MAX_RETRIEVAL_WORKERS)) as pool:
        results = list(
            pool.map(
                lambda c: evaluate_complaint(
                    c, grounding, identities, profile, active_sources, now
                ),
                pending,
            )
        )
    return sorted(results, key=lambda r: (-r.confidence, -r.age_hours))


def summarise(results: list[LeakResult]) -> dict[str, Any]:
    total = len(results)
    by_verdict: dict[str, int] = {}
    for r in results:
        by_verdict[r.verdict] = by_verdict.get(r.verdict, 0) + 1
    answerable = total - by_verdict.get("unknown_insufficient_sources", 0)
    confidences = [r.confidence for r in results if r.verdict != "unknown_insufficient_sources"]
    leaked = [r for r in results if r.verdict == "leaked"]
    return {
        "complaints_examined": total,
        "by_verdict": by_verdict,
        "leaks": by_verdict.get("leaked", 0),
        "answerable": answerable,
        "answerable_rate": round(answerable / total, 4) if total else 0.0,
        "mean_confidence": round(sum(confidences) / len(confidences), 4) if confidences else 0.0,
        "unanswered_customer_hours": round(sum(r.age_hours for r in leaked), 1),
    }
