"""The kill shot: scope the same question to fewer sources and measure the damage.

The full four-source run is treated as the reference answer. Each narrower scope
is then scored against it, which turns "the answer got worse" into two numbers a
judge can read off a screen:

  missed leaks  - real leaks the narrow scope cannot see at all
  false leaks   - complaints it calls leaked that were in fact tracked, in a
                  source it was not allowed to look at

Both are computed at run time from whatever is loaded. Neither is asserted.
"""

from __future__ import annotations

from typing import Any, Optional

from .pipeline import GhostThread, RunReport


def _leak_ids(report: RunReport) -> set[str]:
    return {r["canonical_id"] for r in report.results if r["verdict"] == "leaked"}


def _actioned_ids(report: RunReport) -> set[str]:
    return {r["canonical_id"] for r in report.results if r["verdict"] == "actioned"}


DEFAULT_SCOPES: list[list[str]] = [
    ["slack"],
    ["slack", "linear"],
    ["slack", "gmail", "linear"],
    ["slack", "gmail", "linear", "github"],
]


def run_killshot(
    engine: GhostThread,
    scopes: Optional[list[list[str]]] = None,
    now: Optional[float] = None,
) -> dict[str, Any]:
    scopes = scopes or DEFAULT_SCOPES
    reference_scope = max(scopes, key=len)
    reference = engine.run(sources=reference_scope, act_on_leaks=False, now=now)
    truth_leaks = _leak_ids(reference)
    truth_actioned = _actioned_ids(reference)

    rows: list[dict[str, Any]] = []
    for scope in scopes:
        report = engine.run(sources=scope, act_on_leaks=False, now=now)
        found = _leak_ids(report)
        # A complaint the narrow scope never even ingested is invisible, not
        # merely unmatched. Both count as a miss, but we separate them because
        # they fail for different reasons and that distinction is the story.
        visible = {r["canonical_id"] for r in report.results}
        missed_invisible = sorted(truth_leaks - visible)
        missed_unmatched = sorted((truth_leaks & visible) - found)
        false_leaks = sorted(found & truth_actioned)

        recall = len(found & truth_leaks) / len(truth_leaks) if truth_leaks else 0.0
        precision = len(found & truth_leaks) / len(found) if found else 0.0

        rows.append(
            {
                "scope": scope,
                "scope_label": " + ".join(scope),
                "summary": report.summary,
                "leaks_reported": len(found),
                "true_leaks_found": len(found & truth_leaks),
                "missed_invisible": missed_invisible,
                "missed_unmatched": missed_unmatched,
                "false_leaks": false_leaks,
                "recall": round(recall, 4),
                "precision": round(precision, 4),
                "f1": round(
                    (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0,
                    4,
                ),
                "answerable_rate": report.summary["answerable_rate"],
                "mean_confidence": report.summary["mean_confidence"],
            }
        )

    return {
        "question": reference.question,
        "reference_scope": reference_scope,
        "reference_leaks": sorted(truth_leaks),
        "backends": reference.backends,
        "rows": rows,
        "headline": _headline(rows),
    }


def _headline(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    worst, best = rows[0], rows[-1]
    return (
        f"Scoped to {worst['scope_label']}: {worst['answerable_rate'] * 100:.0f}% of complaints "
        f"answerable, F1 {worst['f1']:.2f}. "
        f"With {best['scope_label']}: {best['answerable_rate'] * 100:.0f}% answerable, F1 {best['f1']:.2f}."
    )
