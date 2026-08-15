"""Thin administrative MCP adapter; all writes are committed by the HTTP server."""

from __future__ import annotations

from typing import Any

from .content import git_identity
from .http_client import delete_json, get_json, patch_json, post_json
from .mcp_server import run_server


def _write(payload: dict[str, Any]) -> dict[str, Any]:
    name, email = git_identity()
    return payload | {"author_name": name, "author_email": email}


def create_server():
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError as exc:
        raise RuntimeError("install MethodGraph with the 'mcp' extra") from exc
    server = MCPServer(
        "MethodGraph Admin",
        description="Audited methodology graph administration through the server-side Git repository.",
        instructions=(
            "Search the read-only graph before writing. Preserve source wording and never invent unsupported fields. "
            "Every mutation needs a concrete reason. Writes are attributed to the local Git identity; Git history "
            "is the audit and rollback mechanism. Delete relations before methods."
        ), version="0.4.0",
    )

    @server.tool()
    def source_add(kind: str, title: str, content: str, reason: str,
                   author: str = "", uri: str | None = None, published_at: str | None = None,
                   locator: str = "", excerpt: str = "", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        return post_json("/v1/admin/sources", _write({"kind": kind, "title": title, "content": content,
            "reason": reason, "author": author, "uri": uri, "published_at": published_at,
            "locator": locator, "excerpt": excerpt, "metadata": metadata or {}}))

    @server.tool()
    def source_delete(source: str, reason: str, expected_revision: str | None = None) -> dict[str, Any]:
        return delete_json(f"/v1/admin/sources/{source}", _write({"reason": reason,
            "expected_revision": expected_revision}))

    @server.tool()
    def methodology_add(title: str, reason: str, when: str = "", why: str = "", how: str = "",
                        philosophy: str = "", boundary: str = "", detail: str = "",
                        source_refs: list[str] | None = None, scope: str = "general",
                        domains: list[str] | None = None, project_ref: str | None = None,
                        importance: str = "normal", authority: str = "agent") -> dict[str, Any]:
        return post_json("/v1/admin/methods", _write({"title": title, "reason": reason,
            "when": when, "why": why, "how": how, "philosophy": philosophy, "boundary": boundary,
            "detail": detail, "source_refs": source_refs or [], "scope": scope, "domains": domains or [],
            "project_ref": project_ref, "importance": importance, "authority": authority}))

    @server.tool()
    def methodology_update(method: str, changes: dict[str, Any], reason: str,
                           expected_revision: str | None = None) -> dict[str, Any]:
        return patch_json(f"/v1/admin/methods/{method}", _write({"changes": changes, "reason": reason,
            "expected_revision": expected_revision}))

    @server.tool()
    def methodology_delete(method: str, reason: str, expected_revision: str | None = None) -> dict[str, Any]:
        return delete_json(f"/v1/admin/methods/{method}", _write({"reason": reason, "expected_revision": expected_revision}))

    @server.tool()
    def relation_add(method_a: str, method_b: str, reason: str, explanation: str = "", detail: str = "",
                     weight: float = 1.0, source_refs: list[str] | None = None,
                     scope: str = "general", project_ref: str | None = None) -> dict[str, Any]:
        return post_json("/v1/admin/relations", _write({"method_a": method_a, "method_b": method_b,
            "reason": reason, "explanation": explanation, "detail": detail, "weight": weight,
            "source_refs": source_refs or [], "scope": scope, "project_ref": project_ref}))

    @server.tool()
    def relation_update(relation: str, changes: dict[str, Any], reason: str,
                        expected_revision: str | None = None) -> dict[str, Any]:
        return patch_json(f"/v1/admin/relations/{relation}", _write({"changes": changes, "reason": reason,
            "expected_revision": expected_revision}))

    @server.tool()
    def relation_delete(relation: str, reason: str, expected_revision: str | None = None) -> dict[str, Any]:
        return delete_json(f"/v1/admin/relations/{relation}", _write({"reason": reason, "expected_revision": expected_revision}))

    @server.tool()
    def history_list(kind: str = "method", object_ref: str = "", limit: int = 50) -> dict[str, Any]:
        suffix = f"?kind={kind}&ref={object_ref}&limit={limit}"
        return get_json(f"/v1/admin/history{suffix}")

    @server.tool()
    def revision_restore(kind: str, object_ref: str, revision: str, reason: str,
                         expected_revision: str | None = None) -> dict[str, Any]:
        return post_json("/v1/admin/restore", _write({"kind": kind, "ref": object_ref,
            "revision": revision, "reason": reason, "expected_revision": expected_revision}))

    return server


def main() -> None:
    run_server(create_server())
