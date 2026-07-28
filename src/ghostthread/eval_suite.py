"""Track A / A4 — the eval suite.

Run before every demo rehearsal. It answers one question: is the grounding
layer still telling the truth about what it can and cannot see?

Why this is not a table of expected verdicts
--------------------------------------------
An eval that says "complaint X must come back a leak" needs complaint X to
exist. Today the tenant holds noise — security alerts, onboarding tickets,
test messages — GitHub holds zero documents, and nothing resolves. A hardcoded
expectation table would be unrunnable now and stale the moment the tenant is
reseeded, and an eval that cannot run is worse than none because its silence
reads as approval.

So there are two layers.

**Layer 1, invariants.** Properties of the *system*, not of the data. They hold
for an empty tenant, for the seeded demo tenant, and for whatever is in there
next week. Every graded claim Track A makes is one of these: scoping away the
work sources makes the system refuse rather than guess; narrowing scope never
manufactures evidence; a resolved verdict always carries the work item it
resolved to; a missing credential produces an honest empty answer rather than a
confident one. These run today and are the real value of this file.

**Layer 2, expected cases**, loaded from `fixtures/eval_cases.json` rather than
written here. Data, not code, for two reasons: `verify_no_hardcoding.py` scans
`src/` for demo entity names and would rightly fail this file if it named one,
and the table has to be fillable by whoever seeds `SEED.md` without editing
Python. A case whose complaint id is not in the tenant is *skipped*, loudly.

**Negative controls.** The acceptance criterion asks for a deliberately-broken
query, and it is the only part of an eval that proves the rest of it can fail.
Each control mutates something that must change the answer and fails if the
answer does not move. A control that cannot be run against the current tenant —
no candidates came back, nothing resolved to break — is skipped with the reason,
never quietly passed.

Three states, kept apart
------------------------
`pass`, `fail`, `skip`. A skip is not a pass. With this tenant most of layer 2
and some controls will skip, and the report says so in the count line and the
exit banner rather than rolling them into a green total.

Nothing here writes to the tenant. Every memory probe is a read against a
freshly generated actor that cannot exist, so the suite leaves no rows behind.
"""

from __future__ import annotations

import json
import math
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Optional

from . import config
from .contracts import ComplaintEvent, IntentProfile, MemoryReadResult
from .intent import get_profile
from .knowledge_query import (
    ALL_PROVIDERS,
    COMPLAINT_PROVIDERS,
    WORK_PROVIDERS,
    ConnectorRegistry,
    Document,
    LeakRun,
    SourceUnavailable,
    detect_leaks,
    detect_leaks_run,
    load_documents_result,
    refresh_documents,
    score_candidate,
    search_work,
)
from .memory import derive_reply_tone, memory_read

PASS = "pass"
FAIL = "fail"
SKIP = "skip"

LAYER_INVARIANT = "invariant"
LAYER_EXPECTED = "expected-case"
LAYER_CONTROL = "negative-control"

# Complaint-side-only scopes. Each must make the system refuse, and each fails
# for its own reason, so they are checked separately rather than as one set.
COMPLAINT_ONLY_SCOPES: tuple[tuple[str, ...], ...] = (
    ("slack",),
    ("gmail",),
    ("slack", "gmail"),
)

EVAL_CASES_FILENAME = "eval_cases.json"

# `evaluate` clamps a candidate score to 1.0 and resolves on `score >=
# threshold`, so the next float above 1.0 is a threshold nothing can reach.
# Derived rather than written down: a literal here would be a threshold this
# file invented, which is the thing the project claims not to do.
UNREACHABLE_THRESHOLD = math.nextafter(1.0, math.inf)
# The identity floor: no cut at all. Calibration divides by `1 - floor`, so
# every candidate above zero relevancy must score at least as high under it.
NO_SEMANTIC_FLOOR = 0.0


@dataclass
class CheckResult:
    """One assertion, with why it landed the way it did.

    `detail` is written for a human reading the terminal at 2am before a
    rehearsal, so a skip explains what was missing rather than saying "skip".
    """

    id: str
    layer: str
    title: str
    status: str
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "layer": self.layer,
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "evidence": self.evidence,
        }


