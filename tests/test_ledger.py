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
from tests.ledger_fixture import build  # noqa: E402


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
        self.assertEqual(self.led.user_version(), 3)

    def test_schema_v1_tables_all_present(self):
        names = {r[0] for r in self.led.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("plans", "tasks", "flights", "artifacts", "landings", "events", "leases"):
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
        self.assertEqual(3, led.user_version())
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


class Parity(unittest.TestCase):
    """Legacy paths converge on the same current ledger without losing meaning."""

    def schema(self, path):
        """Every table, index and trigger by name, and every table's column names."""
        conn = sqlite3.connect(path)
        objects = conn.execute(
            "SELECT type, name, tbl_name FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name").fetchall()
        columns = {name: {r[1] for r in conn.execute(f"PRAGMA table_info({name})")}
                   for kind, name, _ in objects if kind == "table"}
        conn.close()
        return objects, columns

    def test_upgraded_v1_equals_fresh_v2(self):
        d = tempfile.mkdtemp(prefix="nexus-parity-")
        self.addCleanup(shutil.rmtree, d, True)
        old = build(os.path.join(d, "old.sqlite"))
        fresh = os.path.join(d, "fresh.sqlite")
        Ledger(fresh).close()
        led = Ledger(old)
        self.addCleanup(led.close)

        self.assertEqual(3, led.user_version())
        self.assertEqual(self.schema(fresh), self.schema(old))
        self.assertEqual([], led.integrity_check())

        counts = dict(led.conn.execute(
            "SELECT kind, count(*) FROM events WHERE kind IN "
            "('objective','observation','radio.message','gate','radio.delivered')"
            " GROUP BY kind").fetchall())
        self.assertEqual({"objective": 1, "observation": 1, "radio.message": 2,
                          "gate": 1, "radio.delivered": 1}, counts)

        expected = ("keep main green", '{"gate": "risky"}')
        self.assertEqual(expected, tuple(led.conn.execute(
            "SELECT objective, autonomy FROM plans WHERE id='plan_1'"
        ).fetchone()))
        self.assertEqual(expected, tuple(led.conn.execute(
            "SELECT objective, autonomy FROM tasks WHERE id='task_1'"
        ).fetchone()))

        bak = sqlite3.connect(old + ".bak-1")
        self.assertEqual(1, bak.execute("PRAGMA user_version").fetchone()[0])
        bak.close()

    def test_missing_objective_reference_rolls_back_v2(self):
        d = tempfile.mkdtemp(prefix="nexus-parity-")
        self.addCleanup(shutil.rmtree, d, True)
        old = build(os.path.join(d, "old.sqlite"))
        conn = sqlite3.connect(old)
        conn.execute("UPDATE tasks SET objective_id='missing'")
        conn.commit()
        conn.close()

        with self.assertRaisesRegex(LedgerError, "references missing objective"):
            Ledger(old)

        conn = sqlite3.connect(old)
        self.addCleanup(conn.close)
        self.assertEqual(1, conn.execute("PRAGMA user_version").fetchone()[0])
        self.assertIsNotNone(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='objectives'"
        ).fetchone())
        self.assertNotIn("objective", {
            row[1] for row in conn.execute("PRAGMA table_info(tasks)")
        })
        self.assertEqual("obj_1", conn.execute(
            "SELECT objective_id FROM plans WHERE id='plan_1'"
        ).fetchone()[0])
        self.assertEqual("missing", conn.execute(
            "SELECT objective_id FROM tasks WHERE id='task_1'"
        ).fetchone()[0])

    def partial_v2(self, path, clear_references=True):
        """The early-v2 shape: legacy tables gone, stale objective_id columns kept."""
        build(path)
        conn = sqlite3.connect(path)
        for table, columns in {
            "plans": ("objective", "autonomy"),
            "tasks": ("objective", "autonomy", "parent_id", "output", "check"),
        }.items():
            for column in columns:
                conn.execute(f'ALTER TABLE {table} ADD COLUMN "{column}" TEXT')
        if clear_references:
            conn.execute("UPDATE plans SET objective_id=NULL")
            conn.execute("UPDATE tasks SET objective_id=NULL")
        for table in ("objectives", "observations", "messages", "gates"):
            conn.execute(f"DROP TABLE {table}")
        conn.execute(
            "INSERT INTO events (ts,kind,subject,payload,source) "
            "VALUES (1,'migration.marker',NULL,'{}','test')"
        )
        conn.execute("PRAGMA user_version=2")
        conn.commit()
        conn.close()
        return path

    def test_partial_v2_equals_fresh_v3(self):
        d = tempfile.mkdtemp(prefix="nexus-parity-")
        self.addCleanup(shutil.rmtree, d, True)
        old = self.partial_v2(os.path.join(d, "old.sqlite"))
        fresh = os.path.join(d, "fresh.sqlite")
        Ledger(fresh).close()
        led = Ledger(old)
        self.addCleanup(led.close)

        self.assertEqual(3, led.user_version())
        self.assertEqual(self.schema(fresh), self.schema(old))
        self.assertEqual([], led.integrity_check())
        self.assertEqual(1, led.conn.execute(
            "SELECT count(*) FROM events WHERE kind='migration.marker'"
        ).fetchone()[0])
        self.assertEqual((None, None), tuple(led.conn.execute(
            "SELECT objective, autonomy FROM plans WHERE id='plan_1'"
        ).fetchone()))
        self.assertEqual((None, None), tuple(led.conn.execute(
            "SELECT objective, autonomy FROM tasks WHERE id='task_1'"
        ).fetchone()))
        bak = sqlite3.connect(old + ".bak-2")
        self.assertEqual(2, bak.execute("PRAGMA user_version").fetchone()[0])
        bak.close()

    def test_partial_v2_with_relationships_is_refused(self):
        d = tempfile.mkdtemp(prefix="nexus-parity-")
        self.addCleanup(shutil.rmtree, d, True)
        old = self.partial_v2(os.path.join(d, "old.sqlite"), clear_references=False)

        with self.assertRaisesRegex(LedgerError, "references cannot be repaired"):
            Ledger(old)

        conn = sqlite3.connect(old)
        self.addCleanup(conn.close)
        self.assertEqual(2, conn.execute("PRAGMA user_version").fetchone()[0])
        self.assertIn("objective_id", {
            row[1] for row in conn.execute("PRAGMA table_info(plans)")
        })


if __name__ == "__main__":
    unittest.main()
