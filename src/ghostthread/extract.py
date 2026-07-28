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
from .contracts import FALLBACK_CATEGORY, ComplaintEvent, ExtractedFacts

FACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "what_broke": {
            "type": "string",
            "description": "One sentence, concrete, naming the failing behaviour.",
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
    },
    "required": ["what_broke", "is_code_issue", "file_hint", "severity"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "You are a triage extractor for a software company's support pipeline. "
    "Given one customer complaint, return only the structured facts requested. "
    "Judge severity by user impact: silent data loss, security exposure and billing "
    "errors are high; broken core workflows are medium; cosmetic and documentation "
    "issues are low. Set is_code_issue to false for account, billing, configuration "
    "and infrastructure problems that no source change would fix. "
    "Only give a file_hint when the complaint points clearly at one component."
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
        return ExtractedFacts(
            complaint_id=complaint.id,
            what_broke=payload["what_broke"],
            is_code_issue=bool(payload["is_code_issue"]),
            file_hint=hint or None,
            severity=max(0.0, min(1.0, float(payload["severity"]))),
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
        """Returns (category, confidence). Confidence tracks how many cues hit.

        Every category is scored and the strongest wins. Returning the first
        category with any hit made declaration order the classifier: a message
        saying the new export is great but the API returns an error scored one
        hit for praise and three for a bug, and was filed as praise. Ties still
        fall to the earlier entry, so the ordering above remains the tiebreak --
        it is just no longer the whole decision.
        """
        best_category = FALLBACK_CATEGORY
        best_hits = 0
        for category, cues in self._CATEGORY_CUES:
            hits = sum(1 for cue in cues if cue in text)
            if hits > best_hits:
                best_category, best_hits = category, hits
        if not best_hits:
            return FALLBACK_CATEGORY, 0.3
        return best_category, round(min(1.0, 0.55 + 0.15 * best_hits), 3)

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