@dataclass
class EvalReport:
    checks: list[CheckResult] = field(default_factory=list)
    profile_origin: str = "unknown"
    match_threshold: float = 0.0
    # Whether the reference full-scope run reached a complaint source at all.
    # False means most of what follows is a skip, and the banner says so.
    grounded: bool = False
    grounding_errors: dict[str, str] = field(default_factory=dict)
    reference_summary: dict[str, Any] = field(default_factory=dict)
    connector_ids: dict[str, Optional[str]] = field(default_factory=dict)
    cases_file: str = ""
    cases_loaded: int = 0
    cases_error: str = ""
    elapsed_ms: float = 0.0

    def of_status(self, status: str) -> list[CheckResult]:
        return [c for c in self.checks if c.status == status]

    @property
    def counts(self) -> dict[str, int]:
        return {
            PASS: len(self.of_status(PASS)),
            FAIL: len(self.of_status(FAIL)),
            SKIP: len(self.of_status(SKIP)),
        }

    @property
    def ok(self) -> bool:
        """A skip does not fail the suite, but it never counts as a pass."""
        return not self.of_status(FAIL)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "counts": self.counts,
            "grounded": self.grounded,
            "grounding_errors": self.grounding_errors,
            "profile_origin": self.profile_origin,
            "match_threshold": self.match_threshold,
            "reference_summary": self.reference_summary,
            "connector_ids": self.connector_ids,
            "cases_file": self.cases_file,
            "cases_loaded": self.cases_loaded,
            "cases_error": self.cases_error,
            "elapsed_ms": self.elapsed_ms,
            "checks": [c.to_dict() for c in self.checks],
        }


