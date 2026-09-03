from __future__ import annotations

import http.client
import importlib.util
import json
import pathlib
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "export_lesson_viewer", ROOT / "scripts" / "export-lesson-viewer.py"
)
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)


def preview(product="mommyai", route="/mommyai/paid/lesson021"):
    return {"schema_version": "tbs.lesson-preview/v2", "product": product,
            "origin": "https://candidate.example", "route": route,
            "deployment_id": "dpl_candidate", "deployment_state": "READY",
            "deployment_sha": "a" * 40, "source_sha": "a" * 40,
            "deployment_created_at": 2000, "source_committed_at": 1000,
            "git_dirty": "0", "source_git_clean": True,
            "deployment_source_git_clean": "1", "outcome": "PASS",
            "verified_at": "2026-09-02T20:00:00Z", "console_errors": [],
            "request_failures": [], "screenshots": [{"width": x} for x in (375, 768, 1440)]}


class ExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self.tmp.name)
        self.receipts = self.base / "receipts"
        self.catalog = self.base / "catalog"
        self.output = self.base / "public"
        self.catalog.mkdir()
        catalog = [{"product": "mommyai", "slug": f"mommyai/paid/lesson{i:03d}",
                    "title": f"Lesson {i + 2}"} for i in range(21)]
        catalog.extend([
            {"product": "mommyai", "slug": "mommyai/paid/lesson020", "title": "Twenty two"},
            {"product": "mommyai", "slug": "mommyai/paid/lesson021", "title": "Twenty three"},
        ])
        (self.catalog / "mommyai.json").write_text(json.dumps(catalog), encoding="utf-8")
        folder = self.receipts / "mommyai" / "L023"
        folder.mkdir(parents=True)
        (folder / "preview.json").write_text(json.dumps(preview()), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_export_is_deterministic_and_preserves_fail_closed_rows(self):
        first = exporter.export(self.receipts, self.catalog, self.output)
        first_bytes = {p.relative_to(self.output): p.read_bytes()
                       for p in self.output.rglob("*") if p.is_file()}
        second = exporter.export(self.receipts, self.catalog, self.output)
        second_bytes = {p.relative_to(self.output): p.read_bytes()
                        for p in self.output.rglob("*") if p.is_file()}
        self.assertEqual(first_bytes, second_bytes)
        self.assertEqual(first, second)
        row = next(row for row in second["lessons"] if row["lesson"] == "L023")
        self.assertEqual(row["status"], "failed")
        self.assertIn("missing production receipt", row["problems"])
        self.assertEqual(row["candidate"]["url"],
                         "https://candidate.example/mommyai/paid/lesson021")
        payload = json.loads((self.output / "api" / "lesson-previews.json").read_text())
        self.assertNotIn("root", payload)

    def test_export_refuses_missing_receipts_instead_of_publishing_empty_data(self):
        with self.assertRaisesRegex(ValueError, "export refused: missing"):
            exporter.export(self.base / "absent", self.catalog, self.output)
        self.assertFalse(self.output.exists())

    def test_exported_routes_assets_and_api_work_over_http(self):
        exporter.export(self.receipts, self.catalog, self.output)
        root = self.output

        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(root), **kwargs)

            def do_GET(self):
                self.path = {"/lessons": "/lessons.html",
                             "/api/lesson-previews": "/api/lesson-previews.json"}.get(
                                 self.path, self.path)
                super().do_GET()

            def log_message(self, format, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def get(path):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
            connection.request("GET", path)
            response = connection.getresponse()
            result = response.status, response.getheader("Content-Type"), response.read()
            connection.close()
            return result

        code, ctype, body = get("/lessons")
        self.assertEqual(code, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b"<title>Lesson previews</title>", body)
        for path in ("/lessons.css", "/lessons.js"):
            self.assertEqual(get(path)[0], 200)
        code, ctype, body = get("/api/lesson-previews")
        self.assertEqual(code, 200)
        self.assertIn("application/json", ctype)
        self.assertEqual(json.loads(body)["counts"], {"total": 23, "failed": 23})


if __name__ == "__main__":
    unittest.main()
