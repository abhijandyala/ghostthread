#!/usr/bin/env python3
"""Load the demo's prior contacts into HydraDB Memories, then check they landed.

The pitch is "every other complaint agent reads one message; we read the
history". That only works if there is history. `fixtures/memory_seed.json`
holds the prior contacts SEED.md describes -- resolutions that already happened
and are not open work -- and this script writes them so the live complaint
arrives as the third contact rather than the first.

Three things this script refuses to do, because each of them would hand the
demo a number it did not earn:

* Write by a different path from production. Every episode goes through
  `memory.memory_write`, the same function pipeline node N7 calls. A seeder with
  its own ingest call proves nothing about the pipeline.
* Report success without writing. `memory_write` returns a `DRY-` id and
  persists nothing while `DRY_RUN` is true, which it is by default, so the
  write path is gated behind `--force` and the mode is printed before anything
  runs.
* Report the count it hoped for. After seeding it reads the memory back through
  `memory.memory_read` and prints what the system actually recalls. If the tone
  is not what the profile's thresholds imply for the number of episodes
  seeded, this exits non-zero.

    scripts/seed_memory.py --dry-run    # print the payloads, write nothing
    scripts/seed_memory.py --force      # write, wait for indexing, verify
    scripts/seed_memory.py --purge --force   # remove them, for a clean rehearsal

Re-running is safe: `memory_write` upserts on `mem-{complaint_id}`, so the
counts do not inflate.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import hydra_db  # noqa: E402

from ghostthread import config, memory  # noqa: E402
from ghostthread.contracts import (  # noqa: E402
    COMPLAINT_SOURCES,
    ComplaintEvent,
    MemoryReadResult,
    MemoryWriteInput,
)
from ghostthread.intent import get_profile  # noqa: E402

SEED_PATH = config.FIXTURES_DIR / "memory_seed.json"

# Memory ingest is asynchronous: the write returns before the row is queryable.
POLL_INTERVAL_SECONDS = 2.0
INDEX_TIMEOUT_SECONDS = 120.0
# A row count that has not moved for this many consecutive polls is treated as
# settled. One unchanged reading is not enough -- indexing pauses mid-flight.
STABLE_POLLS = 3


# --- fixture ------------------------------------------------------------------


class Episode:
    """One prior contact, ready to write.

    A thin wrapper rather than a dataclass because its only job is to turn a
    fixture row into a `MemoryWriteInput` and to remember the offset it came
    from, for the report.
    """

    def __init__(self, actor: str, topic_id: str, raw: dict[str, Any]) -> None:
        self.actor = actor
        self.topic_id = topic_id
        self.complaint_id = str(raw["complaint_id"])
        self.days_ago = int(raw["resolved_days_ago"])
        self.record = MemoryWriteInput(
            actor=actor,
            complaint_id=self.complaint_id,
            complaint_summary=str(raw["summary"]),
            category=str(raw["category"]),
            action_taken=list(raw.get("action_taken") or []),
            ticket_url=raw.get("ticket_url") or None,
            # Stored as an offset so the fixture does not rot. A hardcoded date
            # would quietly age into "resolved three months ago" and eventually
            # out of any window the demo talks about.
            resolved_at=_days_ago_iso(self.days_ago),
        )

    @property
    def memory_id(self) -> str:
        """The id `memory_write` derives, needed by `--purge`."""
        return f"mem-{self.complaint_id}"


class Topic:
    def __init__(self, actor: str, display_name: str, raw: dict[str, Any]) -> None:
        self.actor = actor
        self.display_name = display_name
        self.id = str(raw["id"])
        self.live = dict(raw["live_complaint"])
        self.episodes = [Episode(actor, self.id, e) for e in raw["episodes"]]

    def probe(self) -> ComplaintEvent:
        """The complaint to verify with: the live third contact, unwritten.

        Using the demo's own message rather than a synthetic query means the
        verification runs the same retrieval the demo will. `memory_read`
        excludes a complaint's own memory from its counts, so this id must not
        be one of the seeded ones -- the fixture keeps them distinct.
        """
        return ComplaintEvent(
            id=str(self.live["id"]),
            source=str(self.live["source"]),
            entity_id=self.actor,
            text=str(self.live["text"]),
            t=time.time(),
            channel_or_thread=str(self.live.get("channel_or_thread") or ""),
            author_email=self.actor,
            actor_display_name=self.display_name,
        )


def _days_ago_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()


def load_topics(path: Path, actor_override: Optional[str] = None) -> list[Topic]:
    """Episodes from the fixture, optionally re-keyed to a different address.

    The actor is the metadata key `memory_read` filters on, and it has to match
    the address the live complaint actually arrives with, exactly. Which address
    that is depends on how the complaint is delivered - a Slack message resolves
    to the poster's workspace address, an email to whatever account sent it - and
    that is not known until the demo is set up. `--actor` re-keys every episode
    so the fixture does not have to be edited, and a wrong guess baked into the
    file is the worst option: it reads back as zero prior contacts, which is a
    legitimate answer and therefore fails silently.
    """
    raw = json.loads(path.read_text())
    topics: list[Topic] = []
    for actor_row in raw.get("actors", []):
        actor = actor_override or str(actor_row["actor"])
        display = str(actor_row.get("display_name") or "")
        for topic_row in actor_row.get("topics", []):
            topics.append(Topic(actor, display, topic_row))
    return topics


# --- tenant -------------------------------------------------------------------


def memory_rows(client: hydra_db.HydraDB) -> Optional[int]:
    """Rows in the memory collection, or None when the count is unknown.

    None rather than zero on failure: "we could not count" and "there is
    nothing there" are the two answers this script exists to keep apart.
    """
    try:
        stats = client.databases.stats(database=config.HYDRA_DATABASE)
        collection = getattr(stats.data, "memory_collection", None)
        count = getattr(collection, "row_count", None)
    except Exception:
        return None
    return None if count is None else int(count)


def wait_for_rows(client: hydra_db.HydraDB, minimum: int) -> tuple[Optional[int], bool]:
    """Poll the row count until it reaches `minimum` and stops moving.

    Returns `(count, settled)`. `settled` False means the timeout was hit while
    the count was still short or still climbing, and anything read afterwards
    is a lower bound rather than the finished state.
    """
    deadline = time.monotonic() + INDEX_TIMEOUT_SECONDS
    last: Optional[int] = None
    unchanged = 0
    while time.monotonic() < deadline:
        count = memory_rows(client)
        if count is not None and count == last:
            unchanged += 1
        else:
            unchanged = 0
        last = count
        if last is not None and last >= minimum and unchanged >= STABLE_POLLS:
            return last, True
        time.sleep(POLL_INTERVAL_SECONDS)
    return last, False


def purge(client: hydra_db.HydraDB, topics: list[Topic]) -> int:
    """Delete the memories this fixture defines. Returns how many were removed.

    Scoped to the seeded ids on purpose: a rehearsal reset must not take out
    memories the pipeline wrote for real complaints.
    """
    ids = [e.memory_id for t in topics for e in t.episodes]
    removed = 0
    for memory_id in ids:
        try:
            client.context.delete(
                database=config.HYDRA_DATABASE,
                collection=memory.MEMORY_COLLECTION,
                type="memory",
                ids=[memory_id],
            )
            removed += 1
            print(f"  deleted {memory_id}")
        except Exception as exc:
            print(f"  FAILED  {memory_id}: {str(exc)[-160:]}")
    return removed


# --- reporting ----------------------------------------------------------------


def describe(record: MemoryWriteInput) -> str:
    return (
        f"    actor       {record.actor}\n"
        f"    complaint   {record.complaint_id}\n"
        f"    category    {record.category}\n"
        f"    resolved    {record.resolved_at}\n"
        f"    ticket      {record.ticket_url or '-'}\n"
        f"    actions     {', '.join(record.action_taken) or '-'}\n"
        f"    summary     {record.complaint_summary}"
    )


def expected_tone(count: int, actor: str, profile: Any) -> str:
    """The tone the profile's thresholds imply for `count` prior contacts.

    Asked of `derive_reply_tone` rather than compared against a number here, so
    the acceptance check retunes with the profile instead of contradicting it.
    """
    return memory.derive_reply_tone(
        MemoryReadResult(
            actor=actor,
            times_reported_by_actor=count,
            times_seen_on_topic=count,
        ),
        profile,
    )


def verify(topic: Topic, profile: Any) -> bool:
    """Read the seeded history back and print what the system actually recalls."""
    probe = topic.probe()
    wanted = len(topic.episodes)
    deadline = time.monotonic() + INDEX_TIMEOUT_SECONDS
    while True:
        result = memory.memory_read(probe, list(COMPLAINT_SOURCES), profile)
        if result.times_reported_by_actor >= wanted or time.monotonic() >= deadline:
            break
        time.sleep(POLL_INTERVAL_SECONDS)

    tone = memory.derive_reply_tone(result, profile)
    target = expected_tone(wanted, topic.actor, profile)
    # What the demo will see when the third contact lands live. Printed because
    # the seeded state alone is one contact short of the moment being rehearsed.
    live_tone = expected_tone(wanted + 1, topic.actor, profile)

    print(f"\n  read back for {topic.actor} on {topic.id}:")
    print(f"    times_reported_by_actor  {result.times_reported_by_actor}")
    print(f"    times_seen_on_topic      {result.times_seen_on_topic}")
    print(f"    sources_used             {', '.join(result.sources_used)}")
    print(f"    stub                     {result.stub}")
    print(f"    reply tone               {tone} (expected {target} for {wanted} episode(s))")
    print(f"    tone on the live contact {live_tone} (at {wanted + 1} contacts)")
    if memory.last_read_truncated():
        print("    WARNING: the read hit its result ceiling; counts are a lower bound")
    if result.likely_regression:
        print(f"    regression               {result.likely_regression.ref}")
    if not result.prior_resolutions:
        print("    prior resolutions        none recalled")
    for prior in result.prior_resolutions:
        print(f"    prior resolution         {prior.resolved_at}  {prior.ticket_url}")
        print(f"                             {prior.summary[:120]}")

    ok = True
    if result.times_reported_by_actor < wanted:
        print(
            f"    FAIL: {wanted} episode(s) seeded but only "
            f"{result.times_reported_by_actor} recalled for this actor"
        )
        ok = False
    if tone != target:
        print(f"    FAIL: tone is {tone}, the profile's thresholds imply {target}")
        ok = False
    return ok


# --- entry point --------------------------------------------------------------


def resolve_mode(args: argparse.Namespace) -> Optional[bool]:
    """Decide whether writes are live, and say so. None means refuse to run.

    `memory_write` reads `config.DRY_RUN` at call time, so flipping it here is
    what makes the write real; the `.env` file is not touched and nothing else
    in the process is affected. An already-live `DRY_RUN=false` environment
    needs no flag, which is the env override.
    """
    if args.dry_run:
        # Pinned rather than left alone: with DRY_RUN=false in the environment,
        # a --dry-run that quietly wrote to the shared tenant would be worse
        # than no dry run at all.
        config.DRY_RUN = True
        print("MODE: dry run. Nothing will be written. Ids come back DRY-prefixed.")
        return False
    if not config.DRY_RUN:
        print("MODE: live. DRY_RUN is already false in the environment.")
        return True
    if args.force:
        config.DRY_RUN = False
        print("MODE: live. DRY_RUN was true and --force overrode it for this process.")
        return True
    print(
        "REFUSING TO RUN: DRY_RUN is true, so memory_write would return DRY- ids "
        "and persist nothing.\nPass --force to write for real, or --dry-run to see "
        "the payloads."
    )
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print payloads, write nothing")
    parser.add_argument("--force", action="store_true", help="write for real, overriding DRY_RUN")
    parser.add_argument("--purge", action="store_true", help="delete the seeded memories and exit")
    parser.add_argument(
        "--actor",
        help=(
            "re-key every episode to this address. Must match the author_email the "
            "live complaint arrives with, or the memory reads back as zero prior contacts"
        ),
    )
    parser.add_argument("--fixture", default=str(SEED_PATH), help="path to the seed fixture")
    args = parser.parse_args()

    path = Path(args.fixture)
    if not path.exists():
        print(f"no seed fixture at {path}")
        return 2
    topics = load_topics(path, actor_override=args.actor)
    episodes = [e for t in topics for e in t.episodes]
    print(f"{path.relative_to(ROOT) if path.is_absolute() else path}: "
          f"{len(episodes)} episode(s) across {len(topics)} topic(s)")

    live = resolve_mode(args)
    if live is None:
        return 2
    if live and not config.HYDRA_TOKEN:
        print("REFUSING TO RUN: no HYDRA_TOKEN, so nothing can be written or verified.")
        return 2

    client = hydra_db.HydraDB(token=config.HYDRA_TOKEN) if config.HYDRA_TOKEN else None
    before = memory_rows(client) if client else None
    print(f"memory rows before: {'unknown' if before is None else before}")

    if args.purge:
        if not live:
            for episode in episodes:
                print(f"  would delete {episode.memory_id}")
            return 0
        removed = purge(client, topics)
        # Deletion is indexed asynchronously too, so the count is polled for
        # stability rather than read once and believed.
        after, settled = wait_for_rows(client, minimum=0)
        print(f"purged {removed} memory id(s); rows now {'unknown' if after is None else after}"
              f"{'' if settled else ' (count had not settled before the timeout)'}")
        return 0

    written: list[str] = []
    for topic in topics:
        print(f"\n{topic.actor} / {topic.id}")
        for episode in topic.episodes:
            print(f"  {episode.complaint_id}  ({episode.days_ago}d ago)")
            print(describe(episode.record))
            memory_id = memory.memory_write(episode.record)
            if memory_id is None:
                print("    -> WRITE FAILED (memory_write returned None)")
                continue
            print(f"    -> {memory_id}")
            written.append(memory_id)

    if not live:
        print(f"\ndry run: {len(written)} payload(s) composed, none persisted. "
              "Re-run with --force to write.")
        return 0

    if len(written) != len(episodes):
        print(f"\n{len(episodes) - len(written)} episode(s) failed to write; not verifying "
              "against a partial seed")
        return 1

    print(f"\nwaiting for indexing (up to {INDEX_TIMEOUT_SECONDS:.0f}s)...")
    after, settled = wait_for_rows(client, minimum=len(episodes))
    print(f"memory rows after: {'unknown' if after is None else after}"
          f"{'' if settled else ' (still moving when the timeout hit)'}")

    profile = get_profile(force_refresh=True)
    print(f"profile origin: {profile.origin}, "
          f"reply_tone_thresholds: {profile.reply_tone_thresholds}")

    ok = all(verify(topic, profile) for topic in topics)
    final = memory_rows(client)
    print(f"\ntenant holds {'unknown' if final is None else final} memory row(s)")
    print("seed verified" if ok else "SEED NOT VERIFIED - see the failures above")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
