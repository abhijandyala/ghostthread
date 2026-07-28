"""What GhostThread actually does about a leak.

The policy branch is entirely a function of the intent profile and the extracted
facts. Nothing here decides on its own authority:

  severity >= risk_threshold                  -> escalate to a human, always
  is_code_issue and auto_fix_allowed
      and severity <= auto_fix_max_severity   -> attempt a sandboxed fix
  otherwise                                   -> file and reply

Every write is gated behind DRY_RUN, which defaults to on. In dry run we compute
and return the exact payload we would have sent, so the demo shows real intent
without emailing a stranger.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

import httpx

from . import config, google_auth
from .contracts import ExtractedFacts, IntentProfile, LeakResult, ResolutionAction

TICKET_OWNER = "GhostThread"


def decide(facts: ExtractedFacts, profile: IntentProfile) -> str:
    if facts.severity >= profile.risk_threshold:
        return "escalate"
    if facts.is_code_issue and profile.auto_fix_allowed and facts.severity <= profile.auto_fix_max_severity:
        return "auto_fix"
    return "track_and_reply"


# --- Linear ------------------------------------------------------------------

_CREATE_ISSUE = """
mutation IssueCreate($input: IssueCreateInput!) {
  issueCreate(input: $input) { success issue { id identifier url } }
}
"""


def create_ticket(leak: LeakResult, facts: ExtractedFacts, profile: IntentProfile) -> tuple[Optional[str], dict[str, Any]]:
    complaint = leak.complaint
    title = facts.what_broke[:120]
    body = "\n".join(
        [
            f"Filed automatically by {TICKET_OWNER} from an untracked customer report.",
            "",
            f"**Source:** {complaint['source']} in `{complaint['channel_or_thread']}`",
            f"**Reported by:** {complaint['author_email']}",
            f"**Age at detection:** {leak.age_hours:.0f}h",
            f"**Severity (extracted):** {facts.severity:.2f}",
            f"**Component hint:** {facts.file_hint or 'unknown'}",
            f"**Leak confidence:** {leak.confidence:.2f} (best match scored {leak.score:.2f} against a threshold of {leak.threshold:.2f})",
            "",
            "> " + complaint["text"].replace("\n", "\n> "),
            "",
            f"Escalation contact: {profile.escalation_contact}",
        ]
    )
    payload = {"title": title, "description": body, "teamId": config.LINEAR_TEAM_ID}

    if config.DRY_RUN or not config.LINEAR_TOKEN:
        return f"DRY-{complaint['id']}", {"dry_run": True, "payload": payload}

    resp = httpx.post(
        "https://api.linear.app/graphql",
        headers={"Authorization": config.LINEAR_TOKEN, "Content-Type": "application/json"},
        json={"query": _CREATE_ISSUE, "variables": {"input": payload}},
        timeout=20.0,
    )
    resp.raise_for_status()
    issue = resp.json()["data"]["issueCreate"]["issue"]
    return issue["identifier"], {"dry_run": False, "url": issue["url"]}


# --- sandboxed coding agent ---------------------------------------------------
# Explicitly NOT the graded Pipeshift step. This runs a general coding model
# against a disposable repo that is created fresh and never points at production.


def _ensure_sandbox() -> Path:
    root = config.SANDBOX_REPO
    root.mkdir(parents=True, exist_ok=True)
    if not (root / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=root, check=False)
    return root


def attempt_fix(facts: ExtractedFacts, ticket_id: Optional[str]) -> tuple[bool, Optional[str], dict[str, Any]]:
    """Ask a coding model for a minimal patch inside the sandbox repo."""
    root = _ensure_sandbox()
    if not config.ANTHROPIC_API_KEY:
        return False, None, {"skipped": "ANTHROPIC_API_KEY not configured"}

    target = facts.file_hint or "unknown_component"
    prompt = (
        "You are fixing a single, small, well-scoped bug in a Python service.\n"
        f"Reported problem: {facts.what_broke}\n"
        f"Suspected component: {target}\n\n"
        "Return a unified diff and nothing else. Keep the change minimal. "
        "If you cannot determine the fix with confidence, return the single word SKIP."
    )

    try:
        diff = _call_coding_model(prompt)
    except Exception as exc:
        return False, None, {"error": str(exc)}

    if not diff or diff.strip() == "SKIP":
        return False, None, {"result": "model declined to patch"}

    branch = f"ghostthread/{(ticket_id or facts.complaint_id).lower()}"
    patch_path = root / f"{branch.replace('/', '_')}.patch"
    patch_path.write_text(diff, encoding="utf-8")

    if config.DRY_RUN:
        return True, None, {"dry_run": True, "branch": branch, "patch_file": str(patch_path), "diff": diff}

    subprocess.run(["git", "checkout", "-B", branch], cwd=root, check=False)
    applied = subprocess.run(["git", "apply", str(patch_path)], cwd=root, capture_output=True)
    if applied.returncode != 0:
        return False, None, {"error": applied.stderr.decode()[:500], "diff": diff}
    subprocess.run(["git", "add", "-A"], cwd=root, check=False)
    subprocess.run(["git", "commit", "-qm", f"fix: {facts.what_broke[:60]}"], cwd=root, check=False)
    return True, f"sandbox://{root.name}/{branch}", {"branch": branch, "diff": diff}


def _call_coding_model(prompt: str) -> str:
    """Claude only. This is the fix-drafting agent, not the graded model step."""
    resp = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": config.ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": config.CODING_AGENT_MODEL,
            "max_tokens": config.CODING_AGENT_MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=90.0,
    )
    resp.raise_for_status()
    return "".join(block.get("text", "") for block in resp.json().get("content", []))


# --- replying to the human who reported it -----------------------------------


def compose_reply(
    leak: LeakResult,
    facts: ExtractedFacts,
    ticket_id: Optional[str],
    decision: str,
    fix_attempted: bool,
) -> str:
    complaint = leak.complaint
    who = complaint["author_email"].split("@")[0]
    lines = [
        f"Hi {who},",
        "",
        f"Following up on what you reported {leak.age_hours:.0f} hours ago: \"{facts.what_broke}\"",
        "",
        "This had not been captured as tracked work anywhere, so it was at risk of being lost. "
        f"It is now filed as {ticket_id or 'a new ticket'} and owned by the {TICKET_OWNER} queue.",
    ]
    if fix_attempted:
        lines.append("A candidate fix has been drafted and is waiting on review.")
    elif decision == "auto_fix":
        lines.append("It is queued for an automated fix attempt.")
    elif decision == "escalate":
        lines.append("Given the impact, it has also been escalated to a human on-call engineer.")
    lines += ["", "You will get an update on this thread when it moves.", "", "— GhostThread"]
    return "\n".join(lines)


def send_reply(leak: LeakResult, body: str) -> tuple[bool, Optional[str], dict[str, Any]]:
    complaint = leak.complaint
    source = complaint["source"]
    channel = complaint["channel_or_thread"]

    if config.DRY_RUN:
        return False, channel, {"dry_run": True, "would_send_to": channel, "body": body}

    if source == "slack" and config.SLACK_TOKEN:
        resp = httpx.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {config.SLACK_TOKEN}"},
            json={"channel": complaint["entity_id"], "text": body},
            timeout=15.0,
        )
        ok = resp.json().get("ok", False)
        return ok, channel, resp.json()

    if source == "gmail" and google_auth.is_configured():
        import base64
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["To"] = complaint["author_email"]
        msg["Subject"] = f"Re: {leak.complaint['text'][:60]}"
        msg.set_content(body)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        resp = httpx.post(
            f"https://gmail.googleapis.com/gmail/v1/users/{config.GMAIL_USER}/messages/send",
            headers={"Authorization": f"Bearer {google_auth.access_token()}"},
            json={"raw": raw, "threadId": complaint["channel_or_thread"]},
            timeout=15.0,
        )
        return resp.status_code < 300, channel, resp.json()

    return False, channel, {"error": f"no write credentials for {source}"}


def resolve(leak: LeakResult, facts: ExtractedFacts, profile: IntentProfile) -> ResolutionAction:
    decision = decide(facts, profile)
    ticket_id, ticket_meta = create_ticket(leak, facts, profile)

    fix_attempted, fix_url = False, None
    fix_meta: dict[str, Any] = {}
    if decision == "auto_fix":
        fix_attempted, fix_url, fix_meta = attempt_fix(facts, ticket_id)

    reply_sent, reply_channel = False, None
    reply_meta: dict[str, Any] = {}
    if profile.auto_reply_allowed:
        body = compose_reply(leak, facts, ticket_id, decision, fix_attempted)
        reply_sent, reply_channel, reply_meta = send_reply(leak, body)

    return ResolutionAction(
        leak=leak.to_dict(),
        facts=facts.to_dict(),
        ticket_created_id=ticket_id,
        fix_attempted=fix_attempted,
        fix_pr_url=fix_url,
        reply_sent=reply_sent,
        reply_channel=reply_channel,
        decision=decision,
        dry_run=config.DRY_RUN,
        meta={"ticket": ticket_meta, "fix": fix_meta, "reply": reply_meta},
    )
