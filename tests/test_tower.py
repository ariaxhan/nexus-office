"""Tower's tick, one behaviour per test, with real detached processes.

Nothing here mocks a process: a flight that is killed is really killed, and a
flight that vanishes really vanished. The tick is a plain function, so the clock
is a parameter and none of this waits on wall time it does not have to.
"""

import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nexus import flights as fl  # noqa: E402
from nexus import tower  # noqa: E402
from nexus.ledger import Ledger  # noqa: E402


def wait_for(predicate, timeout=15.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class TowerCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nexus-tower-")
        self.root = os.path.join(self.dir, "flights")
        self.led = Ledger(os.path.join(self.dir, "ledger.sqlite"))

    def tearDown(self):
        for flight in self.led.flights():
            fl.kill(flight["pid"])
        self.led.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def plan(self, cmd="echo hi > out.txt", **kwargs):
        schedule = kwargs.pop("schedule", {"every": 1})
        budget = {"timeout_s": 30, "max_retries": 1, "concurrency": 4}
        budget.update(kwargs.pop("budget", {}))
        return self.led.add_plan(
            kwargs.pop("name", "job"), schedule=schedule, inputs={"cmd": cmd},
            outputs=kwargs.pop("outputs", ["out.txt"]), budget=budget,
            resolution_policy=kwargs.pop("policy", {"may_accept": True}),
            resources=kwargs.pop("resources", []))

    def tick(self, now=None, **kwargs):
        return tower.tick(self.led, now=now, root=self.root, **kwargs)

    def settle(self, ticks=40, pause=0.1):
        for _ in range(ticks):
            self.tick()
            if not self.led.flights(states=("running", "queued")):
                return
            time.sleep(pause)


class Scheduling(TowerCase):
    def test_a_due_plan_makes_a_task_then_a_flight_then_an_artifact(self):
        self.plan()
        self.tick()
        task = self.led.tasks()[0]
        self.assertEqual("running", task["state"])
        flight = self.led.flights()[0]
        self.assertEqual(task["id"], flight["task_id"])
        self.assertTrue(wait_for(lambda: (self.tick(), self.led.flights(
            states=("produced",)))[1]))
        produced = self.led.flights(states=("produced",))[0]
        artifacts = self.led.artifacts(produced["id"])
        self.assertEqual(1, len(artifacts))
        self.assertTrue(os.path.exists(artifacts[0]["ref"]))
        self.assertEqual("done", self.led.task(produced["task_id"])["state"])
        self.assertEqual([], self.led.integrity_check())

    def test_every_seconds_is_not_due_again_until_the_interval_passes(self):
        self.plan(schedule={"every": 60}, budget={"timeout_s": 600})
        now = time.time()
        self.assertEqual(1, self.tick(now=now)["scheduled"])
        self.assertEqual(0, self.tick(now=now + 30)["scheduled"])
        self.assertEqual(1, self.tick(now=now + 61)["scheduled"])

    def test_at_hhmm_is_due_once_that_day(self):
        plan = self.plan(schedule={"at": "03:00"})
        row = self.led.plan(plan)
        three_am = time.mktime(time.localtime(time.time())[:3] + (3, 0, 0, 0, 0, -1))
        self.assertEqual((False, None, None), tower.due(self.led, row, three_am - 60))
        ready, key, _ = tower.due(self.led, row, three_am + 60)
        self.assertTrue(ready)
        self.assertIn("at:", key)

    def test_on_event_fires_once_per_event(self):
        plan = self.plan(schedule={"on": "webhook.pr"})
        self.assertEqual(0, self.tick()["scheduled"])
        self.led.event("webhook.pr", "repo/1", {}, "webhook")
        self.assertEqual(1, self.tick()["scheduled"])
        row = self.led.plan(plan)
        self.assertEqual((False, None, None), tower.due(self.led, row, time.time()))

    def test_concurrency_caps_flights_in_the_air(self):
        self.plan(cmd="sleep 5", outputs=[], budget={"concurrency": 2})
        now = time.time()
        for i in range(4):
            self.tick(now=now + i * 2)
        self.assertLessEqual(len(self.led.flights(states=("running",))), 2)


class Acceptance(TowerCase):
    def test_a_duplicate_dedupe_key_is_rejected_not_run_twice(self):
        plan = self.plan()
        self.led.add_task("a", origin="plan", plan_id=plan, dedupe_key="same")
        self.led.add_task("b", origin="plan", plan_id=plan, dedupe_key="same")
        tower.accept_tasks(self.led)
        states = sorted(t["state"] for t in self.led.tasks())
        self.assertIn("rejected_duplicate", states)
        self.assertEqual(1, len([s for s in states if s == "running"]))

    def test_policy_can_refuse_a_task(self):
        plan = self.plan(policy={"may_accept": False})
        task = self.led.add_task("a", origin="plan", plan_id=plan, dedupe_key="k")
        tower.accept_tasks(self.led)
        self.assertEqual("rejected_policy", self.led.task(task)["state"])
        self.assertEqual([], list(self.led.flights()))

    def test_a_quarantined_plan_accepts_nothing(self):
        plan = self.plan()
        self.led.quarantine_plan(plan, "test")
        task = self.led.add_task("a", origin="plan", plan_id=plan)
        tower.accept_tasks(self.led)
        self.assertEqual("rejected_policy", self.led.task(task)["state"])

    def test_a_plan_cannot_widen_its_objective_boundary(self):
        objective = self.led.add_objective("careful", "nothing without a person",
                                           autonomy_policy={"may_accept": False})
        plan = self.led.add_plan("bold", schedule={"every": 60}, inputs={"cmd": "true"},
                                 objective_id=objective,
                                 resolution_policy={"may_accept": True})
        task = self.led.add_task("a", origin="plan", plan_id=plan)
        tower.accept_tasks(self.led)
        self.assertEqual("rejected_policy", self.led.task(task)["state"])

    def test_a_plan_may_narrow_its_objective_boundary(self):
        objective = self.led.add_objective("open", "go ahead",
                                           autonomy_policy={"may_accept": True,
                                                            "may_spend_up_to": 10})
        plan = self.led.add_plan("thrifty", schedule={"every": 60}, inputs={"cmd": "true"},
                                 objective_id=objective,
                                 resolution_policy={"may_spend_up_to": 2})
        policy = tower.effective_policy(self.led, self.led.plan(plan))
        self.assertEqual({"may_accept": True, "may_spend_up_to": 2}, policy)

    def test_an_accepted_task_with_no_flight_gets_one_after_a_crash(self):
        plan = self.plan()
        task = self.led.add_task("a", origin="plan", plan_id=plan, state="accepted")
        tower.accept_tasks(self.led)
        self.assertEqual(1, len(self.led.flights(task_id=task)))
        self.assertEqual("running", self.led.task(task)["state"])


class Budgets(TowerCase):
    def test_a_flight_past_its_timeout_fails_with_code_timeout(self):
        self.plan(cmd="sleep 30", outputs=[], budget={"timeout_s": 1})
        self.tick()
        flight = self.led.flights()[0]
        self.assertTrue(wait_for(lambda: self.led.flight(flight["id"])["pid"]))
        self.tick(now=time.time() + 5)
        row = self.led.flight(flight["id"])
        self.assertEqual("failed", row["state"])
        self.assertIn('"code": "timeout"', row["result"])
        self.assertFalse(fl.alive(row["pid"]))

    def test_a_failing_script_fails_with_its_exit_code_not_its_output(self):
        self.plan(cmd="echo ERROR; exit 3", outputs=[])
        self.settle()
        row = self.led.flights(states=("failed",))[0]
        self.assertIn('"code": "exit_nonzero"', row["result"])

    def test_a_script_that_prints_error_and_exits_zero_succeeded(self):
        self.plan(cmd="echo ERROR: not really; exit 0", outputs=[])
        self.settle()
        self.assertTrue(self.led.flights(states=("produced",)))

    def test_a_missing_declared_output_is_a_structured_failure(self):
        self.plan(cmd="true", outputs=["out.txt"])
        self.settle()
        row = self.led.flights(states=("failed",))[0]
        self.assertIn('"code": "missing_output"', row["result"])

    def test_retries_stop_at_the_budget_and_the_task_is_abandoned(self):
        self.plan(cmd="exit 1", outputs=[], schedule={"every": 3600},
                  budget={"max_retries": 1})
        self.settle(ticks=20)
        attempts = sorted(f["attempt"] for f in self.led.flights())
        self.assertEqual([1, 2], attempts)
        self.assertEqual("abandoned", self.led.tasks()[0]["state"])

    def test_a_plan_quarantines_after_consecutive_failures(self):
        plan = self.plan(cmd="exit 1", outputs=[], budget={"max_retries": 2})
        self.settle(ticks=25)
        self.assertIsNotNone(self.led.plan(plan)["quarantined_at"])


class Reconciliation(TowerCase):
    def test_a_killed_flight_becomes_failed_vanished_and_is_retried(self):
        self.plan(cmd="sleep 30", outputs=[], schedule={"every": 3600},
                  budget={"max_retries": 1})
        self.tick()
        flight = self.led.flights()[0]
        self.assertTrue(wait_for(lambda: self.led.flight(flight["id"])["pid"]))
        fl.kill(self.led.flight(flight["id"])["pid"])
        self.assertTrue(wait_for(lambda: (self.tick(), self.led.flight(
            flight["id"])["state"] == "failed")[1]))
        row = self.led.flight(flight["id"])
        self.assertIn('"code": "vanished"', row["result"])
        self.assertFalse(os.path.exists(row["workspace"]))
        self.assertEqual(2, len(self.led.flights()))
        self.assertEqual([], self.led.integrity_check())

    def test_a_malformed_result_is_a_structured_failure_not_a_crash(self):
        self.plan(cmd="sleep 30", outputs=[])
        self.tick()
        flight = self.led.flights()[0]
        with open(os.path.join(flight["workspace"], "result.json"), "w") as handle:
            handle.write("{not json")
        self.tick()
        row = self.led.flight(flight["id"])
        self.assertEqual("failed", row["state"])
        self.assertIn('"code": "malformed_result"', row["result"])

    def test_a_duplicate_tick_launches_nothing_twice(self):
        self.plan(cmd="sleep 3", outputs=[], schedule={"every": 3600})
        self.tick()
        self.tick()
        self.tick()
        self.assertEqual(1, len(self.led.flights()))

    def test_reconcile_notices_an_applying_landing_after_a_crash(self):
        plan = self.plan()
        flight = self.led.create_flight(plan)
        for state in ("running", "produced", "verified"):
            self.led.set_state(flight, state)
        landing = self.led.create_landing(flight, "repo#main", state="verified")
        self.led.start_applying(landing, "sha1")
        self.tick()
        self.assertTrue(self.led.events(kind="landing.needs_reconcile"))
        self.assertEqual(1, self.tick(landing_probe=lambda target: "sha1")
                         ["reconciled_landings"])
        self.assertEqual("landed", self.led.flight(flight)["state"])


class ResourceLeases(TowerCase):
    def test_two_plans_on_the_same_resource_do_not_fly_together(self):
        self.plan(name="a", cmd="sleep 3", outputs=[], resources=["repo#main"])
        self.plan(name="b", cmd="sleep 3", outputs=[], resources=["repo#main"])
        self.tick()
        self.assertEqual(1, len(self.led.flights(states=("running",))))
        self.assertEqual(1, len(self.led.flights(states=("queued",))))
        self.assertEqual(1, len(self.led.leases()))

    def test_the_lease_is_released_when_the_flight_ends(self):
        self.plan(name="a", cmd="true", outputs=[], resources=["mailbox:hello"])
        self.settle()
        self.assertEqual([], list(self.led.leases()))


class EscapeHatch(TowerCase):
    def test_pause_stops_launching_but_keeps_reconciling(self):
        self.plan()
        tower.pause(self.led)
        report = self.tick()
        self.assertTrue(report.get("paused"))
        self.assertEqual([], list(self.led.flights()))
        tower.resume(self.led)
        self.assertEqual(1, self.tick()["launched"])

    def test_status_reads_only_the_ledger(self):
        self.plan()
        self.tick()
        text = tower.status(self.led)
        self.assertIn("integrity  ok", text)
        self.assertIn("job", text)


if __name__ == "__main__":
    unittest.main()
