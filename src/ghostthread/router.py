"""The router. Pipeline nodes N4 (policy lookup) and N5 (routing).

`route(facts, profile) -> RoutingDecision` is a pure function: same inputs, same
output, no I/O, no clock, no network. That is what makes it testable and what
makes the live policy-change demo honest -- the only thing that changed between
two runs of the same message is the profile.

Two rules this file exists to enforce:

1. **No category name appears as a string literal driving control flow.** Every
   branch here keys off the *policy for* a category, never the category itself.
   If you find yourself typing `if category == "security_concern"`, the policy
   is missing a field -- add the field, don't add the branch.

2. **No undefined fallthrough.** `profile.policy_for()` raises on an unknown
   category rather than defaulting to something permissive. A taxonomy entry
   with no policy is a configuration bug and should fail loudly at startup, not
   silently grant or deny actions at runtime.

3. **The router only ever subtracts.** Gates here remove actions the policy
   granted and can raise the approval requirement; they never add an action the
   policy withheld. A rule that needs to grant something belongs in the profile,
   as an opt-in flag the policy sets (see `escalate_on_risk`).

Every threshold comes from the intent profile. `scripts/verify_no_hardcoding.py`
runs an AST pass over this file and fails the build on any comparison against a
numeric literal.
"""

from __future__ import annotations

from .contracts import (
    FALLBACK_CATEGORY,
    ExtractedFacts,
    IntentProfile,
    RoutingDecision,
)

# Structural, not behavioural: these name the *capabilities* the profile can
# grant, so the gates below can talk about them without hardcoding policy.
FIX_ACTION = "propose_sandbox_fix"
REPLY_ACTION = "reply"
ESCALATE_ACTION = "escalate"


def route(facts: ExtractedFacts, profile: IntentProfile) -> RoutingDecision:
    """Decide what may be done about one classified complaint."""
    reasons: list[str] = []

    # -- N4: which policy applies -------------------------------------------
    # A classification we do not trust is not a classification. Collapse to the
    # fallback category before looking anything up, so the policy that governs
    # a low-confidence guess is the fallback's policy and not the guess's.
    category = facts.category
    forced_fallback = False
    floor = profile.min_confidence_for_autoaction
    if facts.confidence < floor:
        forced_fallback = True
        category = FALLBACK_CATEGORY
        reasons.append(
            f"confidence {facts.confidence:.2f} below floor {floor:.2f}, "
            f"forced to {FALLBACK_CATEGORY}"
        )

    policy = profile.policy_for(category)

    # -- N5: what the policy permits, minus what the gates remove ------------
    actions = list(policy.get("allowed_actions", []))
    customer_facing = bool(policy.get("customer_facing", True))

    if not customer_facing and REPLY_ACTION in actions:
        actions.remove(REPLY_ACTION)
        reasons.append(f"{category} is not customer-facing, reply withheld")

    if not profile.auto_reply_allowed and REPLY_ACTION in actions:
        actions.remove(REPLY_ACTION)
        reasons.append("auto_reply_allowed is off")

    if FIX_ACTION in actions:
        removed = _gate_sandbox_fix(facts, profile, policy, reasons)
        if removed:
            actions.remove(FIX_ACTION)

    # Severity above the risk threshold always reaches a human, whatever the
    # category's own policy says. This is a floor on caution, not a category
    # rule, which is why it is applied after the policy rather than inside it.
    #
    # The floor raises *approval*, not *authority*. Requiring a human is a
    # restriction the router may always impose; handing out the escalate action
    # would be granting a capability, and the router grants nothing the policy
    # did not. A category the profile deliberately gives no actions at all (spam
    # being the obvious one) must not acquire one by being scored severe.
    escalated_by_risk = facts.severity >= profile.risk_threshold
    if escalated_by_risk:
        may_escalate = ESCALATE_ACTION in policy.get("allowed_actions", []) or bool(
            policy.get("escalate_on_risk", False)
        )
        if may_escalate:
            if ESCALATE_ACTION not in actions:
                actions.append(ESCALATE_ACTION)
            reasons.append(
                f"severity {facts.severity:.2f} at or above risk threshold "
                f"{profile.risk_threshold:.2f}, escalating"
            )
        else:
            # Say so rather than dropping it silently: "risk was seen and the
            # policy declined to escalate" is a reviewable statement, and the
            # human-approval flag below still holds the line.
            reasons.append(
                f"severity {facts.severity:.2f} at or above risk threshold "
                f"{profile.risk_threshold:.2f}, but the policy for {category} does "
                f"not permit escalation; held for human approval instead"
            )

    requires_approval = bool(policy.get("requires_human_approval", False))
    if escalated_by_risk:
        requires_approval = True

    if not reasons:
        reasons.append(f"policy for {category} applied as written")

    return RoutingDecision(
        complaint_id=facts.complaint_id,
        category=category,
        actions=actions,
        requires_human_approval=requires_approval,
        escalation_contact=policy.get("escalation_contact") or profile.escalation_contact,
        customer_facing=customer_facing,
        reason="; ".join(reasons),
        forced_fallback=forced_fallback,
    )


def _gate_sandbox_fix(
    facts: ExtractedFacts,
    profile: IntentProfile,
    policy: dict,
    reasons: list[str],
) -> bool:
    """True if the sandbox fix should be withheld. Reasons appended in place."""
    if not profile.auto_fix_allowed:
        reasons.append("auto_fix_allowed is off")
        return True

    if not facts.is_code_issue:
        reasons.append("not a code issue, no fix attempted")
        return True

    # A category may tighten the global ceiling but never loosen it.
    ceiling = min(
        float(policy.get("autofix_max_severity", profile.auto_fix_max_severity)),
        profile.auto_fix_max_severity,
    )
    if facts.severity > ceiling:
        reasons.append(
            f"severity {facts.severity:.2f} over the autofix ceiling {ceiling:.2f}"
        )
        return True

    if not profile.sandbox_repo_allowlist:
        reasons.append("no sandbox repo on the allowlist")
        return True

    return False


def validate_policy(profile: IntentProfile) -> list[str]:
    """Startup check: every category in the taxonomy has a policy entry.

    Called by the smoke test and by `/health`, so a profile that would produce
    an undefined fallthrough is caught before a judge types anything.
    """
    problems: list[str] = []
    missing = profile.missing_categories()
    if missing:
        problems.append(f"category_policy missing entries for: {', '.join(missing)}")
    if not profile.category_policy:
        problems.append("category_policy is empty; the router cannot route")
    if not profile.sandbox_repo_allowlist:
        problems.append("sandbox_repo_allowlist is empty; no fix can ever be proposed")
    if not profile.never_automerge:
        problems.append("never_automerge is false; refusing to run with automerge enabled")
    return problems
