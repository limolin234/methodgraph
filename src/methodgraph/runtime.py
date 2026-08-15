"""Environment-driven construction shared by adapters."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from .config import AppConfig, load_config
from .content import GitContentRepository
from .embedding import LocalEmbeddingIndex, OpenAICompatibleBackend, SentenceTransformerBackend
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


def build_configured_service(config: AppConfig | None = None) -> tuple[MethodGraphService, GitContentRepository]:
    """Construct the authoritative server runtime and synchronize Git HEAD."""
    config = config or load_config()
    store = MethodGraphStore(Path(config.server.database).expanduser())
    store.initialize()
    content = GitContentRepository(
        config.server.content_repo, committer_name=config.server.committer_name,
        committer_email=config.server.committer_email, push_remote=config.server.push_remote,
    )
    content.initialize()
    if content.head() and content.head() != store.indexed_commit():
        try:
            content.sync_projection(store)
        except Exception:
            # Keep the last valid projection available; /readyz reports the mismatch.
            pass
    vector_index = None
    if config.embedding.provider == "local":
        backend = SentenceTransformerBackend(config.embedding.model, device=config.embedding.device)
        vector_index = LocalEmbeddingIndex(store, backend)
    elif config.embedding.provider == "openai_compatible":
        backend = OpenAICompatibleBackend(
            model_name=config.embedding.model, base_url=config.embedding.base_url,
            api_key_env=config.embedding.api_key_env, timeout=config.embedding.timeout,
            batch_size=config.embedding.batch_size,
        )
        vector_index = LocalEmbeddingIndex(store, backend)
    if vector_index is not None:
        _start_background_indexer(store, vector_index)
    return MethodGraphService(store, MethodRetriever(store, vector_index=vector_index),
                              history_provider=content.history), content


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
