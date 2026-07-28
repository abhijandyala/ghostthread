"""Cross-source entity resolution.

The same human is an opaque member id in Slack, an address in Gmail and a login
handle in GitHub. Without collapsing those into one identity the join cannot see
that a Slack complaint and a GitHub PR are about the same person, which is most
of why a single-source query cannot answer the question.

Identities are *derived* from whatever is currently loaded, never looked up in a
table keyed by demo names.

Slack is the hard case and it gets its own section at the bottom of this file.
A Slack message carries an opaque member id and a display name and no address
anywhere, while every join in this project -- entity resolution here, and
`memory_read`'s actor filter -- keys on email. So the member id is resolved
against Slack's own directory rather than guessed at from the display name.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from . import config
from .contracts import ComplaintEvent, WorkEvent

_HANDLE = re.compile(r"@([A-Za-z0-9][A-Za-z0-9-_]{1,38})")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def extract_email(text: Any) -> str:
    """The first address in a value, lowercased, or empty if it holds none.

    Metadata carries addresses in several shapes: bare, or wrapped in a display
    name as `Name <a@b.com>`. A value with no address at all comes back empty
    rather than being passed through, because a non-address sitting in an email
    field is indistinguishable downstream from a real one, and the join it
    silently fails to make looks exactly like "this person has no history".
    """
    match = _EMAIL.search(str(text or ""))
    return match.group(0).lower() if match else ""


@dataclass
class Identity:
    canonical: str
    emails: set[str] = field(default_factory=set)
    handles: set[str] = field(default_factory=set)
    seen_in: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical": self.canonical,
            "emails": sorted(self.emails),
            "handles": sorted(self.handles),
            "seen_in": sorted(self.seen_in),
        }


class IdentityGraph:
    def __init__(self) -> None:
        self._by_key: dict[str, Identity] = {}

    def _touch(self, canonical: str) -> Identity:
        ident = self._by_key.get(canonical)
        if ident is None:
            ident = Identity(canonical=canonical)
            self._by_key[canonical] = ident
        return ident

    def _link(self, canonical: str, *, email: str = "", handle: str = "", source: str = "") -> None:
        ident = self._touch(canonical)
        if email:
            ident.emails.add(email.lower())
            self._by_key[email.lower()] = ident
        if handle:
            ident.handles.add(handle.lower())
            self._by_key[handle.lower()] = ident
        if source:
            ident.seen_in.add(source)

    def build(
        self,
        complaints: Iterable[ComplaintEvent],
        work: Iterable[WorkEvent],
        declared: Optional[list[dict[str, Any]]] = None,
    ) -> "IdentityGraph":
        for row in declared or []:
            email = (row.get("email") or "").lower()
            if not email:
                continue
            self._link(email, email=email)
            for key in ("slack", "github"):
                handle = row.get(key)
                if handle:
                    self._link(email, handle=handle)
            if row.get("slack"):
                self._by_key[row["slack"].lower()] = self._by_key[email]

        for c in complaints:
            email = (c.author_email or "").lower()
            canonical = email or f"{c.source}:{c.entity_id}"
            self._link(canonical, email=email, source=c.source)
            if c.entity_id:
                self._by_key[c.entity_id.lower()] = self._by_key[canonical]

        for w in work:
            email = (w.reporter_email or "").lower()
            canonical = email or f"{w.source}:{w.entity_id}"
            self._link(canonical, email=email, source=w.source)
            # Free-text mentions are a real signal: a PR body crediting a
            # reporter by handle ties that PR back to a Slack complainant.
            blob = f"{w.title} {w.description}"
            for handle in _HANDLE.findall(blob):
                existing = self._by_key.get(handle.lower())
                if existing is not None:
                    existing.handles.add(handle.lower())
                    existing.seen_in.add(w.source)
                else:
                    self._link(canonical, handle=handle, source=w.source)
            for found in _EMAIL.findall(blob):
                if found.lower() != email:
                    self._link(canonical, email=found, source=w.source)
        return self

    def resolve(self, *keys: str) -> Optional[Identity]:
        for key in keys:
            if not key:
                continue
            ident = self._by_key.get(key.lower())
            if ident is not None:
                return ident
        return None

    def same_person(self, complaint: ComplaintEvent, work: WorkEvent) -> tuple[float, str]:
        """Returns (confidence, human-readable reason)."""
        left = self.resolve(complaint.author_email, complaint.entity_id)
        right = self.resolve(work.reporter_email, work.entity_id)
        if left is not None and right is not None and left is right:
            return 1.0, f"same identity {left.canonical}"

        if left is not None:
            blob = f"{work.title} {work.description}".lower()
            for handle in left.handles:
                if f"@{handle}" in blob:
                    return 0.85, f"@{handle} mentioned in {work.source} {work.id}"
            for email in left.emails:
                if email in blob:
                    return 0.9, f"{email} referenced in {work.source} {work.id}"
        return 0.0, "no identity overlap"

    def summary(self) -> list[dict[str, Any]]:
        seen: list[Identity] = []
        for ident in self._by_key.values():
            if ident not in seen:
                seen.append(ident)
        multi = [i.to_dict() for i in seen if len(i.seen_in) > 1]
        return sorted(multi, key=lambda d: -len(d["seen_in"]))


# --- Slack member id -> email ------------------------------------------------
# A Slack message, however it reaches us, names its author as `U0BL91BSTDL` and
# nothing more. That id is a perfectly good identifier of a Slack account and a
# useless one for joining across sources, so it is exchanged for an address
# here, once, against Slack's own directory.

SLACK_USERS_INFO_URL = "https://slack.com/api/users.info"
SLACK_LOOKUP_TIMEOUT_SECONDS = 10.0


@dataclass
class SlackMember:
    """Everything the directory could establish about one member id.

    `reason` is populated whenever `email` is empty, and it is the honest half
    of this dataclass: a missing address because the author is a bot, because
    the token lacks `users:read.email`, and because Slack was unreachable are
    three different facts that all produce the same empty string.
    """

    user_id: str
    email: str = ""
    display_name: str = ""
    is_bot: bool = False
    reason: str = ""


def _users_info(token: str, user_id: str) -> dict[str, Any]:
    """One `users.info` call. Injected in `SlackDirectory` so it can be faked."""
    import httpx

    response = httpx.get(
        SLACK_USERS_INFO_URL,
        params={"user": user_id},
        headers={"Authorization": f"Bearer {token}"},
        timeout=SLACK_LOOKUP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


class SlackDirectory:
    """Member id -> address, resolved lazily and cached for the process.

    Only the ids actually seen are looked up. Enumerating the workspace would
    be one call per member for a corpus that usually mentions two of them, and
    it would fail differently on a large workspace than a small one.

    Failures are cached alongside successes. A bot has no address, and a
    workspace whose token lacks `users:read.email` will never grant one, so
    re-asking per document buys an API call per message to learn the same
    nothing. Nothing here ever invents an address: an unresolved id yields an
    empty email and a reason, and an empty actor is a real answer that
    `memory_read` correctly reports zero prior contacts for.
    """

    def __init__(
        self,
        token: Optional[str] = None,
        transport: Optional[Callable[[str, str], dict[str, Any]]] = None,
    ) -> None:
        self._token = config.SLACK_TOKEN if token is None else token
        self._transport = transport or _users_info
        self._cache: dict[str, SlackMember] = {}
        # Documents are inspected on a thread pool, so two loads can ask for
        # the same id at once. The lock is held across the lookup rather than
        # only around the dict: a workspace has few members, so serialising
        # costs one round trip and saves duplicating every one of them.
        self._lock = threading.Lock()

    def member(self, user_id: Any) -> SlackMember:
        key = str(user_id or "").strip()
        if not key:
            return SlackMember(user_id="", reason="no Slack member id on the document")
        with self._lock:
            cached = self._cache.get(key)
            if cached is None:
                cached = self._lookup(key)
                self._cache[key] = cached
        return cached

    def members(self, user_ids: Iterable[Any]) -> dict[str, SlackMember]:
        out: dict[str, SlackMember] = {}
        for user_id in user_ids:
            key = str(user_id or "").strip()
            if key and key not in out:
                out[key] = self.member(key)
        return out

    def emails(self, user_ids: Iterable[Any]) -> dict[str, str]:
        """Member id -> address, omitting every id that resolved to nothing."""
        return {
            key: member.email
            for key, member in self.members(user_ids).items()
            if member.email
        }

    def unresolved(self) -> dict[str, str]:
        """Member id -> why it has no address, for everything asked so far."""
        with self._lock:
            return {
                key: member.reason
                for key, member in self._cache.items()
                if not member.email
            }

    def _lookup(self, user_id: str) -> SlackMember:
        if not self._token:
            return SlackMember(
                user_id=user_id,
                reason="no SLACK_TOKEN configured, so member ids cannot be resolved",
            )
        try:
            payload = self._transport(self._token, user_id)
        except Exception as exc:
            return SlackMember(
                user_id=user_id,
                reason=f"users.info failed: {type(exc).__name__}: {exc}",
            )
        if not isinstance(payload, dict) or not payload.get("ok"):
            error = ""
            if isinstance(payload, dict):
                error = str(payload.get("error") or "")
            return SlackMember(
                user_id=user_id,
                reason=f"users.info refused: {error or 'no reason reported'}",
            )

        user = payload.get("user") or {}
        profile = user.get("profile") or {}
        is_bot = bool(user.get("is_bot") or user.get("is_app_user"))
        email = extract_email(profile.get("email"))
        display = str(
            profile.get("display_name")
            or profile.get("real_name")
            or user.get("real_name")
            or user.get("name")
            or ""
        )
        reason = ""
        if not email:
            reason = (
                "Slack author is a bot or app, which has no address"
                if is_bot
                else "no email on the Slack profile, or the token lacks users:read.email"
            )
        return SlackMember(
            user_id=user_id,
            email=email,
            display_name=display,
            is_bot=is_bot,
            reason=reason,
        )


_slack_directory: Optional[SlackDirectory] = None
_slack_directory_lock = threading.Lock()


def slack_directory() -> SlackDirectory:
    """The process-wide directory. Shared so the cache is shared."""
    global _slack_directory
    with _slack_directory_lock:
        if _slack_directory is None:
            _slack_directory = SlackDirectory()
        return _slack_directory


def reset_slack_directory(directory: Optional[SlackDirectory] = None) -> None:
    """Replace the shared directory. For tests, and after a credential change."""
    global _slack_directory
    with _slack_directory_lock:
        _slack_directory = directory


def slack_emails(user_ids: Iterable[Any]) -> dict[str, str]:
    """Resolve member ids to addresses through the shared directory."""
    return slack_directory().emails(user_ids)
