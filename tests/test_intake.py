"""Issue #289: the intake state machine, read off the ledger, faulting on the mechanism."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nexus import intake, work  # noqa: E402
from nexus.ledger import Ledger, new_id  # noqa: E402

H = 3600
T0 = intake.SINCE + 10 * H


def issue(number, labels=("ready",), body="", state="open", title="probe"):
    return {"number": number, "title": title, "state": state, "body": body,
            "created_at": intake._iso(T0), "updated_at": intake._iso(T0),
            "labels": [{"name": name} for name in labels]}


class IntakeCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nexus-intake-")
        self.led = Ledger(os.path.join(self.dir, "ledger.sqlite"))
        self.filed = []

    def tearDown(self):
        self.led.close()

    def gh(self, args):
        self.filed.append(args)
        return f"https://github.com/o/r/issues/{900 + len(self.filed)}"

    def capture(self, number, at=T0, **kw):
        tid = work.capture(self.led, "o/r", issue(number, **kw))
        self.led.conn.execute("UPDATE tasks SET created_at=? WHERE id=?", (at, tid))
        return tid

    def at(self, kind, subject, ts, payload=None):
        self.led.event(kind, subject, payload or {}, "test", ts)

    def claim(self, tid, ts):
        pid = work.plan(self.led)
        self.led.conn.execute("INSERT INTO flights(id,task_id,plan_id,state,created_at) VALUES (?,?,?,?,?)",
                              (new_id("flt"), tid, pid, "running", ts))

    def walk(self, tid):
        return intake.item(self.led, self.led.task(tid))

    def sweep(self, now, observe=True):
        if observe:  # discovery still sees every open item
            for task in self.led.tasks():
                self.at("work.observed", task["id"], now)
        return intake.sweep(self.led, now, gh=self.gh, force=True)

    # ---- the walk ----------------------------------------------------------

    def test_full_lifecycle_reads_every_state_from_existing_records(self):
        tid = self.capture(1)
        self.assertEqual(self.walk(tid)["state"], "FILED")
        self.claim(tid, T0 + H)
        self.assertEqual(self.walk(tid)["state"], "CLAIMED")
        self.at("work.receipt", tid, T0 + 2 * H)
        self.assertEqual(self.walk(tid)["state"], "RECEIPT")
        self.at("lifecycle.verified", "github:o/r#1", T0 + 2.1 * H, {"task_id": tid})
        self.assertEqual(self.walk(tid)["state"], "VERIFIED")
        self.at("work.closed", tid, T0 + 2.1 * H)
        self.assertEqual(self.walk(tid)["state"], "CLOSED")
        report = self.sweep(T0 + 3 * H)
        self.assertEqual(report["faults"], [])
        states = [e["payload"] for e in self.led.events(kind="intake.state")]
        self.assertEqual(len(states), 1)  # first sweep sees it closed

    def test_transitions_are_recorded_once_per_change(self):
        tid = self.capture(2)
        self.sweep(T0 + 60)
        self.sweep(T0 + 120)
        self.claim(tid, T0 + 180)
        self.sweep(T0 + 240)
        rows = [e["payload"] for e in self.led.events(kind="intake.state")]
        self.assertEqual(len(rows), 2)
        self.assertIn('"CLAIMED"', rows[1])

    # ---- faults ------------------------------------------------------------

    def test_unclaimed_queue_item_past_two_hours_files_one_fault_on_its_repo(self):
        self.capture(3)
        self.assertEqual(self.sweep(T0 + 1.9 * H)["faults"], [])
        report = self.sweep(T0 + 2.5 * H)
        self.assertEqual(len(report["faults"]), 1)
        args = self.filed[0]
        self.assertEqual(args[:4], ["issue", "create", "-R", "o/r"])
        title, body = args[5], args[7]
        self.assertTrue(title.startswith("[intake-fault] o/r#3 sat in FILED"))
        self.assertIn("overrun 30m", title)
        self.assertIn("state: **FILED**", body)
        self.sweep(T0 + 5 * H)  # dedupe by item and state
        self.assertEqual(len(self.filed), 1)

    def test_late_claim_still_faults_the_overrun(self):
        tid = self.capture(4)
        self.claim(tid, T0 + 3 * H)
        self.sweep(T0 + 3.1 * H)
        self.assertEqual(len(self.filed), 1)
        self.assertIn("left FILED: ", self.filed[0][7])

    def test_non_queue_item_has_no_filed_bound(self):
        self.capture(5, labels=())
        self.assertEqual(self.sweep(T0 + 30 * H)["faults"], [])

    def test_claimed_bound_defaults_to_24h_and_the_commission_may_set_its_own(self):
        a = self.capture(6)
        b = self.capture(7, body="Do it.\n\nLane bound: 4 h\n")
        self.claim(a, T0 + 60)
        self.claim(b, T0 + 60)
        self.sweep(T0 + 5 * H)
        self.assertEqual([args[5].split()[1] for args in self.filed], ["o/r#7"])
        self.sweep(T0 + 25 * H)
        self.assertEqual(sorted(args[5].split()[1] for args in self.filed), ["o/r#6", "o/r#7"])

    def test_receipt_without_verification_for_an_hour_faults(self):
        tid = self.capture(8)
        self.claim(tid, T0 + 60)
        self.at("work.receipt", tid, T0 + 120)
        self.sweep(T0 + 120 + 50 * 60)
        self.assertEqual(self.filed, [])
        self.sweep(T0 + 120 + 70 * 60)
        self.assertIn("sat in RECEIPT", self.filed[0][5])

    def test_fault_issues_and_pre_activation_items_are_never_judged(self):
        self.capture(9, title="[intake-fault] o/r#3 sat in FILED")
        self.capture(10, at=intake.SINCE - H)
        self.assertEqual(self.sweep(T0 + 50 * H)["items"], 0)

    def test_an_item_discovery_stopped_seeing_is_not_judged(self):
        self.capture(12)
        self.sweep(T0 + 60)
        self.assertEqual(self.sweep(T0 + 30 * H, observe=False)["items"], 0)

    def test_filing_failure_keeps_the_claim_and_retries(self):
        self.capture(11)

        def broken(args):
            raise RuntimeError("gh down")
        self.at("work.observed", self.led.tasks()[0]["id"], T0 + 3 * H)
        report = intake.sweep(self.led, T0 + 3 * H, gh=broken, force=True)
        self.assertEqual(len(report["errors"]), 1)
        self.sweep(T0 + 3.1 * H)
        self.assertEqual(len(self.filed), 1)
        self.assertEqual(len(self.led.events(kind="intake.fault")), 1)

    def test_lane_bound_grammar(self):
        self.assertEqual(intake.lane_bound(""), 24 * H)
        self.assertEqual(intake.lane_bound("**Lane bound:** 36h"), 36 * H)
        self.assertEqual(intake.lane_bound("lane bound: 2 days"), 2 * 86400)
        self.assertEqual(intake.lane_bound("lane bound: 90 min"), 90 * 60)


if __name__ == "__main__":
    unittest.main()
