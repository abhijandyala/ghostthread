"""The frozen interface contract.

Every module in GhostThread speaks these five shapes and nothing else. They are
intentionally plain dataclasses so they serialise straight to JSON for the API,
the RocketRide node payloads, and the demo UI.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Optional

Source = Literal["slack", "gmail", "linear", "github"]
COMPLAINT_SOURCES: tuple[str, ...] = ("slack", "gmail")
WORK_SOURCES: tuple[str, ...] = ("linear", "github")

Verdict = Literal["actioned", "leaked", "unknown_insufficient_sources"]


class _JsonMixin:
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ComplaintEvent(_JsonMixin):
    id: str
    source: str  # "slack" | "gmail"
    entity_id: str
    text: str
    t: float
    channel_or_thread: str
    author_email: str


@dataclass
class WorkEvent(_JsonMixin):
    id: str
    source: str  # "linear" | "github"
    entity_id: str
    title: str
    description: str
    t_created: float
    status: str
    reporter_email: str


@dataclass
class ExtractedFacts(_JsonMixin):
    """Output of the Pipeshift-hosted extraction model."""

    complaint_id: str
    what_broke: str
    is_code_issue: bool
    file_hint: Optional[str]
    severity: float
    model: str = ""
    latency_ms: float = 0.0


@dataclass
class MatchSignal(_JsonMixin):
    """One piece of evidence that a complaint did or did not become work.

    Kept explicit so the UI can show *why* a verdict was reached, and so the
    kill shot can show which signals disappear when sources are removed.
    """

    name: str
    value: float
    weight: float
    source: str
    detail: str = ""

    @property
    def contribution(self) -> float:
        return self.value * self.weight


@dataclass
class LeakResult(_JsonMixin):
    canonical_id: str
    complaint: dict[str, Any]
    matched_work: Optional[dict[str, Any]]
    verdict: str
    sources_used: list[str]
    confidence: float
    score: float = 0.0
    threshold: float = 0.0
    signals: list[dict[str, Any]] = field(default_factory=list)
    age_hours: float = 0.0
    unanswered: bool = False
    # Sources that produced positive evidence, as opposed to those consulted.
    evidence_sources: list[str] = field(default_factory=list)


@dataclass
class WorkItemRef(_JsonMixin):
    source: str  # "linear" | "github"
    id: str
    url: str
    title: str = ""
    score: float = 0.0


@dataclass
class LeakVerdict(_JsonMixin):
    """Track A's output, per issue cluster. Frozen with Track B.

    `confidence` is None whenever the verdict is "unknown" - the point of the
    kill shot is that the system refuses to score an answer it cannot ground,
    rather than reporting a confident false negative.
    """

    issue_cluster_id: str
    verdict: str  # "leak" | "resolved" | "unknown"
    confidence: Optional[float]
    complaint_ids: list[str]
    matched_work_items: list[dict[str, Any]]
    sources_used: list[str]  # connector_ids in scope
    sources_missing: list[str]  # connector_ids out of scope
    # Beyond the frozen contract: kept so the UI can explain a verdict and so
    # the eval suite can assert on more than the label.
    complaint_texts: list[str] = field(default_factory=list)
    actor_emails: list[str] = field(default_factory=list)
    providers_used: list[str] = field(default_factory=list)
    providers_missing: list[str] = field(default_factory=list)
    best_score: float = 0.0
    threshold: float = 0.0
    reasons: list[str] = field(default_factory=list)
    latency_ms: float = 0.0


@dataclass
class ResolutionAction(_JsonMixin):
    leak: dict[str, Any]
    facts: dict[str, Any]
    ticket_created_id: Optional[str]
    fix_attempted: bool
    fix_pr_url: Optional[str]
    reply_sent: bool
    reply_channel: Optional[str]
    decision: str = ""
    dry_run: bool = True
    # Raw payloads for whatever each side effect did (or would have done in a
    # dry run). Rendered verbatim in the demo UI so the action is inspectable.
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class IntentProfile(_JsonMixin):
    """Governance document. Read from InsForge at call time, never inlined."""

    watch_sources: list[str]
    join_sources: list[str]
    min_sources_required: int
    risk_threshold: float
    match_threshold: float
    match_weights: dict[str, float]
    explicit_reference_bonus: float
    # Retrieval relevancy at or below this carries no signal. Retrieval backends
    # compress scores into a high narrow band; this is where that band starts.
    semantic_floor: float
    correlation_window_hours: float
    auto_fix_allowed: bool
    auto_fix_max_severity: float
    auto_reply_allowed: bool
    escalation_contact: str
    # A2 cut rule: customers are not GitHub users, so a complaint rarely
    # resembles a commit. When true, a GitHub match only counts if it cites a
    # Linear issue that also matched the same complaint.
    anchor_github_via_linear: bool = False
    # HydraDB recall mode. "thinking" expands and reranks the query but costs
    # ~4.2s against ~0.4s for "auto", which alone exceeds the latency budget.
    recall_mode: str = "auto"
    version: str = "0"
    origin: str = "unknown"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "IntentProfile":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})
