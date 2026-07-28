"""The kill shot: ask the same question of fewer connectors and measure the damage.

The full four-provider run is the reference answer. Every narrower scope is
re-run through `knowledge_query.detect_leaks` and scored against it, so "the
answer got worse" becomes numbers a judge can read off a screen.

The scoping is a query re-scope, not a display filter. `detect_leaks` reads
complaints out of HydraDB per provider and issues one query per work-side
connector, each `client.query(collections=[one], metadata_filters=
{"additional_metadata": {"connector_id": "<uuid>"}})`. The connector id is a
scalar, not a list: a list ANDs rather than ORs and matches zero documents,
which is why the queries are per connector and not one query for all of them.
A source outside the scope is genuinely unreadable for that run, and the
connector UUIDs in and out of scope are reported per row for exactly that
reason.

Two failures are separated because they are different failures:

  refusal      - no work-side connector is in scope, so absence of tracked work
                 cannot be established at all. Every verdict becomes `unknown`
                 with a null confidence. The system gets quieter.
  false leaks  - a work-side connector is in scope but not all of them, so a
                 complaint tracked only in the missing source is reported as a
                 leak. The system stays loud and becomes wrong.

Nothing here is asserted. If a scope produces the reference answer, that is what
gets reported: `degradation_observed` goes false and the note says so. And if
the reference run reached nothing at all, `degradation_observed` goes null and
nothing is claimed, because a table of zeros that reads "scoping changed
nothing" is the confident null result this whole project argues against.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from .contracts import IntentProfile, LeakVerdict
from .intent import get_profile
from .knowledge_query import (
    ALL_PROVIDERS,
    ConnectorRegistry,
    LeakRun,
    detect_leaks_run,
    summarise,
)

QUESTION = (
    "Which customer complaints in Slack or Gmail never became tracked work "
    "in Linear or GitHub?"
)

# Flavour A strips both work sources; flavour B strips one of them. They fail
# differently, which is the whole point of showing both.
DEFAULT_SCOPES: list[list[str]] = [
    ["slack"],
    ["slack", "gmail", "linear"],
    list(ALL_PROVIDERS),
]

LABEL_CHARS = 96
PERCENT = 100.0


def _label(verdict: LeakVerdict) -> str:
    """A human-readable handle for a cluster, so a judge can see what was lost."""
    text = verdict.complaint_texts[0] if verdict.complaint_texts else ""
    flat = " ".join(text.split())
    return flat[:LABEL_CHARS] if flat else verdict.issue_cluster_id


class _Answer:
    """One `detect_leaks` run, indexed the way the scoring needs it.

    Indexing is per complaint id rather than per cluster id: `detect_leaks`
    folds complaints that resolved onto the same work item into one cluster, so
    a cluster id is not stable across scopes but a complaint id is.

    `grounded` is carried through from the run because zero clusters means two
    incompatible things — the tenant is empty, or HydraDB was never reached —
    and every number below is worthless in the second case.
    """

    def __init__(self, scope: list[str], run: LeakRun, elapsed_s: float):
        verdicts = run.verdicts
        self.scope = scope
        self.run = run
        self.verdicts = verdicts
        self.grounded = run.grounded
        self.errors = dict(run.errors)
        self.latency_ms = round(elapsed_s * 1000.0, 1)
        self.summary = summarise(verdicts)

        self.verdict_of: dict[str, str] = {}
        self.cluster_of: dict[str, LeakVerdict] = {}
        for verdict in verdicts:
            for complaint_id in verdict.complaint_ids:
                self.verdict_of[complaint_id] = verdict.verdict
                self.cluster_of[complaint_id] = verdict

    @property
    def visible(self) -> set[str]:
        return set(self.verdict_of)

    def complaints_with(self, verdict: str) -> set[str]:
        return {cid for cid, got in self.verdict_of.items() if got == verdict}

    def describe(self, complaint_ids: set[str]) -> list[dict[str, Any]]:
        """Name the complaints behind a count, deduplicated by cluster."""
        seen: set[int] = set()
        out: list[dict[str, Any]] = []
        for complaint_id in sorted(complaint_ids):
            cluster = self.cluster_of.get(complaint_id)
            if cluster is None or id(cluster) in seen:
                continue
            seen.add(id(cluster))
            out.append(
                {
                    "complaint_id": complaint_id,
                    "cluster_id": cluster.issue_cluster_id,
                    "label": _label(cluster),
                    "verdict": cluster.verdict,
                    "confidence": cluster.confidence,
                    "matched_work_items": cluster.matched_work_items,
                }
            )
        return out


def _run(
    scope: list[str], profile: IntentProfile, registry: ConnectorRegistry
) -> _Answer:
    started = time.perf_counter()
    try:
        run = detect_leaks_run(scope, profile=profile, registry=registry)
    except Exception as exc:
        # Degrade rather than crash, but record the reason: an _Answer that is
        # not grounded suppresses every claim downstream.
        run = LeakRun(
            providers_requested=list(scope),
            errors={"*": f"{type(exc).__name__}: {exc}"},
        )
    return _Answer(scope, run, time.perf_counter() - started)


def _fscore(precision: float, recall: float) -> float:
    total = precision + recall
    return (2.0 * precision * recall / total) if total else 0.0


def _row(
    answer: _Answer,
    reference: _Answer,
    registry: ConnectorRegistry,
) -> dict[str, Any]:
    scope = answer.scope
    out_of_scope = [p for p in ALL_PROVIDERS if p not in scope]

    truth_leaks = reference.complaints_with("leak")
    truth_resolved = reference.complaints_with("resolved")

    reported = answer.complaints_with("leak")
    found = reported & truth_leaks

    # A complaint the narrow scope never ingested is invisible, not merely
    # unmatched. Both are misses, but they fail for different reasons and the
    # distinction is the story.
    missed = truth_leaks - reported
    missed_invisible = missed - answer.visible
    missed_unmatched = missed & answer.visible

    # Reported as leaked here, but tracked work exists at full scope in a source
    # this run was not allowed to read. This is the confidently-wrong failure.
    false_leaks = reported & truth_resolved

    precision = len(found) / len(reported) if reported else 0.0
    recall = len(found) / len(truth_leaks) if truth_leaks else 0.0

    unknowns = answer.summary["by_verdict"].get("unknown", 0)
    return {
        "scope": list(scope),
        "scope_label": " + ".join(scope),
        "is_reference": scope == reference.scope,
        # False means every count in this row is an artefact of an unreachable
        # backend, not a finding about the tenant.
        "grounded": answer.grounded,
        "grounding_errors": answer.errors,
        "complaint_providers_read": list(answer.run.complaint_providers_read),
        "complaint_providers_absent": list(answer.run.complaint_providers_absent),
        "summary": answer.summary,
        "by_verdict": answer.summary["by_verdict"],
        "clusters": answer.summary["clusters"],
        "leaks_reported": len(reported),
        "true_leaks_found": len(found),
        "answerable_rate": answer.summary["answerable_rate"],
        "mean_confidence": answer.summary["mean_confidence"],
        # Confidence falls with coverage even when no label flips: fewer
        # independent work sources agreeing there is nothing there is a weaker
        # claim, and that is a real degradation worth showing on its own.
        "mean_confidence_delta": round(
            answer.summary["mean_confidence"] - reference.summary["mean_confidence"], 4
        ),
        "unknowns": unknowns,
        "refused": bool(unknowns) and not answer.summary["answerable"],
        # Kept as id lists because the demo UI counts them; the `_detail` twins
        # carry the text a human needs to see who was dropped.
        "false_leaks": sorted(false_leaks),
        "false_leaks_detail": answer.describe(false_leaks),
        "missed_invisible": sorted(missed_invisible),
        "missed_invisible_detail": reference.describe(missed_invisible),
        "missed_unmatched": sorted(missed_unmatched),
        "missed_unmatched_detail": reference.describe(missed_unmatched),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(_fscore(precision, recall), 4),
        # The proof that this is a query re-scope and not a UI filter.
        "providers_in_scope": list(scope),
        "providers_out_of_scope": out_of_scope,
        "connector_ids_in_scope": registry.connector_ids(list(scope)),
        "connector_ids_out_of_scope": registry.connector_ids(out_of_scope),
        "providers_without_connector": [
            p for p in ALL_PROVIDERS if registry.connector_id(p) is None
        ],
        "latency_ms": answer.latency_ms,
    }


def _ungrounded_note(reference: _Answer) -> str:
    """Why the reference run cannot support any claim. Empty when it can."""
    if not reference.grounded:
        detail = "; ".join(f"{p}: {e}" for p, e in sorted(reference.errors.items()))
        return (
            "the reference run could not read any complaint source"
            + (f" ({detail})" if detail else "")
        )
    if not reference.summary["clusters"]:
        absent = reference.run.complaint_providers_absent
        return (
            "the reference run read its complaint sources and they hold no complaints"
            + (f" (no live collection for {', '.join(absent)})" if absent else "")
        )
    return ""


def _headline(
    rows: list[dict[str, Any]],
    reference_row: Optional[dict[str, Any]],
    reference: _Answer,
) -> str:
    if not rows or reference_row is None:
        return ""

    # With no reference answer there is nothing to degrade from, and every row
    # below is zero against zero. Saying "scoping changed nothing" here would
    # be the confident null result rather than a finding.
    blocked = _ungrounded_note(reference)
    if blocked:
        return (
            f"No answer: {blocked}. The scope table below is zero against zero and "
            f"nothing about source scoping can be concluded from it."
        )

    parts = [
        f"Full scope ({reference_row['scope_label']}): "
        f"{reference_row['leaks_reported']} leaks, "
        f"{reference_row['answerable_rate'] * PERCENT:.0f}% answerable."
    ]
    for row in rows:
        if row["is_reference"]:
            continue
        if row["refused"]:
            parts.append(
                f"Scoped to {row['scope_label']}: no work-side connector, so all "
                f"{row['clusters']} clusters go unknown and the system refuses to answer "
                f"({row['answerable_rate'] * PERCENT:.0f}% answerable)."
            )
        elif row["false_leaks"]:
            parts.append(
                f"Scoped to {row['scope_label']}: {len(row['false_leaks'])} false leaks "
                f"— tracked work exists in {' + '.join(row['providers_out_of_scope'])} "
                f"but is unreadable, so precision drops to {row['precision']:.2f}."
            )
        else:
            parts.append(
                f"Scoped to {row['scope_label']}: F1 {row['f1']:.2f}, "
                f"{len(row['missed_invisible'])} leaks invisible, no false leaks, "
                f"mean confidence {row['mean_confidence_delta']:+.2f}."
            )
    return " ".join(parts)


def _degradation(rows: list[dict[str, Any]], reference: _Answer) -> dict[str, Any]:
    """State plainly which flavour of degradation was actually observed.

    A fabricated difference is worse than an honest null result, so a scope that
    reproduces the reference answer is reported as reproducing it.

    Every claim below is a comparison against the reference answer, so with no
    reference answer there is nothing to say. `degradation_observed` goes null
    rather than false: false is a finding, and we do not have one.
    """
    narrow = [r for r in rows if not r["is_reference"]]

    blocked = _ungrounded_note(reference)
    if blocked:
        notes = [
            f"no degradation assessed: {blocked}, so there is no reference answer "
            f"to degrade from and every scope below compares zero against zero"
        ]
        notes.extend(f"{p}: {e}" for p, e in sorted(reference.errors.items()))
        return {
            "assessed": False,
            "refusal_scopes": [],
            "false_leak_scopes": [],
            "unchanged_scopes": [],
            "degradation_observed": None,
            "grounded": reference.grounded,
            "grounding_errors": reference.errors,
            "notes": notes,
        }

    refusals = [r["scope_label"] for r in narrow if r["refused"]]
    with_false = [r["scope_label"] for r in narrow if r["false_leaks"]]
    unchanged = [
        r
        for r in narrow
        if not r["refused"] and not r["false_leaks"] and not r["missed_invisible"]
        and not r["missed_unmatched"]
    ]

    notes: list[str] = []
    if refusals:
        notes.append(
            "complaint-side-only scopes refuse to answer rather than guess: "
            + ", ".join(refusals)
        )
    if with_false:
        notes.append("partial work-side scopes report false leaks: " + ", ".join(with_false))
    else:
        notes.append(
            "no false leaks observed: no complaint in this tenant resolves to work that "
            "lives only in an out-of-scope work source, so dropping one work source cannot "
            "flip a resolved verdict to a leak yet"
        )
    for row in unchanged:
        if row["mean_confidence_delta"]:
            notes.append(
                f"{row['scope_label']} reproduced the reference verdict labels exactly; the "
                f"only degradation is confidence, mean {row['mean_confidence']} against "
                f"{row['mean_confidence'] - row['mean_confidence_delta']:.4f} at full scope"
            )
        else:
            # Claiming "the only degradation is confidence" while quoting the
            # same number twice is a fabricated difference. Dropping a source
            # that was never queryable costs nothing, and saying so is the
            # finding.
            notes.append(
                f"{row['scope_label']} reproduced the reference answer exactly, confidence "
                f"included (mean {row['mean_confidence']}): every source it gave up was one "
                f"the reference run could not query either, so there was nothing to lose"
            )

    return {
        "assessed": True,
        "refusal_scopes": refusals,
        "false_leak_scopes": with_false,
        "unchanged_scopes": [r["scope_label"] for r in unchanged],
        "degradation_observed": bool(refusals or with_false) or any(
            r["missed_invisible"] or r["missed_unmatched"] for r in narrow
        ),
        "grounded": reference.grounded,
        "grounding_errors": reference.errors,
        "notes": notes,
    }


def _grounding_backend(reference: _Answer) -> str:
    """What actually grounded this run, as opposed to what was configured.

    Reported from the outcome of the reference read rather than named as a
    constant: a hardcoded "hydradb" claims a backend answered when the run may
    never have reached it, which is the one thing this panel must not do.
    """
    if not reference.grounded:
        return "unavailable"
    return "hydradb (partial)" if reference.errors else "hydradb"


def run_killshot(
    engine: Any = None,
    scopes: Optional[list[list[str]]] = None,
    now: Optional[float] = None,
    profile: Optional[IntentProfile] = None,
    registry: Optional[ConnectorRegistry] = None,
) -> dict[str, Any]:
    """Run every scope, score it against the full-scope answer, and report.

    `engine` and `now` are accepted and ignored. The kill shot used to drive the
    8-node pipeline; it now queries HydraDB directly through `detect_leaks`, but
    `api.py` calls `run_killshot(engine, scopes=...)` and that call site keeps
    working unchanged.
    """
    del engine, now  # kept for the Track B call site

    profile = profile or get_profile()
    registry = registry or ConnectorRegistry()
    scopes = [list(s) for s in (scopes or DEFAULT_SCOPES)]
    reference_scope = list(ALL_PROVIDERS)

    started = time.perf_counter()
    # The reference goes first so every narrower scope is timed warm, against
    # the same cached document set. Only the retrieval queries differ, and those
    # are not cached, so a re-scope really does re-query HydraDB.
    reference = _run(reference_scope, profile, registry)

    rows: list[dict[str, Any]] = []
    for scope in scopes:
        answer = reference if scope == reference_scope else _run(scope, profile, registry)
        rows.append(_row(answer, reference, registry))
    if not any(r["is_reference"] for r in rows):
        rows.append(_row(reference, reference, registry))

    reference_row = next((r for r in rows if r["is_reference"]), None)
    return {
        "question": QUESTION,
        "reference_scope": reference_scope,
        "reference_leaks": sorted(reference.complaints_with("leak")),
        "reference_resolved": sorted(reference.complaints_with("resolved")),
        "reference_summary": reference.summary,
        "reference_latency_ms": reference.latency_ms,
        # Whether this payload is an answer at all. Everything below is
        # meaningless when it is false, and the UI should say so rather than
        # render a table of zeros.
        "grounded": reference.grounded,
        "grounding_errors": reference.errors,
        "connector_ids": {p: registry.connector_id(p) for p in ALL_PROVIDERS},
        "profile_origin": profile.origin,
        "match_threshold": profile.match_threshold,
        "backends": {
            "grounding": _grounding_backend(reference),
            "intent_profile": profile.origin,
        },
        "rows": rows,
        "degradation": _degradation(rows, reference),
        "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 1),
        "headline": _headline(rows, reference_row, reference),
    }
