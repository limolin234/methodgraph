"""Authoritative MethodGraph HTTP service."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from .config import AppConfig, load_config
from .content import GitContentRepository, _check_identity
from .models import MethodRecord, RelationRecord, SourceRecord
from .runtime import build_configured_service
from .service import MethodGraphService
from .store import AUTHORITIES, IMPORTANCE, SCOPES, SOURCE_KINDS, _relation_id


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _identity(payload: dict[str, Any]) -> tuple[str, str]:
    return (_check_identity(str(payload.get("author_name", "")), "author_name"),
            _check_identity(str(payload.get("author_email", "")), "author_email"))


def _reason(payload: dict[str, Any]) -> str:
    value = str(payload.get("reason", "")).strip()
    if not value:
        raise ValueError("reason must not be empty")
    return value


def _short_prompt_has_problem_signal(prompt: str) -> bool:
    folded = prompt.casefold()
    signals = ("怎么", "如何", "为什么", "问题", "方案", "边界", "设计", "实现", "分析",
               "比较", "选择", "诊断", "计划", "review", "debug", "design", "plan", "why", "how")
    return any(signal in folded for signal in signals)


def _validate_method(method: MethodRecord, service: MethodGraphService) -> None:
    if not method.title.strip():
        raise ValueError("title must not be empty")
    duplicate = service.store.get_method(method.title)
    if duplicate and duplicate.method_id != method.method_id:
        raise ValueError(f"an active method already has title: {method.title}")
    if method.scope not in SCOPES or method.importance not in IMPORTANCE or method.authority not in AUTHORITIES:
        raise ValueError("invalid authority, scope, or importance")
    if not isinstance(method.metadata, dict):
        raise ValueError("metadata must be an object")
    for source_id in method.source_ids:
        if service.store.get_source(source_id) is None:
            raise KeyError(source_id)


def _validate_relation(relation: RelationRecord, service: MethodGraphService) -> None:
    if relation.scope not in SCOPES or relation.authority not in AUTHORITIES:
        raise ValueError("invalid relation authority or scope")
    if not 0 <= float(relation.weight) <= 1:
        raise ValueError("weight must be between 0 and 1")
    if not isinstance(relation.metadata, dict):
        raise ValueError("metadata must be an object")
    for source_id in relation.source_ids:
        if service.store.get_source(source_id) is None:
            raise KeyError(source_id)


class AdminOperations:
    def __init__(self, service: MethodGraphService, content: GitContentRepository):
        self.service = service
        self.store = service.store
        self.content = content

    def _finish(self, head: str, revision: str = "") -> dict[str, Any]:
        projection = self.content.sync_projection(self.store)
        result: dict[str, Any] = {"commit": head, "projection": projection}
        if revision:
            result["revision_ref"] = revision
        return result

    def source_add(self, payload: dict[str, Any]) -> dict[str, Any]:
        content = str(payload.get("content", ""))
        title = str(payload.get("title", "")).strip()
        kind = str(payload.get("kind", ""))
        if not title or not content:
            raise ValueError("source title and content must not be empty")
        if kind not in SOURCE_KINDS:
            raise ValueError(f"unsupported source kind: {kind}")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        source_id = f"src_{digest[:24]}"
        existing = self.store.get_source(source_id)
        if existing:
            return {"source_ref": existing.source_id, "content_hash": existing.content_hash,
                    "unchanged": True, "commit": self.content.head()}
        source = SourceRecord(source_id=source_id, kind=kind, title=title, content=content,
            content_hash=digest, captured_at=_now(), author=str(payload.get("author", "")).strip(),
            uri=payload.get("uri"), published_at=payload.get("published_at"),
            locator=str(payload.get("locator", "")).strip(), excerpt=str(payload.get("excerpt", "")).strip(),
            metadata=payload.get("metadata") or {})
        if not isinstance(source.metadata, dict):
            raise ValueError("metadata must be an object")
        name, email = _identity(payload)
        head, revision = self.content.add_source(source, author_name=name, author_email=email,
            reason=_reason(payload), expected_revision=payload.get("expected_revision"))
        return self._finish(head, revision) | {"source_ref": source_id, "content_hash": digest}

    def method_add(self, payload: dict[str, Any]) -> dict[str, Any]:
        title = str(payload.get("title", "")).strip()
        if not title:
            raise ValueError("title must not be empty")
        scope = str(payload.get("scope", "general"))
        importance = str(payload.get("importance", "normal"))
        if scope not in SCOPES or importance not in IMPORTANCE:
            raise ValueError("invalid scope or importance")
        source_ids = tuple(dict.fromkeys(str(x) for x in payload.get("source_refs", [])))
        for source_id in source_ids:
            if self.store.get_source(source_id) is None:
                raise KeyError(source_id)
        now = _now()
        method = MethodRecord(method_id=_id("method"), title=title,
            when=str(payload.get("when", "")).strip(), why=str(payload.get("why", "")).strip(),
            how=str(payload.get("how", "")).strip(), philosophy=str(payload.get("philosophy", "")).strip(),
            boundary=str(payload.get("boundary", "")).strip(), detail=str(payload.get("detail", "")).strip(),
            authority=str(payload.get("authority", "agent")), scope=scope,
            domains=tuple(dict.fromkeys(str(x).strip() for x in payload.get("domains", []) if str(x).strip())),
            project_ref=payload.get("project_ref"), importance=importance, created_at=now, updated_at=now,
            created_by=str(payload.get("author_name", "")), source_ids=source_ids,
            metadata=payload.get("metadata") or {})
        _validate_method(method, self.service)
        name, email = _identity(payload)
        head, revision = self.content.add_method(method, author_name=name, author_email=email, reason=_reason(payload))
        return self._finish(head, revision) | {"method_ref": method.method_id}

    def method_update(self, method_ref: str, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.store.get_method(method_ref)
        if current is None:
            raise KeyError(method_ref)
        changes = payload.get("changes") or {}
        if not isinstance(changes, dict):
            raise ValueError("changes must be an object")
        allowed = {"title", "when", "why", "how", "philosophy", "boundary", "detail",
                   "source_refs", "scope", "domains", "project_ref", "importance", "metadata"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unsupported changes: {sorted(unknown)}")
        values = {key: changes[key] for key in allowed - {"source_refs", "domains"} if key in changes}
        if "source_refs" in changes:
            values["source_ids"] = tuple(dict.fromkeys(str(x) for x in changes["source_refs"]))
            for source_id in values["source_ids"]:
                if self.store.get_source(source_id) is None:
                    raise KeyError(source_id)
        if "domains" in changes:
            values["domains"] = tuple(dict.fromkeys(str(x).strip() for x in changes["domains"] if str(x).strip()))
        values["updated_at"] = _now()
        updated = replace(current, **values)
        _validate_method(updated, self.service)
        name, email = _identity(payload)
        head, revision = self.content.add_method(updated, author_name=name, author_email=email,
            reason=_reason(payload), expected_revision=payload.get("expected_revision"))
        return self._finish(head, revision) | {"method_ref": current.method_id}

    def relation_add(self, payload: dict[str, Any]) -> dict[str, Any]:
        first = self.store.get_method(str(payload.get("method_a", "")))
        second = self.store.get_method(str(payload.get("method_b", "")))
        if not first or not second:
            raise KeyError("both methods must exist")
        if first.method_id == second.method_id:
            raise ValueError("a relation must connect different methods")
        if self.store.get_relation(first.method_id, second.method_id):
            raise ValueError("relation already exists")
        a, b = sorted((first.method_id, second.method_id))
        weight = float(payload.get("weight", 1.0))
        if not 0 <= weight <= 1:
            raise ValueError("weight must be between 0 and 1")
        now = _now()
        relation = RelationRecord(relation_id=_relation_id(a, b), method_a_id=a, method_b_id=b,
            explanation=str(payload.get("explanation", "")).strip(), detail=str(payload.get("detail", "")).strip(),
            weight=weight, authority=str(payload.get("authority", "agent")), scope=str(payload.get("scope", "general")),
            project_ref=payload.get("project_ref"), created_at=now, updated_at=now,
            created_by=str(payload.get("author_name", "")),
            source_ids=tuple(dict.fromkeys(str(x) for x in payload.get("source_refs", []))),
            metadata=payload.get("metadata") or {})
        _validate_relation(relation, self.service)
        name, email = _identity(payload)
        head, revision = self.content.add_relation(relation, author_name=name, author_email=email, reason=_reason(payload))
        return self._finish(head, revision) | {"relation_ref": relation.relation_id}

    def relation_update(self, relation_ref: str, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.store.get_relation_by_id(relation_ref)
        if current is None:
            raise KeyError(relation_ref)
        changes = payload.get("changes") or {}
        allowed = {"explanation", "detail", "weight", "source_refs", "scope", "project_ref", "metadata"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unsupported changes: {sorted(unknown)}")
        values = {key: changes[key] for key in allowed - {"source_refs"} if key in changes}
        if "source_refs" in changes:
            values["source_ids"] = tuple(dict.fromkeys(str(x) for x in changes["source_refs"]))
        values["updated_at"] = _now()
        updated = replace(current, **values)
        _validate_relation(updated, self.service)
        name, email = _identity(payload)
        head, revision = self.content.add_relation(updated, author_name=name, author_email=email,
            reason=_reason(payload), expected_revision=payload.get("expected_revision"))
        return self._finish(head, revision) | {"relation_ref": current.relation_id}

    def delete(self, kind: str, object_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if kind == "method":
            method = self.store.get_method(object_id)
            if method is None:
                raise KeyError(object_id)
            object_id = method.method_id
            if self.store.relations_for([object_id]):
                raise ValueError("delete connected relations before deleting a method")
        elif kind == "relation":
            relation = self.store.get_relation_by_id(object_id)
            if relation is None:
                raise KeyError(object_id)
            object_id = relation.relation_id
        elif kind == "source":
            source = self.store.get_source(object_id)
            if source is None:
                raise KeyError(object_id)
            if any(object_id in item.source_ids for item in self.store.list_methods()) or \
               any(object_id in item.source_ids for item in self.store.list_relations()):
                raise ValueError("delete methods and relations citing this source first")
            object_id = source.source_id
        name, email = _identity(payload)
        head = self.content.delete(kind, object_id, author_name=name, author_email=email,
            reason=_reason(payload), expected_revision=payload.get("expected_revision"))
        return self._finish(head) | {"deleted": True, "kind": kind, "ref": object_id}

    def restore(self, payload: dict[str, Any]) -> dict[str, Any]:
        kind, object_id = str(payload.get("kind", "")), str(payload.get("ref", ""))
        if kind not in {"method", "relation", "source"} or not object_id:
            raise ValueError("kind and ref are required")
        name, email = _identity(payload)
        head, revision = self.content.restore(kind, object_id, str(payload.get("revision", "")),
            author_name=name, author_email=email, reason=_reason(payload),
            expected_revision=payload.get("expected_revision"))
        return self._finish(head, revision) | {"restored": True, "kind": kind, "ref": object_id}


def create_app(config: AppConfig | None = None):
    try:
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.responses import JSONResponse
        from starlette.routing import Route
    except ImportError as exc:
        raise RuntimeError("install MethodGraph with the 'server' extra") from exc

    config = config or load_config()
    service, content = build_configured_service(config)
    admin = AdminOperations(service, content)

    async def payload(request: Request) -> dict[str, Any]:
        value = await request.json()
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    async def health(_request: Request):
        head, indexed = content.head(), service.store.indexed_commit()
        return JSONResponse({"ok": True, "synchronized": head == indexed,
                             "head": head, "indexed_commit": indexed})

    async def ready(_request: Request):
        head, indexed = content.head(), service.store.indexed_commit()
        synchronized = head == indexed
        return JSONResponse({"ok": synchronized, "synchronized": synchronized,
                             "head": head, "indexed_commit": indexed},
                            status_code=200 if synchronized else 503)

    async def search(request: Request):
        data = await payload(request)
        context = str(data.pop("context", ""))
        return JSONResponse(service.methodology_search(context, **data))

    async def get(request: Request):
        data = await payload(request)
        return JSONResponse(service.methodology_get(data.get("items", []), mode=str(data.get("mode", "detail"))))

    async def neighbors(request: Request):
        data = await payload(request)
        method = str(data.pop("method", ""))
        return JSONResponse(service.methodology_neighbors(method, **data))

    async def hook(request: Request):
        data = await payload(request)
        prompt = str(data.get("prompt", "")).strip()
        session_id = str(data.get("session_id") or data.get("conversation_id") or "") or None
        if not prompt:
            return JSONResponse({})
        recent = service.store.recent_activation_queries(session_id, channel="hook_prompt", limit=2) if session_id else []
        folded = prompt.casefold()
        exact_method = service.store.get_method(prompt) or next(
            (item for item in service.store.list_methods()
             if item.title.casefold().startswith((folded + " ", folded + "("))), None
        )
        if len(prompt) <= 12 and exact_method is None and not _short_prompt_has_problem_signal(prompt) and not recent:
            service.store.record_activation(query=prompt, retrieved=[], injected=[], session_id=session_id,
                                            channel="hook_prompt")
            return JSONResponse({})
        context_parts = [f"Current user request:\n{prompt}"]
        if recent:
            context_parts.append("Recent user requests (oldest first):\n" + "\n".join(reversed(recent)))
        cwd = data.get("cwd")
        if cwd:
            context_parts.append(f"workspace={cwd}")
        service.store.record_activation(query=prompt, retrieved=[], injected=[], session_id=session_id, channel="hook_prompt")
        min_score = float(data.get("min_score", 0.22))
        if exact_method is not None:
            min_score = min(min_score, 0.18)
        search_context = prompt if exact_method is not None else "\n\n".join(context_parts)
        packet = service.methodology_search(search_context, method_limit=int(data.get("method_limit", 6)),
            neighbor_limit=int(data.get("neighbor_limit", 2)), session_id=session_id,
            min_score=min_score, channel="hook")
        rendered = service.render_injection(packet)
        if not rendered:
            return JSONResponse({})
        return JSONResponse({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": rendered}})

    async def admin_route(request: Request):
        data = await payload(request)
        name = request.path_params["name"]
        ref = request.path_params.get("ref")
        if name == "sources" and request.method == "POST": result = admin.source_add(data)
        elif name == "methods" and request.method == "POST": result = admin.method_add(data)
        elif name == "methods" and request.method == "PATCH": result = admin.method_update(ref, data)
        elif name == "methods" and request.method == "DELETE": result = admin.delete("method", ref, data)
        elif name == "relations" and request.method == "POST": result = admin.relation_add(data)
        elif name == "relations" and request.method == "PATCH": result = admin.relation_update(ref, data)
        elif name == "relations" and request.method == "DELETE": result = admin.delete("relation", ref, data)
        elif name == "sources" and request.method == "DELETE": result = admin.delete("source", ref, data)
        elif name == "restore" and request.method == "POST": result = admin.restore(data)
        else: raise KeyError(request.url.path)
        return JSONResponse(result)

    async def history(request: Request):
        kind = request.query_params.get("kind", "method")
        ref = request.query_params.get("ref", "")
        return JSONResponse({"history": content.history(kind, ref, int(request.query_params.get("limit", "50")))})

    async def on_error(_request: Request, exc: Exception):
        status = 404 if isinstance(exc, KeyError) else 409 if "stale revision" in str(exc) else 400
        return JSONResponse({"error": type(exc).__name__, "detail": str(exc)}, status_code=status)

    routes = [Route("/healthz", health), Route("/readyz", ready), Route("/v1/search", search, methods=["POST"]),
        Route("/v1/get", get, methods=["POST"]), Route("/v1/neighbors", neighbors, methods=["POST"]),
        Route("/v1/hooks/retrieve", hook, methods=["POST"]),
        Route("/v1/hooks/claude/user-prompt-submit", hook, methods=["POST"]),
        Route("/v1/admin/history", history, methods=["GET"]),
        Route("/v1/admin/{name}", admin_route, methods=["POST"]),
        Route("/v1/admin/{name}/{ref}", admin_route, methods=["PATCH", "DELETE"])]
    return Starlette(routes=routes, exception_handlers={ValueError: on_error, KeyError: on_error,
                                                        RuntimeError: on_error, PermissionError: on_error})


def main() -> None:
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("install MethodGraph with the 'server' extra") from exc
    config = load_config()
    uvicorn.run(create_app(config), host=config.server.host, port=config.server.port)
