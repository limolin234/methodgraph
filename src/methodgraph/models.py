"""Domain records used by the store, retriever, and adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class SourceRecord:
    source_id: str
    kind: str
    title: str
    content: str
    content_hash: str
    captured_at: str
    author: str = ""
    uri: str | None = None
    published_at: str | None = None
    locator: str = ""
    excerpt: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MethodRecord:
    """A self-sufficient card and optional second-level detail."""

    method_id: str
    title: str
    when: str = ""
    why: str = ""
    how: str = ""
    philosophy: str = ""
    boundary: str = ""
    detail: str = ""
    revision_id: str = ""
    authority: str = "agent"
    scope: str = "general"
    domains: tuple[str, ...] = ()
    project_ref: str | None = None
    importance: str = "normal"
    created_at: str = ""
    updated_at: str = ""
    created_by: str = ""
    source_ids: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def render_card(self) -> str:
        parts = [f"## {self.title}"]
        for label, value in (
            ("When", self.when),
            ("Why", self.why),
            ("How", self.how),
            ("Philosophy", self.philosophy),
            ("Boundary", self.boundary),
        ):
            if value:
                parts.append(f"{label}: {value}")
        return "\n\n".join(parts)

    def retrieval_text(self) -> str:
        parts = [f"Title: {self.title}"]
        for label, value in (
            ("When", self.when),
            ("Why", self.why),
            ("How", self.how),
            ("Philosophy", self.philosophy),
            ("Boundary", self.boundary),
        ):
            if value:
                parts.append(f"{label}: {value}")
        return "\n".join(parts)


@dataclass(frozen=True, slots=True)
class RelationRecord:
    """An untyped connection; its semantics live in natural-language prose."""

    relation_id: str
    method_a_id: str
    method_b_id: str
    explanation: str = ""
    detail: str = ""
    weight: float = 1.0
    revision_id: str = ""
    authority: str = "agent"
    scope: str = "general"
    project_ref: str | None = None
    created_at: str = ""
    updated_at: str = ""
    created_by: str = ""
    source_ids: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SearchHit:
    method: MethodRecord
    score: float
    reason: str
    seed: bool = True