class _Suite:
    """Holds the runs so a scope is queried once and asserted on many times."""

    def __init__(
        self,
        profile: Optional[IntentProfile] = None,
        registry: Optional[ConnectorRegistry] = None,
    ) -> None:
        self.profile = profile or get_profile()
        self.registry = registry or ConnectorRegistry()
        self.checks: list[CheckResult] = []
        self._runs: dict[tuple[str, ...], LeakRun] = {}

    # -- plumbing ------------------------------------------------------------

    def record(
        self,
        check_id: str,
        layer: str,
        title: str,
        status: str,
        detail: str = "",
        **evidence: Any,
    ) -> CheckResult:
        result = CheckResult(
            id=check_id,
            layer=layer,
            title=title,
            status=status,
            detail=detail,
            evidence=evidence,
        )
        self.checks.append(result)
        return result

    def run_scope(self, scope: tuple[str, ...]) -> LeakRun:
        """One `detect_leaks_run`, cached and never allowed to raise.

        A crashed run is reported as an ungrounded one so the checks that
        depend on it skip with a reason instead of taking the whole suite down.
        """
        if scope not in self._runs:
            try:
                run = detect_leaks_run(
                    list(scope), profile=self.profile, registry=self.registry
                )
            except Exception as exc:
                run = LeakRun(
                    providers_requested=list(scope),
                    errors={"*": f"{type(exc).__name__}: {exc}"},
                )
            self._runs[scope] = run
        return self._runs[scope]

    def all_verdicts(self) -> list[tuple[tuple[str, ...], Any]]:
        """Every verdict produced by every scope run so far, tagged by scope."""
        return [
            (scope, verdict)
            for scope, run in self._runs.items()
            for verdict in run.verdicts
        ]

    # -- layer 1: invariants -------------------------------------------------

    def check_complaint_only_refuses(self) -> None:
        """The graded kill-shot claim, one scope at a time.

        With no work-side connector in scope, absence of tracked work cannot be
        established, so every verdict must be `unknown` with a null confidence.
        A scope that produced no verdicts at all proves nothing — the assertion
        is vacuously true over an empty set — so that is a skip.
        """
        for scope in COMPLAINT_ONLY_SCOPES:
            label = "+".join(scope)
            check_id = f"inv.refuses_without_work_source.{label}"
            title = f"scope {label} makes every verdict unknown"
            run = self.run_scope(scope)

            if not run.grounded:
                self.record(
                    check_id,
                    LAYER_INVARIANT,
                    title,
                    SKIP,
                    "no complaint source could be read for this scope, so no "
                    "verdict was produced to assert on",
                    errors=run.errors,
                )
                continue
            if not run.verdicts:
                self.record(
                    check_id,
                    LAYER_INVARIANT,
                    title,
                    SKIP,
                    "the scope was read and holds no complaints, so the "
                    "invariant is vacuous here and proves nothing",
                    complaints_examined=run.complaints_examined,
                    absent=run.complaint_providers_absent,
                    empty=run.complaint_providers_empty,
                )
                continue

            wrong_verdict = [
                v.issue_cluster_id for v in run.verdicts if v.verdict != "unknown"
            ]
            scored = [
                v.issue_cluster_id for v in run.verdicts if v.confidence is not None
            ]
            if wrong_verdict or scored:
                self.record(
                    check_id,
                    LAYER_INVARIANT,
                    title,
                    FAIL,
                    f"{len(wrong_verdict)} verdict(s) were not unknown and "
                    f"{len(scored)} carried a confidence; with no work-side "
                    f"connector in scope the system must refuse, not answer",
                    not_unknown=wrong_verdict,
                    scored=scored,
                )
                continue
            self.record(
                check_id,
                LAYER_INVARIANT,
                title,
                PASS,
                f"all {len(run.verdicts)} cluster(s) unknown, confidence null",
                clusters=len(run.verdicts),
            )

    def check_narrower_never_resolves_more(self, reference: LeakRun) -> None:
        """Removing a source cannot create evidence.

        Every proper subset of the reference scope is re-run and its resolved
        count compared. This is the property that a display-filter kill shot
        would satisfy trivially and a genuinely re-scoped query has to earn.
        """
        wider = tuple(ALL_PROVIDERS)
        narrower: list[tuple[str, ...]] = [
            *COMPLAINT_ONLY_SCOPES,
            ("slack", "gmail", "linear"),
            ("slack", "gmail", "github"),
        ]
        if not reference.grounded:
            self.record(
                "inv.monotonic_resolved",
                LAYER_INVARIANT,
                "a narrower scope never resolves more than a wider one",
                SKIP,
                "the full-scope reference run could not read any complaint "
                "source, so there is no wider answer to compare against",
                errors=reference.errors,
            )
            return

        wide_resolved = _resolved_ids(reference)
        offenders: list[dict[str, Any]] = []
        compared: list[str] = []
        skipped: list[str] = []
        for scope in narrower:
            run = self.run_scope(scope)
            if not run.grounded:
                skipped.append("+".join(scope))
                continue
            compared.append("+".join(scope))
            narrow_resolved = _resolved_ids(run)
            if len(narrow_resolved) > len(wide_resolved):
                offenders.append(
                    {
                        "scope": "+".join(scope),
                        "resolved": len(narrow_resolved),
                        "resolved_at_full_scope": len(wide_resolved),
                        "invented": sorted(narrow_resolved - wide_resolved),
                    }
                )

        if not compared:
            self.record(
                "inv.monotonic_resolved",
                LAYER_INVARIANT,
                "a narrower scope never resolves more than a wider one",
                SKIP,
                "no narrower scope could be read, so nothing was compared",
                unreadable=skipped,
            )
            return
        if offenders:
            self.record(
                "inv.monotonic_resolved",
                LAYER_INVARIANT,
                "a narrower scope never resolves more than a wider one",
                FAIL,
                "a narrower scope reported more resolved complaints than the "
                "full-scope run; removing a source created evidence",
                offenders=offenders,
            )
            return
        self.record(
            "inv.monotonic_resolved",
            LAYER_INVARIANT,
            "a narrower scope never resolves more than a wider one",
            PASS,
            f"{len(compared)} narrower scope(s) compared against full scope "
            f"({len(wide_resolved)} resolved)",
            compared=compared,
            unreadable=skipped,
            resolved_at_full_scope=len(wide_resolved),
        )

    def check_verdict_shape(self) -> None:
        """Three shape invariants over every verdict any scope produced.

        Grouped because they share the same walk and the same skip condition,
        but each is reported on its own row: "the verdicts are malformed" is
        not an actionable failure, "a resolved verdict named no work item" is.
        """
        tagged = self.all_verdicts()
        rules: list[tuple[str, str, Any]] = [
            (
                "inv.resolved_carries_work_item",
                "resolved carries a work item, leak carries none",
                _bad_matched_work_items,
            ),
            (
                "inv.sources_used_missing_disjoint",
                "sources_used and sources_missing are disjoint real connector ids",
                self._bad_source_ids,
            ),
            (
                "inv.confidence_null_iff_unknown",
                "confidence is null if and only if the verdict is unknown",
                _bad_confidence,
            ),
        ]
        for check_id, title, rule in rules:
            if not tagged:
                self.record(
                    check_id,
                    LAYER_INVARIANT,
                    title,
                    SKIP,
                    "no scope produced a verdict, so there is nothing to check",
                )
                continue
            offenders = [
                {"scope": "+".join(scope), **problem}
                for scope, verdict in tagged
                if (problem := rule(verdict))
            ]
            if offenders:
                self.record(
                    check_id,
                    LAYER_INVARIANT,
                    title,
                    FAIL,
                    f"{len(offenders)} of {len(tagged)} verdict(s) violate this",
                    offenders=offenders[: _EVIDENCE_LIMIT],
                )
                continue
            self.record(
                check_id,
                LAYER_INVARIANT,
                title,
                PASS,
                f"{len(tagged)} verdict(s) across "
                f"{len(self._runs)} scope(s) hold",
                verdicts_checked=len(tagged),
            )

    def _bad_source_ids(self, verdict: Any) -> dict[str, Any]:
        known = {
            cid for p in ALL_PROVIDERS if (cid := self.registry.connector_id(p))
        }
        used = set(verdict.sources_used)
        missing = set(verdict.sources_missing)
        overlap = used & missing
        unknown = (used | missing) - known
        if not overlap and not unknown:
            return {}
        return {
            "cluster": verdict.issue_cluster_id,
            "in_both": sorted(overlap),
            "not_a_connector_id": sorted(unknown),
        }

    # -- layer 1: memory -----------------------------------------------------

    def check_fresh_actor_has_no_history(self) -> None:
        """A person we have never heard from has zero prior contacts.

        The actor and the topic are both freshly generated, so a nonzero count
        here is an invented one. `first_contact` is the correct tone for it and
        is asserted alongside, because the tone is the part that reaches the
        customer.
        """
        complaint = _synthetic_complaint()
        try:
            memory = memory_read(complaint, list(COMPLAINT_PROVIDERS), self.profile)
        except Exception as exc:
            self.record(
                "inv.fresh_actor_no_history",
                LAYER_INVARIANT,
                "an actor with no history reads back zeros and first_contact",
                FAIL,
                f"memory_read raised on a fresh actor: {type(exc).__name__}: {exc}",
            )
            return

        tone = derive_reply_tone(memory, self.profile)
        problems = []
        if memory.times_reported_by_actor:
            problems.append(
                f"times_reported_by_actor is {memory.times_reported_by_actor} "
                f"for an actor generated moments ago"
            )
        if memory.times_seen_on_topic:
            problems.append(
                f"times_seen_on_topic is {memory.times_seen_on_topic} for a "
                f"topic string that has never been written"
            )
        if memory.prior_resolutions:
            problems.append(
                f"{len(memory.prior_resolutions)} prior resolution(s) recalled "
                f"for a complaint that does not exist"
            )
        if tone != "first_contact":
            problems.append(f"reply tone is {tone!r}, expected 'first_contact'")

        if problems:
            self.record(
                "inv.fresh_actor_no_history",
                LAYER_INVARIANT,
                "an actor with no history reads back zeros and first_contact",
                FAIL,
                "; ".join(problems),
                actor=memory.actor,
                tone=tone,
            )
            return
        self.record(
            "inv.fresh_actor_no_history",
            LAYER_INVARIANT,
            "an actor with no history reads back zeros and first_contact",
            PASS,
            f"zero prior contacts, tone {tone!r}, stub={memory.stub}",
            stub=memory.stub,
        )

    def check_memory_without_credential(self) -> None:
        """No token means an honest empty read, not a crash and not a guess."""
        complaint = _synthetic_complaint()
        title = "memory_read with no credential returns an honest empty result"
        with _blanked_hydra_credential():
            try:
                memory = memory_read(
                    complaint, list(COMPLAINT_PROVIDERS), self.profile
                )
            except Exception as exc:
                self.record(
                    "inv.memory_degrades_without_credential",
                    LAYER_INVARIANT,
                    title,
                    FAIL,
                    f"memory_read raised with the credential blanked: "
                    f"{type(exc).__name__}: {exc}",
                )
                return

        problems = _fabricated_memory(memory)
        if problems:
            self.record(
                "inv.memory_degrades_without_credential",
                LAYER_INVARIANT,
                title,
                FAIL,
                "; ".join(problems),
            )
            return
        self.record(
            "inv.memory_degrades_without_credential",
            LAYER_INVARIANT,
            title,
            PASS,
            f"returned zeros with stub={memory.stub} and no exception",
            stub=memory.stub,
            sources_used=memory.sources_used,
        )

    def check_leaks_without_credential(self) -> None:
        """With nothing reachable, the query must say so rather than say nothing.

        Both halves of the contract are asserted: `detect_leaks` raises
        `SourceUnavailable` so an empty list can only ever mean "read them and
        found nothing", and `detect_leaks_run` reports the same state as
        `grounded=False` without raising.
        """
        title = "with no credential, leak detection reports it is not grounded"
        raised: Optional[Exception] = None
        run: Optional[LeakRun] = None
        crash: Optional[Exception] = None
        # The document and collection caches survive a credential change, so a
        # warm cache would answer from the last good read and this check would
        # pass without ever going near the blanked credential.
        refresh_documents()
        try:
            with _blanked_hydra_credential():
                try:
                    detect_leaks(
                        list(ALL_PROVIDERS),
                        profile=self.profile,
                        registry=self.registry,
                    )
                except SourceUnavailable as exc:
                    raised = exc
                try:
                    run = detect_leaks_run(
                        list(ALL_PROVIDERS),
                        profile=self.profile,
                        registry=self.registry,
                    )
                except Exception as exc:
                    crash = exc
        finally:
            # Leave nothing cached from the blanked run for the next caller.
            refresh_documents()

        problems = []
        if raised is None:
            problems.append(
                "detect_leaks did not raise SourceUnavailable, so an empty "
                "result is indistinguishable from an unreachable backend"
            )
        if crash is not None:
            problems.append(
                f"detect_leaks_run raised instead of degrading: "
                f"{type(crash).__name__}: {crash}"
            )
        elif run is not None:
            if run.grounded:
                problems.append(
                    "detect_leaks_run reported grounded=True with the "
                    "credential blanked"
                )
            if not run.errors:
                problems.append(
                    "detect_leaks_run reported no errors, so nothing explains "
                    "the empty answer"
                )
            if run.verdicts:
                problems.append(
                    f"detect_leaks_run returned {len(run.verdicts)} verdict(s) "
                    f"with nothing reachable"
                )

        if problems:
            self.record(
                "inv.leaks_degrade_without_credential",
                LAYER_INVARIANT,
                title,
                FAIL,
                "; ".join(problems),
            )
            return
        self.record(
            "inv.leaks_degrade_without_credential",
            LAYER_INVARIANT,
            title,
            PASS,
            f"detect_leaks raised SourceUnavailable; detect_leaks_run returned "
            f"grounded=False with {len(run.errors) if run else 0} reason(s)",
            reasons=dict(run.errors) if run else {},
        )

    # -- layer 2: expected cases --------------------------------------------

    def check_expected_cases(self, report: EvalReport) -> None:
        cases, error = _load_cases()
        report.cases_file = str(config.FIXTURES_DIR / EVAL_CASES_FILENAME)
        report.cases_loaded = len(cases)
        report.cases_error = error

        if error:
            self.record(
                "case.load",
                LAYER_EXPECTED,
                "the expected-case table loads",
                FAIL,
                error,
            )
            return
        if not cases:
            self.record(
                "case.load",
                LAYER_EXPECTED,
                "the expected-case table loads",
                SKIP,
                f"no expected cases defined in {EVAL_CASES_FILENAME}; layer 2 "
                f"is empty until the demo tenant is seeded",
            )
            return

        reference = self.run_scope(tuple(ALL_PROVIDERS))
        by_complaint = {
            complaint_id: verdict
            for verdict in reference.verdicts
            for complaint_id in verdict.complaint_ids
        }
        for case in cases:
            self._check_one_case(case, reference, by_complaint)

    def _check_one_case(
        self,
        case: dict[str, Any],
        reference: LeakRun,
        by_complaint: dict[str, Any],
    ) -> None:
        case_id = str(case.get("id") or case.get("complaint_id") or "<unnamed>")
        complaint_id = str(case.get("complaint_id") or "")
        expected = str(case.get("expect_verdict") or "")
        check_id = f"case.{case_id}"
        title = f"{complaint_id or case_id} is {expected or '<unspecified>'}"

        if not complaint_id or not expected:
            self.record(
                check_id,
                LAYER_EXPECTED,
                title,
                FAIL,
                "case is missing complaint_id or expect_verdict",
                case=case,
            )
            return
        if not reference.grounded:
            self.record(
                check_id,
                LAYER_EXPECTED,
                title,
                SKIP,
                "the full-scope run could not read any complaint source",
                errors=reference.errors,
            )
            return

        verdict = by_complaint.get(complaint_id)
        if verdict is None:
            self.record(
                check_id,
                LAYER_EXPECTED,
                title,
                SKIP,
                f"complaint {complaint_id!r} is not present in this tenant; "
                f"no expected case can be evaluated against it",
                note=str(case.get("note") or ""),
            )
            return

        problems = []
        if verdict.verdict != expected:
            problems.append(
                f"verdict is {verdict.verdict!r}, expected {expected!r}"
            )
        wanted_sources = [str(s) for s in case.get("expect_work_sources") or []]
        if wanted_sources:
            got = sorted({str(w.get("source")) for w in verdict.matched_work_items})
            if got != sorted(wanted_sources):
                problems.append(
                    f"matched work came from {got}, expected {sorted(wanted_sources)}"
                )
        if problems:
            self.record(
                check_id,
                LAYER_EXPECTED,
                title,
                FAIL,
                "; ".join(problems),
                cluster=verdict.issue_cluster_id,
                confidence=verdict.confidence,
                matched_work_items=verdict.matched_work_items,
            )
            return
        self.record(
            check_id,
            LAYER_EXPECTED,
            title,
            PASS,
            f"verdict {verdict.verdict!r}, confidence {verdict.confidence}",
            cluster=verdict.issue_cluster_id,
        )

    # -- negative controls ---------------------------------------------------

    def check_bogus_connector_returns_nothing(self, probe: Optional[Document]) -> None:
        """Point the query at a connector id that does not exist.

        The kill shot's entire honesty claim rests on
        `metadata_filters.additional_metadata.connector_id` actually scoping the
        query. If a made-up id still returns candidates, the filter is inert and
        every scoped row in the kill shot is a display filter wearing a costume.
        """
        check_id = "ctl.bogus_connector_id"
        title = "a nonexistent connector id returns zero candidates"
        scope = [p for p in WORK_PROVIDERS if self.registry.connector_id(p)]
        if probe is None or not scope:
            self.record(
                check_id,
                LAYER_CONTROL,
                title,
                SKIP,
                "no complaint document to query with, or no work-side "
                "connector provisioned, so the filter cannot be exercised",
                work_scope=scope,
            )
            return

        real = search_work(
            probe.search_text, scope, self.registry, self.profile
        )
        if not real.candidates:
            self.record(
                check_id,
                LAYER_CONTROL,
                title,
                SKIP,
                "the same query with the real connector id also returned "
                "nothing, so zero candidates under a bogus id proves nothing",
                searched=real.searched,
                unavailable=real.unavailable,
                failed=real.failed,
            )
            return

        bogus_id = str(uuid.uuid4())
        bogus = ConnectorRegistry(
            {p: {"connector_id": bogus_id, "sub_tenant_id": p} for p in scope}
        )
        broken = search_work(probe.search_text, scope, bogus, self.profile)
        if broken.candidates:
            self.record(
                check_id,
                LAYER_CONTROL,
                title,
                FAIL,
                f"{len(broken.candidates)} candidate(s) came back under a "
                f"connector id that exists nowhere; the connector filter is "
                f"not scoping the query",
                real_candidates=len(real.candidates),
                bogus_candidates=len(broken.candidates),
                bogus_connector_id=bogus_id,
            )
            return
        self.record(
            check_id,
            LAYER_CONTROL,
            title,
            PASS,
            f"{len(real.candidates)} candidate(s) with the real connector id, "
            f"0 with a generated one",
            real_candidates=len(real.candidates),
            searched=real.searched,
        )

    def check_unreachable_threshold_flips_resolved(self, reference: LeakRun) -> None:
        """Raise the match threshold out of reach; every resolution must fall."""
        check_id = "ctl.unreachable_match_threshold"
        title = "an unreachable match_threshold turns every resolved into a leak"
        resolved = _resolved_ids(reference)
        if not reference.grounded or not resolved:
            self.record(
                check_id,
                LAYER_CONTROL,
                title,
                SKIP,
                "the full-scope run resolved nothing, so there is no "
                "resolution for a raised threshold to break",
                grounded=reference.grounded,
                resolved_at_full_scope=len(resolved),
            )
            return

        strict = replace(self.profile, match_threshold=UNREACHABLE_THRESHOLD)
        try:
            run = detect_leaks_run(
                list(ALL_PROVIDERS), profile=strict, registry=self.registry
            )
        except Exception as exc:
            self.record(
                check_id,
                LAYER_CONTROL,
                title,
                FAIL,
                f"the re-run raised: {type(exc).__name__}: {exc}",
            )
            return

        survivors = sorted(_resolved_ids(run))
        if survivors:
            self.record(
                check_id,
                LAYER_CONTROL,
                title,
                FAIL,
                f"{len(survivors)} complaint(s) still resolved against a "
                f"threshold no score can reach; the threshold is not being read "
                f"from the profile",
                survivors=survivors[:_EVIDENCE_LIMIT],
                threshold=UNREACHABLE_THRESHOLD,
            )
            return
        self.record(
            check_id,
            LAYER_CONTROL,
            title,
            PASS,
            f"all {len(resolved)} resolution(s) flipped to leak when the "
            f"threshold moved out of reach",
            resolved_before=len(resolved),
            threshold=UNREACHABLE_THRESHOLD,
        )

    def check_removing_semantic_floor_raises_scores(
        self, probe: Optional[Document]
    ) -> None:
        """Drop the relevancy floor; calibrated scores must rise.

        `semantic_floor` is the one profile knob that touches every score, so if
        scores are indifferent to it they are not being calibrated against the
        profile at all.
        """
        check_id = "ctl.semantic_floor_moves_scores"
        title = "lowering semantic_floor raises candidate scores"
        scope = [p for p in WORK_PROVIDERS if self.registry.connector_id(p)]
        if probe is None or not scope:
            self.record(
                check_id,
                LAYER_CONTROL,
                title,
                SKIP,
                "no complaint document to query with, or no work-side "
                "connector provisioned",
            )
            return
        if self.profile.semantic_floor <= NO_SEMANTIC_FLOOR:
            self.record(
                check_id,
                LAYER_CONTROL,
                title,
                SKIP,
                f"the profile already sets semantic_floor to "
                f"{self.profile.semantic_floor}, so there is no floor to remove",
            )
            return

        search = search_work(probe.search_text, scope, self.registry, self.profile)
        if not search.candidates:
            self.record(
                check_id,
                LAYER_CONTROL,
                title,
                SKIP,
                "no candidates came back, so there is no score to move",
                searched=search.searched,
                unavailable=search.unavailable,
                failed=search.failed,
            )
            return

        loose = replace(self.profile, semantic_floor=NO_SEMANTIC_FLOOR)
        moved = []
        dropped = []
        for candidate in search.candidates:
            before, _ = score_candidate(
                probe, candidate, search.candidates, self.profile
            )
            after, _ = score_candidate(probe, candidate, search.candidates, loose)
            row = {
                "candidate": candidate.document_id,
                "relevancy": round(candidate.score, 4),
                "score_at_profile_floor": round(before, 4),
                "score_with_no_floor": round(after, 4),
            }
            if after < before:
                dropped.append(row)
            elif after > before:
                moved.append(row)

        if dropped:
            self.record(
                check_id,
                LAYER_CONTROL,
                title,
                FAIL,
                f"{len(dropped)} candidate(s) scored lower with the floor "
                f"removed, which calibration cannot produce",
                dropped=dropped[:_EVIDENCE_LIMIT],
            )
            return
        if not moved:
            self.record(
                check_id,
                LAYER_CONTROL,
                title,
                FAIL,
                f"none of {len(search.candidates)} candidate score(s) changed "
                f"when semantic_floor moved from "
                f"{self.profile.semantic_floor} to {NO_SEMANTIC_FLOOR}; scores "
                f"are not reading the profile",
                candidates=len(search.candidates),
            )
            return
        self.record(
            check_id,
            LAYER_CONTROL,
            title,
            PASS,
            f"{len(moved)} of {len(search.candidates)} candidate score(s) rose "
            f"when the floor was removed",
            moved=moved[:_EVIDENCE_LIMIT],
        )


