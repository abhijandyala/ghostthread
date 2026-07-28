"""Environment plumbing. No behavioural constants live here.

Anything that changes *what GhostThread decides* belongs in the InsForge intent
profile, not in this file. This module only knows about credentials, hostnames
and degraded-mode switches.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# --- HydraDB (real-time grounding) ------------------------------------------
HYDRA_TOKEN = os.getenv("HYDRA_TOKEN", "")
HYDRA_DATABASE = os.getenv("HYDRA_DATABASE", "ghostthread")
HYDRA_TENANT_ID = os.getenv("HYDRA_TENANT_ID", "ghostthread")
HYDRA_BASE_URL = os.getenv("HYDRA_BASE_URL") or None

# --- Anthropic (model specialisation, two models) ---------------------------
# Pipeshift was the original provider here and has been removed: the account had
# no usable key, and an integration nobody can run is a claim rather than a
# feature. The specialisation argument is unchanged and is now made with two
# Anthropic models rather than two Pipeshift deployments -- classification wants
# a small fast model, code generation wants a model that reasons about code.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")


def _model(name: str, default: str) -> str:
    """A blank env var means "not configured", not "use the empty string".

    `PIPESHIFT_MODEL=` in `.env` used to override the code default with "",
    which sent an empty model id to the API. An unset variable and a variable
    set to nothing are the same intent, so both fall through to the default.
    """
    return (os.getenv(name) or "").strip() or default


# N3, classification: every complaint goes through a constrained decode against
# a fixed JSON schema. Small and fast, because the shape is what matters.
EXTRACTION_MODEL = _model("EXTRACTION_MODEL", "claude-haiku-4-5")
# N6, code generation: a diff, an explanation and a confidence. Reasoning about
# a codebase is the whole task, so this is the strong model.
CODING_AGENT_MODEL = _model("CODING_AGENT_MODEL", "claude-opus-5")
# Thinking is on by default on the code model and `max_tokens` caps thinking
# plus response together, so this has to leave room for both.
CODING_AGENT_MAX_TOKENS = int(os.getenv("CODING_AGENT_MAX_TOKENS", "8000"))
EXTRACTION_MAX_TOKENS = int(os.getenv("EXTRACTION_MAX_TOKENS", "1024"))
# Effort on the code model. Not supported by the small classification model, so
# it is only ever sent on the code call.
CODING_AGENT_EFFORT = _model("CODING_AGENT_EFFORT", "medium")

# --- InsForge (intent profile / governance) ---------------------------------
INSFORGE_BASE_URL = os.getenv("INSFORGE_BASE_URL", "")
INSFORGE_API_KEY = os.getenv("INSFORGE_API_KEY", "")
INSFORGE_TABLE = os.getenv("INSFORGE_TABLE", "intent_profiles")
INSFORGE_PROFILE_KEY = os.getenv("INSFORGE_PROFILE_KEY", "ghostthread")
# Pipeline node N8. UNIQUE on complaint_id is what makes a retried webhook safe
# across instances; see actions_log.py.
INSFORGE_ACTIONS_TABLE = os.getenv("INSFORGE_ACTIONS_TABLE", "actions_log")
# An absent actions_log table is a misprovisioned project, not an outage, and
# silently downgrading the idempotency guarantee over it is the regression this
# flag exists to prevent. Set false to require scripts/seed_insforge.py instead.
INSFORGE_AUTO_PROVISION = _flag("INSFORGE_AUTO_PROVISION", default=True)
INTENT_PROFILE_TTL_SECONDS = float(os.getenv("INTENT_PROFILE_TTL_SECONDS", "5"))
# Edge functions. `policy-read` is pipeline node N4's transport as the PRD
# specifies it: the policy is served over a public endpoint so a caller does not
# need the admin credential. Set INSFORGE_POLICY_FUNCTION empty to read the table
# directly instead -- same document, one fewer hop. Both are labelled `insforge`
# because both are InsForge; `intent.policy_transport()` says which one answered.
INSFORGE_POLICY_FUNCTION = os.getenv("INSFORGE_POLICY_FUNCTION", "policy-read")
INSFORGE_DASHBOARD_FUNCTION = os.getenv("INSFORGE_DASHBOARD_FUNCTION", "memory-dashboard")
# Short on purpose. A cold Deno isolate must cost latency, never the policy: the
# direct table read is waiting behind it.
INSFORGE_FUNCTION_TIMEOUT = float(os.getenv("INSFORGE_FUNCTION_TIMEOUT", "4"))
LOCAL_PROFILE_PATH = Path(
    os.getenv("LOCAL_PROFILE_PATH", REPO_ROOT / "insforge" / "intent_profile.json")
)

SANDBOX_REPO = Path(os.getenv("SANDBOX_REPO", REPO_ROOT / "sandbox_repo"))

# --- Write-side connectors ---------------------------------------------------
LINEAR_TOKEN = os.getenv("LINEAR_TOKEN", "")
LINEAR_TEAM_ID = os.getenv("LINEAR_TEAM_ID", "")
SLACK_TOKEN = os.getenv("SLACK_TOKEN", "")

# Gmail: a static access token dies after an hour, so the refresh token is the
# credential that actually matters. GMAIL_TOKEN stays supported as an override.
GMAIL_TOKEN = os.getenv("GMAIL_TOKEN", "")
GMAIL_REFRESH_TOKEN = os.getenv("GMAIL_REFRESH_TOKEN", "")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GMAIL_USER = os.getenv("GMAIL_USER", "me")
# HydraDB's gmail connector authenticates over IMAP with an app password, not
# OAuth. The OAuth credentials above are still what we use to *send* replies.
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")

# Written by scripts/setup_connectors.py. Maps provider -> connector_id, which
# is what the kill shot filters on.
CONNECTOR_STATE_PATH = Path(
    os.getenv("CONNECTOR_STATE_PATH", REPO_ROOT / "state" / "connectors.json")
)
HYDRA_LOOKBACK_DAYS = int(os.getenv("HYDRA_LOOKBACK_DAYS", "30"))
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")  # "owner/name"

# --- RocketRide Cloud (managed pipeline) ------------------------------------
ROCKETRIDE_URI = os.getenv("ROCKETRIDE_URI", "https://api.rocketride.ai")
ROCKETRIDE_APIKEY = os.getenv("ROCKETRIDE_APIKEY", "")
# The public URL of *this* service, as RocketRide's tool_http_request node must
# be able to reach it. It is not a literal in ghostthread.pipe: the pipeline
# refers to ${ROCKETRIDE_GHOSTTHREAD_URL} and the engine resolves that from the
# RocketRide account environment at run time, so rotating the tunnel is an
# account setting rather than a code change. Set locally too, for the scripts
# that print or check the endpoint.
ROCKETRIDE_GHOSTTHREAD_URL = os.getenv("ROCKETRIDE_GHOSTTHREAD_URL", "")
# Port the public tunnel points at. scripts/serve_public.py binds it.
PUBLIC_PORT = int(os.getenv("PUBLIC_PORT", "8000"))

# --- Safety -----------------------------------------------------------------
# Default ON. Nothing leaves the building until a human flips this.
DRY_RUN = _flag("DRY_RUN", default=True)

FIXTURES_DIR = Path(os.getenv("FIXTURES_DIR", REPO_ROOT / "fixtures"))


def capability_report() -> dict[str, bool]:
    """What is actually wired right now. Rendered in the UI so a judge can see
    at a glance which integrations are live versus running on local transport."""
    return {
        "hydradb": bool(HYDRA_TOKEN),
        # One credential, two models, chosen per task. Both flags read the same
        # key on purpose: there is no configuration in which classification is
        # live and code generation is not, and pretending otherwise would put a
        # green badge next to something that cannot run.
        "anthropic": bool(ANTHROPIC_API_KEY),
        "extraction_model": bool(ANTHROPIC_API_KEY),
        "code_model": bool(ANTHROPIC_API_KEY),
        "insforge": bool(INSFORGE_BASE_URL and INSFORGE_API_KEY),
        # The idempotency log survives a restart only when it is in Postgres.
        "durable_idempotency": bool(INSFORGE_BASE_URL and INSFORGE_API_KEY),
        "coding_agent": bool(ANTHROPIC_API_KEY),
        "fix_generator": bool(ANTHROPIC_API_KEY),
        "linear_write": bool(LINEAR_TOKEN),
        "slack_write": bool(SLACK_TOKEN),
        "gmail_write": bool(
            GMAIL_TOKEN or (GMAIL_REFRESH_TOKEN and GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)
        ),
        "dry_run": DRY_RUN,
    }
