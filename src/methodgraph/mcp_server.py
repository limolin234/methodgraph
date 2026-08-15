"""Thin read-only MCP adapter. The HTTP service owns storage and retrieval."""

from __future__ import annotations

from typing import Any

from .http_client import post_json


def create_server():
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError as exc:
        raise RuntimeError("install MethodGraph with the 'mcp' extra") from exc
    server = MCPServer(
        "MethodGraph",
        description="Search and explore the server-side methodology graph.",
        instructions=(
            "Use methodology_search before planning complex, open-ended, ambiguous, or boundary-heavy work. "
            "Use methodology_get for detail only when the returned card says it has additional detail. "
            "Use methodology_neighbors to follow a relevant graph connection. Treat cards as guidance, not facts; "
            "respect When and Boundary and reconcile conflicts explicitly."
        ), version="0.4.0",
    )

    @server.tool()
    def methodology_search(context: str, method_limit: int = 6, neighbor_limit: int = 2,
                           exclude_recent: bool = True, session_id: str | None = None,
                           project: str | None = None, scopes: list[str] | None = None,
                           min_score: float = 0.08) -> dict[str, Any]:
        return post_json("/v1/search", {"context": context, "method_limit": method_limit,
            "neighbor_limit": neighbor_limit, "exclude_recent": exclude_recent,
            "session_id": session_id, "project": project, "scopes": scopes, "min_score": min_score})

    @server.tool()
    def methodology_get(items: list[dict[str, Any]], mode: str = "detail") -> dict[str, Any]:
        return post_json("/v1/get", {"items": items, "mode": mode})

    @server.tool()
    def methodology_neighbors(method: str, context: str | None = None,
                               limit: int = 6, cursor: str | None = None) -> dict[str, Any]:
        return post_json("/v1/neighbors", {"method": method, "context": context,
            "limit": limit, "cursor": cursor})

    return server


def run_server(server) -> None:
    server.run()


def main() -> None:
    run_server(create_server())