_EVIDENCE_LIMIT = 10


# --- rule helpers -------------------------------------------------------------


def _resolved_ids(run: LeakRun) -> set[str]:
    return {
        complaint_id
        for verdict in run.verdicts
        if verdict.verdict == "resolved"
        for complaint_id in verdict.complaint_ids
    }


def _bad_matched_work_items(verdict: Any) -> dict[str, Any]:
    """A resolution must name what it resolved to; a leak must name nothing."""
    count = len(verdict.matched_work_items)
    if verdict.verdict == "resolved" and not count:
        return {
            "cluster": verdict.issue_cluster_id,
            "problem": "resolved with no matched_work_items",
        }
    if verdict.verdict == "leak" and count:
        return {
            "cluster": verdict.issue_cluster_id,
            "problem": f"leak carries {count} matched work item(s)",
        }
    return {}


def _bad_confidence(verdict: Any) -> dict[str, Any]:
    unknown = verdict.verdict == "unknown"
    scored = verdict.confidence is not None
    if unknown == scored:
        return {
            "cluster": verdict.issue_cluster_id,
            "problem": f"verdict {verdict.verdict!r} with confidence "
            f"{verdict.confidence!r}",
        }
    return {}


def _fabricated_memory(memory: MemoryReadResult) -> list[str]:
    problems = []
    if memory.times_reported_by_actor or memory.times_seen_on_topic:
        problems.append(
            f"returned counts {memory.times_reported_by_actor}/"
            f"{memory.times_seen_on_topic} with no credential to read them with"
        )
    if memory.prior_resolutions:
        problems.append(
            f"returned {len(memory.prior_resolutions)} prior resolution(s) "
            f"with no credential"
        )
    if memory.likely_regression is not None:
        problems.append("named a likely regression with no credential")
    return problems


