"""Issue #273, shape 1: the surface guard refuses broken-state text that
carries no live repair. Nothing here touches a real surface: the ledger is
a temp file and assert_sendable only reads it."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nexus import surface_guard as guard  # noqa: E402
from nexus.ledger import Ledger  # noqa: E402

BROKEN = "Execution still impaired: the coding runner is stopped."
BROKEN2 = "Tower stopped plan `code-work`: 5 consecutive failures."
CLEAN = "Morning briefing sent. Three items, all delivered."
NARRATIVE = "The streaming service went down on Tuesday, the company said."


class SurfaceGuardCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nexus-guard-")
        self.led = Ledger(os.path.join(self.dir, "ledger.sqlite"))

    def tearDown(self):
        self.led.close()

    def test_broken_state_is_detected(self):
        self.assertTrue(guard.broken_state_hits(BROKEN))
        self.assertTrue(guard.broken_state_hits(BROKEN2))

    def test_clean_and_narrative_text_passes(self):
        self.assertEqual([], guard.broken_state_hits(CLEAN))
        self.assertEqual([], guard.broken_state_hits(NARRATIVE))
        self.assertTrue(guard.assert_sendable(CLEAN))
        self.assertTrue(guard.assert_sendable(NARRATIVE))

    def test_broken_without_reference_is_blocked(self):
        with self.assertRaises(guard.SurfaceBlocked):
            guard.assert_sendable(BROKEN)
        with self.assertRaises(guard.SurfaceBlocked):
            guard.assert_sendable(BROKEN, repair_ref={"issue": "ariaxhan/nexus-office#273"})

    def test_broken_with_live_repair_passes(self):
        tid = self.led.add_task("Repair code-work: dead-pointer", "self-repair")
        ref = {"issue": "ariaxhan/nexus-office#273", "task_id": tid}
        self.assertTrue(guard.assert_sendable(
            BROKEN, repair_ref=ref, ledger_path=self.led.path))

    def test_broken_with_finished_repair_is_blocked(self):
        tid = self.led.add_task("Repair code-work: dead-pointer", "self-repair")
        self.led.set_task_state(tid, "abandoned", decided_by="test",
                                expect="candidate")
        ref = {"issue": "ariaxhan/nexus-office#273", "task_id": tid}
        with self.assertRaises(guard.SurfaceBlocked):
            guard.assert_sendable(BROKEN, repair_ref=ref,
                                  ledger_path=self.led.path)

    def test_tier3_escalation_shape_passes(self):
        tid = self.led.add_task("Repair code-work: dead-pointer", "self-repair")
        self.led.set_task_state(tid, "abandoned", decided_by="test",
                                expect="candidate")
        ref = {"issue": "ariaxhan/nexus-office#273", "task_id": tid,
               "escalated": True, "attempts": 3,
               "single_act": "restore the dead path"}
        self.assertTrue(guard.assert_sendable(
            BROKEN, repair_ref=ref, ledger_path=self.led.path))


if __name__ == "__main__":
    unittest.main()
