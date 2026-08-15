"""Git-backed authoritative content and SQLite projection synchronization."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Any

import yaml

from .models import MethodRecord, RelationRecord, SourceRecord
from .store import MethodGraphStore, _relation_id


_SAFE_IDENTITY = re.compile(r"^[^\r\n\x00]+$")


def _check_identity(value: str, field: str) -> str:
    value = str(value).strip()
    if not value or not _SAFE_IDENTITY.match(value):
        raise ValueError(f"{field} must be a non-empty single line")
    return value


def git_identity() -> tuple[str, str]:
    def read(key: str, fallback: str) -> str:
        result = subprocess.run(["git", "config", key], text=True, capture_output=True, check=False)
        return result.stdout.strip() or fallback
    return _check_identity(read("user.name", "unknown-user"), "author_name"), _check_identity(
        read("user.email", "unknown@example.invalid"), "author_email")


def _frontmatter(data: dict[str, Any], body: str) -> str:
    return "---\n" + yaml.safe_dump(data, allow_unicode=True, sort_keys=True).strip() + "\n---\n\n" + body


def _read_markdown(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}, text
    _, rest = text.split("---\n", 1)
    front, sep, body = rest.partition("\n---\n")
    if not sep:
        raise ValueError(f"invalid frontmatter: {path}")
    loaded = yaml.safe_load(front) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"frontmatter must be a mapping: {path}")
    return loaded, body[1:] if body.startswith("\n") else body


def _list(value: Any) -> tuple[str, ...]:
    return tuple(str(item) for item in (value or []) if str(item).strip())


class GitContentRepository:
    """One-file-per-object Git repository. Git HEAD is the only content authority."""

    def __init__(self, path: str | Path, *, committer_name: str = "MethodGraph Server",
                 committer_email: str = "methodgraph@localhost", push_remote: str = ""):
        self.path = Path(path).expanduser().resolve()
        self.committer_name = _check_identity(committer_name, "committer_name")
        self.committer_email = _check_identity(committer_email, "committer_email")
        self.push_remote = push_remote.strip()
        self._lock = threading.RLock()

    def _git(self, *args: str, check: bool = True, env: dict[str, str] | None = None) -> str:
        result = subprocess.run(["git", *args], cwd=self.path, text=True,
                                capture_output=True, check=False, env=env)
        if check and result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"git {' '.join(args)} failed")
        return result.stdout.strip()

    def initialize(self) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        for directory in ("methods", "relations", "sources"):
            (self.path / directory).mkdir(exist_ok=True)
        if not (self.path / ".git").exists():
            subprocess.run(["git", "init", "--initial-branch=main"], cwd=self.path,
                           text=True, capture_output=True, check=True)

    def head(self) -> str:
        self.initialize()
        return self._git("rev-parse", "HEAD", check=False) or ""

    def _blob_revision(self, relative: str) -> str:
        head = self.head()
        if not head:
            return ""
        return self._git("rev-parse", f"{head}:{relative}", check=False)

    def _path(self, kind: str, object_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", object_id):
            raise ValueError("object id contains unsupported characters")
        return self.path / f"{kind}s" / f"{object_id}.md"

    def _commit(self, message: str, *, author_name: str, author_email: str) -> str:
        self._git("add", "methods", "relations", "sources")
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"], cwd=self.path, check=False,
        )
        if result.returncode == 0:
            return self.head()
        env = os.environ.copy()
        env.update({"GIT_AUTHOR_NAME": _check_identity(author_name, "author_name"),
                    "GIT_AUTHOR_EMAIL": _check_identity(author_email, "author_email"),
                    "GIT_COMMITTER_NAME": self.committer_name,
                    "GIT_COMMITTER_EMAIL": self.committer_email})
        self._git("commit", "-m", message, env=env)
        if self.push_remote:
            self._git("push", self.push_remote, "HEAD")
        return self.head()

    def _ensure_remote_fast_forward(self) -> None:
        if not self.push_remote:
            return
        self._git("fetch", self.push_remote)
        remote_head = self._git("rev-parse", f"{self.push_remote}/main", check=False)
        local_head = self.head()
        if not remote_head:
            return
        if not local_head:
            raise RuntimeError("remote content repository is non-empty; initialize from it first")
        result = subprocess.run(["git", "merge-base", "--is-ancestor", remote_head, local_head],
                                cwd=self.path, check=False)
        if result.returncode != 0:
            raise RuntimeError("content repository is not fast-forward with its remote")

    def restore(self, kind: str, object_id: str, revision: str, *, author_name: str,
                author_email: str, reason: str, expected_revision: str | None = None) -> tuple[str, str]:
        with self._lock:
            author_name = _check_identity(author_name, "author_name")
            author_email = _check_identity(author_email, "author_email")
            self.initialize()
            self._ensure_remote_fast_forward()
            path = self._path(kind, object_id)
            relative = path.relative_to(self.path).as_posix()
            current = self._blob_revision(relative)
            if expected_revision is not None and expected_revision != current:
                raise RuntimeError(f"stale revision for {kind}/{object_id}")
            restored = self._git("show", f"{revision}:{relative}")
            path.write_text(restored + ("" if restored.endswith("\n") else "\n"), encoding="utf-8")
            head = self._commit(f"restore {kind} {object_id}: {reason}",
                                author_name=author_name, author_email=author_email)
            return head, self._blob_revision(relative)

    def _write(self, kind: str, object_id: str, data: dict[str, Any], body: str,
               *, author_name: str, author_email: str, message: str,
               expected_revision: str | None = None, commit: bool = True) -> tuple[str, str]:
        with self._lock:
            author_name = _check_identity(author_name, "author_name")
            author_email = _check_identity(author_email, "author_email")
            self.initialize()
            self._ensure_remote_fast_forward()
            path = self._path(kind, object_id)
            relative = path.relative_to(self.path).as_posix()
            current = self._blob_revision(relative)
            if expected_revision is not None and expected_revision != current:
                raise RuntimeError(f"stale revision for {kind}/{object_id}: expected {expected_revision}, current {current}")
            path.write_text(_frontmatter(data, body), encoding="utf-8")
            if not commit:
                return "", ""
            head = self._commit(message, author_name=author_name, author_email=author_email)
            return head, self._blob_revision(relative)

    def add_source(self, source: SourceRecord, *, author_name: str, author_email: str,
                   reason: str, expected_revision: str | None = None,
                   commit: bool = True) -> tuple[str, str]:
        data = {"source_id": source.source_id, "kind": source.kind, "title": source.title,
                "author": source.author, "uri": source.uri, "published_at": source.published_at,
                "locator": source.locator, "excerpt": source.excerpt, "captured_at": source.captured_at,
                "content_hash": source.content_hash, "metadata": source.metadata}
        return self._write("source", source.source_id, data, source.content,
                           author_name=author_name, author_email=author_email,
                           message=f"source: {reason}", expected_revision=expected_revision, commit=commit)

    def add_method(self, method: MethodRecord, *, author_name: str, author_email: str,
                   reason: str, expected_revision: str | None = None,
                   commit: bool = True) -> tuple[str, str]:
        data = {"method_id": method.method_id, "title": method.title, "when": method.when,
                "why": method.why, "how": method.how, "philosophy": method.philosophy,
                "boundary": method.boundary,
                "authority": method.authority, "scope": method.scope, "domains": list(method.domains),
                "project_ref": method.project_ref, "importance": method.importance,
                "created_at": method.created_at, "updated_at": method.updated_at,
                "created_by": method.created_by, "source_ids": list(method.source_ids),
                "metadata": method.metadata}
        body = f"## Detail\n\n{method.detail}" if method.detail else ""
        return self._write("method", method.method_id, data, body,
                           author_name=author_name, author_email=author_email,
                           message=f"method {method.method_id}: {reason}", expected_revision=expected_revision,
                           commit=commit)

    def add_relation(self, relation: RelationRecord, *, author_name: str, author_email: str,
                     reason: str, expected_revision: str | None = None,
                     commit: bool = True) -> tuple[str, str]:
        data = {"relation_id": relation.relation_id, "method_a_id": relation.method_a_id,
                "method_b_id": relation.method_b_id, "explanation": relation.explanation,
                "weight": relation.weight,
                "authority": relation.authority, "scope": relation.scope,
                "project_ref": relation.project_ref, "created_at": relation.created_at,
                "updated_at": relation.updated_at, "created_by": relation.created_by,
                "source_ids": list(relation.source_ids), "metadata": relation.metadata}
        body_parts = []
        if relation.detail:
            body_parts = ["## Detail", "", relation.detail]
        return self._write("relation", relation.relation_id, data, "\n".join(body_parts),
                           author_name=author_name, author_email=author_email,
                           message=f"relation {relation.relation_id}: {reason}", expected_revision=expected_revision,
                           commit=commit)

    def delete(self, kind: str, object_id: str, *, author_name: str, author_email: str,
               reason: str, expected_revision: str | None = None) -> str:
        with self._lock:
            author_name = _check_identity(author_name, "author_name")
            author_email = _check_identity(author_email, "author_email")
            self.initialize()
            self._ensure_remote_fast_forward()
            path = self._path(kind, object_id)
            relative = path.relative_to(self.path).as_posix()
            current = self._blob_revision(relative)
            if expected_revision is not None and expected_revision != current:
                raise RuntimeError(f"stale revision for {kind}/{object_id}")
            if not path.exists():
                raise KeyError(object_id)
            path.unlink()
            return self._commit(f"retire {kind} {object_id}: {reason}", author_name=author_name, author_email=author_email)

    def _records(self) -> tuple[list[SourceRecord], list[MethodRecord], list[RelationRecord]]:
        self.initialize()
        sources: list[SourceRecord] = []
        methods: list[MethodRecord] = []
        relations: list[RelationRecord] = []
        for path in sorted((self.path / "sources").glob("*.md")):
            data, body = _read_markdown(path)
            expected_hash = str(data.get("content_hash", ""))
            content = body
            while content.endswith("\n") and expected_hash and hashlib.sha256(content.encode()).hexdigest() != expected_hash:
                content = content[:-1]
            sources.append(SourceRecord(source_id=str(data["source_id"]), kind=str(data["kind"]),
                title=str(data["title"]), content=content, content_hash=expected_hash or hashlib.sha256(content.encode()).hexdigest(),
                captured_at=str(data.get("captured_at", "")), author=str(data.get("author", "")),
                uri=data.get("uri"), published_at=data.get("published_at"), locator=str(data.get("locator", "")),
                excerpt=str(data.get("excerpt", "")), metadata=data.get("metadata") or {}))
        for path in sorted((self.path / "methods").glob("*.md")):
            data, body = _read_markdown(path)
            detail = body.split("## Detail", 1)[1].strip() if "## Detail" in body else ""
            relative = path.relative_to(self.path).as_posix()
            methods.append(MethodRecord(method_id=str(data["method_id"]), title=str(data["title"]),
                when=str(data.get("when", "")), why=str(data.get("why", "")), how=str(data.get("how", "")),
                philosophy=str(data.get("philosophy", "")), boundary=str(data.get("boundary", "")), detail=detail,
                revision_id=self._blob_revision(relative), authority=str(data.get("authority", "agent")),
                scope=str(data.get("scope", "general")), domains=_list(data.get("domains")), project_ref=data.get("project_ref"),
                importance=str(data.get("importance", "normal")), created_at=str(data.get("created_at", "")),
                updated_at=str(data.get("updated_at", "")), created_by=str(data.get("created_by", "")),
                source_ids=_list(data.get("source_ids")), metadata=data.get("metadata") or {}))
        for path in sorted((self.path / "relations").glob("*.md")):
            data, body = _read_markdown(path)
            detail = body.split("## Detail", 1)[1].strip() if "## Detail" in body else ""
            relative = path.relative_to(self.path).as_posix()
            relations.append(RelationRecord(relation_id=str(data["relation_id"]), method_a_id=str(data["method_a_id"]),
                method_b_id=str(data["method_b_id"]), explanation=str(data.get("explanation", "")), detail=detail,
                weight=float(data.get("weight", 1.0)), revision_id=self._blob_revision(relative),
                authority=str(data.get("authority", "agent")), scope=str(data.get("scope", "general")),
                project_ref=data.get("project_ref"), created_at=str(data.get("created_at", "")),
                updated_at=str(data.get("updated_at", "")), created_by=str(data.get("created_by", "")),
                source_ids=_list(data.get("source_ids")), metadata=data.get("metadata") or {}))
        return sources, methods, relations

    def load(self) -> tuple[list[SourceRecord], list[MethodRecord], list[RelationRecord]]:
        with self._lock:
            return self._records()

    def sync_projection(self, store: MethodGraphStore) -> dict[str, int | str]:
        sources, methods, relations = self.load()
        store.replace_projection(sources=sources, methods=methods, relations=relations, indexed_commit=self.head())
        return {"sources": len(sources), "methods": len(methods), "relations": len(relations), "commit": self.head()}

    def export_store(self, store: MethodGraphStore, *, author_name: str, author_email: str,
                     reason: str = "import existing SQLite content") -> str:
        with self._lock:
            self.initialize()
            self._ensure_remote_fast_forward()
            for source in store.list_sources():
                self.add_source(source, author_name=author_name, author_email=author_email, reason=reason, commit=False)
            for method in store.list_methods():
                self.add_method(method, author_name=author_name, author_email=author_email, reason=reason, commit=False)
            for relation in store.list_relations():
                self.add_relation(relation, author_name=author_name, author_email=author_email, reason=reason, commit=False)
            return self._commit(reason, author_name=author_name, author_email=author_email)

    def history(self, kind: str, object_id: str, limit: int = 50) -> list[dict[str, Any]]:
        relative = self._path(kind, object_id).relative_to(self.path).as_posix()
        output = self._git("log", f"-{max(1, min(limit, 500))}", "--format=%H%x09%an%x09%ae%x09%aI%x09%s", "--", relative, check=False)
        rows = []
        for line in output.splitlines():
            commit, name, email, created_at, subject = line.split("\t", 4)
            rows.append({"revision_id": commit, "actor": name, "actor_email": email,
                         "created_at": created_at, "reason": subject})
        return rows
