"""Cross-source entity resolution.

The same human is an opaque member id in Slack, an address in Gmail and a login
handle in GitHub. Without collapsing those into one identity the join cannot see
that a Slack complaint and a GitHub PR are about the same person, which is most
of why a single-source query cannot answer the question.

Identities are *derived* from whatever is currently loaded, never looked up in a
table keyed by demo names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from .contracts import ComplaintEvent, WorkEvent

_HANDLE = re.compile(r"@([A-Za-z0-9][A-Za-z0-9-_]{1,38})")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


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
