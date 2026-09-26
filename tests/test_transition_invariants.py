"""Terminal state needs terminal evidence, refused at the ledger write, not avoided by today's callers."""

import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from nexus import terminal, work
from nexus.ledger import Ledger
from nexus.terminal import IllegalTransition
from tests import test_office_ask, test_terminal_evidence  # fixtures only

SHA = "a" * 40


class LedgerCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nexus-terminal-")
        self.led = Ledger(os.path.join(self.dir, "ledger.sqlite"))
        plan = self.led.add_plan("nightly", schedule={"every": 60}, inputs={"cmd": "true"},
                                 budget={"timeout_s": 5, "max_retries": 1})
        self.task = self.led.add_task("run it", origin="plan", plan_id=plan, dedupe_key="k")
        self.fid = self.led.create_flight(plan, task_id=self.task)
        for state in ("running", "produced"):
            self.led.set_state(self.fid, state)

    def tearDown(self):
        self.led.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def refused(self, state, evidence):
        with self.assertRaises(IllegalTransition):
            self.led.set_state(self.fid, state, evidence=evidence)
        self.assertEqual("produced", self.led.flight(self.fid)["state"])


class ExecutorFailureIsNeverVerified(LedgerCase):
    def test_crash_result_cannot_verify(self):
        self.refused("verified", None)
        self.refused("verified", terminal.landed({"state": "FAILED", "reason": "exit_1"}, "exit_1"))
        self.refused("verified", terminal.outputs({"ok": False, "error": {"code": "exit_1"}}))
        self.refused("verified", terminal.delivered({"verified": True, "evidence": ["x"], "returncode": 1}))


class WrongRevisionIsNeverVerified(LedgerCase):
    def test_receipt_must_name_the_landed_sha(self):
        self.refused("verified", terminal.landed({"state": "LANDED", "sha": SHA}, "landed " + "b" * 40))
        self.refused("verified", terminal.landed({"state": "LANDED"}, "landed somewhere"))
        self.refused("verified", terminal.landed({"state": "LANDED", "sha": SHA}, None))
        self.led.set_state(self.fid, "verified", evidence=terminal.landed({"state": "LANDED", "sha": SHA}, "on origin " + SHA))
        self.led.set_state(self.fid, "landing")
        with self.assertRaises(IllegalTransition):
            self.led.set_state(self.fid, "landed", evidence=terminal.landed({"state": "LANDED", "sha": SHA}, "fixture proof"))
        self.assertEqual("landing", self.led.flight(self.fid)["state"])


class UnknownNeverSettles(LedgerCase):
    def test_unknown_or_timeout_outcome_cannot_settle_a_flight_or_task(self):
        for outcome in ({"state": "UNKNOWN", "sha": SHA}, {"state": "LANDED", "sha": SHA, "outcome": "timeout"},
                        {"state": "LANDED", "sha": SHA, "status": "unknown"}):
            self.refused("verified", terminal.landed(outcome, SHA))
        for evidence in (terminal.closed("receipt", "unknown"), terminal.closed("receipt", None),
                         terminal.closed(None, "closed"), terminal.applied(None), None):
            with self.assertRaises(IllegalTransition):
                self.led.set_task_state(self.task, "done", evidence=evidence)
        self.assertNotEqual("done", self.led.task(self.task)["state"])

    def test_unconfirmed_close_leaves_the_task_pending(self):
        with patch("nexus.work.close_issue", return_value=None), \
                patch("nexus.work.pending", return_value="pending"):
            with self.assertRaises(IllegalTransition):
                work.close_then_done(self.led, self.fid, {"task": self.task}, "tower receipt", [])
        self.assertNotEqual("done", self.led.task(self.task)["state"])


class OpenSourceIsNeverSkipped(test_terminal_evidence.TowerFixture):
    def test_done_with_open_source_is_refused_and_the_task_stays_queued(self):
        self.led.event("work.receipt", self.task["id"], {"sha": "abc"}, "work")  # a stale receipt
        with self.assertRaises(IllegalTransition):
            self.led.set_task_state(self.task["id"], "done", decided_by="tower receipt",
                                    evidence=terminal.closed("tower receipt", "open"))
        self.led.event("work.closed", self.task["id"], {"repo": self.entry["repo"]}, "work")
        work.discover(self.led, self.entry)
        queue, _ = work.selection_queue(self.led, self.entry)
        self.assertEqual([self.task["id"]], [task["id"] for task in queue])


class TimeoutIsNeverReplayed(unittest.TestCase):
    """Not covered by the terminal layer: Ask raises UnsafeReplay itself. This pins that guard."""
    setUp = test_office_ask.AskQueueTest.setUp
    _start_drain = test_office_ask.AskQueueTest._start_drain
    _finish_worker = test_office_ask.AskQueueTest._finish_worker
    _wait_for = test_office_ask.AskQueueTest._wait_for

    def test_timed_out_run_is_not_executed_again_on_the_other_model(self):
        ask = test_office_ask.ask
        available = {"items": [{"id": "gpt-6-sol"}, {"id": "claude:sonnet"}], "default": "claude:sonnet"}
        runs = []

        def hung(*args, **kwargs):
            runs.append(args)
            raise subprocess.TimeoutExpired("claude", 1800)
        with patch.object(ask, "models", return_value=available), \
                patch.object(ask, "claude_binary", return_value="/bin/claude"), \
                patch.object(ask, "claude_environment", return_value={}), \
                patch.object(ask.subprocess, "run", side_effect=hung), \
                patch.object(ask, "AppServer", side_effect=AssertionError("second execution")) as codex:
            receipt = ask.send({"request_id": "invariant-timeout-1", "text": "Send it", "model": "claude:sonnet"})
            self._start_drain()
            self._wait_for(lambda: not ask.read()["busy"])
        reply = next(row for row in ask.read()["messages"] if row["id"] == receipt["reply_id"])
        self.assertEqual(1, len(runs))
        codex.assert_not_called()
        self.assertEqual("failed", reply["status"])


if __name__ == "__main__":
    unittest.main()
