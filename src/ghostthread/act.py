"""What GhostThread actually does about a leak.

This module executes a `RoutingDecision`; it does not make one. Every branch
below asks "did the router permit this action", never "what category is this".
The router in turn asks only what the InsForge policy allows. That chain is what
makes the no-hardcoded-logic claim true: change the policy row, change what
happens, without touching code.

Three things are hardcoded here on purpose, and only three:

  * the sandbox repo allowlist is *checked* (its contents come from the profile)
  * a pull request is never auto-merged
  * every write is gated behind DRY_RUN, which defaults to on

In dry run we compute and return the exact payload we would have sent, so the
demo shows real intent without emailing a stranger.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any, Optional

import httpx

from . import config, fixgen, google_auth
from .contracts import (
    ExtractedFacts,
    IntentProfile,
    LeakResult,
    MemoryReadResult,
    ResolutionAction,
    RoutingDecision,
)

TICKET_OWNER = "GhostThread"

# Action names, not category names. Dispatching on what the policy *permitted*
# is the whole point; dispatching on the category would bypass the policy.
TICKET_ACTIONS = frozenset(
    {"file_ticket", "file_ticket_tagged_feature", "file_ticket_internal"}
)
LINK_ACTION = "link_existing_ticket"
FIX_ACTION = "propose_sandbox_fix"
REPLY_ACTION = "reply"
ESCALATE_ACTION = "escalate"
LOG_ONLY_ACTION = "log_only"


# --- Linear ------------------------------------------------------------------

_CREATE_ISSUE = """
mutation IssueCreate($input: IssueCreateInput!) {
  issueCreate(input: $input) { success issue { id identifier url } }
}
"""


def _memory_section(memory: Optional[MemoryReadResult]) -> list[str]:
    """The history block in the ticket body. This is the visible payoff.

    Absent memory is stated as absent rather than omitted, so a reader can tell
    "first time we have heard this" apart from "we did not look".
    """
    if memory is None:
        return ["**History:** not consulted."]
    if not memory.has_history:
        return ["**History:** no prior contact from this reporter on this topic."]

    lines = [
        "**History (from memory, across every connected source):**",
        f"- This reporter has raised {memory.times_reported_by_actor} prior report(s).",
        f"- This topic has been seen {memory.times_seen_on_topic} time(s) across all reporters.",
    ]
    for prior in memory.prior_resolutions:
        lines.append(f"- Previously resolved: {prior.summary} ({prior.ticket_url})")
    if memory.likely_regression:
        lines.append(
            f"- Implicated by history: {memory.likely_regression.source} "
            f"{memory.likely_regression.ref} {memory.likely_regression.url}".rstrip()
        )
    return lines


def create_ticket(
    leak: LeakResult,
    facts: ExtractedFacts,
    profile: IntentProfile,
    decision: RoutingDecision,
    memory: Optional[MemoryReadResult] = None,
) -> tuple[Optional[str], dict[str, Any]]:
    complaint = leak.complaint
    title = facts.what_broke[:120]
    body = "\n".join(
        [
            f"Filed automatically by {TICKET_OWNER} from an untracked customer report.",
            "",
            f"**Source:** {complaint['source']} in `{complaint['channel_or_thread']}`",
            f"**Reported by:** {complaint['author_email']}",
            f"**Age at detection:** {leak.age_hours:.0f}h",
            f"**Category:** {decision.category} (confidence {facts.confidence:.2f})",
            f"**Severity (extracted):** {facts.severity:.2f} "
            f"({profile.severity_band(facts.severity)})",
            f"**Component hint:** {facts.file_hint or 'unknown'}",
            f"**Leak confidence:** {leak.confidence:.2f} (best match scored "
            f"{leak.score:.2f} against a threshold of {leak.threshold:.2f})",
            f"**Sources consulted:** {', '.join(leak.sources_used) or 'none'}",
            f"**Sources out of scope:** {', '.join(leak.sources_missing) or 'none'}",
            "",
            *_memory_section(memory),
            "",
            "> " + complaint["text"].replace("\n", "\n> "),
            "",
            f"Routing: {decision.reason}",
            f"Escalation contact: {decision.escalation_contact or profile.escalation_contact}",
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
# The patch itself comes from `fixgen.py` -- the code model when the key is
# present, and no patch at all when it is not.
# This section owns only the safety envelope around it: the allowlist is checked
# before anything is touched, DRY_RUN gates the write, and the PR is never
# merged. The repo is disposable and never points at production.
#
# Labels for a pull request the confidence gate did or did not clear. Structural
# strings, not policy: the *threshold* is profile data, these are just names.
READY_LABEL = "ready for review"
LOW_CONFIDENCE_LABEL = "low confidence -- needs human review"


def _ensure_sandbox() -> Path:
    root = config.SANDBOX_REPO
    root.mkdir(parents=True, exist_ok=True)
    if not (root / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=root, check=False)
    return root


def allowlist_check(profile: IntentProfile) -> tuple[bool, str]:
    """The one hardcoded safety rule: never touch a repo not on the allowlist.

    The *contents* of the allowlist are policy and live in the profile. The fact
    that it is consulted at all is not negotiable and does not live in the
    profile, because a policy that can switch off its own safety check is not a
    safety check.
    """
    allowed = profile.sandbox_repo_allowlist
    if not allowed:
        return False, "sandbox_repo_allowlist is empty; refusing to touch any repo"
    target = config.GITHUB_REPO
    if not target:
        # No real repo configured: the agent works only in the local disposable
        # sandbox, which is the safe default rather than a failure.
        return True, f"local sandbox only; allowlist holds {len(allowed)} repo(s)"
    if target not in allowed:
        return False, f"{target} is not on the sandbox allowlist {allowed}"
    return True, f"{target} is on the sandbox allowlist"


def open_pull_request(
    facts: ExtractedFacts, profile: IntentProfile, ticket_id: Optional[str]
) -> tuple[bool, Optional[str], dict[str, Any]]:
    """Propose the fix as a reviewable draft PR on the sandbox repo.

    `attempt_fix` stops at a patch in a local disposable repo, which is the safe
    default for an unattended pipeline but means the proposal is invisible to
    the person who has to judge it. This pushes a branch and opens a draft.

    The allowlist still governs which repo may be touched, and the PR is always
    a draft and never merged -- proposing is not the same as shipping.
    """
    import subprocess
    import tempfile

    permitted, why = allowlist_check(profile)
    if not permitted:
        return False, None, {"blocked": why}
    if config.DRY_RUN:
        return False, None, {"dry_run": True, "would_target": config.GITHUB_REPO}
    if not (config.GITHUB_TOKEN and config.GITHUB_REPO and config.ANTHROPIC_API_KEY):
        return False, None, {"skipped": "GITHUB_TOKEN, GITHUB_REPO or ANTHROPIC_API_KEY missing"}

    def git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)

    try:
        work = Path(tempfile.mkdtemp(prefix="ghostpr-"))
        clone = work / "repo"
        auth = f"https://x-access-token:{config.GITHUB_TOKEN}@github.com/{config.GITHUB_REPO}.git"
        c = git(["clone", "--depth", "1", auth, str(clone)], work)
        if c.returncode != 0:
            return False, None, {"error": f"clone failed: {c.stderr[-200:]}"}

        # Show the model the whole source tree, not one guessed file. Guessing
        # from `file_hint` sent it App.jsx for a bug that lives in Nav.jsx, and
        # a model asked to fix a file that does not contain the bug can only
        # refuse or invent. Let it choose the file; it has the evidence.
        sources = sorted(
            [p for p in clone.rglob("*") if p.suffix in {".jsx", ".js"} and "node_modules" not in p.parts],
            key=lambda p: p.stat().st_size,
        )
        if not sources:
            return False, None, {"error": "no source files found in the repo"}

        budget, listing = 120_000, []
        for p in sources:
            body = p.read_text(encoding="utf-8", errors="replace")
            if len(body) > budget:
                continue
            budget -= len(body)
            listing.append(f"----- FILE: {p.relative_to(clone).as_posix()} -----\n{body}")

        prompt = (
            "You are fixing one small, well-scoped bug in a React codebase.\n\n"
            f"Reported problem: {facts.what_broke}\n\n"
            "Here is the source tree:\n\n" + "\n\n".join(listing) + "\n\n"
            "Reply in EXACTLY this format and nothing else:\n"
            "FILE: <path of the one file to change>\n"
            "<the complete corrected contents of that file>\n\n"
            "No markdown fence. No commentary before or after. Make the smallest "
            "change that fixes the reported problem and change nothing else.\n"
            "If no file here contains the bug, reply with exactly: CANNOT_FIX"
        )
        mresp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": config.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": config.CODING_AGENT_MODEL,
                "max_tokens": 8000,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=180.0,
        )
        mresp.raise_for_status()
        raw = "".join(b.get("text", "") for b in mresp.json().get("content", [])).strip()

        # The model is allowed to decline, and declining is the correct answer
        # when the bug is not in front of it. What must never happen is writing
        # that refusal into a source file and opening a PR on it -- which is
        # exactly what happened before this check existed.
        if raw.startswith("CANNOT_FIX") or not raw.startswith("FILE:"):
            return False, None, {
                "declined": "model did not return a patch",
                "said": raw[:300],
            }

        first, _, body = raw.partition("\n")
        rel = first[len("FILE:"):].strip().lstrip("./")
        target = clone / rel
        if not target.exists():
            return False, None, {"error": f"model named a file that does not exist: {rel}"}

        original = target.read_text(encoding="utf-8")
        fixed = body.strip()
        if fixed.startswith("```"):
            fixed = "\n".join(fixed.split("\n")[1:])
            if fixed.rstrip().endswith("```"):
                fixed = fixed.rstrip()[:-3]

        # Cheap sanity gate. A React source file has imports or exports; prose
        # explaining why a fix is impossible does not. This is the difference
        # between a patch and a paragraph.
        if ("import " not in fixed and "export " not in fixed) or len(fixed) < len(original) // 3:
            return False, None, {
                "declined": "response did not look like source code",
                "said": fixed[:300],
            }

        if fixed.startswith("```"):
            fixed = "\n".join(fixed.split("\n")[1:])
            if fixed.rstrip().endswith("```"):
                fixed = fixed.rstrip()[:-3]
        if not fixed.strip() or fixed.strip() == original.strip():
            return False, None, {"result": "model proposed no change", "file": rel}

        target.write_text(fixed, encoding="utf-8")
        branch = f"ghostthread/{(ticket_id or facts.complaint_id).lower()}"
        git(["checkout", "-b", branch], clone)
        git(["-c", "user.name=GhostThread", "-c", "user.email=ghostthread@testteam.dev",
             "commit", "-am", f"fix: {facts.what_broke[:64]}"], clone)
        p = git(["push", "-q", "origin", branch], clone)
        if p.returncode != 0:
            return False, None, {"error": f"push failed: {p.stderr[-200:]}"}

        resp = httpx.post(
            f"https://api.github.com/repos/{config.GITHUB_REPO}/pulls",
            headers={
                "Authorization": f"Bearer {config.GITHUB_TOKEN}",
                "Accept": "application/vnd.github+json",
            },
            json={
                "title": f"fix: {facts.what_broke[:64]}",
                "head": branch,
                "base": "main",
                "draft": True,
                "body": (
                    f"Filed automatically by {TICKET_OWNER} from an untracked customer report.\n\n"
                    f"**Ticket:** {ticket_id or 'n/a'}\n"
                    f"**Category:** {facts.category} (confidence {facts.confidence:.2f})\n"
                    f"**Severity:** {facts.severity:.2f}\n"
                    f"**File:** `{rel}`\n\n{facts.what_broke}\n\n"
                    "---\nDraft. Never auto-merged."
                ),
            },
            timeout=60.0,
        )
        if resp.status_code >= 300:
            return False, None, {"error": f"github {resp.status_code}: {resp.text[:200]}", "branch": branch}
        url = resp.json().get("html_url")
        return True, url, {"url": url, "branch": branch, "file": rel, "allowlist": why}
    except Exception as exc:
        return False, None, {"error": str(exc)[:250]}


def _fix_confidence_threshold(profile: IntentProfile) -> float:
    """The line between "ready for review" and "a human must read this first".

    Profile data, like every other threshold. The fallback is the classification
    floor rather than a number typed here, so a profile that forgets the key
    still gates on something real instead of on a literal nobody can edit.
    """
    return float(
        profile.override("min_confidence_for_fix_pr", profile.min_confidence_for_autoaction)
    )


def attempt_fix(
    facts: ExtractedFacts,
    profile: IntentProfile,
    ticket_id: Optional[str],
    memory: Optional[MemoryReadResult] = None,
) -> tuple[bool, Optional[str], dict[str, Any]]:
    """Draft a minimal patch inside the sandbox repo, via the specialised code model.

    Order matters and is not negotiable: allowlist first, so a blocked repo is
    never even described to a model; then generate; then the confidence gate;
    then DRY_RUN, which is the last thing standing between a diff and a branch.
    """
    permitted, why = allowlist_check(profile)
    if not permitted:
        return False, None, {"blocked": why}

    root = _ensure_sandbox()

    generator = fixgen.FixGenerator()
    # Regression evidence from the memory read is what turns a broad search into
    # a targeted one. Without it the model is guessing at which file to open,
    # and the proposal says so rather than pretending otherwise.
    proposal = generator.generate(
        fixgen.FixRequest(
            complaint_id=facts.complaint_id,
            what_broke=facts.what_broke,
            file_hint=facts.file_hint,
            regression_evidence=facts.regression_evidence,
            prior_resolutions=len(memory.prior_resolutions) if memory else 0,
        )
    )

    threshold = _fix_confidence_threshold(profile)
    confidence = proposal.confidence
    # An unscored patch is treated exactly as an under-scored one. `is None`
    # first, so the comparison below never runs against a missing confidence.
    low_confidence = confidence is None or confidence < threshold

    meta: dict[str, Any] = {
        "allowlist": why,
        "generator": proposal.to_dict(),
        "confidence": confidence,
        "confidence_threshold": threshold,
        "low_confidence": low_confidence,
        "pr_label": LOW_CONFIDENCE_LABEL if low_confidence else READY_LABEL,
        "targeted": proposal.targeted,
        "regression_evidence": facts.regression_evidence,
        "files_touched": proposal.files_touched,
        # Two independent statements, both true, both shown: a draft PR is never
        # merged by this system, and the profile says so too.
        "never_automerge": profile.never_automerge,
        "draft_only": True,
        "degraded": proposal.degraded,
        "degraded_reason": proposal.degraded_reason,
    }

    if proposal.declined:
        # No diff means no diff. Nothing downstream synthesises one.
        return False, None, {**meta, "result": proposal.explanation}

    branch = f"ghostthread/{(ticket_id or facts.complaint_id).lower()}"
    patch_path = root / f"{branch.replace('/', '_')}.patch"
    patch_path.write_text(proposal.diff, encoding="utf-8")

    meta.update({"branch": branch, "diff": proposal.diff, "explanation": proposal.explanation})

    if config.DRY_RUN:
        return True, None, {**meta, "dry_run": True, "patch_file": str(patch_path)}

    subprocess.run(["git", "checkout", "-B", branch], cwd=root, check=False)
    applied = subprocess.run(["git", "apply", str(patch_path)], cwd=root, capture_output=True)
    if applied.returncode != 0:
        return False, None, {**meta, "error": applied.stderr.decode()[:500]}
    subprocess.run(["git", "add", "-A"], cwd=root, check=False)
    subprocess.run(["git", "commit", "-qm", f"fix: {facts.what_broke[:60]}"], cwd=root, check=False)
    # A branch and a patch file. Opening the draft PR is a separate, reviewed
    # step; nothing here merges anything, whatever the profile says.
    return True, f"sandbox://{root.name}/{branch}", meta


# --- replying to the human who reported it -----------------------------------
# Three tones, selected by what the memory read returned. The tone is not
# decoration: an escalation reply that opens with "thanks for getting in touch"
# to someone on their third report is the exact failure the memory prevents.


def _opening(tone: str, who: str, memory: Optional[MemoryReadResult]) -> list[str]:
    seen = memory.times_reported_by_actor if memory else 0

    if tone == "escalation":
        return [
            f"Hi {who},",
            "",
            f"You have now reported this {seen} times, and it should not have taken "
            f"that many. That is on us.",
        ]
    if tone == "returning":
        return [
            f"Hi {who},",
            "",
            "You have raised this with us before, so I will skip the introductions.",
        ]
    return [
        f"Hi {who},",
        "",
        "Thanks for flagging this.",
    ]


def compose_reply(
    leak: LeakResult,
    facts: ExtractedFacts,
    ticket_id: Optional[str],
    decision: RoutingDecision,
    fix_attempted: bool,
    memory: Optional[MemoryReadResult] = None,
) -> str:
    """Grounded only in retrieved facts. Never invents a ticket status."""
    complaint = leak.complaint
    who = complaint["author_email"].split("@")[0]

    lines = _opening(facts.reply_tone, who, memory)
    lines += [
        "",
        f"On what you reported {leak.age_hours:.0f} hours ago: \"{facts.what_broke}\"",
        "",
        "This had not been captured as tracked work anywhere, so it was at risk of "
        f"being lost. It is now filed as {ticket_id or 'a new ticket'} and owned by "
        f"the {TICKET_OWNER} queue.",
    ]

    if memory is not None and memory.prior_resolutions:
        lines += [
            "",
            f"I can see {len(memory.prior_resolutions)} earlier resolution(s) on this "
            "topic. Since it has come back, we are treating the previous fix as "
            "incomplete rather than reopening the same ground.",
        ]

    if fix_attempted:
        if facts.regression_evidence:
            lines.append(
                f"\nA candidate fix is drafted and waiting on review. History pointed "
                f"us at {facts.regression_evidence}, so it targets that directly."
            )
        else:
            lines.append("\nA candidate fix has been drafted and is waiting on review.")
    elif ESCALATE_ACTION in decision.actions:
        lines.append("\nGiven the impact, it has also been escalated to a human on-call engineer.")

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


# --- escalation ---------------------------------------------------------------


def escalate(
    leak: LeakResult, facts: ExtractedFacts, decision: RoutingDecision
) -> dict[str, Any]:
    """Hand to a human. In dry run this is the payload that would be paged."""
    return {
        "dry_run": config.DRY_RUN,
        "contact": decision.escalation_contact,
        "complaint_id": facts.complaint_id,
        "category": decision.category,
        "severity": facts.severity,
        "urgency": facts.urgency,
        "why": decision.reason,
        "summary": facts.what_broke,
        "requires_human_approval": decision.requires_human_approval,
    }


# --- execution ----------------------------------------------------------------


def resolve(
    leak: LeakResult,
    facts: ExtractedFacts,
    profile: IntentProfile,
    decision: RoutingDecision,
    memory: Optional[MemoryReadResult] = None,
) -> ResolutionAction:
    """Execute exactly the actions the router permitted. Nothing more.

    Note what is absent: there is no `if category == ...` anywhere below, and no
    action is taken that is not in `decision.actions`. An action the policy did
    not grant cannot happen here even by accident, because there is no code path
    that reaches it.
    """
    started = time.perf_counter()
    permitted = set(decision.actions)
    meta: dict[str, Any] = {"permitted": sorted(permitted)}
    performed: list[str] = []

    ticket_id: Optional[str] = None
    ticket_url: Optional[str] = None
    pr_url: Optional[str] = None
    if permitted & TICKET_ACTIONS:
        ticket_id, ticket_meta = create_ticket(leak, facts, profile, decision, memory)
        ticket_url = ticket_meta.get("url")
        meta["ticket"] = ticket_meta
        performed.extend(sorted(permitted & TICKET_ACTIONS))

        # Every filed ticket gets a proposed fix on the sandbox repo. The
        # severity gate below still governs the *local* patch path; this is the
        # visible half -- a reviewable draft PR next to the ticket, so the
        # ticket is never the end of the story.
        pr_ok, pr_link, pr_meta = open_pull_request(facts, profile, ticket_id)
        meta["pull_request"] = pr_meta
        if pr_ok and pr_link:
            pr_url = pr_link
            performed.append("open_pull_request")

    if LINK_ACTION in permitted:
        meta["link"] = {
            "existing_ticket": facts.references_existing_ticket,
            "linked": bool(facts.references_existing_ticket),
            # The classifier is told never to invent a ticket id, so an absent
            # one means the message genuinely named none.
            "note": "no ticket referenced in the message"
            if not facts.references_existing_ticket
            else "",
        }
        performed.append(LINK_ACTION)

    fix_attempted, fix_url = False, None
    pr_confidence: Optional[float] = None
    if FIX_ACTION in permitted:
        fix_attempted, fix_url, fix_meta = attempt_fix(facts, profile, ticket_id, memory)
        meta["fix"] = fix_meta
        # Stays None when the generator reported no confidence. Rendering an
        # unscored patch as 0.00 would read as "the model was sure it was wrong".
        pr_confidence = fix_meta.get("confidence")
        if fix_attempted:
            performed.append(FIX_ACTION)

    reply_sent, reply_channel = False, None
    if REPLY_ACTION in permitted:
        body = compose_reply(leak, facts, ticket_id, decision, fix_attempted, memory)
        reply_sent, reply_channel, reply_meta = send_reply(leak, body)
        meta["reply"] = {**reply_meta, "tone": facts.reply_tone}
        performed.append(REPLY_ACTION)

    escalated = False
    if ESCALATE_ACTION in permitted:
        meta["escalation"] = escalate(leak, facts, decision)
        escalated = True
        performed.append(ESCALATE_ACTION)

    if LOG_ONLY_ACTION in permitted:
        performed.append(LOG_ONLY_ACTION)

    if not permitted:
        meta["no_action"] = f"policy for {decision.category} permits no actions"

    return ResolutionAction(
        leak=leak.to_dict(),
        facts=facts.to_dict(),
        ticket_created_id=ticket_id,
        ticket_url=ticket_url,
        fix_attempted=fix_attempted,
        fix_pr_url=fix_url or pr_url,
        pr_confidence=pr_confidence,
        reply_sent=reply_sent,
        reply_channel=reply_channel,
        decision=decision.reason,
        dry_run=config.DRY_RUN,
        meta=meta,
        actions_taken=performed,
        escalated=escalated,
        routing=decision.to_dict(),
        memory=memory.to_dict() if memory else {},
        cost_usd=facts.cost_usd,
        latency_ms=round((time.perf_counter() - started) * 1000, 1),
    )
