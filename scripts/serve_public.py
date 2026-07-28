#!/usr/bin/env python3
"""Serve GhostThread and put it on a public URL, in one command.

    PYTHONPATH=src python scripts/serve_public.py
    PYTHONPATH=src python scripts/serve_public.py --set-account-url   # also tell RocketRide
    PYTHONPATH=src python scripts/serve_public.py --no-tunnel         # localhost only

Why this exists
---------------
RocketRide Cloud's `tool_http_request` node runs on RocketRide's infrastructure,
not on this laptop, so it cannot reach `127.0.0.1:8000`. The brief also requires
the pipeline to be reachable off the builder's own network -- a local script is
explicitly disqualified. So the service needs a public origin, and
`${ROCKETRIDE_GHOSTTHREAD_URL}` in `rocketride/ghostthread.pipe` is where that
origin is named. This script produces it and tells you what to do with it.

It starts uvicorn, opens a tunnel with whichever of cloudflared or ngrok is
installed, waits for the URL, then proves the URL works by fetching `/health`
*through the tunnel* rather than through localhost. A tunnel that is up but not
routing looks identical to a working one until something calls it, and the thing
that calls it is a judge.

Neither tunnel installed
------------------------
It says so, prints the install commands, and exits non-zero rather than serving
on localhost while implying anything was published. `--no-tunnel` is available
when localhost is what you actually want.

Nothing here writes to the RocketRide account unless you pass
`--set-account-url`, which reads the current user-scope environment, merges one
key into it, and writes it back -- `account.set_env` replaces the whole scope, so
a blind write would delete every other variable on it.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ghostthread import config  # noqa: E402

URL_KEY = "ROCKETRIDE_GHOSTTHREAD_URL"
TUNNEL_URL = re.compile(r"https://[A-Za-z0-9._-]+\.(?:trycloudflare\.com|ngrok(?:-free)?\.(?:app|io|dev))")

INSTALL_HELP = """
Neither cloudflared nor ngrok is on PATH, so there is no way to publish a URL
from this machine. Install one:

  cloudflared (no account needed for a quick tunnel)
    winget install --id Cloudflare.cloudflared
    brew install cloudflared
    https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/

  ngrok (needs a free account and one-time `ngrok config add-authtoken <token>`)
    winget install ngrok.ngrok
    brew install ngrok
    https://ngrok.com/download

Then re-run this script. Use --no-tunnel if you only want localhost.
""".strip()


def find_tunnel() -> Optional[tuple[str, str]]:
    """(name, executable path) for the first tunnel we can drive, or None."""
    for name in ("cloudflared", "ngrok"):
        path = shutil.which(name)
        if path:
            return name, path
    return None


def tunnel_command(name: str, exe: str, port: int) -> list[str]:
    if name == "cloudflared":
        # `tunnel --url` is the quick tunnel: no account, no config file. It
        # prints a fresh https://<random>.trycloudflare.com on every start,
        # which is exactly why the pipeline refers to an account variable
        # rather than hardcoding a hostname.
        return [exe, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"]
    return [exe, "http", str(port), "--log", "stdout", "--log-format", "logfmt"]


def start_api(port: int) -> subprocess.Popen:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    env.setdefault("PYTHONUNBUFFERED", "1")
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "ghostthread.api:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT),
        env=env,
    )


def wait_for_local(port: int, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=3.0).is_success:
                return True
        except Exception:
            pass
        time.sleep(1.0)
    return False


def read_public_url(proc: subprocess.Popen, timeout: float = 90.0) -> Optional[str]:
    """Scrape the tunnel's own output. Both tools print the URL to stdout."""
    deadline = time.time() + timeout
    assert proc.stdout is not None
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                return None
            time.sleep(0.2)
            continue
        sys.stdout.write(f"  tunnel| {line.rstrip()}\n")
        match = TUNNEL_URL.search(line)
        if match:
            return match.group(0)
    return None


def verify_public(url: str) -> tuple[bool, str]:
    """Fetch /health through the public origin, not through localhost."""
    try:
        resp = httpx.get(f"{url.rstrip('/')}/health", timeout=30.0, follow_redirects=True)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if not resp.is_success:
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    try:
        body = resp.json()
    except Exception:
        return False, "200, but the body was not JSON"
    backends = body.get("backends") or {}
    return True, ", ".join(f"{k}={v}" for k, v in backends.items())


