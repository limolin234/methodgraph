"""SQLite authority store with immutable revisions and auditable recovery."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from array import array
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import MethodRecord, RelationRecord, SourceRecord

AUTHORITIES = frozenset({"human", "agent"})
SCOPES = frozenset({"general", "domain", "project"})
IMPORTANCE = frozenset({"core", "major", "normal", "minor"})
SOURCE_KINDS = frozenset(
    {"book", "handbook", "paper", "standard", "post", "team", "project", "agent_synthesis", "other"}
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _object(value: str | None) -> dict[str, Any]:
    loaded = json.loads(value or "{}")
    return loaded if isinstance(loaded, dict) else {}


def _list(value: str | None) -> tuple[str, ...]:
    loaded = json.loads(value or "[]")
    return tuple(str(item) for item in loaded) if isinstance(loaded, list) else ()


def _required(name: str, value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} must not be empty")
    return cleaned


def _relation_id(method_a_id: str, method_b_id: str) -> str:
    pair = "\0".join(sorted((method_a_id, method_b_id)))
    return f"relation_{hashlib.sha256(pair.encode()).hexdigest()[:24]}"


class MethodGraphStore:
    """Current records are projections; revision snapshots are the recovery log."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA journal_mode = WAL")
        db.execute("PRAGMA busy_timeout = 30000")
        return db

    def initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS mg_sources (
                    source_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    author TEXT NOT NULL DEFAULT '',
                    uri TEXT,
                    published_at TEXT,
                    locator TEXT NOT NULL DEFAULT '',
                    excerpt TEXT NOT NULL DEFAULT '',
                    captured_at TEXT NOT NULL,
                    content_hash TEXT NOT NULL UNIQUE,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS mg_methods (
                    method_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    when_text TEXT NOT NULL DEFAULT '',
                    why_text TEXT NOT NULL DEFAULT '',
                    how_text TEXT NOT NULL DEFAULT '',
                    philosophy TEXT NOT NULL DEFAULT '',
                    boundary TEXT NOT NULL DEFAULT '',
                    detail TEXT NOT NULL DEFAULT '',
                    revision_id TEXT NOT NULL,
                    authority TEXT NOT NULL CHECK(authority IN ('human','agent')),
                    scope TEXT NOT NULL CHECK(scope IN ('general','domain','project')),
                    domains_json TEXT NOT NULL DEFAULT '[]',
                    project_ref TEXT,
                    importance TEXT NOT NULL CHECK(importance IN ('core','major','normal','minor')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    retired_at TEXT,
                    created_by TEXT NOT NULL,
                    source_ids_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_mg_method_title_active
                    ON mg_methods(title COLLATE NOCASE) WHERE retired_at IS NULL;

                CREATE TABLE IF NOT EXISTS mg_relations (
                    relation_id TEXT PRIMARY KEY,
                    method_a_id TEXT NOT NULL,
                    method_b_id TEXT NOT NULL,
                    explanation TEXT NOT NULL DEFAULT '',
                    detail TEXT NOT NULL DEFAULT '',
                    weight REAL NOT NULL CHECK(weight >= 0 AND weight <= 1),
                    revision_id TEXT NOT NULL,
                    authority TEXT NOT NULL CHECK(authority IN ('human','agent')),
                    scope TEXT NOT NULL CHECK(scope IN ('general','domain','project')),
                    project_ref TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    retired_at TEXT,
                    created_by TEXT NOT NULL,
                    source_ids_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(method_a_id) REFERENCES mg_methods(method_id),
                    FOREIGN KEY(method_b_id) REFERENCES mg_methods(method_id),
                    UNIQUE(method_a_id, method_b_id)
                );

                CREATE TABLE IF NOT EXISTS mg_revisions (
                    revision_id TEXT PRIMARY KEY,
                    object_kind TEXT NOT NULL CHECK(object_kind IN ('method','relation')),
                    object_id TEXT NOT NULL,
                    operation TEXT NOT NULL CHECK(operation IN ('create','update','retire','restore')),
                    snapshot_json TEXT NOT NULL,
                    transaction_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    actor_authority TEXT NOT NULL CHECK(actor_authority IN ('human','agent')),
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_mg_revision_object
                    ON mg_revisions(object_kind, object_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_mg_revision_tx
                    ON mg_revisions(transaction_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS mg_embedding_projections (
                    object_kind TEXT NOT NULL CHECK(object_kind IN ('method','relation')),
                    object_id TEXT NOT NULL,
                    revision_id TEXT NOT NULL,
                    model TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    vector_blob BLOB NOT NULL,
                    indexed_at TEXT NOT NULL,
                    PRIMARY KEY(object_kind, object_id, revision_id, model)
                );

                CREATE TABLE IF NOT EXISTS mg_activation_events (
                    event_id TEXT PRIMARY KEY,
                    session_id TEXT,
                    query TEXT NOT NULL,
                    retrieved_json TEXT NOT NULL,
                    injected_json TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_mg_activation_session
                    ON mg_activation_events(session_id, occurred_at DESC);
                """
            )

    def add_source(
        self,
        *,
        kind: str,
        title: str,
        content: str,
        author: str = "",
        uri: str | None = None,
        published_at: str | None = None,
        locator: str = "",
        excerpt: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> SourceRecord:
        kind = _required("kind", kind)
        if kind not in SOURCE_KINDS:
            raise ValueError(f"unsupported source kind: {kind}")
        title = _required("title", title)
        content = _required("content", content)
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        source_id = f"src_{content_hash[:24]}"
        captured_at = _now()
        with self._connect() as db:
            db.execute(
                """INSERT OR IGNORE INTO mg_sources(
                       source_id, kind, title, author, uri, published_at, locator,
                       excerpt, captured_at, content_hash, content, metadata_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (source_id, kind, title, author.strip(), uri, published_at,
                 locator.strip(), excerpt.strip(), captured_at, content_hash,
                 content, _json(metadata or {})),
            )
            row = db.execute("SELECT * FROM mg_sources WHERE source_id = ?", (source_id,)).fetchone()
        assert row is not None
        return self._source(row)

    def get_source(self, source_ref: str) -> SourceRecord | None:
        folded = source_ref.casefold()
        with self._connect() as db:
            rows = db.execute("SELECT * FROM mg_sources ORDER BY captured_at DESC").fetchall()
        for row in rows:
            if str(row["source_id"]) == source_ref or str(row["title"]).casefold() == folded:
                return self._source(row)
        return None

    def list_sources(self) -> list[SourceRecord]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM mg_sources ORDER BY captured_at DESC").fetchall()
        return [self._source(row) for row in rows]

    def put_method(
        self,
        *,
        title: str | None = None,
        when: str | None = None,
        why: str | None = None,
        how: str | None = None,
        philosophy: str | None = None,
        boundary: str | None = None,
        detail: str | None = None,
        source_ids: Iterable[str] | None = None,
        method_id: str | None = None,
        authority: str | None = None,
        scope: str | None = None,
        domains: Iterable[str] | None = None,
        project_ref: str | None = None,
        importance: str | None = None,
        metadata: dict[str, Any] | None = None,
        actor: str = "local-user",
        actor_authority: str = "human",
        reason: str = "upsert methodology",
        transaction_id: str | None = None,
        operation: str | None = None,
    ) -> tuple[MethodRecord, str]:
        self._actor(actor_authority)
        existing = self.get_method(method_id) if method_id else None
        if existing and actor_authority == "agent" and existing.authority == "human":
            raise PermissionError("agents cannot modify human-authored methods")
        if existing is None and title is None:
            raise ValueError("title is required for a new method")
        assigned_authority = authority or (existing.authority if existing else actor_authority)
        if actor_authority == "agent" and assigned_authority != "agent":
            raise PermissionError("agents cannot create human-authority methods")
        self._governance(assigned_authority, scope or (existing.scope if existing else "general"),
                         importance or (existing.importance if existing else "normal"))
        now = _now()
        revision_id = _new("mrev")
        tx = transaction_id or _new("tx")
        method_id = existing.method_id if existing else (method_id or _new("method"))
        values = {
            "title": _required("title", title if title is not None else existing.title),
            "when": self._field(when, existing.when if existing else ""),
            "why": self._field(why, existing.why if existing else ""),
            "how": self._field(how, existing.how if existing else ""),
            "philosophy": self._field(philosophy, existing.philosophy if existing else ""),
            "boundary": self._field(boundary, existing.boundary if existing else ""),
            "detail": self._field(detail, existing.detail if existing else ""),
        }
        source_tuple = tuple(dict.fromkeys(source_ids if source_ids is not None else (existing.source_ids if existing else ())))
        domain_tuple = tuple(dict.fromkeys(str(x).strip() for x in (domains if domains is not None else (existing.domains if existing else ())) if str(x).strip()))
        record = MethodRecord(
            method_id=method_id, revision_id=revision_id,
            authority=assigned_authority,
            scope=scope or (existing.scope if existing else "general"),
            domains=domain_tuple,
            project_ref=project_ref if project_ref is not None else (existing.project_ref if existing else None),
            importance=importance or (existing.importance if existing else "normal"),
            created_at=existing.created_at if existing else now,
            updated_at=now,
            created_by=existing.created_by if existing else actor,
            source_ids=source_tuple,
            metadata=metadata if metadata is not None else (existing.metadata if existing else {}),
            **values,
        )
        with self._connect() as db:
            self._ensure_sources(db, source_tuple)
            db.execute(
                """INSERT INTO mg_methods(
                       method_id,title,when_text,why_text,how_text,philosophy,boundary,
                       detail,revision_id,authority,scope,domains_json,project_ref,
                       importance,created_at,updated_at,retired_at,created_by,
                       source_ids_json,metadata_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?)
                   ON CONFLICT(method_id) DO UPDATE SET
                       title=excluded.title, when_text=excluded.when_text,
                       why_text=excluded.why_text, how_text=excluded.how_text,
                       philosophy=excluded.philosophy, boundary=excluded.boundary,
                       detail=excluded.detail, revision_id=excluded.revision_id,
                       authority=excluded.authority, scope=excluded.scope,
                       domains_json=excluded.domains_json, project_ref=excluded.project_ref,
                       importance=excluded.importance, updated_at=excluded.updated_at,
                       retired_at=NULL, source_ids_json=excluded.source_ids_json,
                       metadata_json=excluded.metadata_json""",
                self._method_values(record),
            )
            self._write_revision(db, "method", method_id,
                                 operation or ("update" if existing else "create"),
                                 self._method_dict(record), tx, actor, actor_authority, reason)
        return record, tx

    def retire_method(self, method_ref: str, *, actor: str = "local-user",
                      actor_authority: str = "human", reason: str = "retire methodology",
                      transaction_id: str | None = None) -> str:
        method = self.get_method(method_ref)
        if method is None:
            raise KeyError(method_ref)
        self._can_manage(actor_authority, method.authority, "method")
        tx, now, revision_id = transaction_id or _new("tx"), _now(), _new("mrev")
        snapshot = self._method_dict(method) | {"revision_id": revision_id, "retired_at": now, "updated_at": now}
        with self._connect() as db:
            db.execute("UPDATE mg_methods SET revision_id=?, updated_at=?, retired_at=? WHERE method_id=?",
                       (revision_id, now, now, method.method_id))
            self._write_revision(db, "method", method.method_id, "retire", snapshot,
                                 tx, actor, actor_authority, reason)
        return tx

    def list_methods(self, *, include_retired: bool = False) -> list[MethodRecord]:
        where = "" if include_retired else "WHERE retired_at IS NULL"
        with self._connect() as db:
            rows = db.execute(f"SELECT * FROM mg_methods {where} ORDER BY title COLLATE NOCASE").fetchall()
        return [self._method(row) for row in rows]

    def get_method(self, method_ref: str | None, *, include_retired: bool = False) -> MethodRecord | None:
        if not method_ref:
            return None
        folded = method_ref.casefold()
        for item in self.list_methods(include_retired=include_retired):
            if item.method_id == method_ref or item.title.casefold() == folded:
                return item
        return None

    def put_relation(
        self, *, method_a_id: str, method_b_id: str,
        explanation: str | None = None, detail: str | None = None,
        weight: float | None = None, source_ids: Iterable[str] | None = None,
        authority: str | None = None, scope: str | None = None,
        project_ref: str | None = None, metadata: dict[str, Any] | None = None,
        actor: str = "local-user", actor_authority: str = "human",
        reason: str = "upsert methodology relation", transaction_id: str | None = None,
        operation: str | None = None,
    ) -> tuple[RelationRecord, str]:
        self._actor(actor_authority)
        first = self.get_method(method_a_id)
        second = self.get_method(method_b_id)
        if first is None or second is None:
            raise KeyError(method_a_id if first is None else method_b_id)
        if first.method_id == second.method_id:
            raise ValueError("a relation must connect two different methods")
        a, b = sorted((first.method_id, second.method_id))
        existing = self.get_relation(a, b)
        if existing and actor_authority == "agent" and existing.authority == "human":
            raise PermissionError("agents cannot modify human-authored relations")
        assigned_authority = authority or (existing.authority if existing else actor_authority)
        if actor_authority == "agent" and assigned_authority != "agent":
            raise PermissionError("agents cannot create human-authority relations")
        assigned_scope = scope or (existing.scope if existing else "general")
        self._governance(assigned_authority, assigned_scope, "normal")
        assigned_weight = float(weight if weight is not None else (existing.weight if existing else 1.0))
        if not 0 <= assigned_weight <= 1:
            raise ValueError("relation weight must be between 0 and 1")
        now, tx, revision_id = _now(), transaction_id or _new("tx"), _new("rrev")
        source_tuple = tuple(dict.fromkeys(source_ids if source_ids is not None else (existing.source_ids if existing else ())))
        record = RelationRecord(
            relation_id=_relation_id(a, b), method_a_id=a, method_b_id=b,
            explanation=self._field(explanation, existing.explanation if existing else ""),
            detail=self._field(detail, existing.detail if existing else ""),
            weight=assigned_weight, revision_id=revision_id, authority=assigned_authority,
            scope=assigned_scope,
            project_ref=project_ref if project_ref is not None else (existing.project_ref if existing else None),
            created_at=existing.created_at if existing else now, updated_at=now,
            created_by=existing.created_by if existing else actor, source_ids=source_tuple,
            metadata=metadata if metadata is not None else (existing.metadata if existing else {}),
        )
        with self._connect() as db:
            self._ensure_sources(db, source_tuple)
            db.execute(
                """INSERT INTO mg_relations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?)
                   ON CONFLICT(relation_id) DO UPDATE SET
                       explanation=excluded.explanation, detail=excluded.detail,
                       weight=excluded.weight, revision_id=excluded.revision_id,
                       authority=excluded.authority, scope=excluded.scope,
                       project_ref=excluded.project_ref, updated_at=excluded.updated_at,
                       retired_at=NULL, source_ids_json=excluded.source_ids_json,
                       metadata_json=excluded.metadata_json""",
                self._relation_values(record),
            )
            self._write_revision(db, "relation", record.relation_id,
                                 operation or ("update" if existing else "create"),
                                 self._relation_dict(record), tx, actor, actor_authority, reason)
        return record, tx

    def retire_relation(self, relation_ref: str | tuple[str, str], *, actor: str = "local-user",
                        actor_authority: str = "human", reason: str = "retire relation") -> str:
        relation = (self.get_relation(*relation_ref) if isinstance(relation_ref, tuple)
                    else self.get_relation_by_id(relation_ref))
        if relation is None:
            raise KeyError(str(relation_ref))
        self._can_manage(actor_authority, relation.authority, "relation")
        tx, now, revision_id = _new("tx"), _now(), _new("rrev")
        snapshot = self._relation_dict(relation) | {"revision_id": revision_id, "retired_at": now, "updated_at": now}
        with self._connect() as db:
            db.execute("UPDATE mg_relations SET revision_id=?,updated_at=?,retired_at=? WHERE relation_id=?",
                       (revision_id, now, now, relation.relation_id))
            self._write_revision(db, "relation", relation.relation_id, "retire", snapshot,
                                 tx, actor, actor_authority, reason)
        return tx

    def list_relations(self, *, include_retired: bool = False) -> list[RelationRecord]:
        where = "" if include_retired else "WHERE r.retired_at IS NULL AND a.retired_at IS NULL AND b.retired_at IS NULL"
        with self._connect() as db:
            rows = db.execute(
                f"""SELECT r.* FROM mg_relations r
                    JOIN mg_methods a ON a.method_id=r.method_a_id
                    JOIN mg_methods b ON b.method_id=r.method_b_id
                    {where} ORDER BY r.relation_id"""
            ).fetchall()
        return [self._relation(row) for row in rows]

    def get_relation(self, method_a_ref: str, method_b_ref: str, *, include_retired: bool = False) -> RelationRecord | None:
        a = self.get_method(method_a_ref, include_retired=include_retired)
        b = self.get_method(method_b_ref, include_retired=include_retired)
        return self.get_relation_by_id(_relation_id(a.method_id, b.method_id), include_retired=include_retired) if a and b else None

    def get_relation_by_id(self, relation_id: str, *, include_retired: bool = False) -> RelationRecord | None:
        return next((r for r in self.list_relations(include_retired=include_retired) if r.relation_id == relation_id), None)

    def relations_for(self, method_ids: Iterable[str]) -> list[RelationRecord]:
        wanted = set(method_ids)
        return [r for r in self.list_relations() if r.method_a_id in wanted or r.method_b_id in wanted]

    def history(self, *, kind: str | None = None, object_ref: str | None = None,
                transaction_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        clauses, params = [], []
        if kind:
            clauses.append("object_kind=?"); params.append(kind)
        if object_ref:
            object_id = self._resolve_object_id(kind, object_ref)
            clauses.append("object_id=?"); params.append(object_id)
        if transaction_id:
            clauses.append("transaction_id=?"); params.append(transaction_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as db:
            rows = db.execute(
                f"SELECT * FROM mg_revisions {where} ORDER BY created_at DESC LIMIT ?",
                (*params, max(1, min(limit, 500))),
            ).fetchall()
        return [{**dict(row), "snapshot": json.loads(row["snapshot_json"])} for row in rows]

    def restore_revision(self, revision_id: str, *, actor: str = "local-user",
                         actor_authority: str = "human", reason: str = "restore revision") -> tuple[str, str]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM mg_revisions WHERE revision_id=?", (revision_id,)).fetchone()
        if row is None:
            raise KeyError(revision_id)
        snapshot = json.loads(row["snapshot_json"])
        kind = str(row["object_kind"])
        if kind == "method":
            current = self.get_method(str(row["object_id"]), include_retired=True)
            if current:
                self._can_manage(actor_authority, current.authority, "method")
            record, tx = self.put_method(
                method_id=snapshot["method_id"], title=snapshot["title"],
                when=snapshot.get("when", ""), why=snapshot.get("why", ""),
                how=snapshot.get("how", ""), philosophy=snapshot.get("philosophy", ""),
                boundary=snapshot.get("boundary", ""), detail=snapshot.get("detail", ""),
                source_ids=snapshot.get("source_ids", []), authority=snapshot.get("authority", "agent"),
                scope=snapshot.get("scope", "general"), domains=snapshot.get("domains", []),
                project_ref=snapshot.get("project_ref"), importance=snapshot.get("importance", "normal"),
                metadata=snapshot.get("metadata", {}), actor=actor,
                actor_authority=actor_authority, reason=reason, operation="restore",
            )
            return record.revision_id, tx
        current_r = self.get_relation_by_id(str(row["object_id"]), include_retired=True)
        if current_r:
            self._can_manage(actor_authority, current_r.authority, "relation")
        record_r, tx = self.put_relation(
            method_a_id=snapshot["method_a_id"], method_b_id=snapshot["method_b_id"],
            explanation=snapshot.get("explanation", ""), detail=snapshot.get("detail", ""),
            weight=snapshot.get("weight", 1.0), source_ids=snapshot.get("source_ids", []),
            authority=snapshot.get("authority", "agent"), scope=snapshot.get("scope", "general"),
            project_ref=snapshot.get("project_ref"), metadata=snapshot.get("metadata", {}),
            actor=actor, actor_authority=actor_authority, reason=reason, operation="restore",
        )
        return record_r.revision_id, tx

    def put_embedding(self, *, object_kind: str, object_id: str, revision_id: str,
                      model: str, vector: Sequence[float]) -> None:
        packed = array("f", (float(v) for v in vector))
        if object_kind not in {"method", "relation"} or not packed:
            raise ValueError("invalid embedding projection")
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO mg_embedding_projections VALUES(?,?,?,?,?,?,?)",
                (object_kind, object_id, revision_id, model, len(packed), packed.tobytes(), _now()),
            )

    def get_embeddings(self, *, object_kind: str, model: str) -> dict[str, tuple[str, tuple[float, ...]]]:
        current = ({m.method_id: m.revision_id for m in self.list_methods()} if object_kind == "method"
                   else {r.relation_id: r.revision_id for r in self.list_relations()})
        with self._connect() as db:
            rows = db.execute("SELECT * FROM mg_embedding_projections WHERE object_kind=? AND model=?",
                              (object_kind, model)).fetchall()
        result = {}
        for row in rows:
            if current.get(str(row["object_id"])) != str(row["revision_id"]):
                continue
            values = array("f"); values.frombytes(row["vector_blob"])
            if len(values) == int(row["dimensions"]):
                result[str(row["object_id"])] = (str(row["revision_id"]), tuple(values))
        return result

    def record_activation(self, *, query: str, retrieved: Iterable[tuple[str, str]],
                          injected: Iterable[tuple[str, str]], session_id: str | None,
                          channel: str, metadata: dict[str, Any] | None = None) -> str:
        event_id = _new("activation")
        with self._connect() as db:
            db.execute("INSERT INTO mg_activation_events VALUES(?,?,?,?,?,?,?,?)",
                       (event_id, session_id, query, _json(list(retrieved)), _json(list(injected)),
                        channel, _now(), _json(metadata or {})))
        return event_id

    def recent_injected_revision_keys(self, session_id: str, *, limit: int = 30) -> set[tuple[str, str]]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT injected_json FROM mg_activation_events
                   WHERE session_id=? AND injected_json != '[]'
                   ORDER BY occurred_at DESC LIMIT ?""",
                (session_id, max(1, limit)),
            ).fetchall()
        result: set[tuple[str, str]] = set()
        for row in rows:
            result.update((str(x[0]), str(x[1])) for x in json.loads(row[0]))
        return result

    def recent_activation_queries(
        self, session_id: str, *, channel: str, limit: int = 2
    ) -> list[str]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT query FROM mg_activation_events
                   WHERE session_id=? AND channel=?
                   ORDER BY occurred_at DESC LIMIT ?""",
                (session_id, channel, max(1, limit)),
            ).fetchall()
        return [str(row["query"]) for row in rows]

    @staticmethod
    def _field(value: str | None, fallback: str) -> str:
        return fallback if value is None else str(value).strip()

    @staticmethod
    def _actor(authority: str) -> None:
        if authority not in AUTHORITIES:
            raise ValueError("actor_authority must be human or agent")

    @classmethod
    def _governance(cls, authority: str, scope: str, importance: str) -> None:
        if authority not in AUTHORITIES or scope not in SCOPES or importance not in IMPORTANCE:
            raise ValueError("invalid authority, scope, or importance")

    @classmethod
    def _can_manage(cls, actor_authority: str, object_authority: str, kind: str) -> None:
        cls._actor(actor_authority)
        if actor_authority == "agent" and object_authority == "human":
            raise PermissionError(f"agents cannot modify human-authored {kind}s")

    @staticmethod
    def _ensure_sources(db: sqlite3.Connection, source_ids: tuple[str, ...]) -> None:
        for source_id in source_ids:
            if db.execute("SELECT 1 FROM mg_sources WHERE source_id=?", (source_id,)).fetchone() is None:
                raise KeyError(source_id)

    @staticmethod
    def _write_revision(db: sqlite3.Connection, kind: str, object_id: str, operation: str,
                        snapshot: dict[str, Any], transaction_id: str, actor: str,
                        actor_authority: str, reason: str) -> None:
        db.execute("INSERT INTO mg_revisions VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (snapshot["revision_id"], kind, object_id, operation, _json(snapshot),
                    transaction_id, actor, actor_authority, reason.strip(), _now()))

    def _resolve_object_id(self, kind: str | None, ref: str) -> str:
        if kind == "method":
            item = self.get_method(ref, include_retired=True); return item.method_id if item else ref
        if kind == "relation":
            item = self.get_relation_by_id(ref, include_retired=True); return item.relation_id if item else ref
        return ref

    @staticmethod
    def _source(row: sqlite3.Row) -> SourceRecord:
        return SourceRecord(source_id=str(row["source_id"]), kind=str(row["kind"]),
                            title=str(row["title"]), author=str(row["author"]), uri=row["uri"],
                            published_at=row["published_at"], locator=str(row["locator"]),
                            excerpt=str(row["excerpt"]), captured_at=str(row["captured_at"]),
                            content_hash=str(row["content_hash"]), content=str(row["content"]),
                            metadata=_object(row["metadata_json"]))

    @staticmethod
    def _method(row: sqlite3.Row) -> MethodRecord:
        return MethodRecord(method_id=str(row["method_id"]), title=str(row["title"]),
                            when=str(row["when_text"]), why=str(row["why_text"]),
                            how=str(row["how_text"]), philosophy=str(row["philosophy"]),
                            boundary=str(row["boundary"]), detail=str(row["detail"]),
                            revision_id=str(row["revision_id"]), authority=str(row["authority"]),
                            scope=str(row["scope"]), domains=_list(row["domains_json"]),
                            project_ref=row["project_ref"], importance=str(row["importance"]),
                            created_at=str(row["created_at"]), updated_at=str(row["updated_at"]),
                            created_by=str(row["created_by"]), source_ids=_list(row["source_ids_json"]),
                            metadata=_object(row["metadata_json"]))

    @staticmethod
    def _relation(row: sqlite3.Row) -> RelationRecord:
        return RelationRecord(relation_id=str(row["relation_id"]), method_a_id=str(row["method_a_id"]),
                              method_b_id=str(row["method_b_id"]), explanation=str(row["explanation"]),
                              detail=str(row["detail"]), weight=float(row["weight"]),
                              revision_id=str(row["revision_id"]), authority=str(row["authority"]),
                              scope=str(row["scope"]), project_ref=row["project_ref"],
                              created_at=str(row["created_at"]), updated_at=str(row["updated_at"]),
                              created_by=str(row["created_by"]), source_ids=_list(row["source_ids_json"]),
                              metadata=_object(row["metadata_json"]))

    @staticmethod
    def _method_dict(record: MethodRecord) -> dict[str, Any]:
        return {name: getattr(record, name) for name in record.__dataclass_fields__} | {
            "domains": list(record.domains), "source_ids": list(record.source_ids)
        }

    @staticmethod
    def _relation_dict(record: RelationRecord) -> dict[str, Any]:
        return {name: getattr(record, name) for name in record.__dataclass_fields__} | {
            "source_ids": list(record.source_ids)
        }

    @staticmethod
    def _method_values(r: MethodRecord) -> tuple[Any, ...]:
        return (r.method_id, r.title, r.when, r.why, r.how, r.philosophy, r.boundary,
                r.detail, r.revision_id, r.authority, r.scope, _json(list(r.domains)),
                r.project_ref, r.importance, r.created_at, r.updated_at,
                r.created_by, _json(list(r.source_ids)), _json(r.metadata))

    @staticmethod
    def _relation_values(r: RelationRecord) -> tuple[Any, ...]:
        return (r.relation_id, r.method_a_id, r.method_b_id, r.explanation, r.detail,
                r.weight, r.revision_id, r.authority, r.scope, r.project_ref,
                r.created_at, r.updated_at, r.created_by, _json(list(r.source_ids)), _json(r.metadata))
