#!/usr/bin/env python3
"""End-to-end smoke test. Run this at every sync point before merging.

Exercises all eight nodes against the fixture corpus and asserts the wiring
holds -- not that the answers are good, which is what the eval suite is for.
It runs with no credentials at all, so it is a build check rather than an
integration check.

Exit codes:
    0  wiring is sound
    1  something in the pipeline is broken

Stubs do NOT fail this. A stubbed node is the expected state during the build;
what would be a disaster is a stub reaching the stage, so the stub list is
printed loudly and `--demo-ready` turns it into a failure. Run the bare version
while building and `--demo-ready` before you present.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ghostthread import actions_log  # noqa: E402
from ghostthread.contracts import (  # noqa: E402
    CATEGORIES,
    ExtractedFacts,
    MemoryReadResult,
    ResolutionAction,
)
from ghostthread.intent import get_profile  # noqa: E402
from ghostthread.memory import derive_reply_tone  # noqa: E402
from ghostthread.pipeline import GhostThread  # noqa: E402
from ghostthread.router import route, validate_policy  # noqa: E402


def check_policy_complete() -> list[str]:
    """Every category in the taxonomy routes somewhere. No fallthrough."""
    profile = get_profile(force_refresh=True)
    problems = validate_policy(profile)

    # Route a synthetic complaint under each category and confirm the router
    # answers rather than raising. Confidence is pinned above the floor so this
    # tests the category's own policy, not the low-confidence collapse.
    for category in CATEGORIES:
        facts = ExtractedFacts(
            complaint_id=f"smoke-{category}",
            what_broke="smoke test",
            is_code_issue=False,
            file_hint=None,
            severity=0.0,
            category=category,
            confidence=1.0,
        )
        try:
            decision = route(facts, profile)
        except Exception as exc:
            problems.append(f"route() raised on category {category!r}: {exc}")
            continue
        if decision.category != category:
            problems.append(
                f"{category} was rerouted to {decision.category} at full confidence"
            )
    return problems


def check_low_confidence_collapses() -> list[str]:
    """Below the profile floor, the guess is discarded whatever it was."""
    profile = get_profile()
    floor = profile.min_confidence_for_autoaction
    if floor <= 0:
        return ["min_confidence_for_autoaction is 0; low-confidence guesses are never caught"]

    facts = ExtractedFacts(
        complaint_id="smoke-lowconf",
        what_broke="smoke test",
        is_code_issue=True,
        file_hint=None,
        severity=0.1,
        category="genuine_bug",
        confidence=floor / 2,
    )
    decision = route(facts, profile)
    if not decision.forced_fallback:
        return [f"confidence {facts.confidence} under floor {floor} did not force the fallback"]
    return []


def check_reply_tone_ladder() -> list[str]:
    """The tone steps up with the counts. This is the memory payoff on stage."""
    profile = get_profile()
    thresholds = profile.reply_tone_thresholds or {}
    if not thresholds:
        return ["reply_tone_thresholds missing from the profile; the tone ladder is dead"]

    # No defaults here on purpose. A `.get(key, 3)` in the check that exists to
    # prove nothing is hardcoded would let the profile lose a step point and
    # still pass, against a number this file made up.
    missing = [key for key in ("returning", "escalation") if key not in thresholds]
    if missing:
        return [
            f"reply_tone_thresholds is missing {', '.join(missing)}; "
            f"the step points must come from the profile and this check "
            f"will not substitute a default for them"
        ]

    cases = [
        (0, "first_contact"),
        (thresholds["returning"], "returning"),
        (thresholds["escalation"], "escalation"),
    ]
    problems = []
    for count, expected in cases:
        got = derive_reply_tone(
            MemoryReadResult(actor="smoke@test", times_reported_by_actor=count), profile
        )
        if got != expected:
            problems.append(f"{count} prior contacts gave tone {got!r}, expected {expected!r}")
    return problems


def check_pipeline_runs() -> tuple[list[str], object]:
    """The whole thing, end to end, on the fixture corpus."""
    problems: list[str] = []
    engine = GhostThread()
    report = engine.run(act_on_leaks=True)

    if not report.results:
        problems.append("pipeline returned no results at all")
    if report.policy_problems:
        problems.extend(report.policy_problems)

    leaked = [r for r in report.results if r["verdict"] == "leaked"]
    if leaked and not report.actions:
        problems.append(f"{len(leaked)} leaks found but no actions were produced")

    for action in report.actions:
        if action.get("idempotent_replay"):
            continue
        if "routing" not in action or not action["routing"]:
            problems.append(f"action for {action['facts']['complaint_id']} carries no routing decision")
        if "memory" not in action:
            problems.append(f"action for {action['facts']['complaint_id']} carries no memory read")

    return problems, report


def check_log_dedupes_a_novel_key() -> list[str]:
    """The log itself, against an id that has provably never been seen.

    This exists because the pipeline-level check below cannot prove the write
    path once the log is durable: on the second smoke run of the day every
    corpus complaint is already in Postgres, so *every* action is a replay and
    "nothing was written twice" becomes vacuously true. A fresh uuid is the only
    key guaranteed novel on both backends.

    It also asserts the part that is the entire reason N8 lives in Postgres: a
    second, independently constructed log -- standing in for a second instance,
    or the same instance after a restart -- must see a row it never wrote. An
    in-process dict cannot pass that, which is why it is only asserted when the
    backend claims it can.
    """
    log = actions_log.ActionsLog()
    complaint_id = f"smoke-idem-{uuid.uuid4().hex}"
    problems: list[str] = []

    if log.seen(complaint_id) is not None:
        problems.append(f"a never-before-seen id was reported as already handled ({log.backend})")

    log.record(complaint_id, _probe_resolution(complaint_id))

    if log.seen(complaint_id) is None:
        problems.append(f"recorded {complaint_id} but the log still reports it unseen")

    if log.backend == "insforge":
        # Empty mirror, so this can only be answered out of Postgres.
        other_instance = actions_log.ActionsLog()
        if other_instance.seen(complaint_id) is None:
            problems.append(
                "the InsForge-backed log did not see a row written by another instance; "
                "the cross-instance guarantee the run report claims is not real"
            )
        _forget_probe_row(complaint_id)
    elif not log.degraded_reason:
        problems.append("log is running in-process but gives no reason for the degradation")

    return problems


def check_pipeline_replays() -> list[str]:
    """The same complaint through the whole pipeline twice must act once."""
    engine = GhostThread()
    first = engine.run(act_on_leaks=True)
    second = engine.run(act_on_leaks=True)

    real_second = [a for a in second.actions if not a.get("idempotent_replay")]
    replays = [a for a in second.actions if a.get("idempotent_replay")]

    problems = []
    if not first.actions:
        problems.append(
            "the corpus produced no actions at all, so the replay path was never exercised"
        )
    if real_second:
        problems.append(
            f"second run produced {len(real_second)} fresh actions; "
            f"idempotency log did not catch the replay"
        )
    elif not replays:
        problems.append(
            "second run recorded no replays; the idempotency log was not consulted"
        )
    return problems


def _probe_resolution(complaint_id: str) -> ResolutionAction:
    """The smallest honest ResolutionAction. Nothing here claims work was done.

    `actor_resolved` stays null so the probe row cannot appear in the memory
    dashboard, which filters on it.
    """
    return ResolutionAction(
        leak={},
        facts={"complaint_id": complaint_id},
        ticket_created_id=None,
        fix_attempted=False,
        fix_pr_url=None,
        reply_sent=False,
        reply_channel=None,
        decision="smoke-test idempotency probe; no action was taken",
        dry_run=True,
    )


def _forget_probe_row(complaint_id: str) -> None:
    """Best-effort cleanup of this check's own row.

    Done here with a raw request rather than by adding a delete to ActionsLog:
    a log the application can erase is not an idempotency log. The test wrote
    the row, so the test cleans it up; failure is ignored because a leftover
    probe row is harmless.
    """
    try:
        httpx.delete(
            actions_log._records_url(),
            params={actions_log.KEY_COLUMN: f"eq.{complaint_id}"},
            headers=actions_log._headers(),
            timeout=8.0,
        )
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--demo-ready",
        action="store_true",
        help="also fail if any node is still a stub. Run this before presenting.",
    )
    args = parser.parse_args()

    pipeline_problems, report = check_pipeline_runs()

    checks = [
        ("every category has a policy and routes", check_policy_complete()),
        ("low confidence collapses to the fallback", check_low_confidence_collapses()),
        ("reply tone ladder steps with the counts", check_reply_tone_ladder()),
        ("pipeline runs end to end", pipeline_problems),
        ("the log dedupes an id it has never seen", check_log_dedupes_a_novel_key()),
        ("same complaint twice = one action", check_pipeline_replays()),
    ]

    total = 0
    for label, problems in checks:
        total += len(problems)
        print(f"[{'PASS' if not problems else 'FAIL'}] {label}")
        for p in problems:
            print(f"       {p}")

    print()
    print(f"backends      {report.backends}")
    # Printed in full because "in-process" on its own does not say whether the
    # credential is missing, the table is missing, or InsForge is down.
    print(f"idempotency   {report.idempotency}")
    print(f"sources       {report.sources_requested}")
    print(f"complaints    {report.summary['complaints_examined']}")
    print(f"verdicts      {report.summary['by_verdict']}")
    print(f"actions       {len(report.actions)}")
    print(f"elapsed       {report.elapsed_ms:.0f}ms")

    if report.actions:
        sample = next((a for a in report.actions if not a.get("idempotent_replay")), None)
        if sample:
            routing = sample.get("routing", {})
            facts = sample.get("facts", {})
            print()
            print("one resolution, end to end:")
            print(f"  complaint   {facts.get('complaint_id')}")
            print(f"  category    {facts.get('category')} (confidence {facts.get('confidence')})")
            print(f"  severity    {facts.get('severity')}")
            print(f"  tone        {facts.get('reply_tone')}")
            print(f"  actions     {routing.get('actions')}")
            print(f"  approval    {routing.get('requires_human_approval')}")
            print(f"  reason      {routing.get('reason')}")

    if report.stubs:
        print()
        print("STUBBED NODES (not demo-ready):")
        for s in report.stubs:
            print(f"  - {s}")
        if args.demo_ready:
            total += len(report.stubs)

    print()
    if total:
        print(f"{total} problem(s) found")
        return 1
    print("wiring is sound" + (" and no stubs remain" if args.demo_ready else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
