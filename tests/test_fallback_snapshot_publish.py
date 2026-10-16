import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/fallback_snapshot_publish.py"
spec = importlib.util.spec_from_file_location("fallback_snapshot_publish", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ProjectionTest(unittest.TestCase):
    def test_stale_cache_requests_one_fresh_build_without_weakening_gate(self):
        cached = {"generated": "2026-10-16T23:40:00Z", "stations": []}
        refreshed = {"generated": "2026-10-16T23:46:40Z", "stations": []}
        now = 1792194410  # 2026-10-16T23:46:50Z
        with patch.object(module, "fetch_world", side_effect=[cached, refreshed]) as fetch:
            self.assertEqual(module.source_world(now=now), refreshed)
            self.assertEqual(fetch.call_args_list[0].args[0], module.WORLD_URL)
            self.assertEqual(fetch.call_args_list[1].args[0], module.WORLD_URL + "?fresh=1")
        with patch.object(module, "fetch_world", side_effect=[cached, cached]) as fetch:
            with self.assertRaisesRegex(ValueError, "too old"):
                module.source_world(now=now)
            self.assertEqual(fetch.call_count, 2)

    def test_two_failures_alert_once_and_recovery_closes(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "publisher-state.json"
            with patch.object(module, "_issue_comment", return_value=True) as comment:
                module.record_result(state, error="ValueError")
                self.assertEqual(json.loads(state.read_text())["consecutive_failures"], 1)
                self.assertEqual(comment.call_count, 0)
                module.record_result(state, error="ValueError")
                self.assertEqual(comment.call_count, 1)
                self.assertTrue(json.loads(state.read_text())["alert_open"])
                module.record_result(state, error="ValueError")
                self.assertEqual(comment.call_count, 1)
                module.record_result(state, stamp="2026-10-16T23:46:47Z")
                self.assertEqual(comment.call_count, 2)
                self.assertEqual(json.loads(state.read_text())["consecutive_failures"], 0)
                self.assertFalse(json.loads(state.read_text())["alert_open"])

    def test_failed_alert_retries_and_corrupt_counter_recovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "publisher-state.json"
            state.write_text(json.dumps({"consecutive_failures": None}))
            with patch.object(module, "_issue_comment", side_effect=[False, True]) as comment:
                module.record_result(state, error="ValueError")
                self.assertEqual(json.loads(state.read_text())["consecutive_failures"], 1)
                module.record_result(state, error="ValueError")
                self.assertFalse(json.loads(state.read_text())["alert_open"])
                module.record_result(state, error="ValueError")
                self.assertTrue(json.loads(state.read_text())["alert_open"])
                self.assertEqual(comment.call_count, 2)

    def test_large_world_projects_only_bounded_cards(self):
        world = {"generated": "2026-10-16T23:00:00Z", "stations": [],
                 "sections": {"tower": {"state": "ok", "secret": "x" * 3000000,
                                        "card": {"title": "Tower", "headline": "14 issues in retry loops"}}}}
        projected = module.project_world(world)
        self.assertEqual(projected["stations"],
                         [{"repo": "Tower", "summary": "14 issues in retry loops", "state": "ok"}])
        self.assertLess(len(str(projected)), 1000)

    def test_no_display_cards_fails_closed(self):
        with self.assertRaises(ValueError):
            module.project_world({"generated": "2026-10-16T23:00:00Z", "sections": {}})


if __name__ == "__main__":
    unittest.main()