def set_account_url(url: str) -> tuple[bool, str]:
    """Merge ROCKETRIDE_GHOSTTHREAD_URL into the RocketRide user environment.

    Read-merge-write on purpose: `account.set_env` replaces every key at the
    scope, so writing one key blindly would delete the rest.
    """
    import asyncio

    try:
        from rocketride import RocketRideClient
    except ImportError:
        return False, "the `rocketride` package is not installed (pip install rocketride)"
    if not config.ROCKETRIDE_APIKEY:
        return False, "ROCKETRIDE_APIKEY is not set, and this script will not invent one"

    async def run() -> tuple[bool, str]:
        client = RocketRideClient(uri=config.ROCKETRIDE_URI, auth=config.ROCKETRIDE_APIKEY)
        await client.connect(timeout=30000)
        try:
            current = await client.account.get_env("user")
            merged = dict(current or {})
            previous = merged.get(URL_KEY)
            merged[URL_KEY] = url
            await client.account.set_env("user", merged)
            was = f" (was {previous})" if previous else ""
            return True, f"{URL_KEY} set on the user scope{was}; {len(merged)} key(s) preserved"
        finally:
            await client.disconnect()

    try:
        return asyncio.run(run())
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def next_steps(url: str, wrote_account: bool) -> str:
    lines = [
        "",
        "=" * 72,
        f"  GhostThread is public at  {url}",
        "=" * 72,
        "",
        "To point the RocketRide pipeline at it:",
        "",
    ]
    if wrote_account:
        lines.append(f"  1. done -- {URL_KEY} is already set on your RocketRide account")
    else:
        lines += [
            f"  1. Set {URL_KEY} on your RocketRide account environment, either in the",
            "     dashboard or by re-running this script with --set-account-url. The",
            "     pipeline resolves ${" + URL_KEY + "} from there at run time, so the URL",
            "     is never committed and rotating the tunnel needs no code change.",
        ]
    lines += [
        "",
        "  2. Set ROCKETRIDE_ANTHROPIC_KEY on the same account environment. The agent's",
        "     planner node needs it and this repository does not carry it.",
        "",
        "  3. Validate and start the pipeline:",
        "",
        "       PYTHONPATH=src python scripts/validate_pipe.py",
        "       rocketride start rocketride/ghostthread.pipe",
        "",
        "     `rocketride start` prints a task token. The public trigger is then:",
        "",
        f"       curl -X POST '{config.ROCKETRIDE_URI.rstrip('/')}/webhook?token=<TOKEN>' \\",
        "            -H 'Content-Type: application/json' \\",
        "            -d '{\"text\": \"CSV export throws a 500 again\"}'",
        "",
        "  4. Test that from a phone on cellular, not from this wifi. The brief asks",
        "     for reachable off your own network and only an off-network call proves it.",
        "",
        "Leave this process running. Ctrl-C stops the API and the tunnel together.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=config.PUBLIC_PORT)
    parser.add_argument("--no-tunnel", action="store_true", help="serve on localhost only")
    parser.add_argument(
        "--set-account-url",
        action="store_true",
        help=f"merge {URL_KEY} into the RocketRide account environment once the tunnel is up",
    )
    args = parser.parse_args()

    found = None if args.no_tunnel else find_tunnel()
    if not args.no_tunnel and found is None:
        print(INSTALL_HELP)
        return 2

    print(f"starting the API on 127.0.0.1:{args.port} ...")
    api = start_api(args.port)
    tunnel: Optional[subprocess.Popen] = None

    try:
        if not wait_for_local(args.port):
            print("the API did not come up on localhost; not opening a tunnel", file=sys.stderr)
            return 1
        print("  API is up")

        if args.no_tunnel:
            print(f"\nserving on http://127.0.0.1:{args.port} (no tunnel; this is NOT public)\n")
            api.wait()
            return 0

        name, exe = found  # type: ignore[misc]
        print(f"opening a public tunnel with {name} ...")
        tunnel = subprocess.Popen(
            tunnel_command(name, exe, args.port),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=str(ROOT),
        )

        url = read_public_url(tunnel)
        if not url:
            print(f"\n{name} did not report a public URL. Its output is above.", file=sys.stderr)
            return 1

        ok, detail = verify_public(url)
        print(f"\n  {'reachable' if ok else 'NOT REACHABLE'} through the tunnel: {detail}")
        if not ok:
            print("  the tunnel is up but not routing to the API; do not hand this URL out", file=sys.stderr)
            return 1

        wrote = False
        if args.set_account_url:
            wrote, detail = set_account_url(url)
            print(f"  RocketRide account: {'ok' if wrote else 'FAILED'} -- {detail}")

        print(next_steps(url, wrote))

        # Idle here so both children stay alive; Ctrl-C lands in the finally.
        while api.poll() is None and tunnel.poll() is None:
            time.sleep(1.0)
        return 0

    except KeyboardInterrupt:
        print("\nshutting down")
        return 0
    finally:
        for proc in (tunnel, api):
            if proc is None or proc.poll() is not None:
                continue
            try:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=10)
            except Exception:
                proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
