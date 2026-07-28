"""InsForge-backed intent profile.

Read at call time, every time, with a short TTL. This is what makes the
"change the policy mid-demo" moment possible: edit the row in InsForge, re-run
the pipeline, watch verdicts and actions change without touching code.

InsForge has no first-class "intent profile" primitive, so the profile is a row
in a normal table with a `json` column, addressed by key. The local JSON file is
the seed and the offline fallback, never the source of truth when InsForge is
configured.

Three transports, tried in order
-------------------------------
1. The `policy-read` edge function (`insforge/edge_functions/policy_read.ts`).
   This is node N4 as the PRD specifies it, and the only one of the three that
   does not need the admin credential -- which is why RocketRide's graph calls
   the same endpoint.
2. The `intent_profiles` table directly. Same document, one fewer hop.
3. `insforge/intent_profile.json` on disk. A seed a human wrote and reviewed,
   used only when InsForge cannot be reached at all.

The first two both report `origin: "insforge"`, because both of them *are*
InsForge and the UI keys its live/degraded badge off that exact string. Which
one actually answered is available separately from `policy_transport()`; it is
a fact about plumbing, not about whether the policy is live.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Optional

import httpx

from . import config
from .contracts import IntentProfile

_lock = threading.Lock()
_cache: dict[str, Any] = {"profile": None, "fetched_at": 0.0, "transport": "unread"}


def _headers() -> dict[str, str]:
    # InsForge's docs and its own SDK disagree on which header authenticates an
    # admin call, so send both. Harmless, and covers either path.
    return {
        "X-API-Key": config.INSFORGE_API_KEY,
        "Authorization": f"Bearer {config.INSFORGE_API_KEY}",
        "Content-Type": "application/json",
    }


def _load_local() -> dict[str, Any]:
    with open(config.LOCAL_PROFILE_PATH, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    raw["origin"] = f"local:{config.LOCAL_PROFILE_PATH.name}"
    return raw


def _load_policy_function() -> Optional[dict[str, Any]]:
    """Node N4: the policy as served by the `policy-read` edge function.

    Invoked without credentials on purpose. The admin key stays inside InsForge
    and the function reads the table on the caller's behalf, which is the whole
    reason the function exists. Any failure returns None and the caller drops to
    the direct table read, so a cold isolate costs a few hundred milliseconds
    and never costs a policy.
    """
    if not (config.INSFORGE_BASE_URL and config.INSFORGE_POLICY_FUNCTION):
        return None
    url = f"{config.INSFORGE_BASE_URL.rstrip('/')}/functions/{config.INSFORGE_POLICY_FUNCTION}"
    try:
        resp = httpx.get(
            url,
            params={"key": config.INSFORGE_PROFILE_KEY},
            timeout=config.INSFORGE_FUNCTION_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    # The function answers 200 with an `error` key for "no such profile". That is
    # not a document, and there is no partial policy worth salvaging out of it.
    raw = body.get("profile")
    if body.get("error") or not isinstance(raw, dict) or not raw:
        return None
    raw = dict(raw)
    raw["origin"] = "insforge"
    return raw


def _load_insforge() -> Optional[dict[str, Any]]:
    if not (config.INSFORGE_BASE_URL and config.INSFORGE_API_KEY):
        return None
    url = f"{config.INSFORGE_BASE_URL.rstrip('/')}/api/database/records/{config.INSFORGE_TABLE}"
    params = {
        "key": f"eq.{config.INSFORGE_PROFILE_KEY}",
        "select": "value",
        "limit": "1",
    }
    try:
        resp = httpx.get(url, params=params, headers=_headers(), timeout=5.0)
        resp.raise_for_status()
        rows = resp.json()
    except Exception:
        return None
    if not rows:
        return None
    raw = rows[0].get("value") or {}
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not raw:
        return None
    raw["origin"] = "insforge"
    return raw


def policy_transport() -> str:
    """Which of the three read paths served the cached profile.

    Not on IntentProfile: that shape is a frozen cross-team contract, and this is
    plumbing rather than policy. `/health` reports it so a judge can see that N4
    is genuinely going through the edge function rather than being described as
    though it does.
    """
    with _lock:
        return _cache["transport"]


def get_profile(force_refresh: bool = False) -> IntentProfile:
    """The only supported way to read policy anywhere in the codebase."""
    now = time.time()
    with _lock:
        fresh = (now - _cache["fetched_at"]) < config.INTENT_PROFILE_TTL_SECONDS
        if _cache["profile"] is not None and fresh and not force_refresh:
            return _cache["profile"]

        # N4 first, then the table, then the reviewed seed on disk. See the
        # module docstring for why the first two are both `origin: insforge`.
        raw = _load_policy_function()
        transport = "edge_function"
        if raw is None:
            raw = _load_insforge()
            transport = "table"
        if raw is None:
            raw = _load_local()
            transport = "local_file"

        profile = IntentProfile.from_dict(raw)
        _cache["profile"] = profile
        _cache["fetched_at"] = now
        _cache["transport"] = transport
        return profile


def push_profile(raw: dict[str, Any]) -> dict[str, Any]:
    """Write the profile back to InsForge (and mirror locally).

    Used by the demo UI's policy editor and by `scripts/seed_insforge.py`.
    """
    payload = dict(raw)
    payload.pop("origin", None)

    with open(config.LOCAL_PROFILE_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=False)
        fh.write("\n")

    result: dict[str, Any] = {"local": True, "insforge": False}
    if config.INSFORGE_BASE_URL and config.INSFORGE_API_KEY:
        base = config.INSFORGE_BASE_URL.rstrip("/")
        url = f"{base}/api/database/records/{config.INSFORGE_TABLE}"
        repr_headers = {**_headers(), "Prefer": "return=representation"}
        try:
            # Update first. Asking for the representation back is the only way to
            # tell "updated one row" from "matched nothing" - a bare PATCH
            # answers 204 with an empty body in both cases.
            patch = httpx.patch(
                url,
                params={"key": f"eq.{config.INSFORGE_PROFILE_KEY}"},
                headers=repr_headers,
                json={"value": payload},
                timeout=8.0,
            )
            updated = []
            if patch.status_code < 300 and patch.content:
                updated = patch.json()

            if not updated:
                insert = httpx.post(
                    url,
                    headers=repr_headers,
                    json=[{"key": config.INSFORGE_PROFILE_KEY, "value": payload}],
                    timeout=8.0,
                )
                insert.raise_for_status()
            result["insforge"] = True
        except Exception as exc:  # surfaced in the UI, never fatal
            result["error"] = str(exc)

    with _lock:
        _cache["profile"] = None
        _cache["fetched_at"] = 0.0
        _cache["transport"] = "unread"
    return result
