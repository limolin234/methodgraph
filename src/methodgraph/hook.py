"""Codex UserPromptSubmit hook that injects MethodGraph cards."""

from __future__ import annotations

import json
import os
import sys
from .http_client import post_json
from .runtime import build_service
from .service import MethodGraphService

HOOK_PROMPT_CHANNEL = "hook_prompt"
DEFAULT_HOOK_MIN_SCORE = "0.22"
RECENT_PROMPT_LIMIT = 2
PROMPT_CONTEXT_CHARS = 2000


def _search_remote(payload: dict, limit: int) -> dict:
    return post_json("/v1/hooks/retrieve", payload | {
        "method_limit": limit,
        "neighbor_limit": int(os.environ.get("METHODGRAPH_HOOK_NEIGHBOR_LIMIT", "2")),
        "min_score": float(os.environ.get("METHODGRAPH_HOOK_MIN_SCORE", DEFAULT_HOOK_MIN_SCORE)),
    })


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
        min_score=float(os.environ.get("METHODGRAPH_HOOK_MIN_SCORE", DEFAULT_HOOK_MIN_SCORE)),
        channel="hook",
    )


def _build_search_context(payload: dict, prompt: str, session_id: str | None) -> str:
    recent: list[str] = []
    if session_id:
        try:
            service = build_service(embedding_model="none")
            recent = service.store.recent_activation_queries(
                str(session_id), channel=HOOK_PROMPT_CHANNEL, limit=RECENT_PROMPT_LIMIT
            )
            service.store.record_activation(
                query=prompt,
                retrieved=[],
                injected=[],
                session_id=str(session_id),
                channel=HOOK_PROMPT_CHANNEL,
                metadata={"turn_id": payload.get("turn_id")},
            )
        except Exception:
            # Context enrichment must not make the prompt hook fail.
            recent = []

    seen = {prompt}
    previous: list[str] = []
    for item in recent:
        cleaned = item.strip()
        if cleaned and cleaned not in seen:
            previous.append(cleaned[:PROMPT_CONTEXT_CHARS])
            seen.add(cleaned)

    sections = [f"Current user request:\n{prompt}"]
    if previous:
        sections.append("Recent user requests:\n" + "\n".join(f"- {item}" for item in previous))
    scope = []
    if cwd := str(payload.get("cwd") or "").strip():
        scope.append(f"workspace={cwd}")
    if project := os.environ.get("METHODGRAPH_PROJECT"):
        scope.append(f"project={project}")
    if scope:
        sections.append("Task scope: " + "; ".join(scope))
    return "\n\n".join(sections)


def build_hook_output(payload: dict, *, allow_remote: bool = True) -> dict:
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        return {}
    session_id = payload.get("session_id") or payload.get("turn_id")
    limit = max(1, int(os.environ.get("METHODGRAPH_HOOK_LIMIT", "6")))
    if allow_remote and os.environ.get("METHODGRAPH_HOOK_REMOTE", "1") != "0":
        try:
            return _search_remote(payload, limit)
        except Exception as exc:
            print(f"MethodGraph remote hook skipped: {exc}", file=sys.stderr)
            if os.environ.get("METHODGRAPH_HOOK_LOCAL_FALLBACK", "0") != "1":
                return {}
    search_context = _build_search_context(payload, prompt, session_id)
    packet = _search_local(search_context, session_id, limit)
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
