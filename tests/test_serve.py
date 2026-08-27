"""The local server that replaced the Worker.

Two things here can be wrong in a way that costs more than a confusing screen.
The socket could be bound somewhere other than loopback, which would put a
surface that applies decisions immediately on whatever network this machine is
sitting on. And a permission answer could reach a gate it was not written for.
So those are what is tested, along with the plain "does it serve the room".

    python3 -m unittest discover -s tests -p 'test_*.py'
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))

SNAP = {
    "generated": "2026-08-26T12:00:00Z",
    "heartbeat": "", "killed": False, "today": {"landed": 2},
    "stations": [{"repo": "acme/thing", "issues": [], "prs": []}],
    "runtime": {"gate": {"state": "clear"}, "board": {"state": "down"}},
    "sections": {},
}


class ServeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import serve

        cls.serve = serve
        serve.log = lambda msg: None  # a passing test suite says nothing
        # Neither of these may touch GitHub or a keychain from a test. The point
        # of the harness is that it runs anywhere, with no credentials at all.
        serve.office_sync.Access = lambda: object()
        serve.office_sync.build_snapshot = lambda access: dict(SNAP)

        cls.world = serve.World()
        cls.world.build()
        # Port 0 is "any free port", so several of these can run at once.
        cls.httpd = serve.make_server(cls.world, None, 0)
        cls.host, cls.port = cls.httpd.server_address[:2]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    # ── plumbing ────────────────────────────────────────────────────────────
    def get(self, path):
        url = f"http://127.0.0.1:{self.port}{path}"
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                return r.status, json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode()
            exc.close()
            return exc.code, json.loads(body or "{}")

    def post(self, path, body):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode(), method="POST",
            headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            exc.close()
            return exc.code, json.loads(raw or "{}")

    # ── the room ────────────────────────────────────────────────────────────
    def test_the_socket_is_loopback_only(self):
        """The bind address IS the security model. There is no password behind
        it, so a wider bind would put a surface that merges PRs on the network."""
        self.assertEqual(self.host, "127.0.0.1")

    def test_world_is_the_snapshot_that_was_built(self):
        code, body = self.get("/api/world")
        self.assertEqual(code, 200)
        self.assertEqual(body["world"], SNAP)
        self.assertEqual(body["at"], SNAP["generated"])
        self.assertIsInstance(body["decisions"], list)
        self.assertTrue(body["server_time"])

    def test_health_says_when_the_picture_was_taken(self):
        code, body = self.get("/api/health")
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["snapshot_at"], SNAP["generated"])

    def test_gate_reports_its_own_state_rather_than_nothing(self):
        code, body = self.get("/api/gate")
        self.assertEqual(code, 200)
        self.assertIn("state", body)

    # ── refusals ────────────────────────────────────────────────────────────
    def test_an_unknown_kind_is_refused_before_anything_runs(self):
        code, body = self.post("/api/decision", {"kind": "delete", "repo": "a/b"})
        self.assertEqual(code, 400)
        self.assertEqual(body["error"], "unknown kind delete")

    def test_a_permit_without_a_well_formed_id_is_refused(self):
        code, body = self.post("/api/decision",
                               {"kind": "permit", "question_id": "nope", "answer": "allow"})
        self.assertEqual(code, 400)
        self.assertIn("question id", body["error"])

    def test_a_permit_for_a_question_that_moved_on_is_409_and_writes_nothing(self):
        """The sharpest edge in the project. Between a gate being shown and being
        answered, the agent can time out and a different gate can open; answering
        by position would approve a command nobody ever saw."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "_meta" / "state").mkdir(parents=True)
            gate = root / "_meta" / "state" / "pending-question.json"
            gate.write_text(json.dumps({
                "id": "a" * 16, "permission": "Bash", "target": "rm -rf /",
                "asked_at": time.time(),
            }))
            before = gate.read_bytes()

            old = os.environ.get("OFFICE_RUNTIME_ROOT")
            os.environ["OFFICE_RUNTIME_ROOT"] = str(root)
            try:
                code, body = self.post("/api/decision", {
                    "kind": "permit", "question_id": "b" * 16, "answer": "allow",
                })
            finally:
                if old is None:
                    os.environ.pop("OFFICE_RUNTIME_ROOT", None)
                else:
                    os.environ["OFFICE_RUNTIME_ROOT"] = old

            self.assertEqual(code, 409)
            self.assertFalse(body["ok"])
            self.assertIn("moved on", body["result"])
            self.assertEqual(gate.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
