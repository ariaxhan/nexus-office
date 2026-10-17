"""Issue #305 (pd-12): registry truth as structured fields + a converger.

The cadence a job is meant to keep is a field (`cadence_s`), not a sentence
in its note. The converger reads the three places that cadence lives
(source registry, deployed registry, the plist launchd loads) plus the
field, and a disagreement is a #300 repair record, never text. Plist or
deployed drift is inside the envelope (`jobctl sync` regenerates both from
the source); a field the source schedule contradicts is a decision."""

import json
import os
import plistlib
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nexus import converger, faults  # noqa: E402
from nexus.ledger import Ledger  # noqa: E402

SIX_HOURLY = [{"hour": h, "minute": 0} for h in (0, 6, 12, 18)]
ID = "com.nexus.canary-wedge-d"


def row(**extra):
    base = {"id": ID, "state": "enabled", "command": ["/bin/true"],
            "schedule": {"kind": "calendar", "at": SIX_HOURLY, "run_at_load": False},
            "cadence_s": 21600, "host": "studio"}
    base.update(extra)
    return base


class ConvergerCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="converger-")
        self.source = os.path.join(self.dir, "source.jsonl")
        self.deployed = os.path.join(self.dir, "deployed.jsonl")
        self.agents = os.path.join(self.dir, "LaunchAgents")
        os.mkdir(self.agents)
        self.led = Ledger(os.path.join(self.dir, "ledger.sqlite"))
        self.synced = 0

    def write_registry(self, path, rows):
        with open(path, "w") as f:
            f.write("# header comment\n")
            for r in rows:
                f.write(json.dumps(r) + "\n")

    def write_plist(self, schedule):
        d = {"Label": ID}
        if schedule["kind"] == "interval":
            d["StartInterval"] = schedule["seconds"]
        else:
            cal = [{k.capitalize(): v for k, v in e.items()} for e in schedule["at"]]
            d["StartCalendarInterval"] = cal[0] if len(cal) == 1 else cal
        with open(os.path.join(self.agents, ID + ".plist"), "wb") as f:
            plistlib.dump(d, f)

    def sync(self):
        """The fake `jobctl sync`: what the real one does to these two files."""
        self.synced += 1
        rows = converger.read_registry(self.source)
        self.write_registry(self.deployed, rows)
        self.write_plist(rows[0]["schedule"])
        return 0

    def converge(self, apply=True):
        return converger.converge(self.led, self.source, self.deployed, self.agents,
                                  host="studio", sync=self.sync if apply else None)

    def aligned(self, **extra):
        self.write_registry(self.source, [row(**extra)])
        self.write_registry(self.deployed, [row(**extra)])
        self.write_plist(row(**extra)["schedule"])


class CadenceMath(unittest.TestCase):
    def test_six_hourly_calendar_is_21600(self):
        self.assertEqual(converger.cadence({"kind": "calendar", "at": SIX_HOURLY}), 21600)

    def test_interval_is_its_seconds(self):
        self.assertEqual(converger.cadence({"kind": "interval", "seconds": 300}), 300)

    def test_hourly_minute_only_entry(self):
        self.assertEqual(converger.cadence({"kind": "calendar", "at": [{"minute": 7}]}), 3600)

    def test_weekly(self):
        self.assertEqual(converger.cadence(
            {"kind": "calendar", "at": [{"weekday": 1, "hour": 9, "minute": 0}]}), 604800)

    def test_irregular_calendar_has_no_cadence(self):
        self.assertIsNone(converger.cadence(
            {"kind": "calendar", "at": [{"hour": 1, "minute": 0}, {"hour": 2, "minute": 0}]}))

    def test_daemon_has_no_cadence(self):
        self.assertIsNone(converger.cadence({"kind": "daemon"}))


