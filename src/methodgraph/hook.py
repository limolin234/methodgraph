"""Codex UserPromptSubmit hook that injects MethodGraph cards."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from .runtime import build_service
from .service import MethodGraphService


def _structured_result(result: Any) -> dict:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    for item in getattr(result, "content", []):
        text = getattr(item, "text", None)
        if text:
            loaded = json.loads(text)
            if isinstance(loaded, dict):
                return loaded
    return {}


async def _search_remote(prompt: str, session_id: str | None, limit: int) -> dict:
    try:
        from mcp import Client
    except ImportError as exc:
        raise RuntimeError("remote hook mode requires the 'mcp' extra") from exc
    url = os.environ.get("METHODGRAPH_MCP_URL", "http://127.0.0.1:8765/mcp")
    async with Client(url, read_timeout_seconds=30) as client:
        result = await client.call_tool(
            "methodology_search",
            {
                "context": prompt,
                "method_limit": limit,
                "neighbor_limit": int(os.environ.get("METHODGRAPH_HOOK_NEIGHBOR_LIMIT", "2")),
                "exclude_recent": True,
                "session_id": session_id,
                "project": os.environ.get("METHODGRAPH_PROJECT") or None,
                "min_score": float(os.environ.get("METHODGRAPH_HOOK_MIN_SCORE", "0.16")),
            },
        )
    return _structured_result(result)


def _search_local(prompt: str, session_id: str | None, limit: int) -> dict:
    # Hooks must remain fast when the persistent embedding server is unavailable.
    service = build_service(embedding_model="none")
    return service.methodology_search(
        prompt,
        method_limit=limit,
        neighbor_limit=int(os.environ.get("METHODGRAPH_HOOK_NEIGHBOR_LIMIT", "2")),
        session_id=session_id,
        exclude_recent=True,
        project=os.environ.get("METHODGRAPH_PROJECT") or None,
        min_score=float(os.environ.get("METHODGRAPH_HOOK_MIN_SCORE", "0.16")),
        channel="hook",
    )


def build_hook_output(payload: dict, *, allow_remote: bool = True) -> dict:
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        return {}
    session_id = payload.get("session_id") or payload.get("turn_id")
    limit = max(1, int(os.environ.get("METHODGRAPH_HOOK_LIMIT", "6")))
    packet: dict = {}
    if allow_remote and os.environ.get("METHODGRAPH_HOOK_REMOTE", "1") != "0":
        try:
            packet = asyncio.run(_search_remote(prompt, session_id, limit))
        except Exception as exc:
            print(f"MethodGraph remote hook fallback: {exc}", file=sys.stderr)
    if not packet.get("methods"):
        packet = _search_local(prompt, session_id, limit)
    context = MethodGraphService.render_injection(packet)
    if not context:
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    }


def main() -> None:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook input must be a JSON object")
        output = build_hook_output(payload)
        if output:
            json.dump(output, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
    except Exception as exc:
        # A retrieval failure must never block the user's prompt.
        print(f"MethodGraph hook skipped: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
