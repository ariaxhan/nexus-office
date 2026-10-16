"""Issue #300: faults are structured repair records, never text.

The acceptance demonstration, in-process: a fault the envelope can fix
walks detected -> repairing -> verified_fixed and emits nothing; a fault
that needs hands reaches state=escalated with one line and emits nothing
else; the 2026-10-15 Tower gate (the repair plan itself quarantined)
replays as a record only. "Nothing emitted" is checked at the two text
sinks that existed: the gh CLI (issues, comments) and the human_asks DB."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OFFICE_HUMAN_ASKS_DB",
                      os.path.join(tempfile.mkdtemp(prefix="faults-asks-"), "asks.sqlite"))

from nexus import faults  # noqa: E402
from nexus import selfrepair as sr  # noqa: E402
from nexus import tower  # noqa: E402
from nexus.ledger import Ledger, loads  # noqa: E402

FIELDS = {"fault_id", "signature", "detected_at", "source", "state",
          "repair", "verification", "escalation"}


class FaultCase(unittest.TestCase):
    def setUp(self):
        self.mode = mock.patch.dict(os.environ, {"NEXUS_FAULT_RECORDS_ONLY": "1"})
        self.mode.start()
        self.dir = tempfile.mkdtemp(prefix="nexus-faults-")
        self._cwd = os.getcwd()
        os.chdir(self.dir)
        self.led = Ledger(os.path.join(self.dir, "ledger.sqlite"))
        self.script = os.path.join(self.dir, "worker.py")
        with open(self.script, "w") as handle:
            handle.write("open('out.txt', 'w').write('ok')\n")
        self.asks_before = len(self.asks())
        self.gh = mock.patch("subprocess.run", wraps=subprocess.run)
        self.run = self.gh.start()

    def tearDown(self):
        self.gh.stop()
        self.led.close()
        os.chdir(self._cwd)
        self.mode.stop()

    def asks(self):
        return tower._human_asks().listing()["items"]

    def assert_silent(self):
        gh = [c for c in self.run.call_args_list
              if c.args and os.path.basename(str(c.args[0][0])) == "gh"]
        self.assertEqual([], gh, "a fault reached GitHub")
        self.assertEqual(self.asks_before, len(self.asks()), "a fault became an ask")
        self.assertEqual([], self.led.events(kind="plan.quarantine_raised"))

    def plan(self, cmd, name):
        return self.led.add_plan(
            name, schedule={"every": 1}, inputs={"cmd": cmd}, outputs=["out.txt"],
            budget={"timeout_s": 30, "max_retries": 1, "concurrency": 2})

    def states(self, fault_id):
        return faults.record(self.led, fault_id)["state"]

    def test_a_fixable_fault_walks_to_verified_fixed_in_silence(self):
        pid = self.plan(f"/nonexistent/python3.99 {self.script}", "synthetic")
        tid = sr.attach_quarantine(self.led, self.led.plan(pid), "dead path",
                                   signature=sr.SIGNATURE_DEAD_POINTER)
        walk = [self.states(tid)]
        self.led.set_task_state(tid, "accepted", decided_by="test", expect="candidate")
        sr.drain(self.led)
        walk.append(self.states(tid))
        proof_task = loads(self.led.events(kind="repair.awaiting_proof",
                                           subject=tid)[-1]["payload"])["proof_task_id"]
        flight = self.led.create_flight(pid, task_id=proof_task)
        self.led.set_state(flight, "running", expect="queued")
        self.led.set_state(flight, "produced", expect="running",
                           result={"ok": True, "artifacts": [{"kind": "file", "ref": "out.txt"}]})
        self.led.set_task_state(tid, "running", decided_by="test", expect="accepted")
        sr.verify(self.led)
        rec = faults.record(self.led, tid)
        walk.append(rec["state"])
        self.assertEqual(["detected", "repairing", "verified_fixed"], walk)
        self.assertEqual(FIELDS, set(rec))
        self.assertEqual("dead-pointer", rec["signature"])
        self.assertEqual("self-repair", rec["source"])
        self.assertIn(sys.executable, rec["repair"]["action"])
        self.assertEqual({"surface": f"flight {flight}", "result": "pass"},
                         {k: rec["verification"][k] for k in ("surface", "result")})
        self.assertIsNone(rec["escalation"])
        self.assertEqual([], faults.escalated(self.led))
        self.assert_silent()

    def test_a_fault_needing_hands_is_exactly_one_escalated_record(self):
        pid = self.plan(f"/nonexistent/zzq-tool-9 {self.script}", "needs-hands")
        tid = sr.attach_quarantine(self.led, self.led.plan(pid), "dead tool",
                                   signature=sr.SIGNATURE_DEAD_POINTER)
        self.led.set_task_state(tid, "accepted", decided_by="test", expect="candidate")
        self.led.set_task_state(tid, "running", decided_by="test", expect="accepted")
        for _ in range(sr.MAX_ATTEMPTS):
            self.assertEqual("repairing", self.states(tid))
            sr.drain(self.led, only_task_id=tid)
        sr.verify(self.led)
        tower._raise_quarantines(self.led)
        sr.verify(self.led)
        [rec] = faults.escalated(self.led)
        self.assertEqual(tid, rec["fault_id"])
        act = rec["escalation"]["act_needed"]
        self.assertEqual("technical", rec["escalation"]["kind"])
        self.assertIn("/nonexistent/zzq-tool-9", act)
        self.assertNotIn("\n", act)
        self.assertEqual(1, len(self.led.events(kind="repair.escalated")))
        self.assert_silent()

    def test_the_quarantined_mechanism_replays_as_a_record_only(self):
        """2026-10-15: the Tower gate quarantined the repair plan itself."""
        repair = sr.ensure_repair_plan(self.led)
        sr.attach_quarantine(self.led, repair, "5 consecutive failures")
        self.assertEqual(1, tower._raise_quarantines(self.led))
        self.assertEqual(0, tower._raise_quarantines(self.led))
        [rec] = faults.escalated(self.led)
        self.assertEqual(sr.mechanism_fault_id(self.led.plan(repair["id"])), rec["fault_id"])
        self.assertTrue(rec["signature"].startswith("mechanism-stopped:"))
        self.assertIn(sr.REPAIR_PLAN_NAME, rec["escalation"]["act_needed"])
        self.assert_silent()

    def test_legacy_plan_keyed_open_does_not_hide_mechanism_fault(self):
        repair = sr.ensure_repair_plan(self.led)
        old_task = self.led.add_task("old repair", "self-repair")
        self.led.event("repair.opened", repair["id"],
                       {"task_id": old_task, "signature": "old"}, "self-repair")
        new_task = self.led.add_task("mechanism repair", "self-repair")
        self.led.quarantine_plan(repair["id"], "broken", repair_task_id=new_task)
        self.assertEqual(1, tower._raise_quarantines(self.led))
        [rec] = [r for r in faults.escalated(self.led)
                 if r["fault_id"] == sr.mechanism_fault_id(self.led.plan(repair["id"]))]
        self.assertEqual("escalated", rec["state"])
        self.assert_silent()

    def test_shadow_keeps_legacy_comment_until_cutover(self):
        repair = sr.ensure_repair_plan(self.led)
        own = self.led.add_task("mechanism repair", "self-repair")
        self.led.quarantine_plan(repair["id"], "broken", repair_task_id=own)
        repair = self.led.plan(repair["id"])
        with mock.patch.dict(os.environ, {"NEXUS_FAULT_RECORDS_ONLY": "0"}), \
             mock.patch.object(sr, "_gh_comment", return_value="posted") as comment:
            sr.escalate_mechanism(self.led, repair, "injected stop", time.time())
        comment.assert_called_once()
        self.assertEqual(1, len(faults.escalated(self.led)))
        [receipt] = self.led.events(kind="repair.legacy_comment",
                                    subject=sr.mechanism_fault_id(repair))
        self.assertEqual("posted", json.loads(receipt["payload"])["gh"])

    def test_tower_acceptance_advances_detected_repairs_without_bypassing_policy(self):
        sr.ensure_repair_plan(self.led)
        pid = self.plan(f"{sys.executable} {self.script}", "stuck")
        tid = sr.attach_quarantine(self.led, self.led.plan(pid), "stuck")
        self.assertEqual("detected", self.states(tid))
        tower.accept_tasks(self.led)
        self.assertEqual("repairing", self.states(tid))
        self.assertEqual("running", self.led.task(tid)["state"])
        self.assertTrue(self.led.flights(task_id=tid))
        self.assertEqual(1, len(self.led.flights(task_id=tid)))

    def test_quarantined_repair_plan_refuses_repair_flight(self):
        repair = sr.ensure_repair_plan(self.led)
        pid = self.plan(f"{sys.executable} {self.script}", "stuck")
        tid = sr.attach_quarantine(self.led, self.led.plan(pid), "stuck")
        own = self.led.add_task("repairer repair", "self-repair")
        self.led.quarantine_plan(repair["id"], "broken", repair_task_id=own)
        tower.accept_tasks(self.led)
        self.assertEqual("rejected_policy", self.led.task(tid)["state"])
        self.assertEqual([], self.led.flights(task_id=tid))

    def test_a_watchdog_reports_records_not_issues(self):
        rec = faults.report(self.led, "launchd:office-sync exited", "watchdog:office-sync",
                            "detected", fault_id="wd-1")
        self.assertEqual("detected", rec["state"])
        faults.report(self.led, "launchd:office-sync exited", "watchdog:office-sync",
                      "repairing", fault_id="wd-1", action="kickstart office-sync")
        rec = faults.report(self.led, "launchd:office-sync exited", "watchdog:office-sync",
                            "verified_fixed", fault_id="wd-1",
                            surface="GET 127.0.0.1/health 200")
        self.assertEqual("verified_fixed", rec["state"])
        self.assertEqual("kickstart office-sync", rec["repair"]["action"])
        self.assertEqual(1, len(self.led.events(kind="repair.opened", subject="wd-1")))
        with self.assertRaises(ValueError):
            faults.report(self.led, "x", "watchdog:y", "escalated", fault_id="wd-2")
        self.assertEqual([], [r for r in faults.records(self.led) if r["fault_id"] == "wd-2"
                              and r["state"] == "escalated"])
        self.assert_silent()

    def test_watchdog_rechecks_dedupe_and_terminal_state_cannot_reopen(self):
        first = faults.report(self.led, "office-door-down", "watchdog:door", "detected")
        again = faults.report(self.led, "office-door-down", "watchdog:door", "detected")
        self.assertEqual(first["fault_id"], again["fault_id"])
        self.assertEqual(1, len(self.led.events(kind="repair.opened")))
        with self.assertRaises(ValueError):
            faults.report(self.led, "office-door-down", "watchdog:door",
                          "verified_fixed", fault_id=first["fault_id"], surface="unproven")
        faults.report(self.led, "office-door-down", "watchdog:door", "repairing",
                      fault_id=first["fault_id"], action="restart Office")
        faults.report(self.led, "office-door-down", "watchdog:door", "repairing",
                      fault_id=first["fault_id"], action="restart Office")
        self.assertEqual(1, len(self.led.events(kind="repair.fixed")))
        faults.report(self.led, "office-door-down", "watchdog:door", "verified_fixed",
                      fault_id=first["fault_id"], surface="Office /api/health")
        with self.assertRaises(ValueError):
            faults.report(self.led, "office-door-down", "watchdog:door", "escalated",
                          fault_id=first["fault_id"], act_needed="restart again")
        recurrence = faults.report(self.led, "office-door-down", "watchdog:door", "detected")
        self.assertNotEqual(first["fault_id"], recurrence["fault_id"])

    def test_the_cli_lists_records_as_data(self):
        faults.report(self.led, "sig", "watchdog:t", "repairing", fault_id="wd-3",
                      action="attempt verified repair")
        faults.report(self.led, "sig", "watchdog:t", "escalated", fault_id="wd-3",
                      act_needed="renew the Apple developer certificate", kind="identity")
        out = subprocess.run(
            [sys.executable, "-m", "nexus", "--ledger", self.led.path, "faults", "list",
             "--state", "escalated"],
            capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))))
        self.assertEqual(0, out.returncode, out.stderr)
        [rec] = json.loads(out.stdout)
        self.assertEqual({"act_needed": "renew the Apple developer certificate",
                          "kind": "identity"}, rec["escalation"])

    def test_office_watch_survives_missing_fault_ledger(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "client"))
        import office_faults
        with mock.patch.object(office_faults.run_board, "LEDGER",
                               os.path.join(self.dir, "not-installed.sqlite")):
            result = office_faults.snapshot()
        self.assertEqual([], result["items"])
        self.assertIn("OperationalError", result["error"])


if __name__ == "__main__":
    unittest.main()
