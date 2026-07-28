#!/usr/bin/env python3
"""Deploy (and verify) GhostThread's InsForge edge functions.

    PYTHONPATH=src python scripts/deploy_edge_functions.py           # deploy + verify
    PYTHONPATH=src python scripts/deploy_edge_functions.py --check   # verify only

Why this exists instead of `npx @insforge/cli functions deploy`
---------------------------------------------------------------
The CLI path in the PRD needs an interactive `npx @insforge/cli login`. The REST
surface does not: `POST /api/functions` and `PUT /api/functions/{slug}` accept
the same `INSFORGE_API_KEY` that `intent.py` and `seed_insforge.py` already use,
so this runs unattended and in CI. If you would rather use the CLI, the two
files under `insforge/edge_functions/` are the same artefacts it would upload.

What the functions get for free
-------------------------------
The Deno runtime injects `INSFORGE_BASE_URL`, `API_KEY` and `ANON_KEY` into the
function environment. `API_KEY` is the admin credential and is what both
functions use to read their tables; `ANON_KEY` is rejected by the database REST
surface (401 AUTH_INVALID_API_KEY), which is *why* these functions exist -- the
credential stays inside InsForge and callers get a public read endpoint.

Note the two spellings. Files are `policy_read.ts` / `memory_dashboard.ts` to
match the build plan; slugs are `policy-read` / `memory-dashboard` because the
invoke path is a URL. The mapping is mechanical, not a lookup table, so a third
function needs no edit here.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Optional

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ghostthread import config  # noqa: E402

FUNCTIONS_DIR = ROOT / "insforge" / "edge_functions"
_TIMEOUT = 60.0

# What each function must be able to answer for the deployment to count as
# working. Verification invokes the live endpoint and checks a real key is
# present -- a 200 carrying an error body is not a passing deploy.
EXPECTED_KEYS = {
    "policy-read": "category_policy",
    "memory-dashboard": "recurring_reporters",
}


def headers() -> dict[str, str]:
    return {
        "X-API-Key": config.INSFORGE_API_KEY,
        "Authorization": f"Bearer {config.INSFORGE_API_KEY}",
        "Content-Type": "application/json",
    }


def base_url() -> str:
    return config.INSFORGE_BASE_URL.rstrip("/")


def slug_for(path: Path) -> str:
    return path.stem.replace("_", "-")


def discover() -> list[Path]:
    return sorted(FUNCTIONS_DIR.glob("*.ts"))


def first_docstring_line(source: str) -> str:
    """The one-line description, taken from the file's own header comment."""
    for line in source.splitlines():
        stripped = line.strip().lstrip("*").strip()
        if stripped and not stripped.startswith("/**") and not stripped.startswith("//"):
            return stripped.rstrip(".")[:200]
    return "GhostThread edge function"


def deploy(path: Path) -> tuple[bool, str]:
    """Create, or update if it is already there. Idempotent either way."""
    slug = slug_for(path)
    source = path.read_text(encoding="utf-8")
    payload: dict[str, Any] = {
        "name": slug.replace("-", " ").title(),
        "slug": slug,
        "description": first_docstring_line(source),
        "code": source,
        "status": "active",
    }

    try:
        created = httpx.post(f"{base_url()}/api/functions", headers=headers(), json=payload, timeout=_TIMEOUT)
    except Exception as exc:
        return False, f"create failed: {type(exc).__name__}: {exc}"

    if created.is_success:
        return True, "created"

    # 409 is the slug already existing, which is the normal path on redeploy.
    if created.status_code == 409 or "already exists" in created.text.lower():
        try:
            updated = httpx.put(
                f"{base_url()}/api/functions/{slug}",
                headers=headers(),
                json={k: v for k, v in payload.items() if k != "slug"},
                timeout=_TIMEOUT,
            )
        except Exception as exc:
            return False, f"update failed: {type(exc).__name__}: {exc}"
        if updated.is_success:
            return True, "updated"
        return False, f"update rejected: HTTP {updated.status_code} {updated.text[:300]}"

    return False, f"create rejected: HTTP {created.status_code} {created.text[:300]}"


def verify(slug: str) -> tuple[bool, str]:
    """Invoke the deployed function for real and look at what came back.

    Invocation is deliberately unauthenticated: these are public read endpoints,
    and if one only answers with an admin key then the thing we claim to have
    built -- a surface that serves policy without handing out the credential --
    does not exist.
    """
    url = f"{base_url()}/functions/{slug}"
    try:
        resp = httpx.get(url, timeout=_TIMEOUT)
    except Exception as exc:
        return False, f"unreachable: {type(exc).__name__}: {exc}"

    if not resp.is_success:
        return False, f"HTTP {resp.status_code}: {resp.text[:300]}"

    try:
        body = resp.json()
    except Exception:
        return False, f"HTTP 200 but the body was not JSON: {resp.text[:200]}"

    if isinstance(body, dict) and body.get("error"):
        return False, f"HTTP 200 carrying an error: {body['error']}"

    expected = EXPECTED_KEYS.get(slug)
    if expected and (not isinstance(body, dict) or expected not in body):
        return False, f"response is missing {expected!r}: {str(body)[:200]}"

    return True, _summarise(slug, body)


def _summarise(slug: str, body: Any) -> str:
    if not isinstance(body, dict):
        return "ok"
    if slug == "policy-read":
        return (
            f"{body.get('categories_defined')} categories, "
            f"version {body.get('version')}, origin {body.get('origin')}"
        )
    if slug == "memory-dashboard":
        totals = body.get("totals") or {}
        window = body.get("window") or {}
        return (
            f"{totals.get('resolutions_logged')} resolutions, "
            f"{totals.get('distinct_reporters')} reporters, "
            f"{len(body.get('recurring_reporters') or [])} recurring"
            + (" (window truncated)" if window.get("truncated") else "")
        )
    return "ok"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify what is deployed; upload nothing")
    parser.add_argument("--only", help="operate on one slug only")
    args = parser.parse_args()

    if not (config.INSFORGE_BASE_URL and config.INSFORGE_API_KEY):
        print("set INSFORGE_BASE_URL and INSFORGE_API_KEY in .env first", file=sys.stderr)
        return 2

    paths = [p for p in discover() if not args.only or slug_for(p) == args.only]
    if not paths:
        print(f"no .ts functions found in {FUNCTIONS_DIR}", file=sys.stderr)
        return 2

    problems = 0
    for path in paths:
        slug = slug_for(path)

        if not args.check:
            ok, detail = deploy(path)
            print(f"[{'OK  ' if ok else 'FAIL'}] deploy   {slug:18} {detail}")
            if not ok:
                problems += 1
                continue

        ok, detail = verify(slug)
        print(f"[{'OK  ' if ok else 'FAIL'}] verify   {slug:18} {detail}")
        print(f"                            {base_url()}/functions/{slug}")
        if not ok:
            problems += 1

    print()
    if problems:
        print(f"{problems} problem(s)")
        return 1
    print(f"{len(paths)} edge function(s) live")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