def _synthetic_complaint() -> ComplaintEvent:
    """A complaint that cannot have a history, because it was invented just now.

    Both the actor and the topic carry a fresh uuid, so the actor-filtered query
    and the topical query both have to come back empty. Read-only: nothing here
    is ever written to the tenant.
    """
    token = uuid.uuid4().hex
    return ComplaintEvent(
        id=f"eval-probe-{token}",
        source=COMPLAINT_PROVIDERS[0],
        entity_id=f"eval-probe-{token}",
        text=f"eval probe {token}: this string has never been written anywhere",
        t=time.time(),
        channel_or_thread=f"eval-probe-{token}",
        author_email=f"eval-probe-{token}@invalid.example",
        actor_display_name="eval probe",
    )


class _blanked_hydra_credential:
    """Blank `config.HYDRA_TOKEN` for the duration of a check, then restore it.

    Patched on the module attribute rather than the environment because that is
    where every call site reads it from, and restored in `finally` so a failing
    assertion cannot leave the process credential-less.
    """

    def __enter__(self) -> None:
        self._saved = config.HYDRA_TOKEN
        config.HYDRA_TOKEN = ""

    def __exit__(self, *exc: Any) -> bool:
        config.HYDRA_TOKEN = self._saved
        return False


def _load_cases() -> tuple[list[dict[str, Any]], str]:
    """Read the expected-case table. Absent is fine; malformed is not."""
    path = config.FIXTURES_DIR / EVAL_CASES_FILENAME
    if not path.exists():
        return [], ""
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return [], f"{path.name} could not be read: {type(exc).__name__}: {exc}"
    cases = raw.get("cases") if isinstance(raw, dict) else raw
    if not isinstance(cases, list):
        return [], f"{path.name} has no 'cases' list"
    return [c for c in cases if isinstance(c, dict)], ""


