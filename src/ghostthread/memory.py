"""Episodic memory over HydraDB. Pipeline nodes N1 (read) and N7 (write).

    >>> TRACK A OWNS THIS FILE. Everything below is a STUB. <<<

The stub exists so Track B can build the classifier, router, actions and UI
against a real signature today, without waiting for the HydraDB queries to land.
Replace the two bodies; do not change the signatures without a sync point.

What the real implementation must do
------------------------------------
`memory_read`  -- POST /query with type="memory", query_by="hybrid",
                  graph_context=True, scoped to `sources` via `collections`,
                  keyed on the resolved actor identity plus the complaint topic.
                  Returns counts, prior resolutions, and any PR/commit the
                  history implicates.

`memory_write` -- POST /context/ingest with type="memory" (NOT "knowledge"),
                  one episodic record per resolution. Returns the id it wrote.

Two rules the real implementation must keep
-------------------------------------------
1. `sources` is threaded down to the query, exactly the way `hydra.candidates()`
   already does it. Scoping the query is the kill shot; filtering the results
   afterwards would be a lie.
2. Never invent history. Zero prior contacts is a real, common answer, and a
   `first_contact` reply is the correct output for it.

Why the stub returns zeros rather than plausible-looking history
----------------------------------------------------------------
Fake counts would make the reply tone and regression targeting *look* like they
work while the memory is not wired at all -- precisely the failure that would
survive right up to the stage. It returns an honest empty result with
`stub=True`, and `scripts/smoke.py` refuses to report the pipeline demo-ready
while any stub is live. To exercise the returning/escalation tone paths before
this is implemented, construct a `MemoryReadResult` directly in a test.
"""

from __future__ import annotations

from typing import Any, Optional

from .contracts import (
    ComplaintEvent,
    IntentProfile,
    MemoryReadResult,
    MemoryWriteInput,
)

# Flipped to False by Track A when the real queries land. `smoke.py` and the
# `/health` endpoint both read it, so the moment it goes False the pipeline
# starts reporting itself as demo-ready.
IS_STUB: bool = True


def memory_read(
    complaint: ComplaintEvent,
    sources: list[str],
    profile: IntentProfile,
    grounding: Any = None,
) -> MemoryReadResult:
    """Prior history for this actor and this topic, scoped to `sources`.

    STUB: returns an honest empty history. See module docstring.
    """
    return MemoryReadResult(
        actor=complaint.author_email or complaint.entity_id,
        times_reported_by_actor=0,
        times_seen_on_topic=0,
        prior_resolutions=[],
        likely_regression=None,
        sources_used=sorted(sources),
        stub=True,
    )


def memory_write(
    record: MemoryWriteInput,
    grounding: Any = None,
) -> Optional[str]:
    """Persist one episodic memory. Returns the written id, or None.

    STUB: writes nothing and returns None. See module docstring.
    """
    return None


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
