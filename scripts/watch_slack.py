#!/usr/bin/env python3
"""Watch Slack and run the pipeline on anything new.

Production would take Slack Events over a webhook. This polls instead, because
a webhook needs a public URL and an app subscription, and on a locked-down
network that is a longer road than it looks.

Everything already in the channel at start-up is marked seen and never acted
on, so starting the watcher does not fire twelve tickets at whatever is already
there. Only messages posted after it starts are processed, once each.

    python scripts/watch_slack.py                 # poll every 5s
    python scripts/watch_slack.py --interval 3
    python scripts/watch_slack.py --catch-up      # also act on what is already there
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx  # noqa: E402

from ghostthread import config, connectors  # noqa: E402

API = "http://127.0.0.1:8000"


def slack_now() -> dict[str, object]:
    try:
        return {c.id: c for c in connectors.fetch_slack()}
    except Exception as exc:
        print(f"  ! slack fetch failed: {str(exc)[:120]}")
        return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--api", default=API)
    ap.add_argument("--catch-up", action="store_true",
                    help="also process messages already in the channel")
    args = ap.parse_args()

    try:
        health = httpx.get(f"{args.api}/health", timeout=10).json()
    except Exception as exc:
        raise SystemExit(f"engine not reachable at {args.api}: {exc}")

    dry = health.get("capabilities", {}).get("dry_run", True)
    print(f"engine up. DRY_RUN={dry}  ({'nothing will be sent' if dry else 'WRITES ARE LIVE'})")

    seen = set() if args.catch_up else set(slack_now())
    print(f"watching #slack every {args.interval:g}s. {len(seen)} existing message(s) ignored.")
    print("post something in Slack -- Ctrl+C to stop.\n")

    while True:
        try:
            current = slack_now()
            for msg_id, c in current.items():
                if msg_id in seen:
                    continue
                seen.add(msg_id)
                text = c.text.replace("\n", " ")[:70]
                print(f"NEW  {text}")
                try:
                    r = httpx.post(
                        f"{args.api}/complaint",
                        json={
                            "text": c.text,
                            "source": "slack",
                            "author_email": c.author_email or "unknown@testteam.dev",
                            "channel_or_thread": c.channel_or_thread,
                            # Slack's own message id, which the Slack connector
                            # keys on too. Sending it means a restart of this
                            # watcher, a second watcher, or a later /run over
                            # the same channel all resolve to one complaint
                            # rather than filing a ticket each.
                            "external_id": c.id,
                        },
                        timeout=300,
                    )
                    r.raise_for_status()
                    for a in r.json().get("actions", []):
                        if a.get("idempotent_replay"):
                            # N8 recognised this complaint. Nothing was filed a
                            # second time; the original outcome is replayed.
                            print("     replay     already handled -- no new ticket, no new PR")
                            continue
                        f = a.get("facts", {})
                        print(f"     category   {f.get('category')} ({f.get('confidence')})")
                        print(f"     tone       {f.get('reply_tone')}  prior contacts {f.get('times_reported_by_actor')}")
                        print(f"     actions    {a.get('actions_taken')}")
                        print(f"     ticket     {a.get('ticket_created_id')} {a.get('ticket_url') or ''}")
                        print(f"     PR         {a.get('fix_pr_url') or a['meta'].get('fix', {}).get('blocked') or 'none'}")
                        print(f"     replied    {a.get('reply_sent')}")
                    if not r.json().get("actions"):
                        print("     no action taken (classified as noise, or no tracked work missing)")
                except Exception as exc:
                    print(f"     ! run failed: {str(exc)[:160]}")
                print()
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nstopped.")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