def _probe_document(reference: LeakRun) -> Optional[Document]:
    """One real complaint document to aim the negative controls at.

    Taken from whichever complaint source actually answered, so the controls
    exercise the same retrieval path the verdicts came out of rather than a
    synthetic query the backend has never seen.
    """
    for provider in reference.complaint_providers_read or COMPLAINT_PROVIDERS:
        load = load_documents_result(provider)
        if load.ok and load.documents:
            return load.documents[0]
    return None


# --- entry point ---------------------------------------------------------------


def run_eval(
    profile: Optional[IntentProfile] = None,
    registry: Optional[ConnectorRegistry] = None,
) -> EvalReport:
    """Run every layer and return the structured result.

    Never raises on a check failure — a failure is a row in the report. It only
    propagates if the profile or the connector registry cannot be read at all,
    because at that point there is no suite to run.
    """
    started = time.perf_counter()
    suite = _Suite(profile=profile, registry=registry)
    report = EvalReport(
        profile_origin=suite.profile.origin,
        match_threshold=suite.profile.match_threshold,
        connector_ids={p: suite.registry.connector_id(p) for p in ALL_PROVIDERS},
    )

    reference = suite.run_scope(tuple(ALL_PROVIDERS))
    report.grounded = reference.grounded
    report.grounding_errors = dict(reference.errors)
    report.reference_summary = {
        "complaints_examined": reference.complaints_examined,
        "clusters": len(reference.verdicts),
        "resolved": len(_resolved_ids(reference)),
        "complaint_providers_read": list(reference.complaint_providers_read),
        "complaint_providers_empty": list(reference.complaint_providers_empty),
        "complaint_providers_absent": list(reference.complaint_providers_absent),
    }

    suite.check_complaint_only_refuses()
    suite.check_narrower_never_resolves_more(reference)
    suite.check_verdict_shape()
    suite.check_expected_cases(report)

    probe = _probe_document(reference)
    suite.check_bogus_connector_returns_nothing(probe)
    suite.check_unreachable_threshold_flips_resolved(reference)
    suite.check_removing_semantic_floor_raises_scores(probe)

    suite.check_fresh_actor_has_no_history()
    # Credential-blanking last: it clears the document cache on the way out, so
    # anything after it pays a full re-read for no extra coverage.
    suite.check_memory_without_credential()
    suite.check_leaks_without_credential()

    report.checks = suite.checks
    report.elapsed_ms = round((time.perf_counter() - started) * 1000.0, 1)
    return report


