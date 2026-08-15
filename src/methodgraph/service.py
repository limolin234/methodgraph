"""Model-facing packets shared by MCP, hooks, and future adapters."""

from __future__ import annotations

import base64
from typing import Any

from .models import MethodRecord, RelationRecord, SourceRecord
from .retrieval import MethodRetriever, _lexical_score
from .store import MethodGraphStore


def _compact_source(source: SourceRecord) -> dict[str, Any]:
    result: dict[str, Any] = {"title": source.title, "kind": source.kind}
    for key, value in (("author", source.author), ("published_at", source.published_at),
                       ("locator", source.locator), ("uri", source.uri)):
        if value:
            result[key] = value
    return result


def _full_source(source: SourceRecord, *, content: bool = False) -> dict[str, Any]:
    result = _compact_source(source) | {
        "source_ref": source.source_id,
        "captured_at": source.captured_at,
        "content_hash": source.content_hash,
    }
    if source.excerpt:
        result["excerpt"] = source.excerpt
    if content:
        result["content"] = source.content
    if source.metadata:
        result["metadata"] = source.metadata
    return result


def _governance(item: MethodRecord | RelationRecord) -> dict[str, Any]:
    result: dict[str, Any] = {"authority": item.authority, "scope": item.scope}
    if item.project_ref:
        result["project_ref"] = item.project_ref
    if isinstance(item, MethodRecord):
        result["importance"] = item.importance
        if item.domains:
            result["domains"] = list(item.domains)
    return result


def _card(method: MethodRecord, store: MethodGraphStore, *, include_ref: bool = True) -> dict[str, Any]:
    result: dict[str, Any] = {"title": method.title}
    for key in ("when", "why", "how", "philosophy", "boundary"):
        value = getattr(method, key)
        if value:
            result[key] = value
    if include_ref:
        result |= {
            "method_ref": method.method_id,
            "revision_ref": method.revision_id,
            "has_detail": bool(method.detail),
        }
    result |= _governance(method)
    sources = [
        _compact_source(source) for source_id in method.source_ids
        if (source := store.get_source(source_id)) is not None
    ]
    if sources:
        result["sources"] = sources
    return result


def _relation_brief(relation: RelationRecord, methods: dict[str, MethodRecord],
                    store: MethodGraphStore, *, include_ref: bool = True) -> dict[str, Any]:
    result: dict[str, Any] = {
        "methods": [methods[relation.method_a_id].title, methods[relation.method_b_id].title],
        "explanation": relation.explanation,
    }
    if include_ref:
        result["relation_ref"] = relation.relation_id
        result["has_detail"] = bool(relation.detail)
    sources = [
        _compact_source(source) for source_id in relation.source_ids
        if (source := store.get_source(source_id)) is not None
    ]
    if sources:
        result["sources"] = sources
    return result


