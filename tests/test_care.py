"""Care cards read the active receipts, never the retired intake snapshot."""
import datetime
import importlib
import json
import os
import pathlib
import sys
import tempfile
import unittest

from test_sections import assert_card

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))
NOW = 1_800_000_000.0


def stamp(age):
    return datetime.datetime.fromtimestamp(NOW - age, datetime.timezone.utc).isoformat()


class CareState(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        os.environ["OFFICE_RUNTIME_ROOT"] = str(self.root)
        from sources import care
        self.care = importlib.reload(care)

    def tearDown(self):
        os.environ.pop("OFFICE_RUNTIME_ROOT", None)
        self.temp.cleanup()

    def write(self, rows=(), scan_age=60, queue_age=60, scan_status="ok", errors=0):
        scan = self.root / self.care.SCAN
        queue = self.root / self.care.QUEUE
        scan.parent.mkdir(parents=True, exist_ok=True)
        scan.write_text(json.dumps({"at": stamp(scan_age), "status": scan_status, "errors": errors}))
        counts = {state.replace("-", "_"): 0 for state in self.care.STATES}
        for row in rows:
            counts[row["state"].replace("-", "_")] += 1
        queue.write_text(json.dumps({"generated_at": stamp(queue_age),
                                     "summary": {"threads": len(rows), **counts}, "rows": list(rows)}))

    def row(self, state, suffix):
        return {"thread": suffix, "inbound_id": suffix + "-message", "state": state,
                "subject": "customer inquiry", "last_inbound_at": stamp(3600)}

    def test_retired_snapshot_cannot_make_care_current(self):
        retired = self.root / "_meta/services/intake/cache/care-last-run.json"
        retired.parent.mkdir(parents=True, exist_ok=True)
        retired.write_text(json.dumps({"at": stamp(60), "notes": ["0 unanswered"]}))
        data = self.care.read(now=NOW)
        self.assertEqual(data["state"], "source-stale")
        self.assertIn("Missing current Care receipt", self.care.card(data)["headline"])

    def test_current_receipts_show_distinct_obligation_states(self):
        rows = [self.row(s, str(i)) for i, s in enumerate(
            ("answered", "no-reply-owed", "waiting", "draft-held", "escalated", "outcome-unverified"))]
        self.write(rows)
        data = self.care.read(now=NOW)
        self.assertEqual(data["state"], "unverified")
        self.assertTrue(data["intake_current"])
        self.assertTrue(data["data_current"])
        self.assertEqual(data["counts"]["no-reply-owed"], 1)
        card = self.care.card(data)
        assert_card(self, card)
        self.assertEqual(card["needs"], 0)  # No receipt says Aria must decide.
        self.assertIn("unverified", card["headline"])
        self.assertEqual(len(card["rows"]), 4)

    def test_stale_intake_and_queue_are_named_separately(self):
        self.write([self.row("answered", "one")], scan_age=5 * 3600)
        data = self.care.read(now=NOW)
        self.assertEqual(data["state"], "intake-stale")
        self.assertIn("intake is stale", self.care.card(data)["headline"])
        self.write([self.row("answered", "one")], queue_age=3 * 3600)
        data = self.care.read(now=NOW)
        self.assertEqual(data["state"], "source-stale")
        self.assertIn("queue source is stale", self.care.card(data)["headline"])

    def test_successful_scan_does_not_override_unverified_outcome(self):
        self.write([self.row("outcome-unverified", "one")])
        data = self.care.read(now=NOW)
        self.assertEqual(data["scan_status"], "ok")
        self.assertNotEqual(data["state"], "ok")
        self.assertEqual(data["counts"]["outcome-unverified"], 1)

    def test_torn_or_inconsistent_queue_cannot_claim_clear(self):
        self.write([self.row("answered", "one")])
        queue = self.root / self.care.QUEUE
        value = json.loads(queue.read_text())
        value["summary"]["answered"] = 2
        queue.write_text(json.dumps(value))
        self.assertEqual(self.care.read(now=NOW)["state"], "source-stale")
        queue.write_text("{broken")
        self.assertEqual(self.care.read(now=NOW)["state"], "source-stale")


if __name__ == "__main__":
    unittest.main()