class Converge(ConvergerCase):
    def test_aligned_canary_is_a_clean_report(self):
        self.aligned()
        report = self.converge()
        self.assertTrue(report["clean"])
        self.assertEqual(report["jobs"][ID],
                         {"field_s": 21600, "source_s": 21600, "deployed_s": 21600,
                          "plist_s": 21600, "deployed_field_s": 21600,
                          "migration": None, "deployed_migration": None})
        self.assertEqual(faults.records(self.led), [])
        self.assertEqual(self.synced, 0)

    def test_seeded_plist_divergence_converges_without_a_reader(self):
        self.aligned()
        self.write_plist({"kind": "interval", "seconds": 300})  # the 300s plist
        report = self.converge()
        self.assertEqual(self.synced, 1)
        self.assertTrue(report["clean"], report)
        [rec] = faults.records(self.led)
        self.assertEqual(rec["state"], "verified_fixed")
        self.assertEqual(rec["source"], converger.SOURCE)
        self.assertTrue(rec["signature"].startswith(f"cadence-divergence:{ID}:plist"))
        self.assertEqual(rec["repair"]["action"], "jobctl sync")
        self.assertIn(ID + ".plist", rec["verification"]["surface"])
        # and the machine now agrees
        self.assertEqual(self.converge()["jobs"][ID]["plist_s"], 21600)

    def test_deployed_registry_drift_is_inside_the_envelope(self):
        self.aligned()
        self.write_registry(self.deployed, [row(schedule={"kind": "interval", "seconds": 300})])
        report = self.converge()
        self.assertTrue(report["clean"])
        [rec] = faults.records(self.led)
        self.assertEqual(rec["state"], "verified_fixed")

    def test_deployed_registry_missing_structured_fields_is_repaired(self):
        self.aligned(migration={"from": "interval", "to": "calendar", "at": "2026-10-15"})
        deployed = converger.read_registry(self.deployed)
        deployed[0].pop("cadence_s")
        deployed[0].pop("migration")
        self.write_registry(self.deployed, deployed)
        self.assertFalse(self.converge(apply=False)["clean"])
        self.assertIsNone(self.converge(apply=False)["jobs"][ID]["deployed_field_s"])
        report = self.converge()
        self.assertTrue(report["clean"], report)
        self.assertEqual(self.synced, 1)
        self.assertEqual(converger.read_registry(self.deployed)[0]["cadence_s"], 21600)
        self.assertEqual(converger.read_registry(self.deployed)[0]["migration"]["to"], "calendar")
        self.assertTrue(any(r["signature"].endswith(":deployed_fields") and
                            r["state"] == "verified_fixed" for r in faults.records(self.led)))

    def test_field_contradicting_source_is_escalated_as_a_decision(self):
        self.aligned(cadence_s=300)
        report = self.converge()
        self.assertFalse(report["clean"])
        self.assertEqual(self.synced, 0)  # sync cannot decide which is meant
        [rec] = faults.records(self.led)
        self.assertEqual(rec["state"], "escalated")
        self.assertEqual(rec["escalation"]["kind"], "decision")
        self.assertIn(ID, rec["escalation"]["act_needed"])
        self.assertNotIn("\n", rec["escalation"]["act_needed"])

    def test_failed_sync_escalates_technical(self):
        self.aligned()
        self.write_plist({"kind": "interval", "seconds": 300})
        report = converger.converge(self.led, self.source, self.deployed, self.agents,
                                    host="studio", sync=lambda: 1)
        self.assertFalse(report["clean"])
        [rec] = faults.records(self.led)
        self.assertEqual(rec["state"], "escalated")
        self.assertEqual(rec["escalation"]["kind"], "technical")

    def test_check_only_detects_and_leaves_disk_alone(self):
        self.aligned()
        self.write_plist({"kind": "interval", "seconds": 300})
        report = self.converge(apply=False)
        self.assertFalse(report["clean"])
        [rec] = faults.records(self.led)
        self.assertEqual(rec["state"], "detected")
        self.assertEqual(self.synced, 0)

    def test_detected_fault_recovers_when_a_later_run_is_clean(self):
        self.aligned()
        self.write_plist({"kind": "interval", "seconds": 300})
        self.converge(apply=False)
        self.write_plist(row()["schedule"])
        self.assertTrue(self.converge(apply=False)["clean"])
        [rec] = faults.records(self.led)
        self.assertEqual(rec["state"], "recovered")

    def test_repeat_divergence_is_one_record_not_many(self):
        self.aligned()
        self.write_plist({"kind": "interval", "seconds": 300})
        self.converge(apply=False)
        self.converge(apply=False)
        self.assertEqual(len(faults.records(self.led)), 1)

    def test_rows_without_the_field_are_still_checked_plist_against_source(self):
        self.aligned()
        rows = converger.read_registry(self.source)
        del rows[0]["cadence_s"]
        self.write_registry(self.source, rows)
        self.write_registry(self.deployed, rows)
        self.write_plist({"kind": "interval", "seconds": 300})
        self.assertFalse(self.converge(apply=False)["clean"])

    def test_never_installed_job_is_jobctls_pending_install_not_drift(self):
        self.aligned()
        os.remove(os.path.join(self.agents, ID + ".plist"))
        self.write_registry(self.deployed, [])
        report = self.converge()
        self.assertTrue(report["clean"])
        self.assertIsNone(report["jobs"][ID]["plist_s"])
        self.assertEqual(self.synced, 0)

    def test_retired_and_offhost_rows_are_skipped(self):
        self.write_registry(self.source, [row(state="retired"), row(id="x", host="other")])
        self.write_registry(self.deployed, [])
        self.assertEqual(self.converge()["jobs"], {})


class Migrate(unittest.TestCase):
    NOTE = ("canary d. Temporary. [2026-10-15: interval->calendar conversion - launchd "
            "StartInterval dead session-wide on this host (nexus-office#284); "
            "cadence preserved exactly.]")

    def test_prose_cadence_becomes_fields(self):
        old = {ID: {"kind": "interval", "seconds": 21600}}
        out, moved = converger.migrate_rows([row(cadence_s=None, note=self.NOTE)], old)
        self.assertEqual(moved, [ID])
        r = out[0]
        self.assertEqual(r["note"], "canary d. Temporary.")
        self.assertEqual(r["cadence_s"], 21600)
        self.assertEqual(r["migration"], {"from": "interval", "to": "calendar",
                                          "at": "2026-10-15", "ref": "nexus-office#284",
                                          "reason": "launchd-startinterval-dead"})

    def test_prose_kept_when_cadence_was_not_preserved(self):
        old = {ID: {"kind": "interval", "seconds": 300}}
        out, moved = converger.migrate_rows([row(cadence_s=None, note=self.NOTE)], old)
        self.assertEqual(moved, [])
        self.assertEqual(out[0]["note"], self.NOTE)

    def test_idempotent(self):
        old = {ID: {"kind": "interval", "seconds": 21600}}
        once, _ = converger.migrate_rows([row(cadence_s=None, note=self.NOTE)], old)
        twice, moved = converger.migrate_rows(once, old)
        self.assertEqual(once, twice)
        self.assertEqual(moved, [])


if __name__ == "__main__":
    unittest.main()
