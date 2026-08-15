"""Environment-driven construction shared by adapters."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from .embedding import LocalEmbeddingIndex, SentenceTransformerBackend
from .retrieval import MethodRetriever
from .service import MethodGraphService
from .store import MethodGraphStore


def build_service(
    *,
    db_path: str | Path | None = None,
    embedding_model: str | None = None,
) -> MethodGraphService:
    path = Path(db_path or os.environ.get("METHODGRAPH_DB", "methodgraph.db"))
    store = MethodGraphStore(path)
    store.initialize()
    configured = (
        embedding_model
        if embedding_model is not None
        else os.environ.get("METHODGRAPH_EMBEDDING_MODEL", "none")
    ).strip()
    vector_index = None
    if configured.casefold() not in {"", "none", "off", "false"}:
        backend = SentenceTransformerBackend(
            configured,
            device=os.environ.get("METHODGRAPH_EMBEDDING_DEVICE") or None,
        )
        vector_index = LocalEmbeddingIndex(store, backend)
        _start_background_indexer(store, vector_index)
    return MethodGraphService(
        store,
        MethodRetriever(store, vector_index=vector_index),
    )


def _start_background_indexer(store: MethodGraphStore, index: LocalEmbeddingIndex) -> None:
    """Build projections outside request handling and refresh them periodically."""
    if os.environ.get("METHODGRAPH_INDEX_MODE", "background").casefold() in {"off", "manual", "false"}:
        return
    interval = max(10, int(os.environ.get("METHODGRAPH_INDEX_INTERVAL", "60")))

    def worker() -> None:
        while True:
            try:
                index.rebuild()
            except Exception:
                # Retrieval remains available through lexical fallback; the next cycle retries.
                pass
            time.sleep(interval)

    threading.Thread(target=worker, name="methodgraph-indexer", daemon=True).start()
