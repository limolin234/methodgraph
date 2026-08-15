from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from methodgraph.store import MethodGraphStore


CARD = {
    "title": "Boundary before decomposition",
    "when": "The system boundary is unclear while responsibilities are assigned.",
    "why": "A stable boundary prevents hidden interface assumptions.",
    "how": "Define the system and external interfaces before decomposing internals.",
    "philosophy": "A system is understood through distinctions and interactions.",
    "boundary": "Revisit a boundary when new evidence invalidates it.",
}


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = MethodGraphStore(Path(self.temp_dir.name) / "methodgraph.db")
        self.store.initialize()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_only_title_is_required_and_source_is_auditable(self):
        source = self.store.add_source(
            kind="book", title="Systems Engineering Handbook",
            content="Define the problem before selecting a solution.",
            author="NASA", published_at="2016", locator="Section 4.2",
        )
        method, tx = self.store.put_method(title="Problem framing", source_ids=[source.source_id])
        self.assertEqual(method.when, "")
        self.assertEqual(method.source_ids, (source.source_id,))
        self.assertTrue(tx.startswith("tx_"))
        self.assertEqual(self.store.history(kind="method", object_ref=method.method_id)[0]["operation"], "create")

    def test_update_retire_and_restore_are_new_revisions(self):
        method, _ = self.store.put_method(**CARD)
        changed, _ = self.store.put_method(method_id=method.method_id, how="Use a context diagram.")
        self.assertNotEqual(method.revision_id, changed.revision_id)
        self.store.retire_method(method.method_id)
        self.assertIsNone(self.store.get_method(method.method_id))
        restored_revision, _ = self.store.restore_revision(method.revision_id)
        restored = self.store.get_method(method.method_id)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.how, CARD["how"])
        self.assertEqual(restored.revision_id, restored_revision)
        self.assertEqual([x["operation"] for x in self.store.history(kind="method", object_ref=method.method_id)][:3],
                         ["restore", "retire", "update"])

    def test_agent_cannot_change_human_content(self):
        human, _ = self.store.put_method(**CARD, actor_authority="human")
        with self.assertRaises(PermissionError):
            self.store.put_method(method_id=human.method_id, how="overwrite", actor_authority="agent")
        agent, _ = self.store.put_method(title="Agent candidate", actor_authority="agent")
        updated, _ = self.store.put_method(method_id=agent.method_id, when="local evidence", actor_authority="agent")
        self.assertEqual(updated.when, "local evidence")

    def test_relation_is_untyped_pair_unique_and_agent_overlay_is_separate(self):
        first, _ = self.store.put_method(**CARD)
        second, _ = self.store.put_method(title="Interface ownership check")
        original, _ = self.store.put_relation(method_a_id=first.method_id,
            method_b_id=second.method_id, explanation="Checks whether the boundary is operational.",
            weight=0.8)
        changed, _ = self.store.put_relation(method_a_id=second.method_id,
            method_b_id=first.method_id, explanation="Consider these together.", weight=0.9)
        self.assertEqual(original.relation_id, changed.relation_id)
        self.assertEqual(len(self.store.list_relations()), 1)
        with self.assertRaises(PermissionError):
            self.store.put_relation(method_a_id=first.method_id,
                method_b_id=second.method_id, weight=0, actor_authority="agent")

    def test_embedding_projection_follows_current_revision(self):
        method, _ = self.store.put_method(**CARD)
        self.store.put_embedding(object_kind="method", object_id=method.method_id,
            revision_id=method.revision_id, model="fake", vector=[0.1, 0.2])
        self.assertEqual(len(self.store.get_embeddings(object_kind="method", model="fake")), 1)
        self.store.put_method(method_id=method.method_id, why="revised")
        self.assertEqual(self.store.get_embeddings(object_kind="method", model="fake"), {})


if __name__ == "__main__":
    unittest.main()
