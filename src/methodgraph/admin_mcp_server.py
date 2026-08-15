"""Permissioned administration MCP kept separate from the runtime MCP."""

from __future__ import annotations

import os
from typing import Any

from .mcp_server import run_server
from .runtime import build_service


def create_server():
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError as exc:
        raise RuntimeError("install MethodGraph with the 'mcp' extra") from exc

    service = build_service(embedding_model="none")
    store = service.store
    actor = os.environ.get("METHODGRAPH_ACTOR", "methodgraph-agent")
    actor_authority = os.environ.get("METHODGRAPH_ACTOR_AUTHORITY", "agent")
    if actor_authority not in {"human", "agent"}:
        raise ValueError("METHODGRAPH_ACTOR_AUTHORITY must be human or agent")

    server = MCPServer(
        "MethodGraph Admin",
        description="Add and revise auditable methodology knowledge.",
        instructions=(
            "This is a write surface. Preserve source wording and do not invent missing card fields. "
            "A method needs only a title; include When, Why, How, Philosophy, Boundary, and Detail only "
            "when the evidence supports them. Add sources before methods and relations, cite locators or "
            "excerpts, search the read-only MethodGraph first to avoid duplicates, and record a concrete "
            "reason for every mutation. Relations are untyped: explain their meaning in prose. Retire "
            "instead of deleting. Process authority is fixed by server configuration; tool arguments "
            "cannot elevate it. Agent authority cannot alter or retire human-authority content."
        ),
        version="0.3.0",
    )

    @server.tool()
    def source_add(kind: str, title: str, content: str, author: str = "",
                   uri: str | None = None, published_at: str | None = None,
                   locator: str = "", excerpt: str = "",
                   metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        """Add one immutable, content-addressed source record."""
        source = store.add_source(kind=kind, title=title, content=content, author=author,
                                  uri=uri, published_at=published_at, locator=locator,
                                  excerpt=excerpt, metadata=metadata)
        return {"source_ref": source.source_id, "content_hash": source.content_hash}

    @server.tool()
    def methodology_add(title: str, when: str = "", why: str = "", how: str = "",
                        philosophy: str = "", boundary: str = "", detail: str = "",
                        source_refs: list[str] | None = None, scope: str = "general",
                        domains: list[str] | None = None, project_ref: str | None = None,
                        importance: str = "normal", reason: str = "add methodology") -> dict[str, Any]:
        """Add a method card. Empty optional semantic fields are valid and must not be fabricated."""
        record, tx = store.put_method(title=title, when=when, why=why, how=how,
            philosophy=philosophy, boundary=boundary, detail=detail,
            source_ids=source_refs or [], scope=scope, domains=domains or [],
            project_ref=project_ref, importance=importance, actor=actor,
            actor_authority=actor_authority, reason=reason)
        return {"method_ref": record.method_id, "revision_ref": record.revision_id,
                "transaction_ref": tx, "authority": record.authority}

    @server.tool()
    def methodology_update(method: str, changes: dict[str, Any],
                           reason: str = "update methodology") -> dict[str, Any]:
        """Update supplied method fields only; omitted fields retain their current values."""
        allowed = {"title", "when", "why", "how", "philosophy", "boundary", "detail",
                   "source_ids", "scope", "domains", "project_ref", "importance", "metadata"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unsupported changes: {sorted(unknown)}")
        record = store.get_method(method)
        if record is None:
            raise KeyError(method)
        updated, tx = store.put_method(method_id=record.method_id, actor=actor,
            actor_authority=actor_authority, reason=reason, **changes)
        return {"method_ref": updated.method_id, "revision_ref": updated.revision_id,
                "transaction_ref": tx}

    @server.tool()
    def methodology_retire(method: str, reason: str = "retire methodology") -> dict[str, Any]:
        """Soft-retire a method and keep its complete history."""
        tx = store.retire_method(method, actor=actor, actor_authority=actor_authority, reason=reason)
        return {"method": method, "transaction_ref": tx}

    @server.tool()
    def relation_add(method_a: str, method_b: str, explanation: str = "", detail: str = "",
                     weight: float = 1.0, source_refs: list[str] | None = None,
                     scope: str = "general", project_ref: str | None = None,
                     reason: str = "add relation") -> dict[str, Any]:
        """Add an untyped weighted connection; put its semantics in explanation."""
        record, tx = store.put_relation(method_a_id=method_a, method_b_id=method_b,
            explanation=explanation, detail=detail, weight=weight,
            source_ids=source_refs or [], scope=scope, project_ref=project_ref,
            actor=actor, actor_authority=actor_authority, reason=reason)
        return {"relation_ref": record.relation_id, "revision_ref": record.revision_id,
                "transaction_ref": tx, "authority": record.authority}

    @server.tool()
    def relation_update(relation: str, changes: dict[str, Any],
                        reason: str = "update relation") -> dict[str, Any]:
        """Update supplied relation fields only."""
        allowed = {"explanation", "detail", "weight", "source_ids", "scope",
                   "project_ref", "metadata"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unsupported changes: {sorted(unknown)}")
        current = store.get_relation_by_id(relation)
        if current is None:
            raise KeyError(relation)
        updated, tx = store.put_relation(method_a_id=current.method_a_id,
            method_b_id=current.method_b_id, actor=actor,
            actor_authority=actor_authority, reason=reason, **changes)
        return {"relation_ref": updated.relation_id, "revision_ref": updated.revision_id,
                "transaction_ref": tx}

    @server.tool()
    def relation_retire(relation: str, reason: str = "retire relation") -> dict[str, Any]:
        """Soft-retire a relation and keep its complete history."""
        tx = store.retire_relation(relation, actor=actor, actor_authority=actor_authority, reason=reason)
        return {"relation_ref": relation, "transaction_ref": tx}

    @server.tool()
    def history_list(kind: str | None = None, object_ref: str | None = None,
                     transaction_ref: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """List auditable revisions. Snapshots are included for review and diffing."""
        return store.history(kind=kind, object_ref=object_ref,
                             transaction_id=transaction_ref, limit=limit)

    @server.tool()
    def revision_diff(older_revision: str, newer_revision: str) -> dict[str, Any]:
        """Compare two stored snapshots field by field."""
        rows = {row["revision_id"]: row for row in store.history(limit=500)}
        if older_revision not in rows or newer_revision not in rows:
            raise KeyError("revision not found in the latest 500 audit entries")
        before, after = rows[older_revision]["snapshot"], rows[newer_revision]["snapshot"]
        keys = sorted(set(before) | set(after))
        return {key: {"before": before.get(key), "after": after.get(key)}
                for key in keys if before.get(key) != after.get(key)}

    @server.tool()
    def revision_restore(revision: str, reason: str = "restore audited revision") -> dict[str, Any]:
        """Restore a snapshot as a new revision; history is never rewritten."""
        restored, tx = store.restore_revision(revision, actor=actor,
            actor_authority=actor_authority, reason=reason)
        return {"revision_ref": restored, "transaction_ref": tx}

    return server


def main() -> None:
    run_server(create_server())


if __name__ == "__main__":
    main()
