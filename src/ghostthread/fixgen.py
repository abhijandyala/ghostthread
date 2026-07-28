"""Pipeshift DeepSeek Coder: root cause + regression evidence -> a candidate patch.

This is the *second* specialised Pipeshift model, and the reason there are two.
`extract.py` runs a small instruction model under a constrained decode because
classification wants speed and a fixed shape. Code generation wants a model that
has actually read code, so it runs against a DeepSeek Coder deployment on the
same OpenAI-compatible Pipeshift endpoint. Same provider, two endpoints, chosen
per task -- which is what "model specialisation" means here.

The specialisation is not decoration. `regression_evidence` from the memory read
is threaded into the prompt, and the returned `files_touched` is what makes the
kill shot visible: scope GitHub away, the evidence goes null, and the patch stops
being targeted. The model reports that itself rather than us asserting it.

Degradation, in strict order:

    1. PIPESHIFT_API_KEY set   -> DeepSeek Coder on Pipeshift. Not degraded.
    2. ANTHROPIC_API_KEY set   -> a general coding model. DEGRADED, and every
                                  proposal says so in `degraded_reason`, which
                                  act.py stamps onto the PR metadata.
    3. neither                 -> no diff at all. `diff` is None and the
                                  explanation says why.

There is deliberately no fourth mode that invents a plausible-looking patch. A
fabricated diff is worse than no diff, because a reviewer cannot tell it apart
from a real one.

Nothing in this module writes anything. It returns a proposal; `act.py` decides
whether the allowlist, DRY_RUN and the profile's confidence threshold let it
become a branch.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

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


# The constrained decode. `strict: true` means the model physically cannot
# return a shape act.py is not ready to read -- same discipline as extract.py.
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
    """Picks the best available backend once, then reports honestly which it is.

    Constructed per call in act.py rather than held on the engine, so flipping a
    credential in `.env` and re-running picks the new backend up without a
    restart -- the same property the intent profile has.
    """

    def __init__(self) -> None:
        self.pipeshift_available = bool(config.PIPESHIFT_API_KEY)
        self.claude_available = bool(config.ANTHROPIC_API_KEY)

        if self.pipeshift_available:
            self.backend = "pipeshift"
            self.model = config.PIPESHIFT_CODE_MODEL
        elif self.claude_available:
            self.backend = "anthropic-fallback"
            self.model = config.CODING_AGENT_MODEL
        else:
            self.backend = "none"
            self.model = ""

        self._client = None
        if self.pipeshift_available:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=config.PIPESHIFT_API_KEY,
                base_url=config.PIPESHIFT_BASE_URL,
            )

    # -- public ---------------------------------------------------------------

    def generate(self, request: FixRequest) -> FixProposal:
        started = time.perf_counter()
        targeted = bool(request.regression_evidence)

        if self.backend == "none":
            return FixProposal(
                diff=None,
                explanation=(
                    "No code-generation credential is configured (neither "
                    "PIPESHIFT_API_KEY nor ANTHROPIC_API_KEY), so no patch was "
                    "drafted. Nothing was guessed in its place."
                ),
                confidence=None,
                backend="none",
                degraded=True,
                degraded_reason="no code model configured; fix generation is off",
                targeted=targeted,
            )

        try:
            if self.backend == "pipeshift":
                proposal = self._generate_pipeshift(request)
            else:
                proposal = self._generate_claude(request)
        except Exception as exc:
            # A failed call is reported as a failed call. It does not silently
            # fall through to the other backend, because a demo that quietly
            # switched models would misrepresent which model produced the patch.
            return FixProposal(
                diff=None,
                explanation=f"The {self.backend} fix generator failed and no patch was produced.",
                confidence=None,
                backend=self.backend,
                model=self.model,
                degraded=True,
                degraded_reason=f"{self.backend} call failed",
                targeted=targeted,
                error=str(exc)[:300],
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
            )

        proposal.targeted = targeted
        proposal.latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return proposal

    # -- the graded path ------------------------------------------------------

    def _generate_pipeshift(self, request: FixRequest) -> FixProposal:
        resp = self._client.chat.completions.create(
            model=config.PIPESHIFT_CODE_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_prompt(request)},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "fix_proposal",
                    "strict": True,
                    "schema": FIX_SCHEMA,
                },
            },
            temperature=0,
            max_tokens=config.CODING_AGENT_MAX_TOKENS,
        )
        payload = json.loads(resp.choices[0].message.content)
        usage = getattr(resp, "usage", None)
        return _proposal_from_payload(
            payload,
            backend="pipeshift",
            model=config.PIPESHIFT_CODE_MODEL,
            degraded=False,
            degraded_reason="",
            usage={
                "prompt_tokens": getattr(usage, "prompt_tokens", 0),
                "completion_tokens": getattr(usage, "completion_tokens", 0),
            }
            if usage
            else {},
        )

    # -- the degraded path ----------------------------------------------------

    def _generate_claude(self, request: FixRequest) -> FixProposal:
        """A general coding model, used only when Pipeshift is not configured.

        Explicitly not the graded model step. Every proposal from here carries
        `degraded=True` and a reason, which act.py copies onto the PR metadata
        and the UI renders, so nobody reading the output can mistake this for
        the specialised code model.
        """
        instruction = (
            f"{SYSTEM_PROMPT}\n\n"
            "Reply with a single JSON object and no prose around it, with keys: "
            '"diff" (string), "explanation" (string), "confidence" (number 0-1), '
            '"files_touched" (array of strings).'
        )
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
                "system": instruction,
                "messages": [{"role": "user", "content": _build_prompt(request)}],
            },
            timeout=90.0,
        )
        resp.raise_for_status()
        body = resp.json()
        text = "".join(block.get("text", "") for block in body.get("content", []))
        payload = _loads_lenient(text)
        usage = body.get("usage") or {}

        reason = (
            "PIPESHIFT_API_KEY is not set, so this patch came from a general "
            "coding model, not the specialised DeepSeek Coder deployment"
        )
        if payload is None:
            # The fallback has no schema enforcement, so an unparseable answer is
            # a real possibility. Report it rather than regex a diff out of prose.
            return FixProposal(
                diff=None,
                explanation=(
                    "The fallback coding model did not return the requested JSON "
                    "shape, so no patch was accepted."
                ),
                confidence=None,
                backend="anthropic-fallback",
                model=config.CODING_AGENT_MODEL,
                degraded=True,
                degraded_reason=reason,
                error="fallback response was not valid JSON",
            )

        return _proposal_from_payload(
            payload,
            backend="anthropic-fallback",
            model=config.CODING_AGENT_MODEL,
            degraded=True,
            degraded_reason=reason,
            usage={
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
            },
        )


# --- shared parsing -----------------------------------------------------------


def _loads_lenient(text: str) -> Optional[dict[str, Any]]:
    """Parse JSON that may be wrapped in a fenced block. Never guesses content."""
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.split("```")[1] if "```" in candidate[3:] else candidate[3:]
        if candidate.lstrip().lower().startswith("json"):
            candidate = candidate.lstrip()[len("json"):]
    start, end = candidate.find("{"), candidate.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        parsed = json.loads(candidate[start : end + 1])
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


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
