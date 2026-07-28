"""N6, code generation: root cause + regression evidence -> a candidate patch.

This is the *second* specialised model, and the reason there are two.
`extract.py` runs a small fast model under a constrained decode because
classification wants speed and a fixed shape. Code generation wants a model that
reasons about code, so it runs against the strong model. Same provider, same
credential, two models chosen per task -- which is what "model specialisation"
means here. It used to mean two Pipeshift deployments; the provider changed, the
argument did not.

The specialisation is not decoration. `regression_evidence` from the memory read
is threaded into the prompt, and the returned `files_touched` is what makes the
kill shot visible: scope GitHub away, the evidence goes null, and the patch stops
being targeted. The model reports that itself rather than us asserting it.

Degradation:

    1. ANTHROPIC_API_KEY set -> the code model, under the same constrained
                                decode discipline as extract.py. Not degraded.
    2. absent                -> no diff at all. `diff` is None and the
                                explanation says why.

There is deliberately no third mode that invents a plausible-looking patch. A
fabricated diff is worse than no diff, because a reviewer cannot tell it apart
from a real one. Note that this rule also covers the model declining: a refusal
or a truncated response returns `diff=None` with the reason, never a partial
diff dressed up as a complete one.

Nothing in this module writes anything. It returns a proposal; `act.py` decides
whether the allowlist, DRY_RUN and the profile's confidence threshold let it
become a branch.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from . import config

# The generator is told to emit this exact token when it cannot determine a fix.
# A model that declines is a correct outcome, not an error.
DECLINE_TOKEN = "SKIP"


@dataclass
class FixRequest:
    """Everything the generator is allowed to know. Assembled by act.py.

    Note what is absent: no category, no policy, no thresholds. The generator
    does not decide whether a fix is permitted -- the router already did, and
    act.py already checked the allowlist. This object only describes the bug.
    """

    complaint_id: str
    what_broke: str
    file_hint: Optional[str] = None
    regression_evidence: Optional[str] = None
    prior_resolutions: int = 0
    language_hint: str = "Python"


@dataclass
class FixProposal:
    """A candidate patch, or an honest account of why there isn't one."""

    diff: Optional[str]
    explanation: str
    # None means "this generator does not report a confidence". It is NOT zero
    # and it is NOT an average: act.py treats an unknown confidence exactly as
    # it treats a low one, so an unscored patch can never be labelled ready.
    confidence: Optional[float]
    files_touched: list[str] = field(default_factory=list)

    # provenance, all rendered in the UI and stamped on the PR
    backend: str = "none"
    model: str = ""
    degraded: bool = False
    degraded_reason: str = ""
    targeted: bool = False
    latency_ms: float = 0.0
    usage: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    @property
    def declined(self) -> bool:
        return self.diff is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "diff": self.diff,
            "explanation": self.explanation,
            "confidence": self.confidence,
            "files_touched": self.files_touched,
            "backend": self.backend,
            "model": self.model,
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason,
            "targeted": self.targeted,
            "latency_ms": self.latency_ms,
            "usage": self.usage,
            "error": self.error,
        }


# The constrained decode. Enforced during generation, so the model cannot return
# a shape act.py is not ready to read -- same discipline as extract.py.
FIX_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "diff": {
            "type": "string",
            "description": (
                "A unified diff and nothing else, or the single word "
                f"{DECLINE_TOKEN} if the fix cannot be determined."
            ),
        },
        "explanation": {
            "type": "string",
            "description": "One short paragraph: the root cause and why this patch addresses it.",
        },
        "confidence": {
            "type": "number",
            "description": (
                "0 to 1. How sure you are this patch is correct and complete. "
                "Score low when you had to guess which file to open -- a low "
                "score routes the patch to a human, which is a useful answer."
            ),
        },
        "files_touched": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Paths the diff modifies. Empty if you declined.",
        },
    },
    "required": ["diff", "explanation", "confidence", "files_touched"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "You are a code-fix generator for a production support pipeline. You are given "
    "one small, well-scoped bug and asked for the minimal patch that fixes it.\n\n"
    "Rules:\n"
    "- Return a unified diff. Keep it as small as the fix allows.\n"
    "- Touch as few files as the fix allows. A broad patch is a failed patch.\n"
    f"- If you cannot determine the fix, set diff to exactly {DECLINE_TOKEN}. "
    "Declining is correct and expected when the evidence is thin.\n"
    "- Never invent file paths. If you were not told which file is implicated, "
    "say so in the explanation and score your confidence down accordingly.\n"
    "- Confidence is the field that decides whether a human reviews this before "
    "anyone reads the code. Do not inflate it."
)


def _build_prompt(request: FixRequest) -> str:
    """The prompt. Evidence in, guesses labelled as guesses.

    The regression-evidence branch is the whole memory pitch in four lines: with
    it the model is pointed at a file, without it the model is told outright that
    it is guessing and asked to price that into its confidence.
    """
    lines = [
        f"Language: {request.language_hint}",
        f"Reported problem: {request.what_broke}",
        f"Suspected component: {request.file_hint or 'unknown'}",
    ]

    if request.regression_evidence:
        lines.append(
            f"History implicates {request.regression_evidence} in this regression. "
            f"Start there and keep the patch inside the blast radius of that change."
        )
    else:
        lines.append(
            "No regression evidence is available for this complaint, so any target "
            "file is a guess. Prefer the smallest plausible change, name the "
            "uncertainty in your explanation, and lower your confidence to match."
        )

    if request.prior_resolutions:
        lines.append(
            f"This topic has been resolved {request.prior_resolutions} time(s) before. "
            f"A recurrence means the earlier fix was incomplete -- do not simply "
            f"reapply it."
        )

    return "\n".join(lines)


