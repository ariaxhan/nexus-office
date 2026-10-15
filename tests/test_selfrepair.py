"""Issue #273: the self-repair loop, in-process.

Shape 2 (a quarantine record requires its repair, at the API and at the
store), shape 3's pointer analysis, and the repair cycle itself: correct
-> unquarantine -> proof re-run -> the worker's own receipt closes the
repair. The subprocess-level proof (real flights) is the acceptance demo
recorded on the issue; this suite pins the logic."""

import os
import sqlite3
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nexus import selfrepair as sr  # noqa: E402
from nexus import tower  # noqa: E402
from nexus.ledger import Ledger, LedgerError  # noqa: E402


class SelfRepairCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nexus-repair-")
        self.root = os.path.join(self.dir, "flights")
        self._cwd = os.getcwd()
        os.chdir(self.dir)  # the drain writes its report into its workspace
        self.led = Ledger(os.path.join(self.dir, "ledger.sqlite"))
        self.script = os.path.join(self.dir, "worker.py")
        with open(self.script, "w") as handle:
            handle.write("open('out.txt', 'w').write('ok')\n")

    def tearDown(self):
        self.led.close()
        os.chdir(self._cwd)

    def plan(self, cmd, name="worker"):
        return self.led.add_plan(
            name, schedule={"every": 1}, inputs={"cmd": cmd},
            outputs=["out.txt"],
            budget={"timeout_s": 30, "max_retries": 1, "concurrency": 2})

    # ---- shape 2: the parked state is unrepresentable --------------------

    def test_bare_quarantine_refused_by_the_api(self):
        pid = self.plan(f"{sys.executable} {self.script}")
        with self.assertRaises(LedgerError):
            self.led.quarantine_plan(pid, "no repair named")

    def test_bare_quarantine_refused_by_the_store(self):
        pid = self.plan(f"{sys.executable} {self.script}")
        with self.assertRaises(sqlite3.IntegrityError):
            self.led.conn.execute(
                "UPDATE plans SET quarantined_at=? WHERE id=?",
                (time.time(), pid))

    def test_quarantine_with_a_live_repair(self):
        pid = self.plan(f"{sys.executable} {self.script}")
        tid = self.led.add_task("Repair worker: test", "self-repair")
        self.led.quarantine_plan(pid, "boom", repair_task_id=tid)
        row = self.led.plan(pid)
        self.assertIsNotNone(row["quarantined_at"])
        self.assertEqual(tid, row["repair_task_id"])

    def test_quarantine_with_a_finished_repair_refused(self):
        pid = self.plan(f"{sys.executable} {self.script}")
        tid = self.led.add_task("Repair worker: test", "self-repair")
        self.led.set_task_state(tid, "abandoned", decided_by="test",
                                expect="candidate")
        with self.assertRaises(LedgerError):
            self.led.quarantine_plan(pid, "boom", repair_task_id=tid)

    # ---- shape 3: pointer analysis ----------------------------------------

    def test_dead_pointers(self):
        live = f"{sys.executable} {self.script}"
        self.assertEqual([], sr.dead_pointers(live))
        dead = f"/nonexistent/python3.99 {self.script}"
        self.assertEqual(["/nonexistent/python3.99"], sr.dead_pointers(dead))
        # the script pointer behind a shell is judged
        shell = "/bin/bash /nonexistent/probe.sh"
        self.assertEqual(["/nonexistent/probe.sh"], sr.dead_pointers(shell))
        wrapped = "/usr/bin/env -i HOME=/Users/x /usr/bin/python3 -I " \
                  "/nonexistent/tool.py"
        self.assertEqual(["/nonexistent/tool.py"], sr.dead_pointers(wrapped))
        # data positions are never judged: an output path the command is
        # about to create, an env value, an inline -c program
        out = f"{sys.executable} {self.script} /nonexistent/out.txt"
        self.assertEqual([], sr.dead_pointers(out))
        env = f"PYTHONPATH=/nonexistent/dir {sys.executable} -V"
        self.assertEqual([], sr.dead_pointers(env))
        inline = "timeout 900 /bin/sh -c 'echo $$ > /nonexistent/pid; sleep 900'"
        self.assertEqual([], sr.dead_pointers(inline))
        self.assertEqual([], sr.dead_pointers("echo $PWD/out.txt"))
        self.assertEqual([], sr.dead_pointers(""))

    def test_verified_replacement_for_a_python_path(self):
        self.assertEqual(sys.executable,
                         sr.verified_replacement("/nonexistent/python3.99"))
        self.assertIsNone(sr.verified_replacement("/nonexistent/zzq-tool-9"))

    # ---- the loop ----------------------------------------------------------

    def test_attach_quarantine_is_one_transition(self):
        pid = self.plan(f"{sys.executable} {self.script}")
        tid = sr.attach_quarantine(self.led, self.led.plan(pid), "test stop",
                                   signature="dead-pointer")
        row = self.led.plan(pid)
        self.assertIsNotNone(row["quarantined_at"])
        self.assertEqual(tid, row["repair_task_id"])
        task = self.led.task(tid)
        self.assertEqual("self-repair", task["origin"])
        self.assertIn("dead-pointer", task["dedupe_key"])

    def test_drain_corrects_proves_and_closes(self):
        pid = self.plan(f"/nonexistent/python3.99 {self.script}")
        tid = sr.attach_quarantine(self.led, self.led.plan(pid), "dead path",
                                   signature=sr.SIGNATURE_DEAD_POINTER)
        self.led.set_task_state(tid, "accepted", decided_by="test",
                                expect="candidate")
        report = sr.drain(self.led)
        self.assertEqual(1, len(report["fixed"]), report)
        plan = self.led.plan(pid)
        self.assertTrue(plan["inputs"].find(sys.executable) >= 0)
        self.assertIsNone(plan["quarantined_at"])  # released for its proof
        awaiting = self.led.events(kind="repair.awaiting_proof", subject=tid)
        self.assertEqual(1, len(awaiting))
        # the proof is the worker's own run: simulate its produced receipt
        from nexus.ledger import loads
        proof_task = loads(awaiting[-1]["payload"], {})["proof_task_id"]
        flight = self.led.create_flight(pid, task_id=proof_task)
        self.led.set_state(flight, "running", expect="queued")
        self.led.set_state(flight, "produced", expect="running",
                           result={"ok": True,
                                   "artifacts": [{"kind": "file", "ref": "out.txt"}]})
        self.led.set_task_state(tid, "running", decided_by="test",
                                expect="accepted")
        out = sr.verify(self.led)
        self.assertEqual(1, out["completed"], out)
        self.assertEqual("done", self.led.task(tid)["state"])
        self.assertEqual(1, len(self.led.events(kind="repair.completed",
                                                subject=tid)))

    def test_proof_without_declared_outputs_is_a_failed_proof(self):
        pid = self.plan(f"/nonexistent/python3.99 {self.script}")
        tid = sr.attach_quarantine(self.led, self.led.plan(pid), "dead path",
                                   signature=sr.SIGNATURE_DEAD_POINTER)
        self.led.set_task_state(tid, "accepted", decided_by="test",
                                expect="candidate")
        report = sr.drain(self.led)
        self.assertEqual(1, len(report["fixed"]), report)
        from nexus.ledger import loads
        awaiting = self.led.events(kind="repair.awaiting_proof", subject=tid)
        proof_task = loads(awaiting[-1]["payload"], {})["proof_task_id"]
        flight = self.led.create_flight(pid, task_id=proof_task)
        self.led.set_state(flight, "running", expect="queued")
        # the proof ran and exited fine but produced nothing it declared:
        # the live 2026-10-15 case, {"ok": true, "artifacts": []}
        self.led.set_state(flight, "produced", expect="running",
                           result={"ok": True, "artifacts": []})
        self.led.set_task_state(tid, "running", decided_by="test",
                                expect="accepted")
        out = sr.verify(self.led)  # the guard's refusal must not escape
        self.assertEqual(1, out["proofs_failed"], out)
        self.assertEqual(0, out["completed"], out)
        self.assertEqual("running", self.led.task(tid)["state"])
        self.assertEqual(1, sr.attempts(self.led, tid))
        failed = self.led.events(kind="repair.attempt_failed", subject=tid)
        self.assertIn("no declared output",
                      loads(failed[-1]["payload"], {})["why"])
        # the quarantine-born plan is stopped again, still owned by the repair
        plan = self.led.plan(pid)
        self.assertIsNotNone(plan["quarantined_at"])
        self.assertEqual(tid, plan["repair_task_id"])
        # the ladder moves: the next pass re-flies the repair -- and the
        # mechanism's own unquarantine-for-proof is NOT a person's release,
        # so nothing stands the repair down
        out = sr.verify(self.led)
        self.assertEqual(1, out["retried"], out)
        self.assertEqual([], self.led.events(kind="repair.stood_down",
                                             subject=tid))
        self.assertEqual("running", self.led.task(tid)["state"])

    def test_a_persons_release_still_stands_a_repair_down(self):
        pid = self.plan(f"{sys.executable} {self.script}")
        tid = sr.attach_quarantine(self.led, self.led.plan(pid), "test stop",
                                   signature="dead-pointer")
        self.led.set_task_state(tid, "accepted", decided_by="test",
                                expect="candidate")
        self.led.set_task_state(tid, "running", decided_by="test",
                                expect="accepted")
        self.led.unquarantine_plan(pid)  # `nexus plans release`
        out = sr.verify(self.led)
        self.assertEqual(1, out.get("stood_down"), out)
        self.assertEqual("done", self.led.task(tid)["state"])
        self.assertEqual(1, len(self.led.events(kind="repair.stood_down",
                                                subject=tid)))

    def test_a_verify_exception_never_blocks_the_tick(self):
        pid = self.plan(f"{sys.executable} {self.script}")
        plan = self.led.plan(pid)
        task = self.led.add_scheduled_task(plan, "tick-test:1", time.time())
        flight = self.led.create_flight(pid, task_id=task)
        real_verify = sr.verify

        def boom(ledger, now=None):
            raise RuntimeError("injected verify failure")

        sr.verify = boom
        try:
            report = tower.tick(self.led, now=time.time(), root=self.root)
        finally:
            sr.verify = real_verify
        # the tick reached launch, and the failure is loud in the ledger
        self.assertGreaterEqual(report["launched"], 1, report)
        self.assertIn("injected verify failure", str(report["repair"]))
        self.assertEqual("running", self.led.flight(flight)["state"])
        errors = self.led.events(kind="tower.tick_error")
        self.assertTrue(any("injected verify failure" in str(e["payload"])
                            for e in errors), errors)

    def test_drain_without_a_replacement_records_the_attempt(self):
        pid = self.plan(f"/nonexistent/zzq-tool-9 {self.script}", name="odd")
        tid = sr.attach_quarantine(self.led, self.led.plan(pid), "dead tool",
                                   signature=sr.SIGNATURE_DEAD_POINTER)
        self.led.set_task_state(tid, "accepted", decided_by="test",
                                expect="candidate")
        report = sr.drain(self.led)
        self.assertEqual(1, len(report["failed"]), report)
        self.assertEqual(1, sr.attempts(self.led, tid))
        self.assertIsNotNone(self.led.plan(pid)["quarantined_at"])

    def test_launch_refuses_a_dead_pointer_into_repair(self):
        pid = self.plan(f"/nonexistent/python3.99 {self.script}")
        plan = self.led.plan(pid)
        task = self.led.add_scheduled_task(plan, "launch-test:1", time.time())
        flight = self.led.create_flight(pid, task_id=task)
        launched = tower._launch(self.led, time.time(), self.root)
        self.assertEqual(0, launched)
        row = self.led.flight(flight)
        self.assertEqual("failed", row["state"])
        from nexus.ledger import loads
        error = (loads(row["result"], {}) or {}).get("error") or {}
        self.assertEqual("dead_pointer", error.get("code"))
        plan = self.led.plan(pid)
        self.assertIsNotNone(plan["quarantined_at"])
        self.assertIsNotNone(plan["repair_task_id"])

    def test_triggers_open_a_repair_on_one_signature(self):
        pid = self.plan(f"{sys.executable} {self.script}", name="flaky")
        for i in range(3):
            flight = self.led.create_flight(pid)
            self.led.set_state(flight, "running", expect="queued")
            self.led.fail(flight, "exit_nonzero", "exit 1", expect="running")
        self.assertEqual(1, sr.triggers(self.led, time.time()))
        self.assertEqual(0, sr.triggers(self.led, time.time()))  # deduped
        self.assertIsNone(self.led.plan(pid)["quarantined_at"])

    def test_status_names_the_repair(self):
        pid = self.plan(f"{sys.executable} {self.script}", name="code-work")
        tid = sr.attach_quarantine(self.led, self.led.plan(pid), "test stop",
                                   signature="dead-pointer")
        text = tower.status(self.led)
        self.assertTrue(text.startswith(f"REPAIRING  code-work since "), text[:120])
        self.assertIn(tid, text.splitlines()[0])
        self.assertNotIn("QUARANTINED", text)


if __name__ == "__main__":
    unittest.main()
