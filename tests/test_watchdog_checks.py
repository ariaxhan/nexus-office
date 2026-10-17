"""#304 pd-05: the office watchdogs' checks as one program, with no model in detection."""

from datetime import datetime, timezone
import os
import tempfile
import unittest

from nexus import dispatch, faults, watchdog_checks as wc
from nexus.ledger import Ledger

NOW = datetime(2026, 10, 16, 22, 9, tzinfo=timezone.utc).timestamp()


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def liveness(*cycles_ago, wedged=(), base=NOW):
    rows = []
    for minutes in cycles_ago:
        for job in wc.REQUIRED_DAEMONS + wc.BOTH_CYCLES_ONLY + ("com.aria.vaults-mount",):
            bad = job in wedged or job == "com.aria.vaults-mount"
            rows.append({"job": job, "at": iso(base - minutes * 60), "rc": 1 if bad else 0,
                         "state": "wedged" if bad else "alive"})
    return rows


def daemons(**over):
    facts = {"liveness": liveness(24, 9), "ledger_mtime": NOW - 30, "ask_check": {"rc": 0},
             "ollama": {"ps_ok": True, "completed": True, "model": wc.OLLAMA_MODEL},
             "assertions": "pid 42069(caffeinate): [0x1] 00:01:00 PreventUserIdleSystemSleep named: x"}
    facts.update(over)
    return facts


class FakeIO:
    """Observation with no machine: what a seeded fault looks like to the program."""

    def __init__(self, facts):
        self.facts = facts


def verdicts(checks):
    return {c["check"]: (c["verdict"], c["signatures"]) for c in checks}


class EvaluateCase(unittest.TestCase):
    def test_daemons_healthy(self):
        self.assertTrue(all(c["verdict"] == "pass" for c in wc.evaluate_daemons(daemons(), NOW)))

    def test_stale_ledger_with_live_tower(self):
        got = verdicts(wc.evaluate_daemons(daemons(ledger_mtime=NOW - 16 * 60), NOW))
        self.assertEqual(got["tower_ledger_freshness"], ("fault", ["tower-ledger-stale"]))

    def test_stale_ledger_with_dead_tower_is_liveness_not_ledger(self):
        got = verdicts(wc.evaluate_daemons(daemons(ledger_mtime=NOW - 3600,
                                                   liveness=liveness(24, 9, wedged=("com.nexus.tower",))), NOW))
        self.assertEqual(got["tower_ledger_freshness"][0], "pass")
        self.assertEqual(got["liveness_depth"][1], ["daemon-wedged:com.nexus.tower"])

    def test_vaults_mount_ignored_dashboard_needs_both_cycles(self):
        rows = liveness(24) + liveness(9, wedged=("com.aria.tradition-dashboard",))
        self.assertEqual(verdicts(wc.evaluate_daemons(daemons(liveness=rows), NOW))["liveness_depth"][0], "pass")

    def test_ollama_tiers_and_stay_awake(self):
        got = verdicts(wc.evaluate_daemons(daemons(ollama={"ps_ok": True, "completed": True, "model": "llama"},
                                                   assertions=""), NOW))
        self.assertEqual(got["ollama_real_completion"][1], ["ollama-wrong-model"])
        self.assertEqual(got["stay_awake"][1], ["stay-awake-lapsed"])
        got = verdicts(wc.evaluate_daemons(daemons(ollama={"ps_ok": False}), NOW))
        self.assertEqual(got["ollama_real_completion"][1], ["ollama-down"])

    def test_dispatch_thresholds(self):
        receipts = [{"job": "x", "started": iso(NOW - 60 * i), "rc": 78, "ok": False} for i in range(25, 30)]
        receipts.append({"job": wc.CANARY, "started": iso(NOW - 6 * 3600), "rc": 0, "ok": True})
        got = verdicts(wc.evaluate_dispatch({"receipts": receipts, "vault_registry": {"rc": 0, "bytes": 4096},
                                             "health_state": {"last_check": iso(NOW - 76 * 60)}}, NOW))
        self.assertEqual(got["dispatch_breathing"][1], ["rc78-cluster", "receipts-stale"])
        self.assertEqual(got["wedge_canary"][0], "pass")  # 6 h old against a 6 h default cadence
        self.assertEqual(got["health_watch_meta"][1], ["health-watch-stale"])
        self.assertEqual(verdicts(wc.evaluate_dispatch({"receipts": receipts, "vault_registry": {"rc": 124}},
                                                       NOW))["vault_registry_readability"][1],
                         ["vault-registry-unreadable"])

    def test_canary_judged_against_observed_cadence(self):
        receipts = [{"job": wc.CANARY, "started": iso(NOW - h * 3600), "rc": 0} for h in (20, 14.3)]
        self.assertEqual(verdicts(wc.evaluate_dispatch({"receipts": receipts}, NOW))["wedge_canary"][0], "fault")

    def test_comms_code_bug_vs_vault_fault_vs_transient(self):
        def r(job, ago, ok, rc=0, tail=""):
            return {"job": job, "started": iso(NOW - ago), "ok": ok, "rc": rc, "stderr_tail": tail}
        receipts = [r("com.nexus.calendar-mirror", 120, True),
                    r("com.aria.granola-sync", 20 * 3600, True), r("com.aria.granola-sync", 7200, False, 78),
                    r("com.aria.granola-sync", 60, False, 1, "no stored authorization"),
                    r("com.nexus.morning-briefing", 300, False, 1,
                      "Traceback (most recent call last):\nNameError: name 'get_creds' is not defined"),
                    r("com.nexus.evening-reflection", 300, False, 1)]
        got = wc.evaluate_comms({"receipts": receipts}, NOW)
        v = verdicts(got)
        self.assertEqual(v["calendar_mirror_lag"][0], "pass")
        self.assertEqual(v["granola_sync_freshness"][1], ["granola-sync-stale"])
        self.assertEqual(v["code_bug_sweep"][1], ["code-bug:NameError:com.nexus.morning-briefing"])
        self.assertEqual(got[2]["evidence"]["failing"]["com.nexus.evening-reflection"], "transient")

    def test_door(self):
        loop = {"ok": True, "snapshot_at": iso(NOW - 60), "revision": "a"}
        got = verdicts(wc.evaluate_door({"loopback": loop, "phone": {"status": 200, "revision": "b"}}, NOW))
        self.assertEqual(got["phone_path"][1], ["revision_mismatch"])
        got = verdicts(wc.evaluate_door({"loopback": {**loop, "snapshot_at": iso(NOW - 21 * 60)},
                                         "phone": {"status": None}}, NOW))
        self.assertEqual(got["door_loopback"][1], ["door_loopback_failed"])
        self.assertEqual(got["phone_path"][1], ["phone_path_failed"])


class RecordCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(os.path.join(self.tmp.name, "ledger.sqlite"))
        self.orig = dict(wc.OBSERVE)

    def tearDown(self):
        wc.OBSERVE.update(self.orig)
        self.ledger.close()
        self.tmp.cleanup()

    def seed(self, base=NOW, **facts):
        wc.OBSERVE["daemons"] = lambda io, now: io.facts
        return FakeIO(daemons(liveness=liveness(24, 9, base=base), **facts))

    def test_seeded_stale_ledger_opens_fault_record_with_no_model(self):
        state = wc.run(self.ledger, "daemons", io=self.seed(ledger_mtime=NOW - 3600), now=NOW)
        recs = faults.records(self.ledger, state="detected")
        self.assertEqual([(r["signature"], r["source"]) for r in recs],
                         [("tower-ledger-stale", "watchdog:office-daemons")])
        self.assertEqual(state["faults"]["tower-ledger-stale"]["consecutive_checks"], 1)
        self.assertEqual(state["escalated"], [])  # detection never messages anyone

    def test_idempotent_streak_recovery_and_one_probe_per_run(self):
        stale = self.seed(ledger_mtime=NOW - 3600)
        first = wc.run(self.ledger, "daemons", io=stale, now=NOW)
        self.assertTrue(wc.run(self.ledger, "daemons", io=stale, now=NOW + 60)["duplicate"])
        probe = dispatch.get(self.ledger, first["probe"]["record_id"])
        self.assertEqual((probe["queue"], probe["state"], probe["payload"]["probe"]), (wc.PROBE_QUEUE, "filed", 0))
        second = wc.run(self.ledger, "daemons", io=self.seed(NOW + 3600, ledger_mtime=NOW + 3600 - 60), now=NOW + 3600)
        self.assertEqual(second["probe"], {"skipped": "previous probe still open"})
        self.assertEqual(second["faults"], {})
        self.assertEqual([r["state"] for r in faults.records(self.ledger)], ["recovered"])
        third = wc.run(self.ledger, "daemons", io=self.seed(NOW + 7200, ledger_mtime=NOW), now=NOW + 7200)
        self.assertEqual(third["faults"]["tower-ledger-stale"]["consecutive_checks"], 1)
        self.assertEqual(len(faults.records(self.ledger)), 2)  # a new generation, not the old record


class CompareCase(unittest.TestCase):
    def test_muse_shapes_reduce_to_verdicts(self):
        self.assertEqual(wc.muse_verdict({"result": "pass"}), "pass")
        self.assertEqual(wc.muse_verdict({"ok": False}), "fault")
        self.assertEqual(wc.muse_verdict({"status": "fault", "signatures_present": ["x"]}), "fault")
        self.assertEqual(wc.muse_verdict({"result": "fresh — no signature triggered", "signatures_triggered": []}),
                         "pass")
        self.assertEqual(wc.muse_verdict({"result": "no code bugs found; evening-reflection rc=1 silent persists"
                                                    " (transient class, no traceback)"}), "pass")

    def test_compare_skips_are_reported_not_matched(self):
        state = {"watchdog": "dispatch", "occurrence_id": "o",
                 "checks": [wc._check("dispatch_breathing"), wc._skip("health_watch_meta", "vm"),
                            wc._check("wedge_canary", ["canary-wedge-d-fault"])]}
        muse = {"last_run": "t", "checks": {"dispatch_breathing": {"status": "pass", "signatures_present": []},
                                            "health_watch_meta": {"status": "pass"},
                                            "wedge_canary": {"status": "fault", "signatures_present": ["x"]}}}
        out = wc.compare(state, muse)
        self.assertTrue(out["match"])
        self.assertEqual(out["uncompared"], ["health_watch_meta"])


if __name__ == "__main__":
    unittest.main()