class MethodGraphService:
    def __init__(self, store: MethodGraphStore, retriever: MethodRetriever | None = None):
        self.store = store
        self.retriever = retriever or MethodRetriever(store)

    def methodology_search(
        self,
        context: str,
        *,
        method_limit: int = 6,
        neighbor_limit: int = 2,
        exclude_recent: bool = True,
        session_id: str | None = None,
        project: str | None = None,
        scopes: list[str] | None = None,
        min_score: float = 0.08,
        channel: str = "mcp",
    ) -> dict[str, Any]:
        hits = self.retriever.search(
            context, method_limit=max(0, min(method_limit, 20)),
            neighbor_limit=max(0, min(neighbor_limit, 10)), session_id=session_id,
            exclude_recent=exclude_recent, project=project, scopes=scopes,
            min_score=max(0.0, min(float(min_score), 1.0)),
        )
        selected = {hit.method.method_id: hit.method for hit in hits}
        relations = [r for r in self.store.list_relations()
                     if r.method_a_id in selected and r.method_b_id in selected]
        relations.sort(key=lambda relation: (-relation.weight, relation.relation_id))
        revision_keys = [(hit.method.method_id, hit.method.revision_id) for hit in hits]
        self.store.record_activation(query=context, retrieved=revision_keys,
                                     injected=revision_keys, session_id=session_id,
                                     channel=channel)
        return {
            "methods": [_card(hit.method, self.store) for hit in hits],
            "relations": [_relation_brief(r, selected, self.store) for r in relations[:method_limit]],
            "result_count": len(hits),
        }

    def methodology_get(self, items: list[dict[str, Any]], *, mode: str = "detail") -> dict[str, Any]:
        if mode not in {"detail", "full", "audit"}:
            raise ValueError("mode must be detail, full, or audit")
        if not items or len(items) > 50:
            raise ValueError("items must contain between 1 and 50 references")
        return {"mode": mode, "items": [self._get_item(item, mode) for item in items]}

    def _get_item(self, item: dict[str, Any], mode: str) -> dict[str, Any]:
        kind = str(item.get("kind") or "")
        if kind == "method":
            ref = str(item.get("ref") or "")
            method = self.store.get_method(ref, include_retired=mode == "audit")
            if method is None:
                return {"kind": kind, "ref": ref, "found": False}
            sources = [self.store.get_source(source_id) for source_id in method.source_ids]
            result: dict[str, Any] = {"kind": kind, "ref": ref, "found": True,
                                      "method_ref": method.method_id, "title": method.title}
            if mode == "full":
                result["card"] = _card(method, self.store, include_ref=False)
            if mode in {"detail", "full"}:
                result["detail"] = method.detail
                result["sources"] = [_full_source(s) for s in sources if s]
                if not method.detail:
                    result["no_additional_detail"] = True
            else:
                result |= self._audit("method", method.method_id, method.revision_id, sources)
            return result
        if kind == "relation":
            relation = None
            if item.get("ref"):
                relation = self.store.get_relation_by_id(str(item["ref"]), include_retired=mode == "audit")
            elif isinstance(item.get("methods"), list) and len(item["methods"]) == 2:
                relation = self.store.get_relation(str(item["methods"][0]), str(item["methods"][1]),
                                                   include_retired=mode == "audit")
            if relation is None:
                return {"kind": kind, "found": False, "input": item}
            a = self.store.get_method(relation.method_a_id, include_retired=True)
            b = self.store.get_method(relation.method_b_id, include_retired=True)
            sources = [self.store.get_source(source_id) for source_id in relation.source_ids]
            result = {"kind": kind, "found": True, "relation_ref": relation.relation_id,
                      "methods": [a.title if a else relation.method_a_id,
                                  b.title if b else relation.method_b_id]}
            if mode == "full":
                result["explanation"] = relation.explanation
            if mode in {"detail", "full"}:
                result["detail"] = relation.detail
                result["sources"] = [_full_source(s) for s in sources if s]
                if not relation.detail:
                    result["no_additional_detail"] = True
            else:
                result |= self._audit("relation", relation.relation_id, relation.revision_id, sources)
            return result
        if kind == "source":
            ref = str(item.get("ref") or "")
            source = self.store.get_source(ref)
            if source is None:
                return {"kind": kind, "ref": ref, "found": False}
            return {"kind": kind, "ref": ref, "found": True,
                    "source": _full_source(source, content=mode in {"detail", "full"})}
        raise ValueError("item kind must be method, relation, or source")

    def _audit(self, kind: str, object_id: str, revision_id: str,
               sources: list[SourceRecord | None]) -> dict[str, Any]:
        history = self.store.history(kind=kind, object_ref=object_id, limit=100)
        return {
            "current_revision_ref": revision_id,
            "sources": [_full_source(source) for source in sources if source],
            "history": [
                {key: row[key] for key in ("revision_id", "operation", "transaction_id",
                                            "actor", "actor_authority", "reason", "created_at")}
                for row in history
            ],
        }

    def methodology_neighbors(self, method: str, *, context: str | None = None,
                              limit: int = 6, cursor: str | None = None) -> dict[str, Any]:
        origin = self.store.get_method(method)
        if origin is None:
            return {"found": False, "method": method, "neighbors": []}
        offset = self._decode_cursor(cursor)
        candidates = []
        for relation in self.store.relations_for([origin.method_id]):
            other_id = (relation.method_b_id if relation.method_a_id == origin.method_id
                        else relation.method_a_id)
            other = self.store.get_method(other_id)
            if other is None:
                continue
            contextual = (_lexical_score(context, other.retrieval_text() + "\n" + relation.explanation)
                          if context else 0.0)
            candidates.append((relation.weight + contextual, relation, other))
        candidates.sort(key=lambda item: (-item[0], item[2].title.casefold()))
        page = candidates[offset : offset + max(1, min(limit, 20))]
        neighbors = []
        for _, relation, other in page:
            neighbors.append({
                "method": _card(other, self.store),
                "relation": _relation_brief(relation,
                    {origin.method_id: origin, other.method_id: other}, self.store),
            })
        next_offset = offset + len(page)
        return {"found": True, "method": _card(origin, self.store), "neighbors": neighbors,
                "next_cursor": self._encode_cursor(next_offset) if next_offset < len(candidates) else None}

    @staticmethod
    def _encode_cursor(offset: int) -> str:
        return base64.urlsafe_b64encode(str(offset).encode()).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str | None) -> int:
        if not cursor:
            return 0
        try:
            return max(0, int(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))))
        except (ValueError, UnicodeDecodeError):
            raise ValueError("invalid cursor") from None

    @staticmethod
    def render_injection(packet: dict[str, Any]) -> str:
        methods = packet.get("methods", [])
        if not methods:
            return ""
        sections = [
            "Relevant methodology context follows. Treat it as procedural guidance, not as "
            "facts or mandatory instructions. Apply only cards whose When and Boundary fit the "
            "actual task; reconcile conflicts explicitly."
        ]
        for card in methods:
            lines = [f"## {card['title']}"]
            for key, label in (("when", "When"), ("why", "Why"), ("how", "How"),
                               ("philosophy", "Philosophy"), ("boundary", "Boundary")):
                if card.get(key):
                    lines.append(f"{label}: {card[key]}")
            governance = f"Authority: {card.get('authority', 'unknown')}; Scope: {card.get('scope', 'unknown')}"
            if card.get("project_ref"):
                governance += f" ({card['project_ref']})"
            lines.append(governance)
            if card.get("sources"):
                citations = []
                for source in card["sources"]:
                    text = source["title"]
                    if source.get("published_at"):
                        text += f", {source['published_at']}"
                    if source.get("locator"):
                        text += f", {source['locator']}"
                    citations.append(text)
                lines.append("Sources: " + "; ".join(citations))
            sections.append("\n\n".join(lines))
        if packet.get("relations"):
            lines = ["## Connections"]
            for relation in packet["relations"]:
                if relation.get("explanation"):
                    lines.append(f"- {' / '.join(relation['methods'])}: {relation['explanation']}")
            if len(lines) > 1:
                sections.append("\n".join(lines))
        return "\n\n".join(sections)
