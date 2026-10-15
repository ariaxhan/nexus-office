"""Issue #289: the intake state machine, read off the ledger, faulting on the mechanism."""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

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
        with mock.patch("time.time", return_value=at):  # capture's own clock: task and revision at `at`
            return work.capture(self.led, "o/r", issue(number, **kw))

    def relabel(self, tid, number, labels, ts):
        self.at("work.issue", tid, ts, issue(number, labels=labels))

    def at(self, kind, subject, ts, payload=None):
        self.led.event(kind, subject, payload or {}, "test", ts)

    def claim(self, tid, ts):
        pid = work.plan(self.led)
        self.led.conn.execute("INSERT INTO flights(id,task_id,plan_id,state,created_at) VALUES (?,?,?,?,?)",
                              (new_id("flt"), tid, pid, "running", ts))

    def walk(self, tid):
        return intake.item(self.led, self.led.task(tid))

    def sweep(self, now, observe=True, claims=None):
        if observe:  # discovery still sees every open item
            for task in self.led.tasks():
                self.at("work.observed", task["id"], now)
        return intake.sweep(self.led, now, gh=self.gh, force=True, claims=claims or {})

    def recorded(self):
        return [json.loads(e["payload"])["state"] for e in self.led.events(kind="intake.state")]

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
        self.assertEqual(self.recorded(), list(intake.STATES))  # one sweep after the fact still walks every state

    def test_settlement_between_sweeps_records_every_state_in_verification_order(self):
        tid = self.capture(13)
        self.claim(tid, T0 + 60)
        self.sweep(T0 + 120)
        # settle_work's order: receipt, verification passed, close, then lifecycle.verified
        self.at("work.receipt", tid, T0 + 200)
        self.at("lifecycle.verification_finished", "github:o/r#13", T0 + 201, {"task_id": tid, "result": "passed"})
        self.at("work.closed", tid, T0 + 202)
        self.at("lifecycle.verified", "github:o/r#13", T0 + 203, {"task_id": tid})
        self.sweep(T0 + 300)
        self.sweep(T0 + 400)
        self.assertEqual(self.recorded(), list(intake.STATES))
        rows = [json.loads(e["payload"]) for e in self.led.events(kind="intake.state")]
        self.assertEqual([r["from"] for r in rows], [None, "FILED", "CLAIMED", "RECEIPT", "VERIFIED"])
        self.assertEqual(rows[3]["at"], T0 + 201)

    def test_failed_verification_is_not_verified(self):
        tid = self.capture(14)
        self.claim(tid, T0 + 60)
        self.at("work.receipt", tid, T0 + 120)
        self.at("lifecycle.verification_finished", "github:o/r#14", T0 + 121, {"task_id": tid, "result": "failed"})
        self.assertEqual(self.walk(tid)["state"], "RECEIPT")

    def test_transitions_are_recorded_once_per_change(self):
        tid = self.capture(2)
        self.sweep(T0 + 60)
        self.sweep(T0 + 120)
        self.claim(tid, T0 + 180)
        self.sweep(T0 + 240)
        self.assertEqual(self.recorded(), ["FILED", "CLAIMED"])

    # ---- coordinator claims (the Watch's doc_claims) -----------------------

    def test_a_coordinator_receipt_claims_the_item(self):
        tid = self.capture(15)
        self.sweep(T0 + H, claims={("O/R", 15): {"activity": T0 + 0.5 * H, "running": True}})
        self.assertEqual(self.walk(tid)["state"], "CLAIMED")
        self.assertEqual(self.walk(tid)["entered"]["CLAIMED"], T0 + 0.5 * H)
        self.sweep(T0 + 3 * H)  # the doc moved on; the ledger kept the claim, so no FILED fault
        self.assertEqual(self.filed, [])

    def test_an_uncaptured_coordinator_item_is_judged_on_its_claim(self):
        doc = {("o/other", 4): {"activity": T0, "running": True}}
        self.assertEqual(self.sweep(T0 + H, claims=doc)["items"], 1)
        self.sweep(T0 + 25 * H, claims=doc)
        self.assertEqual(len(self.filed), 1)
        self.assertIn("o/other#4 sat in CLAIMED", self.filed[0][5])
        self.assertEqual(self.filed[0][3], "o/other")

    # ---- queue timing from recorded eligibility ----------------------------

    def test_ready_added_late_starts_the_filed_clock_late(self):
        tid = self.capture(16, labels=())
        self.relabel(tid, 16, ("ready",), T0 + 20 * H)
        self.assertEqual(self.sweep(T0 + 21 * H)["faults"], [])
        self.sweep(T0 + 22.5 * H)
        self.assertEqual(len(self.filed), 1)

    def test_removing_ready_after_an_overrun_keeps_the_breach(self):
        tid = self.capture(17)
        self.relabel(tid, 17, (), T0 + 3 * H)
        self.claim(tid, T0 + 4 * H)
        self.sweep(T0 + 5 * H)
        self.assertEqual(len(self.filed), 1)
        self.assertIn("sat in FILED 3h00m", self.filed[0][5])

    def test_queue_intervals_accumulate_across_label_changes(self):
        tid = self.capture(18)
        self.relabel(tid, 18, ("ready", "hold"), T0 + H)
        self.relabel(tid, 18, ("ready",), T0 + 10 * H)
        self.assertEqual(self.sweep(T0 + 10.5 * H)["faults"], [])  # 1.5 h queued
        self.sweep(T0 + 11.5 * H)
        self.assertEqual(len(self.filed), 1)

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

    def test_a_live_filing_claim_blocks_a_concurrent_caller(self):
        self.capture(19)
        walk = intake.walks(self.led, T0 + 3 * H)[0]
        mark = intake.marker(walk, "FILED")
        self.at("intake.fault_filing", mark, T0 + 3 * H,
                {"pid": os.getpid(), "until": T0 + 3 * H + 300})  # another caller mid-create
        self.assertIsNone(intake.file_fault(self.led, walk, "FILED", 3 * H, 2 * H, gh=self.gh, now=T0 + 3 * H))
        self.assertEqual(self.filed, [])
        later = T0 + 3 * H + 301  # its lease ran out without a recorded fault: free again
        self.assertTrue(intake.file_fault(self.led, walk, "FILED", 3 * H, 2 * H, gh=self.gh, now=later))
        self.assertIsNone(intake.file_fault(self.led, walk, "FILED", 3 * H, 2 * H, gh=self.gh, now=later))
        self.assertEqual(len(self.filed), 1)

    def test_a_dead_owner_frees_the_filing_claim(self):
        self.capture(20)
        walk = intake.walks(self.led, T0 + 3 * H)[0]
        self.at("intake.fault_filing", intake.marker(walk, "FILED"), T0 + 3 * H,
                {"pid": 2 ** 22 + 7, "until": T0 + 4 * H})
        self.assertTrue(intake.file_fault(self.led, walk, "FILED", 3 * H, 2 * H, gh=self.gh, now=T0 + 3 * H))

    def test_lane_bound_grammar(self):
        self.assertEqual(intake.lane_bound(""), 24 * H)
        self.assertEqual(intake.lane_bound("**Lane bound:** 36h"), 36 * H)
        self.assertEqual(intake.lane_bound("lane bound: 2 days"), 2 * 86400)
        self.assertEqual(intake.lane_bound("lane bound: 90 min"), 90 * 60)


if __name__ == "__main__":
    unittest.main()
