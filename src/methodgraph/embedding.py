"""Local embedding projection backed by SQLite and a pluggable encoder."""

from __future__ import annotations

import math
import threading
from collections.abc import Mapping, Sequence
from typing import Protocol

from .store import MethodGraphStore

DEFAULT_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-4B"
QUERY_TASK = (
    "Given the current problem state, retrieve methodologies that should influence "
    "how an agent reasons about and handles the problem."
)


class EmbeddingBackend(Protocol):
    @property
    def model_name(self) -> str: ...

    def encode_query(self, text: str) -> Sequence[float]: ...

    def encode_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...


class SentenceTransformerBackend:
    """Lazy local encoder suitable for a persistent MCP HTTP process."""

    def __init__(self, model_name: str = DEFAULT_EMBEDDING_MODEL, device: str | None = None):
        self._model_name = model_name
        self.device = device
        self._model = None

    @property
    def model_name(self) -> str:
        return self._model_name

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            import torch
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "local embeddings require the 'embedding' extra"
            ) from exc
        device = self.device or ("cuda" if torch.cuda.is_available() else "cpu")
        model_kwargs = {"torch_dtype": torch.float16} if device == "cuda" else {}
        self._model = SentenceTransformer(
            self.model_name,
            device=device,
            model_kwargs=model_kwargs,
            trust_remote_code=False,
        )
        return self._model

    def encode_query(self, text: str) -> Sequence[float]:
        model = self._load()
        options = {"normalize_embeddings": True, "show_progress_bar": False}
        if hasattr(model, "encode_query"):
            result = model.encode_query([text], **options)
        else:
            instructed = f"Instruct: {QUERY_TASK}\nQuery: {text}"
            result = model.encode([instructed], **options)
        return result[0].tolist()

    def encode_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        if not texts:
            return []
        model = self._load()
        options = {"normalize_embeddings": True, "show_progress_bar": False}
        if hasattr(model, "encode_document"):
            result = model.encode_document(list(texts), **options)
        else:
            result = model.encode(list(texts), **options)
        return [item.tolist() for item in result]


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


class LocalEmbeddingIndex:
    """Brute-force cosine is deliberate until corpus size justifies an ANN service."""

    def __init__(self, store: MethodGraphStore, backend: EmbeddingBackend):
        self.store = store
        self.backend = backend
        self._write_lock = threading.Lock()

    def rebuild(self, *, force: bool = False) -> dict[str, int]:
        indexed = {"method": 0, "relation": 0}
        with self._write_lock:
            self._index_methods(force=force, counter=indexed)
            self._index_relations(force=force, counter=indexed)
        return indexed

    def _index_methods(self, *, force: bool, counter: dict[str, int]) -> None:
        existing = self.store.get_embeddings(
            object_kind="method", model=self.backend.model_name
        )
        methods = [
            item
            for item in self.store.list_methods()
            if force or item.method_id not in existing
        ]
        vectors = self.backend.encode_documents(
            [item.retrieval_text() for item in methods]
        )
        for item, vector in zip(methods, vectors, strict=True):
            self.store.put_embedding(
                object_kind="method",
                object_id=item.method_id,
                revision_id=item.revision_id,
                model=self.backend.model_name,
                vector=vector,
            )
            counter["method"] += 1

    def _index_relations(self, *, force: bool, counter: dict[str, int]) -> None:
        existing = self.store.get_embeddings(
            object_kind="relation", model=self.backend.model_name
        )
        relations = [
            item
            for item in self.store.list_relations()
            if force or item.relation_id not in existing
        ]
        vectors = self.backend.encode_documents(
            [f"{item.explanation}\n{item.detail}".strip() for item in relations]
        )
        for item, vector in zip(relations, vectors, strict=True):
            self.store.put_embedding(
                object_kind="relation",
                object_id=item.relation_id,
                revision_id=item.revision_id,
                model=self.backend.model_name,
                vector=vector,
            )
            counter["relation"] += 1

    def search_methods(self, query: str, limit: int) -> Mapping[str, float]:
        if not self.store.get_embeddings(object_kind="method", model=self.backend.model_name):
            return {}
        return self._search("method", query, limit)

    def search_relations(self, query: str, limit: int) -> Mapping[str, float]:
        if not self.store.get_embeddings(object_kind="relation", model=self.backend.model_name):
            return {}
        return self._search("relation", query, limit)

    def _search(self, object_kind: str, query: str, limit: int) -> Mapping[str, float]:
        query_vector = self.backend.encode_query(query)
        projections = self.store.get_embeddings(
            object_kind=object_kind, model=self.backend.model_name
        )
        scored = [
            (object_id, max(0.0, _cosine(query_vector, vector)))
            for object_id, (_, vector) in projections.items()
        ]
        scored.sort(key=lambda item: (-item[1], item[0]))
        return dict(scored[: max(1, limit)])