class FixGenerator:
    """The code model, or an honest refusal to draft anything.

    Constructed per call in act.py rather than held on the engine, so flipping a
    credential in `.env` and re-running picks the new backend up without a
    restart -- the same property the intent profile has.
    """

    def __init__(self) -> None:
        self.available = bool(config.ANTHROPIC_API_KEY)
        self.backend = "anthropic" if self.available else "none"
        self.model = config.CODING_AGENT_MODEL if self.available else ""

        self._client = None
        if self.available:
            import anthropic

            self._client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    # -- public ---------------------------------------------------------------

    def generate(self, request: FixRequest) -> FixProposal:
        started = time.perf_counter()
        targeted = bool(request.regression_evidence)

        if not self.available:
            return FixProposal(
                diff=None,
                explanation=(
                    "ANTHROPIC_API_KEY is not configured, so no patch was drafted. "
                    "Nothing was guessed in its place."
                ),
                confidence=None,
                backend="none",
                degraded=True,
                degraded_reason="no code model configured; fix generation is off",
                targeted=targeted,
            )

        try:
            proposal = self._generate(request)
        except Exception as exc:
            # A failed call is reported as a failed call. There is nothing to
            # silently fall through to, and inventing one would misrepresent
            # which model produced the patch.
            #
            # The model id is in the reason on purpose: the most likely cause is
            # a CODING_AGENT_MODEL pointing at a model this account cannot
            # reach, and "code model call failed" sends someone debugging the
            # network instead of reading one line of .env.
            return FixProposal(
                diff=None,
                explanation="The fix generator failed and no patch was produced.",
                confidence=None,
                backend=self.backend,
                model=self.model,
                degraded=True,
                degraded_reason=f"call to {self.model!r} failed ({type(exc).__name__})",
                targeted=targeted,
                error=str(exc)[:300],
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
            )

        proposal.targeted = targeted
        proposal.latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return proposal

    # -- the graded path ------------------------------------------------------

    def _generate(self, request: FixRequest) -> FixProposal:
        """A constrained decode on the code model.

        No `temperature`: the model rejects sampling parameters outright. Depth
        is controlled by `effort` instead, which is the parameter that actually
        exists on this model.
        """
        resp = self._client.messages.create(
            model=config.CODING_AGENT_MODEL,
            max_tokens=config.CODING_AGENT_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_prompt(request)}],
            output_config={
                "effort": config.CODING_AGENT_EFFORT,
                "format": {"type": "json_schema", "schema": FIX_SCHEMA},
            },
        )

        usage = getattr(resp, "usage", None)
        usage_dict = (
            {
                "prompt_tokens": getattr(usage, "input_tokens", 0),
                "completion_tokens": getattr(usage, "output_tokens", 0),
            }
            if usage
            else {}
        )

        # Two ways to get back something that is not a patch. Both are reported
        # as "no patch", never as a partial one: a truncated diff looks like a
        # real diff to a reviewer, which is the one outcome this module exists
        # to prevent.
        if resp.stop_reason == "refusal":
            return FixProposal(
                diff=None,
                explanation=(
                    "The model's safety classifiers declined this request, so no patch "
                    "was drafted."
                ),
                confidence=None,
                backend=self.backend,
                model=self.model,
                degraded=True,
                degraded_reason="refused by the code model's safety classifiers",
                usage=usage_dict,
                error="stop_reason=refusal",
            )

        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            payload = json.loads(text)
        except Exception:
            truncated = resp.stop_reason == "max_tokens"
            return FixProposal(
                diff=None,
                explanation=(
                    "The patch did not fit in the token budget and was cut off, so it "
                    "was discarded rather than shown half-written."
                    if truncated
                    else "The code model did not return a readable patch, so none was accepted."
                ),
                confidence=None,
                backend=self.backend,
                model=self.model,
                degraded=True,
                degraded_reason=(
                    "response hit max_tokens; raise CODING_AGENT_MAX_TOKENS"
                    if truncated
                    else "response was not valid JSON"
                ),
                usage=usage_dict,
                error=f"stop_reason={resp.stop_reason}",
            )

        return _proposal_from_payload(
            payload,
            backend=self.backend,
            model=self.model,
            degraded=False,
            degraded_reason="",
            usage=usage_dict,
        )


# --- shared parsing -----------------------------------------------------------


def _proposal_from_payload(
    payload: dict[str, Any],
    backend: str,
    model: str,
    degraded: bool,
    degraded_reason: str,
    usage: Optional[dict[str, Any]] = None,
) -> FixProposal:
    raw_diff = (payload.get("diff") or "").strip()
    explanation = (payload.get("explanation") or "").strip()
    files = [str(f) for f in (payload.get("files_touched") or []) if str(f).strip()]

    confidence: Optional[float]
    try:
        confidence = max(0.0, min(1.0, float(payload["confidence"])))
    except (KeyError, TypeError, ValueError):
        # An unscored patch is not a zero-confidence patch and must not be
        # rendered as one. None propagates all the way to the PR label.
        confidence = None

    if not raw_diff or raw_diff == DECLINE_TOKEN:
        return FixProposal(
            diff=None,
            explanation=explanation
            or "The model declined to patch this; it could not determine a safe fix.",
            confidence=confidence,
            files_touched=[],
            backend=backend,
            model=model,
            degraded=degraded,
            degraded_reason=degraded_reason,
            usage=usage or {},
        )

    return FixProposal(
        diff=raw_diff,
        explanation=explanation or "No explanation was returned with the patch.",
        confidence=confidence,
        files_touched=files,
        backend=backend,
        model=model,
        degraded=degraded,
        degraded_reason=degraded_reason,
        usage=usage or {},
    )
