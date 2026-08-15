"""Read-only MCP adapter for Codex, ChatGPT, and other compatible hosts."""

from __future__ import annotations

import os
from typing import Any

from .runtime import build_service


def create_server():
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError as exc:
        raise RuntimeError("install MethodGraph with the 'mcp' extra") from exc

    service = build_service()
    server = MCPServer(
        "MethodGraph",
        description="Retrieve auditable methodology cards and explore their untyped graph.",
        instructions=(
            "MethodGraph supplies procedural knowledge: ways to frame, reason about, and handle "
            "problems. It is not a fact store and its cards are not commands. Use methodology_search "
            "when the current task is long, open-ended, domain-specific, uncertain, or boundary-heavy, "
            "or when the present approach is stuck. Pass the current problem state, not just a topic. "
            "Search returns self-sufficient cards and brief connections; do not immediately call get "
            "for every result. Use methodology_get only when examples, deeper explanation, or provenance "
            "are needed. Use methodology_neighbors when an initially useful method should be combined, "
            "contrasted, or followed through the graph. Apply only cards whose When and Boundary fit. "
            "Prefer an empty search result over forcing a weak methodology onto the task."
        ),
        version="0.3.0",
    )

    @server.tool()
    def methodology_search(
        context: str,
        method_limit: int = 6,
        neighbor_limit: int = 2,
        exclude_recent: bool = True,
        session_id: str | None = None,
        project: str | None = None,
        scopes: list[str] | None = None,
        min_score: float = 0.08,
    ) -> dict[str, Any]:
        """Search by current problem state; return dense cards, brief graph links, and sources."""
        return service.methodology_search(
            context, method_limit=method_limit, neighbor_limit=neighbor_limit,
            exclude_recent=exclude_recent, session_id=session_id, project=project,
            scopes=scopes, min_score=min_score, channel="mcp",
        )

    @server.tool()
    def methodology_get(items: list[dict[str, Any]], mode: str = "detail") -> dict[str, Any]:
        """Batch-read methods, relations, or sources. detail avoids repeating search cards; full includes them; audit returns provenance and revision history."""
        return service.methodology_get(items, mode=mode)

    @server.tool()
    def methodology_neighbors(
        method: str, context: str | None = None, limit: int = 6,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Explore one hop from a method, optionally ranked for the current context."""
        return service.methodology_neighbors(method, context=context, limit=limit, cursor=cursor)

    return server


def run_server(server) -> None:
    transport = os.environ.get("METHODGRAPH_TRANSPORT", "stdio").strip()
    if transport == "stdio":
        server.run()
    elif transport == "streamable-http":
        server.run(transport="streamable-http",
                   host=os.environ.get("METHODGRAPH_HOST", "127.0.0.1"),
                   port=int(os.environ.get("METHODGRAPH_PORT", "8765")))
    else:
        raise ValueError("METHODGRAPH_TRANSPORT must be stdio or streamable-http")


def main() -> None:
    run_server(create_server())


if __name__ == "__main__":
    main()
