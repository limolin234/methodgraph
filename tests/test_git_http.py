from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from methodgraph.api import create_app
from methodgraph.config import AppConfig, ClientConfig, EmbeddingConfig, ServerConfig
from methodgraph.content import GitContentRepository
from methodgraph.embedding import OpenAICompatibleBackend
from methodgraph.store import MethodGraphStore


class GitContentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db = root / "source.db"
        self.repo_path = root / "content"
        self.store = MethodGraphStore(self.db)
        self.store.initialize()
        self.source = self.store.add_source(kind="book", title="Systems Handbook", content="source text")
        self.method, _ = self.store.put_method(title="Boundary framing", when="scope is unclear",
            why="boundaries determine ownership", how="draw a context boundary", detail="worked example",
            source_ids=[self.source.source_id], actor="legacy")
        self.other, _ = self.store.put_method(title="Interface check")
        self.relation, _ = self.store.put_relation(method_a_id=self.method.method_id,
            method_b_id=self.other.method_id, explanation="Use together", detail="relation example")
        self.repo = GitContentRepository(self.repo_path, committer_name="MethodGraph Server",
                                         committer_email="server@example.test")
        self.repo.export_store(self.store, author_name="Alice", author_email="alice@example.test")

    def tearDown(self):
        self.temp.cleanup()

    def test_markdown_round_trip_projection_and_git_identity(self):
        projected = MethodGraphStore(Path(self.temp.name) / "projected.db")
        result = self.repo.sync_projection(projected)
        restored = projected.get_method(self.method.method_id)
        self.assertEqual((result["sources"], result["methods"], result["relations"]), (1, 2, 1))
        self.assertEqual(restored.detail, "worked example")
        self.assertEqual(restored.source_ids, (self.source.source_id,))
        self.assertEqual(projected.get_source(self.source.source_id).content, "source text")
        projected.put_embedding(object_kind="method", object_id=restored.method_id,
                                revision_id=restored.revision_id, model="fake", vector=[1.0, 0.0])
        self.repo.sync_projection(projected)
        self.assertIn(restored.method_id, projected.get_embeddings(object_kind="method", model="fake"))
        author = subprocess.check_output(["git", "log", "-1", "--format=%an <%ae>"],
                                         cwd=self.repo_path, text=True).strip()
        committer = subprocess.check_output(["git", "log", "-1", "--format=%cn <%ce>"],
                                            cwd=self.repo_path, text=True).strip()
        self.assertEqual(author, "Alice <alice@example.test>")
        self.assertEqual(committer, "MethodGraph Server <server@example.test>")

    def test_stale_revision_rejected_and_restore_creates_new_commit(self):
        projected = MethodGraphStore(Path(self.temp.name) / "projected.db")
        self.repo.sync_projection(projected)
        current = projected.get_method(self.method.method_id)
        changed = current.__class__(**{name: getattr(current, name) for name in current.__dataclass_fields__}
                                    | {"why": "changed"})
        old_commit = self.repo.head()
        self.repo.add_method(changed, author_name="Alice", author_email="alice@example.test",
                             reason="change rationale", expected_revision=current.revision_id)
        with self.assertRaisesRegex(RuntimeError, "stale revision"):
            self.repo.add_method(changed, author_name="Alice", author_email="alice@example.test",
                                 reason="stale", expected_revision=current.revision_id)
        before_restore = self.repo.head()
        self.repo.restore("method", self.method.method_id, old_commit, author_name="Alice",
                          author_email="alice@example.test", reason="restore previous method")
        self.assertNotEqual(before_restore, self.repo.head())
        self.repo.sync_projection(projected)
        self.assertEqual(projected.get_method(self.method.method_id).why, self.method.why)

    def test_invalid_identity_is_rejected(self):
        with self.assertRaises(ValueError):
            self.repo.add_method(self.method, author_name="bad\nname", author_email="x@example.test", reason="bad")

    def test_http_search_and_admin_write(self):
        try:
            from starlette.testclient import TestClient
        except ImportError:
            self.skipTest("server extra is not installed")
        projection = Path(self.temp.name) / "api.db"
        config = AppConfig(client=ClientConfig(), server=ServerConfig(
            content_repo=str(self.repo_path), database=str(projection)),
            embedding=EmbeddingConfig(provider="none", model="none"))
        with TestClient(create_app(config)) as client:
            health = client.get("/healthz")
            self.assertEqual(health.status_code, 200)
            search = client.post("/v1/search", json={"context": "scope is unclear", "min_score": 0.01})
            self.assertEqual(search.status_code, 200)
            self.assertGreater(search.json()["result_count"], 0)
            audit = client.post("/v1/get", json={"items": [{"kind": "method", "ref": self.method.method_id}],
                                                  "mode": "audit"})
            self.assertTrue(audit.json()["items"][0]["history"])
            trivial = client.post("/v1/hooks/retrieve", json={"prompt": "好的", "session_id": "new-session"})
            self.assertEqual(trivial.json(), {})
            exact = client.post("/v1/hooks/retrieve", json={"prompt": "Boundary", "session_id": "exact-session",
                                                             "min_score": 0.01})
            self.assertIn("hookSpecificOutput", exact.json())
            added = client.post("/v1/admin/methods", json={"title": "New method", "reason": "test write",
                "author_name": "Bob", "author_email": "bob@example.test"})
            self.assertEqual(added.status_code, 200, added.text)
            self.assertIsNotNone(MethodGraphStore(projection).get_method("New method"))
            stale = client.patch(f"/v1/admin/methods/{added.json()['method_ref']}", json={
                "changes": {"why": "x"}, "reason": "stale update", "expected_revision": "wrong",
                "author_name": "Bob", "author_email": "bob@example.test"})
            self.assertEqual(stale.status_code, 409)


class EmbeddingAPITest(unittest.TestCase):
    def test_openai_compatible_backend_batches_and_orders(self):
        requests: list[dict] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                payload = json.loads(self.rfile.read(length))
                requests.append(payload)
                data = [{"index": index, "embedding": [float(len(text)), 1.0]}
                        for index, text in enumerate(payload["input"])]
                body = json.dumps({"data": list(reversed(data))}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

            def log_message(self, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            backend = OpenAICompatibleBackend(model_name="fake", base_url=f"http://127.0.0.1:{server.server_port}/v1",
                                               batch_size=2)
            vectors = backend.encode_documents(["a", "bb", "ccc"])
            self.assertEqual(vectors, [[1.0, 1.0], [2.0, 1.0], [3.0, 1.0]])
            self.assertEqual([len(item["input"]) for item in requests], [2, 1])
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == "__main__":
    unittest.main()
