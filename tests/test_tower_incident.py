"""Office owns one durable Tower health issue after the TBS probe cutover."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
import tower_incident


class TowerIncident(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "state.json"
        self.open = None
        self.calls = []

    def call(self, args):
        self.calls.append(args)
        if args[0:2] == ["issue", "list"]:
            return json.dumps([self.open] if self.open else [])
        if args[0:2] == ["issue", "create"]:
            self.open = {"number": 88, "title": tower_incident.TITLE,
                         "body": args[args.index("--body") + 1]}
            return "https://github.com/ariaxhan/nexus-office/issues/88"
        if args[0:2] == ["issue", "edit"]:
            self.open["body"] = args[args.index("--body") + 1]
            return ""
        if args[0:2] == ["issue", "close"]:
            self.open = None
            return ""
        raise AssertionError(args)

    def test_one_issue_then_recovery_closes_it(self):
        bad = {"state": "unreadable", "detail": "Tower ledger failed integrity check"}
        first = tower_incident.sync(bad, call=self.call, path=self.path)
        self.assertEqual(first["state"], "unhealthy")
        self.assertIsNone(first["pending"])
        tower_incident.sync(bad, call=self.call, path=self.path)
        self.assertEqual(1, sum(args[:2] == ["issue", "create"] for args in self.calls))
        self.assertEqual(0, sum(args[:2] == ["issue", "edit"] for args in self.calls))
        good = {"state": "ok", "tower": {"state": "running"}}
        tower_incident.sync(good, call=self.call, path=self.path)
        self.assertIsNone(self.open)
        self.assertEqual("healthy", json.loads(self.path.read_text())["state"])

    def test_github_outage_is_a_retryable_local_receipt(self):
        bad = {"state": "ok", "tower": {"state": "down", "detail": "no tick"}}
        failed = tower_incident.sync(bad, call=lambda args: (_ for _ in ()).throw(RuntimeError("offline")),
                                     path=self.path)
        self.assertEqual("offline", failed["pending"])
        self.assertTrue(self.path.exists())
        recovered = tower_incident.sync(bad, call=self.call, path=self.path)
        self.assertIsNone(recovered["pending"])
        self.assertEqual(88, self.open["number"])


if __name__ == "__main__":
    unittest.main()
