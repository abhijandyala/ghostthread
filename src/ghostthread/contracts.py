"""The frozen interface contract.

Every module in GhostThread speaks these shapes and nothing else. They are
intentionally plain dataclasses so they serialise straight to JSON for the API,
the RocketRide node payloads, and the demo UI.

This is the ONLY file both tracks edit. Track A (HydraDB grounding + memory)
and Track B (extraction, policy, routing, actions, UI) both import it, so a
change here breaks the other person's build. Edit it at a scheduled sync point,
with the other person present, or not at all.

Where the PRD and the existing code disagreed on a name, the code won: renames
cost files and buy nothing. Where the PRD carried genuinely new information --
the category taxonomy, memory counts, reply tone, routing -- it was added.
Every field added to an existing shape has a default, so code written against
the previous version still constructs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Optional

Source = Literal["slack", "gmail", "linear", "github"]
COMPLAINT_SOURCES: tuple[str, ...] = ("slack", "gmail")
WORK_SOURCES: tuple[str, ...] = ("linear", "github")
ALL_SOURCES: tuple[str, ...] = COMPLAINT_SOURCES + WORK_SOURCES

# Kept from v1 deliberately. `killshot.py`, `leaks.py`, `pipeline.py`, the UI and
# the README table all key off these exact strings. The PRD's
# leak/resolved/unknown spelling carries no extra information.
Verdict = Literal["actioned", "leaked", "unknown_insufficient_sources"]

# The 13-way taxonomy. Declared here so there is exactly one list of them in the
# system; the *policy* for each lives in the intent profile, never in code.
# Naming a category in dispatch logic is what the anti-hardcoding rule forbids --
# declaring the vocabulary is not.
Category = Literal[
    "genuine_bug",
    "user_error",
    "question",
    "feature_request",
    "feedback_positive",
    "feedback_negative",
    "duplicate_or_known_issue",
    "security_concern",
    "billing_or_account",
    "outage_or_urgent",
    "spam_or_unrelated",
    "internal_notice",
    "unclear",
]
CATEGORIES: tuple[str, ...] = (
    "genuine_bug",
    "user_error",
    "question",
    "feature_request",
    "feedback_positive",
    "feedback_negative",
    "duplicate_or_known_issue",
    "security_concern",
    "billing_or_account",
    "outage_or_urgent",
    "spam_or_unrelated",
    "internal_notice",
    "unclear",
)

# The catch-all a low-confidence classification collapses to. Named once, here,
# so the router can reference it without a bare string literal.
FALLBACK_CATEGORY: str = "unclear"

ReplyTone = Literal["first_contact", "returning", "escalation"]
ActorType = Literal["customer", "internal_employee", "unknown", "automated"]
Sentiment = Literal["neutral", "frustrated", "angry", "positive"]
Urgency = Literal["low", "medium", "high", "critical"]

Action = Literal[
    "file_ticket",
    "file_ticket_tagged_feature",
    "file_ticket_internal",
    "link_existing_ticket",
    "propose_sandbox_fix",
    "reply",
    "escalate",
    "log_only",
]


class _JsonMixin:
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --- raw source events --------------------------------------------------------
# Unchanged from v1. `t` stays a float epoch because every age/temporal
# calculation in leaks.py derives from it; ISO strings are a rendering concern.


@dataclass
class ComplaintEvent(_JsonMixin):
    id: str
    source: str  # "slack" | "gmail"
    entity_id: str
    text: str
    t: float
    channel_or_thread: str
    author_email: str
    actor_display_name: str = ""


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


# --- memory (Track A owns the implementation, both tracks read the shape) ------


@dataclass
class PriorResolution(_JsonMixin):
    """One thing we already did about this topic, recalled from memory."""

    ticket_url: str
    resolved_at: str
    summary: str


@dataclass
class RegressionRef(_JsonMixin):
    """A PR or commit the history implicates in the current complaint."""

    source: str  # "github" | "linear"
    ref: str
    url: str = ""


@dataclass
class MemoryReadResult(_JsonMixin):
    """Output of the memory read (pipeline node N1).

    This is the whole pitch in one object: what we already know about this
    person and this topic, before anything is decided. When the memory is
    scoped away, the counts go to zero and `likely_regression` goes None --
    which is what the kill shot makes visible.
    """

    actor: str
    times_reported_by_actor: int = 0
    times_seen_on_topic: int = 0
    prior_resolutions: list[PriorResolution] = field(default_factory=list)
    likely_regression: Optional[RegressionRef] = None
    sources_used: list[str] = field(default_factory=list)
    # True while this is coming from the stub rather than a real HydraDB read.
    # The smoke test and the UI both refuse to call a run demo-ready while any
    # stub is live, so a placeholder cannot reach the stage by accident.
    stub: bool = False

    @property
    def has_history(self) -> bool:
        return self.times_reported_by_actor > 0 or self.times_seen_on_topic > 0


@dataclass
class MemoryWriteInput(_JsonMixin):
    """Episodic memory written back after a resolution (pipeline node N7).

    One per resolution, `type="memory"` on ingest -- not knowledge. This is what
    makes the next complaint smarter than this one.
    """

    actor: str
    complaint_id: str
    complaint_summary: str
    category: str
    action_taken: list[str] = field(default_factory=list)
    ticket_url: Optional[str] = None
    resolved_at: str = ""


# --- extraction ---------------------------------------------------------------


@dataclass
class ExtractedFacts(_JsonMixin):
    """Output of the Pipeshift-hosted extraction model.

    The first four fields are the v1 contract and remain load-bearing:
    `severity` is compared numerically against profile thresholds in act.py, so
    it stays a float rather than becoming the PRD's low/medium/high enum. The
    human-readable band is derived from the profile at render time.

    The memory-derived fields (`times_*`, `reply_tone`, `regression_evidence`)
    are NOT trusted to the model. The pipeline stamps them from MemoryReadResult
    after extraction, because a counted fact should never be a generated one.
    """

    complaint_id: str
    what_broke: str
    is_code_issue: bool
    file_hint: Optional[str]
    severity: float

    # classification
    category: str = FALLBACK_CATEGORY
    confidence: float = 0.0
    actor_type: str = "unknown"
    sentiment: str = "neutral"
    urgency: str = "low"
    multi_intent: bool = False
    references_existing_ticket: Optional[str] = None

    # stamped from MemoryReadResult, never generated
    times_reported_by_actor: int = 0
    times_seen_on_topic: int = 0
    reply_tone: str = "first_contact"
    regression_evidence: Optional[str] = None

    # provenance
    model: str = ""
    latency_ms: float = 0.0
    cost_usd: float = 0.0


# --- the join -----------------------------------------------------------------


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
    # Sources deliberately out of scope for this run. Empty on a full run; the
    # kill shot panel reads this to say what the answer could not see.
    sources_missing: list[str] = field(default_factory=list)


# --- routing ------------------------------------------------------------------


@dataclass
class RoutingDecision(_JsonMixin):
    """Output of the pure router (pipeline node N5).

    `route(facts, profile) -> RoutingDecision` is a pure function. Every action
    in `actions` was permitted by the policy for this category; nothing is
    granted by the router on its own authority.
    """

    complaint_id: str
    category: str
    actions: list[str] = field(default_factory=list)
    requires_human_approval: bool = False
    escalation_contact: Optional[str] = None
    customer_facing: bool = True
    # Why this came out the way it did, in one line, for the UI and the ticket.
    reason: str = ""
    # True when classification confidence fell under the profile floor and the
    # category was forced to the fallback regardless of what was guessed.
    forced_fallback: bool = False


# --- outcome ------------------------------------------------------------------


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

    # v2 additions. These map 1:1 onto the InsForge `actions_log` columns, which
    # is why they are named the way they are.
    actions_taken: list[str] = field(default_factory=list)
    ticket_url: Optional[str] = None
    pr_confidence: Optional[float] = None
    escalated: bool = False
    memory_write_id: Optional[str] = None
    memory: dict[str, Any] = field(default_factory=dict)
    routing: dict[str, Any] = field(default_factory=dict)
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    timestamp: str = ""


# --- governance ---------------------------------------------------------------


@dataclass
class IntentProfile(_JsonMixin):
    """Governance document. Read from InsForge at call time, never inlined.

    One document rather than the PRD's three tables: intent.py already reads it
    with a 5s TTL and the UI already edits it, so folding `category_policy` and
    `global_overrides` in here gets the live-policy-change demo for free instead
    of building a second read path. It is still "policy in InsForge, separate
    from app code" -- which is the thing being graded.
    """

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

    # v2: per-category routing policy, keyed by category name. The router reads
    # this and nothing else. A category missing from here is a hard error, not a
    # silent fallthrough.
    category_policy: dict[str, Any] = field(default_factory=dict)
    global_overrides: dict[str, Any] = field(default_factory=dict)
    # Minimum prior-contact counts at which the reply tone steps up. Data, so
    # the tone ladder can be retuned on stage without a deploy.
    reply_tone_thresholds: dict[str, int] = field(default_factory=dict)
    # Upper bound of each severity band, for rendering a float as a word.
    severity_bands: dict[str, float] = field(default_factory=dict)

    # -- Track A / grounding knobs --------------------------------------------
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

    # -- policy accessors ------------------------------------------------------
    # Every consumer goes through these so no module ever indexes the raw dict
    # and quietly tolerates a missing key.

    def policy_for(self, category: str) -> dict[str, Any]:
        """Policy for one category. Raises if undefined -- never falls through."""
        policy = self.category_policy.get(category)
        if policy is None:
            raise KeyError(
                f"no category_policy entry for {category!r}; "
                f"every category must be defined in the intent profile"
            )
        return policy

    def override(self, key: str, default: Any = None) -> Any:
        return self.global_overrides.get(key, default)

    @property
    def min_confidence_for_autoaction(self) -> float:
        return float(self.override("min_confidence_for_autoaction", 0.0))

    @property
    def sandbox_repo_allowlist(self) -> list[str]:
        return list(self.override("sandbox_repo_allowlist", []))

    @property
    def never_automerge(self) -> bool:
        return bool(self.override("never_automerge", True))

    def missing_categories(self) -> list[str]:
        """Categories in the taxonomy with no policy entry. Must be empty."""
        return [c for c in CATEGORIES if c not in self.category_policy]

    def severity_band(self, severity: float) -> str:
        """Render a severity float as a word, using bands from the profile."""
        for label, ceiling in sorted(self.severity_bands.items(), key=lambda kv: kv[1]):
            if severity <= ceiling:
                return label
        return "high"
