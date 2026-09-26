"""A Tower lane records no session pid: its teardown is proven only by a dead owner and no
live process carrying NEXUS_FLIGHT. Anything unprovable refuses settlement."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nexus import cli, flights, work
from nexus.ledger import Ledger

ISSUE = {"number": 183, "title": "Visible Tower work", "state": "open", "updated_at": "2026-09-25T00:00:00Z",
         "body": "", "labels": [{"name": "ready"}]}


class TowerLaneTeardown(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.led = Ledger(str(Path(self.dir.name) / "ledger.sqlite"))
        with patch.object(flights, "alive", return_value=True):
            work.capture(self.led, "ariaxhan/nexus-office", ISSUE)
            self.fid = work.claim(self.led, "ariaxhan/nexus-office", 183, 46378, runner=True)
        self.led.event("work.executing", self.fid, {"issue": 183, "lane": "tower-v2"}, "work")

    def tearDown(self):
        self.dir.cleanup()

    def stop(self, *, owner_alive=False, carriers=()):
        with patch.object(flights, "alive", return_value=owner_alive), \
             patch.object(flights, "flight_env_pids", return_value=carriers) as scan:
            return work.stop(self.led, self.led.flight(self.fid)), scan

    def test_dead_owner_and_no_carrier_confirms_teardown_and_settles(self):
        with patch.object(flights, "alive", return_value=False), \
             patch.object(flights, "flight_env_pids", return_value=[]):
            self.assertEqual(cli._cancel(self.led, self.led.flight(self.fid), self.fid), 0)
        self.assertEqual(self.led.flight(self.fid)["state"], "cancelled")
        self.assertTrue(self.led.events(kind="work.teardown", subject=self.fid))

    def test_live_carrier_refuses(self):
        self.assertFalse(self.stop(carriers=[4242])[0])

    def test_live_owner_refuses_without_scanning(self):
        stopped, scan = self.stop(owner_alive=True)
        self.assertFalse(stopped)
        scan.assert_not_called()

    def test_unprovable_process_table_refuses(self):
        self.assertFalse(self.stop(carriers=None)[0])

    def test_missing_owner_pid_refuses(self):
        self.led.conn.execute("UPDATE flights SET pid=NULL WHERE id=?", (self.fid,))
        self.assertFalse(self.stop()[0])

    def test_refused_cancel_leaves_flight_resolving_with_evidence(self):
        with patch.object(flights, "alive", return_value=False), \
             patch.object(flights, "flight_env_pids", return_value=[4242]):
            self.assertEqual(cli._cancel(self.led, self.led.flight(self.fid), self.fid), 1)
        self.assertEqual(self.led.flight(self.fid)["state"], "resolving")
        self.assertTrue(self.led.events(kind="work.executing", subject=self.fid))

    def test_recorded_session_path_is_unchanged(self):
        self.led.event("work.process", self.fid, {"pid": 777}, "work")
        with patch.object(flights, "alive", return_value=False), \
             patch.object(flights, "_kill_owned_session", return_value=True) as kill, \
             patch.object(flights, "flight_env_pids") as scan:
            self.assertTrue(work.stop(self.led, self.led.flight(self.fid)))
        kill.assert_called_once_with(777)
        scan.assert_not_called()


class FlightEnvScan(unittest.TestCase):
    def test_finds_a_real_process_by_exact_flight_id(self):
        import os, subprocess, sys, time
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                 env=dict(os.environ, NEXUS_FLIGHT="flt_scanprobe01"))
        try:
            time.sleep(0.5)
            self.assertIn(child.pid, flights.flight_env_pids("flt_scanprobe01"))
            self.assertEqual(flights.flight_env_pids("flt_scanprobe0"), [])
        finally:
            child.kill()
            child.wait()

    def test_ps_failure_is_unknown_not_empty(self):
        with patch.object(flights.subprocess, "run", side_effect=OSError("no ps")):
            self.assertIsNone(flights.flight_env_pids("flt_x"))


if __name__ == "__main__":
    unittest.main()
