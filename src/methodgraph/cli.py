"""Small administrative CLI kept separate from model-facing MCP tools."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .embedding import DEFAULT_EMBEDDING_MODEL, LocalEmbeddingIndex, SentenceTransformerBackend
from .runtime import build_service
from .store import MethodGraphStore


def _read_text(base: Path, item: dict, field: str) -> str:
    file_field = f"{field}_file"
    if file_field in item:
        return (base / str(item[file_field])).read_text(encoding="utf-8")
    return str(item.get(field, ""))


def import_bundle(store: MethodGraphStore, bundle_path: str | Path) -> dict[str, int]:
    path = Path(bundle_path)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("bundle root must be a JSON object")
    base = path.parent
    source_refs: dict[str, str] = {}
    for item in loaded.get("sources", []):
        source = store.add_source(
            kind=str(item["kind"]),
            title=str(item["title"]),
            content=_read_text(base, item, "content"),
            author=str(item.get("author", "")),
            uri=item.get("uri"),
            published_at=item.get("published_at"),
            locator=str(item.get("locator", "")),
            excerpt=str(item.get("excerpt", "")),
            metadata=item.get("metadata"),
        )
        source_refs[str(item.get("ref") or source.source_id)] = source.source_id

    method_refs: dict[str, str] = {}
    for item in loaded.get("methods", []):
        title = str(item["title"])
        existing = store.get_method(str(item.get("method_id") or title))
        source_ids = [source_refs[str(ref)] for ref in item.get("source_refs", [])]
        method, _ = store.put_method(
            method_id=existing.method_id if existing else item.get("method_id"),
            title=title,
            when=str(item.get("when", "")),
            why=str(item.get("why", "")),
            how=str(item.get("how", "")),
            philosophy=str(item.get("philosophy", "")),
            boundary=str(item.get("boundary", "")),
            detail=_read_text(base, item, "detail"),
            source_ids=source_ids,
            authority=item.get("authority"),
            scope=item.get("scope"),
            domains=item.get("domains"),
            project_ref=item.get("project_ref"),
            importance=item.get("importance"),
            metadata=item.get("metadata"),
            reason=f"import method {title}",
        )
        method_refs[str(item.get("ref") or title)] = method.method_id

    for item in loaded.get("relations", []):
        endpoints = item.get("methods")
        if not isinstance(endpoints, list) or len(endpoints) != 2:
            raise ValueError("each relation requires exactly two method refs")
        source_ids = [source_refs[str(ref)] for ref in item.get("source_refs", [])]
        store.put_relation(
            method_a_id=method_refs.get(str(endpoints[0]), str(endpoints[0])),
            method_b_id=method_refs.get(str(endpoints[1]), str(endpoints[1])),
            explanation=str(item["explanation"]),
            detail=_read_text(base, item, "detail"),
            weight=float(item.get("weight", 1.0)),
            source_ids=source_ids,
            metadata=item.get("metadata"),
            reason=f"import relation {endpoints[0]} / {endpoints[1]}",
        )
    return {
        "sources": len(loaded.get("sources", [])),
        "methods": len(loaded.get("methods", [])),
        "relations": len(loaded.get("relations", [])),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="methodgraph")
    parser.add_argument(
        "--db", default=os.environ.get("METHODGRAPH_DB", "methodgraph.db")
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="initialize or migrate the database")

    importer = commands.add_parser("import", help="import a reviewed JSON bundle")
    importer.add_argument("bundle")

    indexer = commands.add_parser("index", help="build local embedding projections")
    indexer.add_argument(
        "--model",
        default=os.environ.get("METHODGRAPH_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
    )
    indexer.add_argument("--device", default=os.environ.get("METHODGRAPH_EMBEDDING_DEVICE"))
    indexer.add_argument("--force", action="store_true")

    search = commands.add_parser("search", help="inspect the model-facing packet")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=6)
    search.add_argument("--model", default=os.environ.get("METHODGRAPH_EMBEDDING_MODEL", "none"))
    return parser


def main() -> None:
    args = _parser().parse_args()
    store = MethodGraphStore(args.db)
    store.initialize()
    if args.command == "init":
        print(args.db)
        return
    if args.command == "import":
        print(json.dumps(import_bundle(store, args.bundle), ensure_ascii=False))
        return
    if args.command == "index":
        backend = SentenceTransformerBackend(args.model, device=args.device)
        result = LocalEmbeddingIndex(store, backend).rebuild(force=args.force)
        print(json.dumps({"model": args.model, **result}, ensure_ascii=False))
        return
    if args.command == "search":
        service = build_service(db_path=args.db, embedding_model=args.model)
        packet = service.methodology_search(
            args.query, method_limit=args.limit
        )
        print(service.render_injection(packet))
        return
    raise AssertionError(args.command)


if __name__ == "__main__":
    main()
