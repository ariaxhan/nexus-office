from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))
from sources import delivery  # noqa: E402
import automation  # noqa: E402


class FakeProducer:
    class Refusal(ValueError):
        pass

    @staticmethod
    def policy_for(config, pr, repo):
        policy = dict(config["repositories"][repo]["default"])
        policy.update(config["repositories"][repo].get("pull_requests", {}).get(str(pr), {}))
        return policy, "c" * 64

    @staticmethod
    def new_state(repo, pr, head, policy, policy_hash, issues):
        return {"repo": repo, "pr": pr, "head_sha": head, "policy_hash": policy_hash,
                "route": policy["route"], "linked_issues": issues, "receipts": {},
                "phase": "bound", "terminal": False, "closed_issues": []}

    @staticmethod
    def production_receipt(state, policy, kind):
        root = pathlib.Path(policy["receipt_root"])
        path = root / state["repo"].replace("/", "__") / str(state["pr"]) / state["head_sha"] / f"{kind}.json"
        try:
            value = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise FakeProducer.Refusal("canonical proof is unavailable") from exc
        if value.get("provenance") != "trusted-test-producer":
            raise FakeProducer.Refusal("canonical proof has untrusted provenance")
        return value

    @staticmethod
    def apply_receipt(state, policy, kind, receipt):
        state["receipts"][kind] = receipt
        route = state["route"]
        phases = {"preview": "preview_verified", "merged": "merged", "staged": "staged",
                  "release": "live_verified", "buzz": "released",
                  "proposal": "proposal_verified", "composite": "source_verified"}
        state["phase"] = phases[kind]
        state["terminal"] = kind in ({"proposal"} if route == "proposal" else
                                     {"composite"} if route == "source" else {"buzz"})
        return state

    @staticmethod
    def validate_persisted_state(state, policy):
        replay = FakeProducer.new_state(state["repo"], state["pr"], state["head_sha"],
                                        policy, state["policy_hash"], state.get("linked_issues") or [])
        missing = False
        for kind in delivery.RECEIPT_ORDER[state["route"]]:
            if kind not in state.get("receipts", {}):
                missing = True
                continue
            if missing:
                raise FakeProducer.Refusal("persisted delivery transitions are out of order")
            canonical = FakeProducer.production_receipt(replay, policy, kind)
            replay = FakeProducer.apply_receipt(replay, policy, kind, canonical)
        if any(replay.get(key) != state.get(key) for key in ("phase", "terminal", "receipts")):
            raise FakeProducer.Refusal("persisted delivery state does not match canonical proofs")
        return replay

    @staticmethod
    def next_actions(state, policy):
        if state["terminal"]:
            return [{"action": "close_issue"}]
        order = delivery.RECEIPT_ORDER[state["route"]]
        missing = next(kind for kind in order if kind not in state["receipts"])
        return [{"action": missing}]


def base_state(**updates):
    value = {"version": 1, "repo": "Thinking-Brain-School/tbs-www", "pr": 10,
             "head_sha": "a" * 40, "policy_hash": "c" * 64, "route": "release",
             "receipts": {}, "phase": "bound", "terminal": False,
             "linked_issues": [11], "closed_issues": []}
    value.update(updates)
    return value


def proof(state, kind, **updates):
    value = {"repo": state["repo"], "pr": state["pr"], "head_sha": state["head_sha"],
             "policy_hash": state["policy_hash"], "outcome": "PASS",
             "provenance": "trusted-test-producer", "kind": kind}
    value.update(updates)
    return value


class DeliverySourceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.directory = self.root / delivery.RELATIVE
        self.states = self.directory / "states"
        self.receipts = self.root / "trusted-receipts"
        self.directory.mkdir(parents=True)
        self.config = {"actions_enabled": False, "repositories": {"Thinking-Brain-School/tbs-www": {
            "default": {"route": "release", "receipt_root": str(self.receipts)},
            "pull_requests": {"85": {"route": "proposal"}}}}}
        self.env = mock.patch.dict(os.environ, {"OFFICE_RUNTIME_ROOT": str(self.root)})
        self.env.start(); self.addCleanup(self.env.stop)
        self.producer = mock.patch.object(delivery, "_producer",
                                          return_value=(FakeProducer, self.config))
        self.producer.start(); self.addCleanup(self.producer.stop)

    def state_path(self, state):
        return (self.states / state["repo"].replace("/", "__") / str(state["pr"]) /
                f"{state['head_sha']}.json")

    def write_state(self, state):
        path = self.state_path(state)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state))
        return path

    def write_proof(self, state, kind, value):
        path = (self.receipts / state["repo"].replace("/", "__") / str(state["pr"]) /
                state["head_sha"] / f"{kind}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def write_conveyor(self, entries=None, active=None, heartbeat=None):
        value = {"schema": delivery.CONVEYOR_SCHEMA, "sequence": 1,
                 "generated_at": "2026-09-03T09:00:00Z", "active_run": active,
                 "last_run": {"finished_at": "2026-09-03T09:00:00Z", "status": "PASS"},
                 "entries": entries or [], "events": []}
        if active is not None and heartbeat is not None:
            active["heartbeat_at"] = heartbeat
        (self.directory / "conveyor.json").write_text(json.dumps(value))

    def entry(self, state, **updates):
        value = {"repo": state["repo"], "pr": state["pr"], "head_sha": state["head_sha"],
                 "phase": state["phase"], "terminal": state["terminal"], "running": False,
                 "next": [], "state_path": str(self.state_path(state)),
                 "updated_at": "2026-09-03T09:00:00Z", "linked_issues": [11],
                 "actuated": False, "terminal_proof": None, "proof_refusal": None,
                 "action_proof": None}
        value.update(updates)
        return value

    def terminal(self, state):
        for kind in delivery.RECEIPT_ORDER[state["route"]]:
            fields = ({"merged_sha": "b" * 40} if kind == "merged" else
                      {"sha": "b" * 40} if kind == "staged" else
                      {"sha": "b" * 40, "target": "https://live.invalid"} if kind == "release" else
                      {"event_id": "event-1", "message_sha256": "d" * 64,
                       "sha": "b" * 40, "target": "https://live.invalid"} if kind == "buzz" else {})
            row = proof(state, kind, **fields)
            state["receipts"][kind] = row
            self.write_proof(state, kind, row)
        state["phase"] = "released"
        state["terminal"] = True

    def test_fabricated_terminal_without_canonical_proofs_is_quarantined(self):
        state = base_state()
        for kind in delivery.RECEIPT_ORDER["release"]:
            state["receipts"][kind] = proof(state, kind, invented=True)
        state.update(phase="released", terminal=True)
        self.write_state(state); self.write_conveyor([self.entry(state)])
        data = delivery.read()
        self.assertEqual(data["state"], "blocked")
        self.assertEqual(data["rows"], [])
        self.assertIn("canonical proof is unavailable", data["quarantined"][0]["problem"])

    def test_saved_receipt_must_equal_canonical_producer_value(self):
        state = base_state()
        saved = proof(state, "preview", deployment="forged")
        state["receipts"]["preview"] = saved
        state["phase"] = "preview_verified"
        self.write_proof(state, "preview", proof(state, "preview", deployment="real"))
        self.write_state(state); self.write_conveyor([self.entry(state)])
        self.assertIn("does not match canonical proofs", delivery.read()["quarantined"][0]["problem"])

    def test_cross_head_state_path_is_quarantined(self):
        state = base_state()
        path = self.states / state["repo"].replace("/", "__") / str(state["pr"]) / f"{'b' * 40}.json"
        path.parent.mkdir(parents=True); path.write_text(json.dumps(state))
        self.write_conveyor([self.entry(state, state_path=str(path))])
        self.assertIn("canonical path", delivery.read()["quarantined"][0]["problem"])

    def test_terminal_replays_real_canonical_proofs(self):
        state = base_state(); self.terminal(state)
        self.write_state(state); self.write_conveyor([
            self.entry(state, terminal_proof=delivery._terminal_projection(state))])
        row = delivery.read()["rows"][0]
        self.assertTrue(row["terminal"])
        self.assertEqual(row["history"][-3:], ["live_verified", "notified", "terminal"])

    def test_missing_or_cross_head_terminal_and_buzz_projection_is_quarantined(self):
        state = base_state(); self.terminal(state); self.write_state(state)
        self.write_conveyor([self.entry(state, terminal_proof=None)])
        self.assertIn("terminal projection", delivery.read()["quarantined"][0]["problem"])
        forged = delivery._terminal_projection(state)
        forged["head_sha"] = "f" * 40
        forged["buzz"]["message_sha256"] = "0" * 64
        self.write_conveyor([self.entry(state, terminal_proof=forged)])
        self.assertIn("terminal projection", delivery.read()["quarantined"][0]["problem"])

    def test_disabled_actuator_with_queued_work_is_unhealthy(self):
        state = base_state(); self.write_state(state)
        self.write_conveyor([self.entry(state)])
        data = delivery.read()
        self.assertEqual(data["state"], "disabled")
        self.assertFalse(data["actuation_enabled"])
        self.assertEqual(delivery.card(data)["needs"], 1)
        view = automation._delivery_view(data)
        self.assertEqual(view["blocked"][0]["repo"], "delivery-actuator")
        self.assertIn("disables actions", view["blocked"][0]["problems"][0])

    def test_enabled_actuator_allows_valid_queue_health(self):
        self.config["actions_enabled"] = True
        state = base_state(); self.write_state(state)
        self.write_conveyor([self.entry(state)])
        self.assertEqual(delivery.read()["state"], "ok")

    def test_duplicate_identity_and_bad_event_sequence_are_unreachable(self):
        state = base_state(); self.write_state(state); entry = self.entry(state)
        self.write_conveyor([entry, dict(entry)])
        self.assertEqual(delivery.read()["state"], "unreachable")
        self.write_conveyor([entry])
        queue = json.loads((self.directory / "conveyor.json").read_text())
        queue["events"] = [{"sequence": 2, "at": "2026-09-03T09:00:00Z", "type": "later"},
                           {"sequence": 1, "at": "2026-09-03T09:00:01Z", "type": "earlier"}]
        (self.directory / "conveyor.json").write_text(json.dumps(queue))
        self.assertEqual(delivery.read()["state"], "unreachable")

    def test_missing_producer_is_unreachable_and_unhealthy(self):
        self.producer.stop()
        self.write_conveyor()
        data = delivery.read()
        self.assertEqual(data["state"], "unreachable")
        self.assertEqual(delivery.card(data)["facts"][0]["tone"], "bad")

    def test_unconfigured_and_never_are_unhealthy(self):
        with mock.patch.dict(os.environ, {"OFFICE_RUNTIME_ROOT": ""}):
            unconfigured = delivery.read()
        empty = self.root / "empty"; empty.mkdir()
        with mock.patch.dict(os.environ, {"OFFICE_RUNTIME_ROOT": str(empty)}):
            never = delivery.read()
        self.assertEqual((unconfigured["state"], never["state"]), ("unconfigured", "never"))
        self.assertEqual((delivery.card(unconfigured)["needs"], delivery.card(never)["needs"]), (1, 1))

    def test_idle_unfinished_state_is_queued_not_running(self):
        state = base_state(); self.write_state(state)
        entry = self.entry(state)
        self.write_conveyor([entry], active=None)
        data = delivery.read()
        self.assertEqual(data["running"], [])
        self.assertEqual([row["pr"] for row in data["queued"]], [10])

    def test_running_flag_without_exact_active_run_is_unreachable(self):
        state = base_state(); self.write_state(state)
        self.write_conveyor([self.entry(state, running=True)], active=None)
        self.assertEqual(delivery.read()["state"], "unreachable")

    def test_fresh_exact_active_identity_is_running(self):
        state = base_state(); self.write_state(state)
        active = {"id": "run-1", "status": "running", "started_at": "2026-09-03T09:00:00Z",
                  "current_repo": state["repo"], "current_pr": state["pr"],
                  "current_head_sha": state["head_sha"]}
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        self.write_conveyor([self.entry(state, running=True)], active, now)
        data = delivery.read()
        self.assertEqual([row["pr"] for row in data["running"]], [10])
        self.assertEqual(data["queued"], [])

    def test_stale_active_heartbeat_makes_conveyor_unreachable(self):
        state = base_state(); self.write_state(state)
        active = {"id": "run-1", "status": "running", "started_at": "2020-01-01T00:00:00Z",
                  "current_repo": state["repo"], "current_pr": state["pr"],
                  "current_head_sha": state["head_sha"]}
        self.write_conveyor([self.entry(state)], active, "2020-01-01T00:00:00Z")
        self.assertEqual(delivery.read()["state"], "unreachable")

    def test_automation_exposes_producer_order_and_bad_health(self):
        rows = [{"repo": "a/old", "pr": 4, "terminal": True, "blocked": False,
                 "at": "2026-09-01T00:00:00Z"},
                {"repo": "a/new", "pr": 5, "terminal": True, "blocked": False,
                 "at": "2026-09-03T00:00:00Z"}]
        running = [{"repo": "a/run", "pr": 1, "terminal": False, "blocked": False}]
        queued = [{"repo": "a/next", "pr": 9, "terminal": False, "blocked": False}]
        page = automation.build({}, [], {"pipeline": {"state": "ok"}, "webhook": {},
            "delivery": {"state": "blocked", "rows": rows, "running": running,
                         "queued": queued, "quarantined": [{"problem": "forged"}]}}, {})
        conveyor = page["delivery"]
        self.assertEqual(conveyor["running_now"][0]["pr"], 1)
        self.assertEqual(conveyor["next_up"][0]["pr"], 9)
        self.assertEqual(conveyor["blocked"][0]["problems"], ["forged"])
        self.assertEqual([row["pr"] for row in conveyor["completed_recently"]], [5, 4])
        self.assertEqual(conveyor["pipeline_health"], "blocked")


if __name__ == "__main__":
    unittest.main()
