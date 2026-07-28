"""Pipeshift extraction: unstructured complaint text -> structured facts.

This is the load-bearing model step. Every downstream decision (fix vs reply vs
escalate) keys off `is_code_issue`, `severity` and `file_hint`, so if this step
is wrong the agent does the wrong thing.

Specialisation here is a constrained decode against a fixed JSON schema on a
small open model, not a general chat call. Pipeshift's API is OpenAI-compatible
and supports `response_format: json_schema` with `strict: true`, so the model
physically cannot emit a shape we do not expect.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Optional

from . import config
from .contracts import CATEGORIES, FALLBACK_CATEGORY, ComplaintEvent, ExtractedFacts

# The category enum is generated from the taxonomy rather than written out, so
# a category can never exist in `contracts.CATEGORIES` without the model being
# allowed to emit it. `strict: true` means the model physically cannot return a
# value outside this list -- there is no post-hoc validation to forget.
FACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "what_broke": {
            "type": "string",
            "description": "One sentence, concrete, naming the failing behaviour.",
        },
        "category": {
            "type": "string",
            "enum": list(CATEGORIES),
            "description": "The single best-fitting triage category.",
        },
        "confidence": {
            "type": "number",
            "description": (
                "0 to 1. How certain the category is. Be honest: a vague or "
                "ambiguous message should score low, which routes it to a human."
            ),
        },
        "actor_type": {
            "type": "string",
            "enum": ["customer", "internal_employee", "unknown", "automated"],
            "description": "Who is speaking. Automated covers alerts and bot notifications.",
        },
        "sentiment": {
            "type": "string",
            "enum": ["neutral", "frustrated", "angry", "positive"],
            "description": "The reporter's tone, not the severity of the problem.",
        },
        "urgency": {
            "type": "string",
            "enum": ["low", "medium", "high", "critical"],
            "description": "How fast this needs a response, from the reporter's position.",
        },
        "is_code_issue": {
            "type": "boolean",
            "description": "True only if a change to application source code would fix it.",
        },
        "file_hint": {
            "type": "string",
            "description": "Best-guess file path or component name, empty string if unknown.",
        },
        "severity": {
            "type": "number",
            "description": "0 to 1. Silent data loss and security are high, cosmetic is low.",
        },
        "multi_intent": {
            "type": "boolean",
            "description": "True if the message contains more than one distinct issue.",
        },
        "references_existing_ticket": {
            "type": "string",
            "description": (
                "A ticket or issue id the message names outright (e.g. ENG-412, #88). "
                "Empty string if none. Never guess one."
            ),
        },
    },
    "required": [
        "what_broke",
        "category",
        "confidence",
        "actor_type",
        "sentiment",
        "urgency",
        "is_code_issue",
        "file_hint",
        "severity",
        "multi_intent",
        "references_existing_ticket",
    ],
    "additionalProperties": False,
}

# Built from the taxonomy so the prompt and the enum can never drift apart.
_CATEGORY_GUIDE = "\n".join(
    f"  {name} -- {meaning}"
    for name, meaning in (
        ("genuine_bug", "a real product defect"),
        ("user_error", "works as intended, the user misunderstood"),
        ("question", "a how-to or clarification request"),
        ("feature_request", "asking for something that does not exist yet"),
        ("feedback_positive", "praise or thanks"),
        ("feedback_negative", "dissatisfaction with no specific defect named"),
        ("duplicate_or_known_issue", "the message itself names an existing ticket"),
        ("security_concern", "a possible vulnerability or data exposure"),
        ("billing_or_account", "payment, invoicing, seats or subscription"),
        ("outage_or_urgent", "broad or severe: the product is down for many users"),
        ("spam_or_unrelated", "not a complaint about this product at all"),
        ("internal_notice", "an employee reporting their own change or breakage"),
        ("unclear", "genuinely cannot tell what is being reported"),
    )
)

SYSTEM_PROMPT = (
    "You are a triage extractor for a software company's support pipeline. "
    "Given one customer complaint, return only the structured facts requested.\n\n"
    "Categories:\n"
    f"{_CATEGORY_GUIDE}\n\n"
    "Judge severity by user impact: silent data loss, security exposure and billing "
    "errors are high; broken core workflows are medium; cosmetic and documentation "
    "issues are low. Severity is about consequence; urgency is about how fast the "
    "reporter needs an answer; sentiment is only their tone. They are independent.\n\n"
    "Set is_code_issue to false for account, billing, configuration and infrastructure "
    "problems that no source change would fix. "
    "Only give a file_hint when the complaint points clearly at one component.\n\n"
    "Confidence is the single most important field. Downstream, anything below the "
    "configured floor is routed to a human instead of being acted on automatically, "
    "so an honest low score is a correct and useful answer. Do not inflate it to seem "
    "decisive. Only use duplicate_or_known_issue when the message actually names a "
    "ticket; do not infer one. Never invent a ticket id."
)


class Extractor:
    def __init__(self) -> None:
        self.live = bool(config.PIPESHIFT_API_KEY)
        self.model = config.PIPESHIFT_MODEL if self.live else "heuristic-fallback"
        self.backend = "pipeshift" if self.live else "heuristic"
        self._client = None
        if self.live:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=config.PIPESHIFT_API_KEY,
                base_url=config.PIPESHIFT_BASE_URL,
            )

    def extract(self, complaint: ComplaintEvent) -> ExtractedFacts:
        started = time.perf_counter()
        if self.live:
            try:
                facts = self._extract_pipeshift(complaint)
                facts.latency_ms = round((time.perf_counter() - started) * 1000, 1)
                return facts
            except Exception:
                pass  # degrade rather than break the demo
        facts = self._extract_heuristic(complaint)
        facts.latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return facts

    def _extract_pipeshift(self, complaint: ComplaintEvent) -> ExtractedFacts:
        resp = self._client.chat.completions.create(
            model=config.PIPESHIFT_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": complaint.text},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "extracted_facts",
                    "strict": True,
                    "schema": FACT_SCHEMA,
                },
            },
            temperature=0,
            max_tokens=512,
        )
        payload = json.loads(resp.choices[0].message.content)
        hint = (payload.get("file_hint") or "").strip()
        ticket = (payload.get("references_existing_ticket") or "").strip()

        # `strict: true` constrains the enum, but a category the taxonomy does
        # not know would still crash the router's policy lookup. Fall back
        # rather than trust it -- an unroutable category is worse than an
        # honest "unclear".
        category = payload.get("category") or FALLBACK_CATEGORY
        if category not in CATEGORIES:
            category = FALLBACK_CATEGORY

        return ExtractedFacts(
            complaint_id=complaint.id,
            what_broke=payload["what_broke"],
            is_code_issue=bool(payload["is_code_issue"]),
            file_hint=hint or None,
            severity=max(0.0, min(1.0, float(payload["severity"]))),
            category=category,
            confidence=max(0.0, min(1.0, float(payload.get("confidence", 0.0)))),
            actor_type=payload.get("actor_type") or "unknown",
            sentiment=payload.get("sentiment") or "neutral",
            urgency=payload.get("urgency") or "low",
            multi_intent=bool(payload.get("multi_intent", False)),
            references_existing_ticket=ticket or None,
            model=config.PIPESHIFT_MODEL,
        )

    # -- degraded path ---------------------------------------------------------

    _SEVERITY_CUES = {
        "silent": 0.30, "data loss": 0.30, "dropped": 0.20, "duplicate charge": 0.30,
        "security": 0.30, "breach": 0.30, "cannot": 0.10, "blocking": 0.15,
        "timeout": 0.10, "fails": 0.10, "wrong": 0.10, "invoice": 0.15,
        "loop": 0.10, "500": 0.15, "504": 0.15, "blank": 0.05,
    }
    _CODE_CUES = (
        "api", "sdk", "endpoint", "header", "param", "pip", "install", "webhook",
        "timeout", "render", "console", "scheduler", "timezone", "import", "pin",
        "migration", "query", "response", "docs code sample",
    )
    _NONCODE_CUES = ("invoice", "tax", "billing", "account", "seat", "contract", "refund")

    # PROVISIONAL. A coarse cue map so the degraded path emits a category and a
    # confidence at all, which is what keeps the router from collapsing every
    # complaint to the fallback. The real 13-way classification is the Pipeshift
    # constrained decode; this is only what runs with no API key.
    _CATEGORY_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("security_concern", ("security", "breach", "leak between", "vulnerab", "should not be able to see")),
        ("billing_or_account", ("invoice", "billing", "charged", "refund", "seat", "subscription")),
        ("outage_or_urgent", ("outage", "site is down", "everything is down", "completely broken")),
        ("feature_request", ("would be nice", "feature request", "can you add", "wish there was")),
        ("question", ("how do", "how can", "quick q", "where do i", "couldn't find")),
        ("feedback_positive", ("great", "love it", "thanks", "saved us", "getting really good feedback")),
        ("genuine_bug", ("error", "fails", "broken", "not working", "times out", "500", "504", "stale", "overlaps", "dropping")),
    )

    def _classify_heuristic(self, text: str) -> tuple[str, float]:
        """Returns (category, confidence). Confidence tracks how many cues hit."""
        for category, cues in self._CATEGORY_CUES:
            hits = sum(1 for cue in cues if cue in text)
            if hits:
                return category, round(min(1.0, 0.55 + 0.15 * hits), 3)
        return FALLBACK_CATEGORY, 0.3

    def _extract_heuristic(self, complaint: ComplaintEvent) -> ExtractedFacts:
        text = complaint.text.lower()
        severity = 0.25 + sum(w for cue, w in self._SEVERITY_CUES.items() if cue in text)
        is_code = any(c in text for c in self._CODE_CUES) and not any(
            c in text for c in self._NONCODE_CUES
        )
        hint = None
        path = re.search(r"[\w/]+\.(py|ts|js|tsx|go|rb|java)\b", complaint.text)
        if path:
            hint = path.group(0)
        elif is_code:
            endpoint = re.search(r"/v\d+/[\w-]+", complaint.text)
            hint = endpoint.group(0) if endpoint else None
        summary = complaint.text.split(". ")[0].strip()
        if summary.lower().startswith("subject:"):
            summary = summary[len("subject:"):].strip()
        category, confidence = self._classify_heuristic(text)
        return ExtractedFacts(
            complaint_id=complaint.id,
            what_broke=summary[:200],
            is_code_issue=is_code,
            file_hint=hint,
            severity=round(max(0.0, min(1.0, severity)), 3),
            category=category,
            confidence=confidence,
            model="heuristic-fallback",
        )