def format_report(report: EvalReport) -> str:
    """The terminal view. Three states, spelled out, never collapsed to two."""
    lines: list[str] = []
    width = max((len(c.id) for c in report.checks), default=0)
    # Grouped for reading, not for running: the credential-blanking invariants
    # have to execute last but belong under the same heading as the rest.
    for layer in (LAYER_INVARIANT, LAYER_EXPECTED, LAYER_CONTROL):
        group = [c for c in report.checks if c.layer == layer]
        if not group:
            continue
        lines.append("")
        lines.append(layer.upper())
        for check in group:
            lines.append(
                f"  [{check.status.upper():4s}] {check.id:{width}s}  {check.title}"
            )
            if check.detail:
                lines.append(f"{'':10s}{check.detail}")

    counts = report.counts
    lines.append("")
    lines.append(f"profile        {report.profile_origin} (match_threshold {report.match_threshold})")
    lines.append(f"grounded       {report.grounded}")
    if report.grounding_errors:
        for provider, error in sorted(report.grounding_errors.items()):
            lines.append(f"               {provider}: {error}")
    lines.append(f"reference      {report.reference_summary}")
    lines.append(f"cases file     {report.cases_file} ({report.cases_loaded} case(s))")
    if report.cases_error:
        lines.append(f"               {report.cases_error}")
    lines.append(f"elapsed        {report.elapsed_ms:.0f}ms")
    lines.append("")
    lines.append(
        f"{counts[PASS]} passed, {counts[FAIL]} failed, {counts[SKIP]} skipped"
    )
    if counts[SKIP]:
        lines.append(
            "a skip is not a pass: the data those checks need is not in the "
            "tenant, so they proved nothing either way"
        )
    lines.append("EVAL FAILED" if not report.ok else "EVAL PASSED")
    return "\n".join(lines)
