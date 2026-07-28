"""InsForge-backed intent profile.

Read at call time, every time, with a short TTL. This is what makes the
"change the policy mid-demo" moment possible: edit the row in InsForge, re-run
the pipeline, watch verdicts and actions change without touching code.

InsForge has no first-class "intent profile" primitive, so the profile is a row
in a normal table with a `json` column, addressed by key. The local JSON file is
the seed and the offline fallback, never the source of truth when InsForge is
configured.
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
_cache: dict[str, Any] = {"profile": None, "fetched_at": 0.0}


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


def get_profile(force_refresh: bool = False) -> IntentProfile:
    """The only supported way to read policy anywhere in the codebase."""
    now = time.time()
    with _lock:
        fresh = (now - _cache["fetched_at"]) < config.INTENT_PROFILE_TTL_SECONDS
        if _cache["profile"] is not None and fresh and not force_refresh:
            return _cache["profile"]

        raw = _load_insforge()
        if raw is None:
            raw = _load_local()

        profile = IntentProfile.from_dict(raw)
        _cache["profile"] = profile
        _cache["fetched_at"] = now
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
    return result
