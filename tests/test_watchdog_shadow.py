"""#304 door source: receipt readback, fault injection, and duplicate guard."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from nexus import faults, watchdog_shadow
from nexus.ledger import Ledger


class DoorShadowCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(os.path.join(self.tmp.name, "ledger.sqlite"))
        self.now = datetime(2026, 10, 16, 22, 9, tzinfo=timezone.utc)
        self.path = Path(self.tmp.name, "2026-10-16T22-00Z.json")

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def receipt(self, loop=True, phone=True, phone_revision="abc", classification=None):
        effect = {"loopback": {"result": {"ok": loop, "revision": "abc"}},
                  "phone": {"result": {"ok": phone, "revision": phone_revision}},
                  "classification": classification or ("door_loopback_failed" if not loop else
                    "phone_path_failed" if not phone else
                    "revision_mismatch" if phone_revision != "abc" else "healthy"),
                  "repair_attempted": False, "muse_owner_retained": True}
        digest = hashlib.sha256(json.dumps(effect, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        item = {"schema": "office-door-shadow/v1", "occurrence_id": "office-door/2026-10-16T22:00Z",
                "observed_at": "2026-10-16T22:08:00+00:00", "effect": effect,
                "effect_sha256": digest}
        self.path.write_text(json.dumps(item))
        return item

    def test_healthy_readback_has_observation_and_no_fault(self):
        self.receipt()
        out = watchdog_shadow.ingest_door(self.ledger, self.path, now=self.now)
        self.assertEqual("healthy", out["classification"])
        self.assertIsNone(out["fault_id"])
        self.assertEqual(1, len(self.ledger.events(kind="watchdog.observed")))
        self.assertEqual([], faults.records(self.ledger))
        self.assertTrue(watchdog_shadow.ingest_door(self.ledger, self.path, now=self.now)["duplicate"])
        self.assertEqual(1, len(self.ledger.events(kind="watchdog.observed")))

    def test_seeded_phone_failure_opens_one_detected_fault_without_repair(self):
        self.receipt(phone=False)
        out = watchdog_shadow.ingest_door(self.ledger, self.path, now=self.now)
        rec = faults.record(self.ledger, out["fault_id"])
        self.assertEqual("phone_path_failed", rec["signature"])
        self.assertEqual("detected", rec["state"])
        self.assertIsNone(rec["repair"])
        self.assertIsNone(rec["escalation"])
        self.assertEqual(1, len(self.ledger.events(kind="repair.opened")))
        watchdog_shadow.ingest_door(self.ledger, self.path, now=self.now)
        self.assertEqual(1, len(self.ledger.events(kind="repair.opened")))

    def test_bad_effect_and_rewritten_occurrence_refused_without_ledger_write(self):
        item = self.receipt(phone=False)
        item["effect"]["classification"] = "healthy"
        self.path.write_text(json.dumps(item))
        with self.assertRaises(ValueError):
            watchdog_shadow.ingest_door(self.ledger, self.path, now=self.now)
        self.assertEqual([], self.ledger.events(kind="watchdog.observed"))
        self.receipt(phone=False)
        watchdog_shadow.ingest_door(self.ledger, self.path, now=self.now)
        self.receipt(loop=False)
        with self.assertRaises(ValueError):
            watchdog_shadow.ingest_door(self.ledger, self.path, now=self.now)

    def test_late_receipt_retained_without_fault_state(self):
        self.receipt()
        later = datetime(2026, 10, 17, 1, tzinfo=timezone.utc)
        out = watchdog_shadow.ingest_door(self.ledger, self.path, now=later)
        self.assertIsNone(out["fault_id"])
        payload = json.loads(self.ledger.events(kind="watchdog.observed")[0]["payload"])
        self.assertTrue(payload["late"])
        self.assertEqual([], faults.records(self.ledger))
        self.receipt(phone=False)
        other = Path(self.tmp.name, "2026-10-16T22-30Z.json")
        item = json.loads(self.path.read_text())
        item["occurrence_id"] = "office-door/2026-10-16T22:30Z"
        item["observed_at"] = "2026-10-16T22:38:00+00:00"
        other.write_text(json.dumps(item))
        watchdog_shadow.ingest_door(self.ledger, other, now=later)
        self.assertEqual([], faults.records(self.ledger))

    def test_missing_revision_and_malformed_receipt_refused(self):
        item = self.receipt()
        del item["effect"]["phone"]["result"]["revision"]
        item["effect_sha256"] = hashlib.sha256(json.dumps(item["effect"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.path.write_text(json.dumps(item))
        with self.assertRaises(ValueError):
            watchdog_shadow.ingest_door(self.ledger, self.path, now=self.now)
        self.path.write_text("[]")
        with self.assertRaises(ValueError):
            watchdog_shadow.ingest_door(self.ledger, self.path, now=self.now)

    def test_healthy_following_failure_marks_natural_recovery(self):
        self.receipt(phone=False)
        fault_id = watchdog_shadow.ingest_door(self.ledger, self.path, now=self.now)["fault_id"]
        item = self.receipt()
        item["occurrence_id"] = "office-door/2026-10-16T22:30Z"
        item["observed_at"] = "2026-10-16T22:38:00+00:00"
        second = Path(self.tmp.name, "2026-10-16T22-30Z.json")
        second.write_text(json.dumps(item))
        now = datetime(2026, 10, 16, 22, 39, tzinfo=timezone.utc)
        watchdog_shadow.ingest_door(self.ledger, second, now=now)
        rec = faults.record(self.ledger, fault_id)
        self.assertEqual("recovered", rec["state"])
        self.assertIsNone(rec["repair"])
        self.assertEqual("observed recovered", rec["verification"]["result"])
        self.assertEqual(1, len(self.ledger.events(kind="watchdog.recovered")))
        third = Path(self.tmp.name, "2026-10-16T23-00Z.json")
        failure = self.receipt(phone=False)
        failure["occurrence_id"] = "office-door/2026-10-16T23:00Z"
        failure["observed_at"] = "2026-10-16T23:08:00+00:00"
        third.write_text(json.dumps(failure))
        newest = watchdog_shadow.ingest_door(
            self.ledger, third, now=datetime(2026, 10, 16, 23, 9, tzinfo=timezone.utc))
        self.assertNotEqual(fault_id, newest["fault_id"])

    def test_older_failure_after_newer_healthy_does_not_reopen(self):
        healthy = self.receipt()
        healthy["occurrence_id"] = "office-door/2026-10-16T22:30Z"
        healthy["observed_at"] = "2026-10-16T22:38:00+00:00"
        newer = Path(self.tmp.name, "2026-10-16T22-30Z.json")
        newer.write_text(json.dumps(healthy))
        watchdog_shadow.ingest_door(
            self.ledger, newer, now=datetime(2026, 10, 16, 22, 39, tzinfo=timezone.utc))
        self.receipt(phone=False)
        watchdog_shadow.ingest_door(self.ledger, self.path,
                                    now=datetime(2026, 10, 16, 22, 39, tzinfo=timezone.utc))
        self.assertEqual([], faults.records(self.ledger))
