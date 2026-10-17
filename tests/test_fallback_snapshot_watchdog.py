import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/fallback_snapshot_watchdog.py"
spec = importlib.util.spec_from_file_location("fallback_snapshot_watchdog", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class WatchdogTest(unittest.TestCase):
    def test_missed_publication_alerts_once_then_recovers(self):
        posted = []
        comment = lambda body: posted.append(body) or True
        fresh = {"status":200,"healthy":True,"snapshot_at":"2026-10-17T00:00:00Z","age_s":100}
        stale = {**fresh,"age_s":680}
        state = module.advance({}, fresh, comment=comment)
        self.assertEqual(posted, [])
        state = module.advance(state, stale, comment=comment)
        self.assertTrue(state["alert_open"])
        self.assertEqual(len(posted), 1)
        state = module.advance(state, stale, comment=comment)
        self.assertEqual(len(posted), 1)
        state = module.advance(state, {**fresh,"snapshot_at":"2026-10-17T00:12:00Z"}, comment=comment)
        self.assertFalse(state["alert_open"])
        self.assertEqual(len(posted), 2)

    def test_unreachable_requires_two_checks_and_failed_comment_retries(self):
        comment = Mock(side_effect=[False, True])
        state = module.advance({}, None, comment=comment)
        self.assertEqual(comment.call_count, 0)
        state = module.advance(state, None, comment=comment)
        self.assertFalse(state["alert_open"])
        state = module.advance(state, None, comment=comment)
        self.assertTrue(state["alert_open"])
        self.assertEqual(comment.call_count, 2)

    def test_probe_reads_bounded_health_and_rejects_wrong_mode(self):
        class Response:
            status = 200
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size): return self.body
        body = json.dumps({"ok":True,"mode":"read-only-fallback",
                           "snapshot_at":"2026-10-17T00:00:00Z"}).encode()
        with patch.object(module, "urlopen", return_value=Response(body)):
            result = module.probe(now=1792195500)
        self.assertEqual(result["status"], 200)
        with patch.object(module, "urlopen", return_value=Response(b'{"mode":"wrong"}')):
            with self.assertRaises(ValueError): module.probe(now=1792195500)


if __name__ == "__main__":
    unittest.main()
