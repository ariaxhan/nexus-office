"""Evidence comes from local files; steering is append-only and bounded by the door."""
import concurrent.futures
import json
import pathlib
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))
import lesson_status as status
import serve


class LessonFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path


class StatusTest(LessonFixture):
    def build(self):
        with patch.object(status, "_main", return_value={"sha": "1234567890", "paths": set(), "error": ""}):
            return status.build(self.root)

    def test_all_lessons_and_missing_evidence_are_explicit(self):
        data = self.build()
        self.assertEqual(len(data["lessons"]), 192)
        self.assertEqual(data["lessons"][-1]["lesson"], "L096")
        row = data["lessons"][0]
        self.assertEqual(row["state"], "unplanned")
        self.assertIsNone(row["published"]["present"])
        self.assertEqual(row["continuity"]["state"], "unknown")
        self.assertEqual(row["planned"]["decision"], "")

    def test_publication_true_false_and_stale_preserve_check_time(self):
        for hours, expected in [(0, "current"), (14, "stale")]:
            checked = (status.dt.datetime.now(status.dt.timezone.utc) - status.dt.timedelta(hours=hours)).isoformat()
            self.write(status.PUBLICATION_SOURCE, json.dumps({"checked_at": checked,
                       "superpowerai": {"L001": True, "L014": False}, "mommyai": {"L024": True, "L025": False}}))
            rows = {(r["product"], r["lesson"]): r["published"] for r in self.build()["lessons"]}
            for product, lesson, value in [("superpowerai", "L001", True), ("superpowerai", "L014", False),
                                           ("mommyai", "L024", True), ("mommyai", "L025", False)]:
                self.assertIs(rows[product, lesson]["present"], value)
                self.assertEqual(rows[product, lesson]["checked_at"], checked)
                self.assertEqual(rows[product, lesson]["state"], expected)
            self.assertIsNone(rows["mommyai", "L096"]["present"])

    def test_invalid_publication_never_becomes_false(self):
        for receipt in ["broken", "[]", '{"checked_at":"bad"}',
                        json.dumps({"checked_at": status.dt.datetime.now(status.dt.timezone.utc).isoformat(),
                                    "superpowerai": {"L001": "false"}})]:
            self.write(status.PUBLICATION_SOURCE, receipt)
            value = self.build()["lessons"][0]["published"]
            self.assertIsNone(value["present"])
            self.assertEqual(value["state"], "unknown")

    def test_independent_signals_and_latest_findings(self):
        base = "proposed/tbs-curriculum/superkidsai/L001"
        self.write(base + "-OPTIONS.md", "decision: keep song\n")
        self.write(base + "-song/lesson.kr.json", "{}")
        self.write(base + "-song/lesson.en.json", "{}")
        self.write("media/spa/L001/receipt.json", "{}")
        self.write("preview-hub/lessons.json", json.dumps([{"product": "superpowerai", "lesson": "L001", "path": "/superpowerai/paid/lesson001"}]))
        self.write("findings/playthrough/song.md", "# Report")
        rows = [{"product": "superpowerai", "lesson": "L001", "gaps": 4},
                {"product": "superpowerai", "lesson": "L001", "verdict": "PASS", "gaps": 0, "report": "findings/playthrough/song.md"}]
        self.write("findings/playthrough/ledger.jsonl", "\n".join(map(json.dumps, rows)))
        self.write("findings/continuity/ledger.jsonl", json.dumps({"product": "superpowerai", "defects": 3, "at": "today"}))
        row = self.build()["lessons"][0]
        self.assertEqual(row["state"], "previewed")
        self.assertEqual(row["planned"]["decision"], "keep song")
        self.assertTrue(row["drafted"]["present"])
        self.assertTrue(row["media"]["present"])
        self.assertEqual(row["playthrough"]["gaps"], 0)
        self.assertEqual(row["continuity"]["scope"], "Product-wide check")
        self.assertTrue(row["playthrough"]["report_url"].startswith("/api/lesson-report?"))

    def test_main_is_remote_ref_not_dirty_working_tree_and_mommy_offset(self):
        repo = self.root / "repos/tbs-landing"
        repo.mkdir(parents=True)
        def git(*args):
            return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()
        git("init", "-q")
        self.write("repos/tbs-landing/src/pages/lessons/LessonPaid21.jsx", "export default {}")
        git("add", "src/pages/lessons/LessonPaid21.jsx")
        git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null", "commit", "-qm", "fixture")
        git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.write("repos/tbs-landing/src/pages/lessons/LessonPaid22.jsx", "uncommitted")
        main = status._main(self.root, "tbs-landing")
        self.assertIn("src/pages/lessons/LessonPaid21.jsx", main["paths"])
        self.assertNotIn("src/pages/lessons/LessonPaid22.jsx", main["paths"])
        row = status._lesson(self.root, "mommyai", 23, {}, main, {"continuity": {}, "playthrough": {}})
        self.assertEqual(row["state"], "on main")
        self.assertEqual(row["on_main"]["sha"], git("rev-parse", "HEAD")[:10])

    def test_build_directory_is_draft_but_one_language_is_not(self):
        self.write("proposed/tbs-curriculum/mommyai/L022-test/lesson.kr.json", "{}")
        self.assertFalse(self.build()["lessons"][117]["drafted"]["present"])
        self.write("proposed/tbs-curriculum/mommyai/L022-test/_build_today/index.html", "page")
        self.assertTrue(self.build()["lessons"][117]["drafted"]["present"])

    def test_steering_preserves_bytes_and_serializes_concurrent_appends(self):
        initial = "decision: keep this\n\n# Lesson\n"
        path = self.write("proposed/tbs-curriculum/mommyai/L096-OPTIONS.md", initial)
        def append(number):
            return status.steer({"product": "mommyai", "lesson": "L096", "text": f"note {number}\n decision: injected"}, self.root)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(append, range(8)))
        content = path.read_text()
        self.assertTrue(content.startswith(initial))
        self.assertEqual(content.count("## steering"), 1)
        self.assertEqual(content.count("\n decision:"), 0)
        self.assertEqual(content.count(" - note "), 8)
        self.assertEqual(status.report(str(path.relative_to(self.root)), self.root), content)

    def test_invalid_targets_and_symlinks_cannot_escape(self):
        for lesson in ("../L001", "L000", "L097", None):
            with self.assertRaises(ValueError):
                status.steer({"product": "mommyai", "lesson": lesson, "text": "hello"}, self.root)
        for text in ("", " ", "x" * 8001, 42):
            with self.assertRaises(ValueError):
                status.steer({"product": "mommyai", "lesson": "L001", "text": text}, self.root)
        outside = self.root.parent / "outside-options.md"
        target = self.root / "proposed/tbs-curriculum/mommyai/L001-OPTIONS.md"
        target.parent.mkdir(parents=True)
        target.symlink_to(outside)
        with self.assertRaises(ValueError):
            status.steer({"product": "mommyai", "lesson": "L001", "text": "no"}, self.root)
        with self.assertRaises(ValueError):
            status.report("findings/continuity/../../../outside-options.md", self.root)

    def test_empty_decision_never_reads_the_next_heading(self):
        self.write("proposed/tbs-curriculum/superkidsai/L001-OPTIONS.md", "decision:\n# Lesson")
        self.assertEqual(self.build()["lessons"][0]["planned"]["decision"], "")

    def test_internal_symlink_cannot_redirect_steering(self):
        other = self.write("proposed/tbs-curriculum/mommyai/L002-OPTIONS.md", "decision: stay\n")
        other.with_name("L001-OPTIONS.md").symlink_to(other)
        with self.assertRaises(ValueError):
            status.steer({"product": "mommyai", "lesson": "L001", "text": "no"}, self.root)
        self.assertEqual(other.read_text(), "decision: stay\n")

    def test_malformed_ledger_is_a_gap_and_unsafe_preview_is_not_linked(self):
        self.write("findings/playthrough/ledger.jsonl", "not json\n{}")
        self.write("preview-hub/lessons.json", json.dumps([{"product": "mommyai", "lesson": "L001", "path": "javascript:alert(1)"}]))
        data = self.build()
        self.assertTrue(any("invalid ledger" in gap for gap in data["gaps"]))
        self.assertEqual(data["lessons"][96]["previewed"]["url"], "")


