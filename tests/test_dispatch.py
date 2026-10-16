"""Issue #301: dispatch records -- code polls, dispatches, verifies; the store refuses the rest."""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nexus import dispatch  # noqa: E402
from nexus.ledger import Ledger  # noqa: E402

T0 = 1_800_000_000.0
DONE = {"outcome": "done", "artifacts": ["file:/tmp/x"], "verification": "test -f /tmp/x",
        "evidence": ["exists"], "next": "none"}


class Proc:
    def __init__(self, rc):
        self.returncode, self.stdout, self.stderr = rc, "", ""


class DispatchCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nexus-dispatch-")
        self.led = Ledger(os.path.join(self.dir, "ledger.sqlite"))
        self.launched = []

    def tearDown(self):
        self.led.close()

    def launch(self, argv, env, log):
        self.launched.append((argv, env["NEXUS_DISPATCH_RECORD_ID"]))
        return 4242

    def request(self, queue="broker-studio"):
        return dispatch.file_control_request(self.led, queue, "lane x", "click Allow", "no shell route: TCC sheet",
                                             "the grant reads allowed", ["/usr/bin/true"], now=T0)

    def poll(self, now, workers=None, run=lambda *a, **k: Proc(0)):
        workers = {"broker-studio": {"argv": ["holder", "{record_id}"], "bound_s": 60}} if workers is None else workers
        return dispatch.poll(self.led, now=now, workers=workers, launch=self.launch, run=run,
                             stop=lambda record: True)

    def test_record_walks_to_closed_on_receipt_and_surface(self):
        rid = self.request()
        self.assertEqual(self.poll(T0 + 1)["dispatched"], 1)
        self.assertEqual(self.launched, [(["holder", rid], rid)])
        rec = dispatch.get(self.led, rid)
        self.assertEqual((rec["state"], rec["attempts"], rec["dispatched_to"]), ("claimed", 1, "broker-studio:pid=4242"))
        dispatch.submit_receipt(self.led, rid, DONE, now=T0 + 5)
        report = self.poll(T0 + 6)
        self.assertEqual((report["verified"], report["closed"]), (1, 1))
        rec = dispatch.get(self.led, rid)
        self.assertEqual(rec["state"], "closed")
        self.assertEqual(rec["verification"]["result"], "pass")
        walk = [__import__("json").loads(e["payload"])["to"] for e in self.led.events("dispatch.state", rid)]
        self.assertEqual(walk, ["filed", "claimed", "claimed", "receipt", "verified", "closed"])

    def test_starved_record_redispatches_on_age_alone_then_escalates(self):
        rid = self.request()
        self.poll(T0 + 1)
        self.assertEqual(self.poll(T0 + 30)["redispatched"], 0)  # inside its bound
        self.assertEqual(self.poll(T0 + 62)["redispatched"], 1)
        self.assertEqual(dispatch.get(self.led, rid)["attempts"], 2)
        self.poll(T0 + 123)
        self.assertEqual(dispatch.get(self.led, rid)["attempts"], 3)
        self.assertEqual(self.poll(T0 + 184)["escalated"], 1)
        rec = dispatch.get(self.led, rid)
        self.assertEqual(rec["state"], "escalated")
        self.assertIn("click Allow", rec["escalation"]["act_needed"])
        self.assertEqual(len(self.launched), 3)

    def test_failed_surface_read_refiles_and_redispatches(self):
        rid = self.request()
        self.poll(T0 + 1)
        dispatch.submit_receipt(self.led, rid, DONE, now=T0 + 2)
        self.assertEqual(self.poll(T0 + 3, run=lambda *a, **k: Proc(1))["refiled"], 1)
        rec = dispatch.get(self.led, rid)
        self.assertEqual((rec["state"], rec["receipt"]), ("filed", None))
        self.poll(T0 + 4)
        self.assertEqual(dispatch.get(self.led, rid)["attempts"], 2)

    def test_blocked_receipt_escalates_with_its_next(self):
        rid = self.request()
        self.poll(T0 + 1)
        dispatch.submit_receipt(self.led, rid, dict(DONE, outcome="blocked", next="aria: approve the payment"))
        self.poll(T0 + 3)
        rec = dispatch.get(self.led, rid)
        self.assertEqual(rec["state"], "escalated")
        self.assertEqual(rec["escalation"]["act_needed"], "aria: approve the payment")

    def test_no_worker_escalates_after_filed_bound(self):
        rid = self.request("broker-macbook")
        self.poll(T0 + 10)
        self.assertEqual(dispatch.get(self.led, rid)["state"], "filed")
        self.poll(T0 + dispatch.FILED_BOUND_S + 1)
        self.assertEqual(dispatch.get(self.led, rid)["escalation"]["kind"], "config")

    def test_store_refuses_state_without_receipt(self):
        rid = self.request()
        for sql in ("UPDATE dispatch_records SET state='receipt' WHERE record_id=?",
                    "UPDATE dispatch_records SET state='verified' WHERE record_id=?",
                    "UPDATE dispatch_records SET state='closed' WHERE record_id=?",
                    "DELETE FROM dispatch_records WHERE record_id=?"):
            with self.assertRaises(sqlite3.IntegrityError, msg=sql):
                self.led.conn.execute(sql, (rid,))
        self.poll(T0 + 1)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "without a receipt"):
            self.led.conn.execute("UPDATE dispatch_records SET state='receipt' WHERE record_id=?", (rid,))
        with self.assertRaisesRegex(sqlite3.IntegrityError, "born filed"):
            self.led.conn.execute("INSERT INTO dispatch_records (record_id,kind,queue,payload,state,created_at,state_at)"
                                  " VALUES ('x','k','q','{}','closed',0,0)")
        with self.assertRaises(ValueError):
            dispatch.submit_receipt(self.led, rid, {"outcome": "done"})  # grammar, not prose
        self.assertEqual(dispatch.get(self.led, rid)["state"], "claimed")

    def test_verified_needs_passing_surface(self):
        rid = self.request()
        self.poll(T0 + 1)
        dispatch.submit_receipt(self.led, rid, DONE)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "passing read"):
            self.led.conn.execute("UPDATE dispatch_records SET state='verified', verification='{\"result\":\"fail\"}'"
                                  " WHERE record_id=?", (rid,))
        with self.assertRaisesRegex(sqlite3.IntegrityError, "passing read"):
            self.led.conn.execute("UPDATE dispatch_records SET state='verified', verification='{}'"
                                  " WHERE record_id=?", (rid,))

    def test_existing_ledger_gets_null_safe_guard(self):
        rid = self.request()
        self.led.conn.execute("DROP TRIGGER dispatch_verification_required")
        self.led.conn.execute(dispatch.SCHEMA[6].replace(
            "coalesce(json_extract(NEW.verification, '$.result'), '')",
            "json_extract(NEW.verification, '$.result')"))
        self.poll(T0 + 1)  # ensure() upgrades a ledger created by the reviewed patch
        dispatch.submit_receipt(self.led, rid, DONE)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "passing read"):
            self.led.conn.execute("UPDATE dispatch_records SET state='verified', verification='{}'"
                                  " WHERE record_id=?", (rid,))

    def test_one_holder_per_queue_and_replacement_stops_first(self):
        first, second = self.request(), self.request()
        stopped = []
        workers = {"broker-studio": {"argv": ["holder"], "bound_s": 60}}
        self.poll(T0 + 1, workers=workers)
        self.assertEqual(dispatch.get(self.led, first)["state"], "claimed")
        self.assertEqual(dispatch.get(self.led, second)["state"], "filed")
        dispatch.poll(self.led, now=T0 + 62, workers=workers, launch=self.launch,
                      stop=lambda record: stopped.append(record["record_id"]) or True)
        self.assertEqual(stopped, [first])
        self.assertEqual(dispatch.get(self.led, first)["attempts"], 2)
        self.assertEqual(dispatch.get(self.led, second)["state"], "filed")
        dispatch.submit_receipt(self.led, first, DONE, now=T0 + 63)
        dispatch.poll(self.led, now=T0 + 64, workers=workers, launch=self.launch,
                      stop=lambda record: stopped.append(record["record_id"]) or True,
                      run=lambda *a, **k: Proc(0))
        self.poll(T0 + 65, workers=workers)
        self.assertEqual(dispatch.get(self.led, second)["state"], "claimed")

    def test_failed_stop_keeps_queue_owned(self):
        first, second = self.request(), self.request()
        self.poll(T0 + 1)
        if dispatch.get(self.led, second)["state"] == "claimed":
            first, second = second, first
        report = dispatch.poll(self.led, now=T0 + 62,
                               workers={"broker-studio": {"argv": ["holder"], "bound_s": 60}},
                               launch=self.launch, stop=lambda record: False)
        self.assertEqual(report["redispatched"], 0)
        self.assertEqual(dispatch.get(self.led, first)["attempts"], 1)
        self.assertEqual(dispatch.get(self.led, second)["state"], "filed")

    def test_pause_reconciles_receipt_without_launching(self):
        rid = self.request()
        self.assertEqual(self.poll(T0 + 1, workers={})["dispatched"], 0)
        self.assertEqual(dispatch.poll(self.led, now=T0 + 2, workers={"broker-studio": {"argv": ["holder"]}},
                                       launch=self.launch, paused=True)["dispatched"], 0)
        self.assertEqual(dispatch.get(self.led, rid)["state"], "filed")
        self.poll(T0 + 3)
        dispatch.submit_receipt(self.led, rid, DONE, now=T0 + 4)
        report = dispatch.poll(self.led, now=T0 + 5, workers={}, launch=self.launch,
                               stop=lambda record: True, run=lambda *a, **k: Proc(0), paused=True)
        self.assertEqual((report["closed"], dispatch.get(self.led, rid)["state"]), (1, "closed"))

    def test_worker_gets_selected_ledger(self):
        rid = self.request()
        seen = {}
        def launch(argv, env, log):
            seen.update(env=env, argv=argv)
            return 4242
        dispatch.poll(self.led, now=T0 + 1, workers={"broker-studio": {"argv": ["holder", "{prompt}"]}},
                      launch=launch)
        self.assertEqual(seen["env"]["NEXUS_LEDGER"], os.path.abspath(self.led.path))
        self.assertIn("--ledger", seen["argv"][1])
        self.assertIn(rid, seen["argv"][1])

    def test_filing_requires_a_surface_command(self):
        with self.assertRaises(dispatch.DispatchError):
            dispatch.file_control_request(self.led, "broker-studio", "r", "a", "s", "surf", [])
        with self.assertRaises(dispatch.DispatchError):
            dispatch.file_control_request(self.led, "issue-263", "r", "a", "s", "surf", ["true"])

    def test_default_workers_follow_the_device(self):
        self.assertEqual(set(dispatch.default_workers("arias-Mac-Studio")), {"broker-studio"})
        self.assertEqual(set(dispatch.default_workers("arias-MacBook-Pro-2")), {"broker-macbook"})
        self.assertEqual(dispatch.default_workers(None), {})

    def test_cli_end_to_end_with_a_real_worker(self):
        """Acceptance 1: no chat, no reader -- a real subprocess worker files the receipt; code verifies."""
        mark = os.path.join(self.dir, "act-done")
        worker = os.path.join(self.dir, "worker.sh")
        root = dispatch.ROOT
        with open(worker, "w") as fh:
            fh.write(f"#!/bin/sh\ntouch {mark}\ncd {root} && {sys.executable} -m nexus --ledger {self.led.path} "
                     f"dispatch receipt \"$NEXUS_DISPATCH_RECORD_ID\" --outcome done --artifact file:{mark} "
                     f"--verification 'test -f {mark}' --evidence 'mark exists'\n")
        os.chmod(worker, 0o755)
        rid = dispatch.file_control_request(self.led, "broker-studio", "probe", "touch the mark", "demo", "mark exists",
                                            ["test", "-f", mark])

        def launch(argv, env, log):
            subprocess.run(argv, env=env, capture_output=True, timeout=60, check=True)
            return 1

        dispatch.poll(self.led, workers={"broker-studio": {"argv": [worker]}}, launch=launch)
        self.assertEqual(dispatch.get(self.led, rid)["state"], "receipt")
        dispatch.poll(self.led, workers={"broker-studio": {"argv": [worker]}}, launch=launch)
        self.assertEqual(dispatch.get(self.led, rid)["state"], "closed")


if __name__ == "__main__":
    unittest.main()
