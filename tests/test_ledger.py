"""The ledger's rules, one test per rule.

Each test names the rule from docs/foundation.md it proves. A rule with no test
here is a rule nothing enforces.
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nexus.ledger import Ledger, LedgerError, TRANSITIONS  # noqa: E402


class LedgerCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nexus-ledger-")
        self.path = os.path.join(self.dir, "ledger.sqlite")
        self.led = Ledger(self.path)
        self.plan = self.led.add_plan("nightly", schedule={"every": 60},
                                      inputs={"cmd": "true"},
                                      budget={"timeout_s": 5, "max_retries": 1})

    def tearDown(self):
        self.led.close()
        shutil.rmtree(self.dir, ignore_errors=True)


class Schema(LedgerCase):
    def test_wal_and_version(self):
        mode = self.led.conn.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(mode.lower(), "wal")
        self.assertEqual(self.led.user_version(), 1)

    def test_schema_v1_tables_all_present(self):
        names = {r[0] for r in self.led.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("objectives", "plans", "observations", "tasks", "flights",
                      "artifacts", "landings", "messages", "gates", "events", "leases"):
            self.assertIn(table, names)

    def test_migration_copies_a_backup_before_touching_the_file(self):
        self.led.close()
        old = sqlite3.connect(self.path)
        old.execute("PRAGMA user_version=0")
        old.commit()
        old.close()
        led = Ledger(self.path)
        self.addCleanup(led.close)
        self.assertTrue(os.path.exists(self.path + ".bak-0"))
        self.assertEqual(1, led.user_version())
        # the copy is the old file, not a fresh one
        self.assertGreater(os.path.getsize(self.path + ".bak-0"), 0)

    def test_a_newer_ledger_is_refused_not_downgraded(self):
        self.led.conn.execute("PRAGMA user_version=99")
        self.led.close()
        with self.assertRaises(LedgerError):
            Ledger(self.path)


class AppendOnlyHistory(LedgerCase):
    def test_updating_an_event_raises(self):
        self.led.event("thing.happened", "s", {"a": 1}, "tower")
        with self.assertRaises(sqlite3.IntegrityError):
            self.led.conn.execute("UPDATE events SET kind='lie' WHERE kind='thing.happened'")

    def test_deleting_an_event_raises(self):
        self.led.event("thing.happened", "s", {}, "tower")
        with self.assertRaises(sqlite3.IntegrityError):
            self.led.conn.execute("DELETE FROM events")

    def test_a_terminal_flight_can_never_be_updated(self):
        flight = self.led.create_flight(self.plan)
        self.led.fail(flight, "boom")
        with self.assertRaises(sqlite3.IntegrityError):
            self.led.conn.execute("UPDATE flights SET state='queued' WHERE id=?", (flight,))

    def test_a_flight_row_is_never_deleted(self):
        flight = self.led.create_flight(self.plan)
        with self.assertRaises(sqlite3.IntegrityError):
            self.led.conn.execute("DELETE FROM flights WHERE id=?", (flight,))

    def test_an_applied_landing_is_history(self):
        flight = self.led.create_flight(self.plan)
        self.led.set_state(flight, "running")
        self.led.set_state(flight, "produced")
        self.led.set_state(flight, "verified")
        landing = self.led.create_landing(flight, "repo#main", state="verified")
        self.led.apply_landing(landing, "abc123")
        with self.assertRaises(sqlite3.IntegrityError):
            self.led.conn.execute("UPDATE landings SET applied_sha='zzz' WHERE id=?", (landing,))


class Transitions(LedgerCase):
    def test_an_illegal_transition_raises(self):
        flight = self.led.create_flight(self.plan)
        with self.assertRaises(LedgerError):
            self.led.set_state(flight, "landed")

    def test_every_state_in_the_table_is_reachable_in_the_table(self):
        reachable = {"queued"} | {to for froms in TRANSITIONS.values() for to in froms}
        self.assertEqual(reachable, set(TRANSITIONS))

    def test_compare_and_set_refuses_a_stale_writer(self):
        flight = self.led.create_flight(self.plan)
        self.assertTrue(self.led.set_state(flight, "running", expect="queued"))
        self.assertFalse(self.led.set_state(flight, "running", expect="queued"))

    def test_landed_requires_an_applied_landing(self):
        flight = self.led.create_flight(self.plan)
        for state in ("running", "produced", "verified"):
            self.led.set_state(flight, state)
        self.led.conn.execute("UPDATE flights SET state='landed' WHERE id=?", (flight,))
        self.assertTrue(any("no applied landing" in p for p in self.led.integrity_check()))

    def test_apply_landing_is_idempotent(self):
        flight = self.led.create_flight(self.plan)
        for state in ("running", "produced", "verified"):
            self.led.set_state(flight, state)
        landing = self.led.create_landing(flight, "repo#main", state="verified")
        self.assertTrue(self.led.apply_landing(landing, "sha1"))
        self.assertFalse(self.led.apply_landing(landing, "sha1"))
        self.assertEqual("landed", self.led.flight(flight)["state"])

    def test_applying_is_recorded_before_the_push(self):
        flight = self.led.create_flight(self.plan)
        for state in ("running", "produced", "verified"):
            self.led.set_state(flight, state)
        landing = self.led.create_landing(flight, "repo#main", state="verified")
        self.assertTrue(self.led.start_applying(landing, "sha1"))
        row = self.led.landing(landing)
        self.assertEqual(("applying", "sha1"), (row["state"], row["expected_sha"]))


class EventsOnEveryChange(LedgerCase):
    def test_every_state_change_writes_an_event(self):
        flight = self.led.create_flight(self.plan)
        self.led.set_state(flight, "running")
        self.led.fail(flight, "boom", "detail")
        kinds = [(e["kind"], e["payload"]) for e in self.led.events(subject=flight)]
        self.assertEqual(3, len([k for k, _ in kinds if k == "flight.state"]))
        self.assertEqual([], self.led.integrity_check())

    def test_a_state_with_no_event_is_an_integrity_problem(self):
        flight = self.led.create_flight(self.plan)
        self.led.conn.execute("UPDATE flights SET state='running' WHERE id=?", (flight,))
        self.assertTrue(any("has no event" in p for p in self.led.integrity_check()))

    def test_a_failure_is_always_a_structured_code(self):
        flight = self.led.create_flight(self.plan)
        self.led.fail(flight, "timeout", "over budget")
        result = self.led.flight(flight)["result"]
        self.assertIn('"code": "timeout"', result)


class Leases(LedgerCase):
    def test_a_resource_is_leased_by_one_flight(self):
        a = self.led.create_flight(self.plan)
        b = self.led.create_flight(self.plan)
        self.assertTrue(self.led.acquire_leases(a, ["repo#main"], until=9e9))
        self.assertFalse(self.led.acquire_leases(b, ["repo#main"], until=9e9))

    def test_all_resources_or_none(self):
        a = self.led.create_flight(self.plan)
        b = self.led.create_flight(self.plan)
        self.led.acquire_leases(a, ["mailbox:hello"], until=9e9)
        self.assertFalse(self.led.acquire_leases(b, ["deploy:prod", "mailbox:hello"], until=9e9))
        self.assertEqual(["mailbox:hello"], [r["resource"] for r in self.led.leases()])

    def test_expiry_frees_a_resource_without_asking_the_holder(self):
        a = self.led.create_flight(self.plan)
        self.led.acquire_leases(a, ["deploy:prod"], until=100)
        self.assertEqual(1, len(self.led.expire_leases(now=101)))
        self.assertEqual([], list(self.led.leases()))

    def test_a_terminal_flight_drops_its_leases(self):
        a = self.led.create_flight(self.plan)
        self.led.acquire_leases(a, ["deploy:prod"], until=9e9)
        self.led.fail(a, "boom")
        self.assertEqual([], list(self.led.leases()))
        self.assertEqual([], self.led.integrity_check())

    def test_a_lease_held_by_a_dead_flight_is_an_integrity_problem(self):
        a = self.led.create_flight(self.plan)
        self.led.acquire_leases(a, ["deploy:prod"], until=9e9)
        self.led.conn.execute("UPDATE flights SET state='cancelled' WHERE id=?", (a,))
        self.assertTrue(any("deploy:prod" in p for p in self.led.integrity_check()))


class TasksAndRadio(LedgerCase):
    def test_a_dedupe_key_is_owned_by_one_live_task(self):
        first = self.led.add_task("run it", origin="plan", plan_id=self.plan, dedupe_key="k")
        self.assertIsNotNone(self.led.live_task_with_key("k"))
        self.led.set_task_state(first, "done")
        self.assertIsNone(self.led.live_task_with_key("k"))

    def test_a_decided_task_is_history(self):
        task = self.led.add_task("run it", origin="plan", plan_id=self.plan)
        self.led.set_task_state(task, "abandoned")
        with self.assertRaises(sqlite3.IntegrityError):
            self.led.conn.execute("UPDATE tasks SET state='candidate' WHERE id=?", (task,))

    def test_a_message_outlives_the_flight_and_goes_to_whoever_holds_the_task(self):
        task = self.led.add_task("run it", origin="plan", plan_id=self.plan)
        dead = self.led.create_flight(self.plan, task_id=task)
        self.led.send_message("keep going", task_id=task, from_flight=dead)
        self.led.fail(dead, "vanished")
        heir = self.led.create_flight(self.plan, task_id=task, attempt=2)
        delivered = self.led.deliver_messages(task, heir)
        self.assertEqual(["keep going"], [m["body"] for m in delivered])
        self.assertEqual([], list(self.led.undelivered(task)))

    def test_a_gate_answer_is_by_id_and_only_once(self):
        gate = self.led.open_gate("merge?", ["yes", "no"], policy_reason="client default branch")
        self.assertTrue(self.led.answer_gate(gate, "yes", "aria"))
        self.assertFalse(self.led.answer_gate(gate, "no", "aria"))

    def test_a_stale_gate_answer_is_refused(self):
        gate = self.led.open_gate("merge?", ["yes"], timeout_at=100)
        self.assertFalse(self.led.answer_gate(gate, "yes", "aria", now=101))
        self.assertIsNone(self.led.gate(gate)["answer"])

    def test_an_observation_can_be_claimed_by_a_task(self):
        obs = self.led.observe("webhook", subject="repo/1", payload={"action": "opened"})
        task = self.led.add_task("look at it", origin="observation", plan_id=self.plan,
                                 observation_id=obs)
        self.assertEqual(task, self.led.observations()[0]["handled_by_task"])


class Integrity(LedgerCase):
    def test_a_healthy_ledger_has_no_problems(self):
        flight = self.led.create_flight(self.plan)
        self.led.set_state(flight, "running")
        self.led.set_state(flight, "produced")
        self.led.add_artifact(flight, "file", "/tmp/out.txt")
        self.assertEqual([], self.led.integrity_check())

    def test_an_unknown_state_is_caught(self):
        flight = self.led.create_flight(self.plan)
        self.led.conn.execute("UPDATE flights SET state='floating' WHERE id=?", (flight,))
        self.assertTrue(any("floating" in p for p in self.led.integrity_check()))


if __name__ == "__main__":
    unittest.main()