class RouteTest(LessonFixture):
    def setUp(self):
        super().setUp()
        self.env = patch.dict("os.environ", {"OFFICE_LESSON_ROOT": str(self.root)})
        self.env.start()
        self.addCleanup(self.env.stop)
        handler = type("LessonHandler", (serve.Handler,), {"chatroom": Mock(), "world": Mock(snapshot={})})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.addCleanup(self.server.server_close)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def request(self, path, body=None, headers=None):
        request = urllib.request.Request(self.url + path, data=body, headers=headers or {})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, response.read(), response.headers
        except urllib.error.HTTPError as error:
            with error:
                return error.code, error.read(), error.headers

    def test_post_roundtrip_report_and_security(self):
        body = json.dumps({"product": "mommyai", "lesson": "L026", "text": "Keep the task concrete"}).encode()
        headers = {"Content-Type": "application/json"}
        code, raw, _ = self.request("/api/lesson-steering", body, headers)
        self.assertEqual(code, 200, raw)
        url = json.loads(raw)["report_url"]
        code, raw, response_headers = self.request(url)
        self.assertEqual(code, 200)
        self.assertIn(b"Keep the task concrete", raw)
        self.assertIn("text/plain", response_headers["Content-Type"])
        self.assertEqual(response_headers["X-Content-Type-Options"], "nosniff")
        for override in ({"Origin": "https://evil.example"}, {"Host": "evil.example"}, {"Content-Type": "text/plain"}):
            self.assertEqual(self.request("/api/lesson-steering", body, headers | override)[0], 403)
        self.assertEqual(self.request("/api/lesson-steering", b"x" * (serve.WRITE_LIMIT + 1), headers)[0], 400)
        self.assertEqual(self.request("/api/lesson-steering")[0], 404)
        code, raw, _ = self.request("/api/lesson-status")
        self.assertEqual(code, 200)
        self.assertEqual(len(json.loads(raw)["lessons"]), 192)
