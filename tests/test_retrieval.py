from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from methodgraph.hook import _build_search_context, build_hook_output
from methodgraph.retrieval import MethodRetriever
from methodgraph.service import MethodGraphService
from methodgraph.store import MethodGraphStore


def put_method(store: MethodGraphStore, title: str, when: str, *, detail: str = ""):
    return store.put_method(title=title, when=when,
        why="这种处理可以减少隐含假设和后续返工。",
        how="先显式描述问题，再采取后续行动。", detail=detail)[0]


class FakeVectorIndex:
    def __init__(self, method_scores=None, relation_scores=None):
        self.method_scores = method_scores or {}
        self.relation_scores = relation_scores or {}
    def search_methods(self, query, limit): return self.method_scores
    def search_relations(self, query, limit): return self.relation_scores


class RetrievalTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "methodgraph.db"
        self.store = MethodGraphStore(self.db_path)
        self.store.initialize()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_search_retrieves_seed_and_bounded_graph_neighbor(self):
        boundary = put_method(self.store, "边界分析", "系统边界不清，接口责任不明确")
        verifier = put_method(self.store, "所有权检查", "需要确认边界是否完整")
        relation, _ = self.store.put_relation(method_a_id=boundary.method_id,
            method_b_id=verifier.method_id, explanation="用于验证边界是否形成可执行责任。")
        index = FakeVectorIndex({boundary.method_id: 0.95}, {relation.relation_id: 0.9})
        hits = MethodRetriever(self.store, vector_index=index).search(
            "系统边界和接口责任不清", method_limit=2, neighbor_limit=1)
        self.assertEqual(hits[0].method.method_id, boundary.method_id)
        self.assertEqual({hit.method.method_id for hit in hits}, {boundary.method_id, verifier.method_id})
        self.assertFalse(hits[1].seed)

    def test_search_packet_hides_scores_and_weights_but_keeps_provenance(self):
        source = self.store.add_source(kind="standard", title="SE Standard",
                                       content="Frame before decomposition", locator="4.2")
        first = self.store.put_method(title="问题定义", when="需求不明确",
                                      source_ids=[source.source_id])[0]
        second = put_method(self.store, "互斥解释", "需求存在歧义")
        self.store.put_relation(method_a_id=first.method_id, method_b_id=second.method_id,
                                explanation="先定义问题，再比较解释。", weight=0.3)
        packet = MethodGraphService(self.store).methodology_search(
            "需求不明确和歧义", method_limit=2, neighbor_limit=1)
        rendered = MethodGraphService.render_injection(packet)
        self.assertIn("method_ref", packet["methods"][0])
        self.assertNotIn("score", str(packet))
        self.assertNotIn("weight", str(packet))
        self.assertIn("SE Standard", rendered)

    def test_cooldown_is_revision_aware_and_can_be_disabled(self):
        method = put_method(self.store, "问题定义", "需求不明确")
        service = MethodGraphService(self.store)
        initial = service.methodology_search("需求不明确", method_limit=1, session_id="s1")
        following = service.methodology_search("需求不明确", method_limit=1, session_id="s1")
        repeated = service.methodology_search("需求不明确", method_limit=1,
                                              session_id="s1", exclude_recent=False)
        self.assertEqual(initial["result_count"], 1)
        self.assertEqual(following["result_count"], 0)
        self.assertEqual(repeated["methods"][0]["method_ref"], method.method_id)
        self.store.put_method(method_id=method.method_id, why="new revision")
        revised = service.methodology_search("需求不明确", method_limit=1, session_id="s1")
        self.assertEqual(revised["result_count"], 1)

    def test_batch_get_modes_and_neighbors(self):
        first = put_method(self.store, "问题定义", "需求不明确", detail="一个完整案例")
        second = put_method(self.store, "边界检查", "边界不明确")
        relation, _ = self.store.put_relation(method_a_id=first.method_id,
            method_b_id=second.method_id, explanation="定义后检查边界", detail="关系案例")
        service = MethodGraphService(self.store)
        detail = service.methodology_get([
            {"kind": "method", "ref": first.method_id},
            {"kind": "relation", "ref": relation.relation_id},
        ])
        self.assertNotIn("card", detail["items"][0])
        self.assertEqual(detail["items"][0]["detail"], "一个完整案例")
        full = service.methodology_get([{"kind": "method", "ref": first.method_id}], mode="full")
        self.assertIn("card", full["items"][0])
        audit = service.methodology_get([{"kind": "method", "ref": first.method_id}], mode="audit")
        self.assertTrue(audit["items"][0]["history"])
        neighbors = service.methodology_neighbors(first.method_id)
        self.assertEqual(neighbors["neighbors"][0]["method"]["method_ref"], second.method_id)

    def test_hook_returns_codex_additional_context_and_fails_open(self):
        put_method(self.store, "问题定义", "需求不明确")
        previous = os.environ.get("METHODGRAPH_DB")
        os.environ["METHODGRAPH_DB"] = str(self.db_path)
        os.environ["METHODGRAPH_HOOK_MIN_SCORE"] = "0.01"
        try:
            output = build_hook_output({"prompt": "需求不明确", "session_id": "session-1"},
                                       allow_remote=False)
            empty = build_hook_output({"prompt": "完全无关内容", "session_id": "session-2"},
                                      allow_remote=False)
        finally:
            os.environ.pop("METHODGRAPH_HOOK_MIN_SCORE", None)
            if previous is None: os.environ.pop("METHODGRAPH_DB", None)
            else: os.environ["METHODGRAPH_DB"] = previous
        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertIn("## 问题定义", output["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(empty, {})

    def test_hook_search_context_uses_recent_raw_prompts_without_recursion(self):
        previous = os.environ.get("METHODGRAPH_DB")
        os.environ["METHODGRAPH_DB"] = str(self.db_path)
        try:
            first = _build_search_context(
                {"turn_id": "t1", "cwd": "/workspace/demo"}, "讨论检索门控", "session-context"
            )
            second = _build_search_context(
                {"turn_id": "t2", "cwd": "/workspace/demo"}, "可以继续", "session-context"
            )
            third = _build_search_context(
                {"turn_id": "t3", "cwd": "/workspace/demo"}, "现在实现", "session-context"
            )
        finally:
            if previous is None:
                os.environ.pop("METHODGRAPH_DB", None)
            else:
                os.environ["METHODGRAPH_DB"] = previous

        self.assertNotIn("Recent user requests", first)
        self.assertIn("讨论检索门控", second)
        self.assertIn("可以继续", third)
        self.assertIn("讨论检索门控", third)
        self.assertEqual(third.count("Current user request:"), 1)
        self.assertIn("workspace=/workspace/demo", third)


if __name__ == "__main__":
    unittest.main()
