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


class DoorTest(ServeTest):
    """The bind address keeps the network out; these keep the browser out."""

    def raw(self, method, path, headers=None, body=None):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"host": f"127.0.0.1:{self.port}", "content-type": "application/json"}
        h.update(headers or {})
        c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=h)
        r = c.getresponse()
        data = json.loads(r.read().decode() or "{}")
        c.close()
        return r.status, data

    def test_a_request_for_another_host_is_refused(self):
        """DNS rebinding: an attacker's name resolving to 127.0.0.1 must read nothing."""
        code, body = self.raw("GET", "/api/world", {"host": "evil.example.com"})
        self.assertEqual(code, 403)
        self.assertNotIn("world", body)

    def test_a_cross_site_write_is_refused(self):
        code, _ = self.raw("POST", "/api/gate", {"sec-fetch-site": "cross-site"},
                           {"question_id": "deadbeefcafe", "answer": "allow"})
        self.assertEqual(code, 403)

    def test_a_write_from_another_origin_is_refused(self):
        code, _ = self.raw("POST", "/api/decision", {"origin": "https://evil.example.com"},
                           {"kind": "comment", "repo": "a/b", "issue": 1, "body": "x"})
        self.assertEqual(code, 403)

    def test_a_form_post_is_refused(self):
        """text/plain needs no preflight, so a plain HTML form could reach here."""
        code, _ = self.raw("POST", "/api/gate", {"content-type": "text/plain"},
                           {"question_id": "deadbeefcafe", "answer": "allow"})
        self.assertEqual(code, 403)

    def test_a_same_origin_write_still_works(self):
        code, body = self.raw("POST", "/api/decision",
                              {"origin": f"http://127.0.0.1:{self.port}", "sec-fetch-site": "same-origin"},
                              {"kind": "nope"})
        self.assertEqual(code, 400)  # past the door, refused by validation as before
        self.assertIn("error", body)

    def test_validation_matches_the_worker_not_python(self):
        v = self.serve.validate
        self.assertIsNotNone(v({"kind": "comment", "repo": "a/b\n", "issue": 7, "body": "x"})[0])
        self.assertIsNotNone(v({"kind": "merge", "repo": "a/b", "pr": "7\n"})[0])
        self.assertIsNotNone(v({"kind": "merge", "repo": "a/b", "pr": "١٢"})[0])
        self.assertIsNone(v({"kind": "merge", "repo": "a/b", "pr": "7"})[0])


class StaticTest(unittest.TestCase):
    def test_a_sibling_directory_named_like_dist_is_not_served(self):
        import tempfile, http.client, serve
        serve.log = lambda msg: None
        tmp = pathlib.Path(tempfile.mkdtemp())
        (tmp / "dist").mkdir(); (tmp / "dist-orders").mkdir()
        (tmp / "dist" / "index.html").write_text("<!doctype html>ROOM")
        (tmp / "dist-orders" / "index.html").write_text("<!doctype html>SIBLING")
        world = serve.World()
        httpd = serve.make_server(world, tmp / "dist", 0)
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True); t.start()
        try:
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            c.request("GET", "/../dist-orders/index.html", headers={"host": f"127.0.0.1:{port}"})
            r = c.getresponse(); body = r.read().decode(); c.close()
            self.assertEqual(r.status, 200)
            self.assertIn("ROOM", body)
            self.assertNotIn("SIBLING", body)
        finally:
            httpd.shutdown(); httpd.server_close()
