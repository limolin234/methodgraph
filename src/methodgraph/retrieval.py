"""Hybrid retrieval followed by bounded expansion over the untyped graph."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Protocol

from .models import MethodRecord, SearchHit
from .store import MethodGraphStore


class VectorIndex(Protocol):
    def search_methods(self, query: str, limit: int) -> Mapping[str, float]: ...
    def search_relations(self, query: str, limit: int) -> Mapping[str, float]: ...


def _features(text: str) -> set[str]:
    normalized = text.casefold()
    result = set(re.findall(r"[a-z0-9_]+", normalized))
    for segment in re.findall(r"[\u3400-\u9fff]+", normalized):
        result.add(segment)
        result.update(segment[i : i + 2] for i in range(max(0, len(segment) - 1)))
    return result


def _lexical_score(query: str, text: str) -> float:
    query_features, text_features = _features(query), _features(text)
    if not query_features or not text_features:
        return 0.0
    overlap = len(query_features & text_features) / len(query_features)
    return min(1.0, overlap + (0.2 if query.casefold() in text.casefold() else 0.0))


def _near_duplicate(left: MethodRecord, right: MethodRecord) -> bool:
    a, b = _features(left.retrieval_text()), _features(right.retrieval_text())
    return bool(a and b and len(a & b) / min(len(a), len(b)) >= 0.88)


class MethodRetriever:
    def __init__(self, store: MethodGraphStore, *, vector_index: VectorIndex | None = None,
                 graph_expansion_weight: float = 0.42):
        self.store = store
        self.vector_index = vector_index
        self.graph_expansion_weight = graph_expansion_weight

    def search(
        self,
        query: str,
        *,
        method_limit: int = 6,
        neighbor_limit: int = 2,
        session_id: str | None = None,
        exclude_recent: bool = True,
        project: str | None = None,
        scopes: Iterable[str] | None = None,
        min_score: float = 0.08,
    ) -> list[SearchHit]:
        query = query.strip()
        if not query or method_limit <= 0:
            return []
        allowed_scopes = set(scopes or ())
        methods = {
            item.method_id: item for item in self.store.list_methods()
            if (not allowed_scopes or item.scope in allowed_scopes)
            and (item.scope != "project" or (project and item.project_ref == project))
        }
        try:
            vector_scores = (dict(self.vector_index.search_methods(query, max(method_limit * 4, 16)))
                             if self.vector_index is not None else {})
        except Exception:
            # A missing model or a transient local accelerator failure must not
            # make methodology retrieval block the agent; lexical retrieval remains useful.
            vector_scores = {}
        scores: dict[str, float] = {}
        for method_id, item in methods.items():
            lexical = (
                0.38 * _lexical_score(query, item.when)
                + 0.20 * _lexical_score(query, item.why)
                + 0.18 * _lexical_score(query, item.how)
                + 0.08 * _lexical_score(query, item.philosophy)
                + 0.10 * _lexical_score(query, item.boundary)
                + 0.06 * _lexical_score(query, item.title)
            )
            vector = max(0.0, min(1.0, float(vector_scores.get(method_id, 0.0))))
            score = 0.25 * lexical + 0.75 * vector if vector_scores else lexical
            importance_prior = {"core": 1.06, "major": 1.03, "normal": 1.0, "minor": 0.97}[item.importance]
            score = min(1.0, score * importance_prior)
            if query.casefold() in item.title.casefold():
                score = max(score, 0.20)
            if score >= min_score:
                scores[method_id] = score

        cooled = (self.store.recent_injected_revision_keys(session_id)
                  if session_id and exclude_recent else set())
        direct = [
            SearchHit(method=methods[mid], score=score, reason="direct semantic retrieval", seed=True)
            for mid, score in sorted(scores.items(), key=lambda x: (-x[1], x[0]))
            if (mid, methods[mid].revision_id) not in cooled
        ]
        seeds = self._deduplicate(direct)[:method_limit]

        if not seeds or neighbor_limit <= 0 or len(seeds) >= method_limit:
            return seeds[:method_limit]
        try:
            relation_scores = (dict(self.vector_index.search_relations(query, max(neighbor_limit * 8, 16)))
                               if self.vector_index is not None else {})
        except Exception:
            relation_scores = {}
        expanded: list[SearchHit] = []
        seed_ids = {hit.method.method_id for hit in seeds}
        for relation in self.store.relations_for(seed_ids):
            if relation.method_a_id in seed_ids:
                seed_id, candidate_id = relation.method_a_id, relation.method_b_id
            else:
                seed_id, candidate_id = relation.method_b_id, relation.method_a_id
            candidate = methods.get(candidate_id)
            if candidate is None or candidate_id in seed_ids or (candidate_id, candidate.revision_id) in cooled:
                continue
            seed_score = next(hit.score for hit in seeds if hit.method.method_id == seed_id)
            fit = float(relation_scores.get(relation.relation_id, 0.5))
            score = seed_score * self.graph_expansion_weight * relation.weight * (0.5 + 0.5 * fit)
            if score >= min_score * 0.5:
                expanded.append(SearchHit(candidate, score,
                    f"graph association from {methods[seed_id].title}", seed=False))
        expanded.sort(key=lambda hit: (-hit.score, hit.method.method_id))
        expanded = self._deduplicate(expanded, existing=[hit.method for hit in seeds])[:neighbor_limit]
        return (seeds + expanded)[:method_limit]

    @staticmethod
    def _deduplicate(hits: list[SearchHit], *, existing: list[MethodRecord] | None = None) -> list[SearchHit]:
        kept: list[SearchHit] = []
        methods = list(existing or [])
        for hit in hits:
            if any(_near_duplicate(hit.method, item) for item in methods):
                continue
            kept.append(hit)
            methods.append(hit.method)
        return kept
