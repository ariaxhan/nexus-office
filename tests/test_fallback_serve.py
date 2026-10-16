"""MacBook fallback starts separately and refuses all effects."""

import hashlib
import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))
import fallback_serve  # noqa: E402


class FallbackCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.snapshot = Path(self.tmp.name, "snapshot.json")
        self.snapshot.write_text(json.dumps({
            "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "stations": [{"repo": "ariaxhan/nexus-office"}]}))
        self.server = fallback_serve.make_server(0, self.snapshot)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.tmp.cleanup()

    def request(self, method, path, host=None, login=None, port=None):
        port = port or self.server.server_port
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        headers = {"Host": host or f"127.0.0.1:{port}"}
        if login:
            headers["Tailscale-User-Login"] = login
        conn.request(method, path, body=b"{}" if method != "GET" else None, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        result = response.status, raw, dict(response.getheaders())
        conn.close()
        return result

    def test_startup_mode_skips_normal_office_imports(self):
        script = ("import runpy,sys;"
                  "sys.argv=['client/serve.py','--read-only-fallback','--help'];"
                  "\ntry:runpy.run_path('client/serve.py',run_name='__main__')"
                  "\nexcept SystemExit as e:assert e.code==0"
                  "\nassert not {'board','buzz','chat','runtime','sessions','webhook','office_api'} & set(sys.modules)")
        result = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("read-only MacBook Office fallback", result.stdout)

    def test_health_world_and_page_are_cached_reads(self):
        before = hashlib.sha256(self.snapshot.read_bytes()).hexdigest()
        code, raw, _ = self.request("GET", "/api/health")
        self.assertEqual(200, code)
        self.assertEqual("read-only-fallback", json.loads(raw)["mode"])
        code, raw, _ = self.request("GET", "/api/world?fresh=1")
        self.assertEqual(200, code)
        self.assertFalse(json.loads(raw)["fresh"])
        code, raw, headers = self.request("GET", "/")
        self.assertEqual(200, code)
        self.assertIn(b"ariaxhan/nexus-office", raw)
        self.assertIn("no-store", headers["Cache-Control"])
        self.assertEqual(before, hashlib.sha256(self.snapshot.read_bytes()).hexdigest())
        self.assertEqual(["snapshot.json"], [p.name for p in Path(self.tmp.name).iterdir()])

    def test_all_write_methods_and_webhook_are_denied(self):
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            for path in ("/api/world", "/webhook", "/"):
                code, raw, _ = self.request(method, path)
                self.assertEqual(405, code, (method, path))
                self.assertIn(b"read-only fallback", raw)
        self.assertEqual(404, self.request("GET", "/office.js")[0])

    def test_stale_snapshot_and_host_identity_fail_closed(self):
        self.assertEqual(403, self.request("GET", "/api/health", host="evil.example")[0])
        self.snapshot.write_text(json.dumps({"generated": "2026-01-01T00:00:00Z", "stations": []}))
        self.assertEqual(503, self.request("GET", "/api/health")[0])
        self.assertEqual(503, self.request("GET", "/api/world")[0])
        self.assertIn(b"Stale snapshot", self.request("GET", "/")[1])
        self.assertIn(b"Open Studio to answer gates", self.request("GET", "/")[1])
        with self.assertRaises(ValueError):
            fallback_serve.make_server(0, self.snapshot, trusted_host="office-mac.example:9443")
        with self.assertRaises(ValueError):
            fallback_serve.make_server(0, self.snapshot, login="aria@example.com")
        guarded = fallback_serve.make_server(0, self.snapshot,
                                             trusted_host="office-mac.example:9443",
                                             login="aria@example.com")
        thread = threading.Thread(target=guarded.serve_forever, daemon=True)
        thread.start()
        try:
            self.assertEqual(403, self.request("GET", "/api/health", port=guarded.server_port)[0])
            self.assertEqual(403, self.request("GET", "/api/health",
                         host="office-mac.example:9443", port=guarded.server_port)[0])
            self.assertEqual(403, self.request("GET", "/api/health",
                         host="office-mac.example:9443", login="é@example.com",
                         port=guarded.server_port)[0])
            self.assertEqual(503, self.request("GET", "/api/health",
                         host="office-mac.example:9443", login="aria@example.com",
                         port=guarded.server_port)[0])
        finally:
            guarded.shutdown()
            guarded.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
