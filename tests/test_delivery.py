from __future__ import annotations

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


def base_state(**updates):
    value = {"version": 1, "repo": "Thinking-Brain-School/tbs-www", "pr": 10,
             "head_sha": "a" * 40, "policy_hash": "p" * 64, "route": "release",
             "receipts": {}, "phase": "bound", "terminal": False,
             "linked_issues": [11], "closed_issues": []}
    value.update(updates)
    return value


def proof(state, **updates):
    value = {"repo": state["repo"], "pr": state["pr"], "head_sha": state["head_sha"],
             "policy_hash": state["policy_hash"], "outcome": "PASS"}
    value.update(updates)
    return value


class DeliverySourceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.directory = self.root / delivery.RELATIVE
        self.directory.mkdir(parents=True)
        self.env = mock.patch.dict(os.environ, {"OFFICE_RUNTIME_ROOT": str(self.root)})
        self.env.start(); self.addCleanup(self.env.stop)

    def write(self, state, name="one.json"):
        path = self.directory / name
        path.write_text(json.dumps(state))
        return path

    def test_merge_is_intermediate_and_visible_as_next_up(self):
        state = base_state(phase="merged")
        state["receipts"]["preview"] = proof(state, deployment_id="dpl", proof_bundle="preview")
        state["receipts"]["merged"] = proof(state, merged_sha="b" * 40)
        self.write(state)
        data = delivery.read()
        row = data["rows"][0]
        self.assertFalse(row["terminal"])
        self.assertEqual(row["next"], "stage")
        self.assertIn("merged", row["history"])

    def test_rollback_is_blocked_not_completed(self):
        state = base_state(phase="live_verified")
        state["receipts"]["release"] = proof(state, outcome="ROLLED_BACK")
        self.write(state)
        row = delivery.read()["rows"][0]
        self.assertTrue(row["blocked"])
        self.assertFalse(row["terminal"])
        self.assertIn("release rolled back", row["problems"])

    def test_terminal_requires_exact_live_pass_and_accepted_buzz(self):
        state = base_state(phase="released", terminal=True)
        state["receipts"] = {
            "preview": proof(state, deployment_id="dpl", proof_bundle="preview"),
            "merged": proof(state, merged_sha="b" * 40),
            "staged": proof(state, sha="b" * 40),
            "release": proof(state, sha="b" * 40, live_receipt="live.json"),
            "buzz": proof(state, accepted=False),
        }
        self.write(state)
        row = delivery.read()["rows"][0]
        self.assertTrue(row["blocked"])
        self.assertFalse(row["terminal"])
        state["receipts"]["buzz"]["accepted"] = True
        self.write(state)
        row = delivery.read()["rows"][0]
        self.assertFalse(row["blocked"])
        self.assertTrue(row["terminal"])
        self.assertEqual(row["history"][-3:], ["live_verified", "notified", "terminal"])

    def test_terminal_receipt_from_another_head_is_blocked(self):
        state = base_state(route="proposal", phase="proposal_verified", terminal=True)
        state["receipts"]["proposal"] = proof(
            state, head_sha="z" * 40, artifact_url="https://preview", proof_bundle="proof")
        self.write(state)
        row = delivery.read()["rows"][0]
        self.assertTrue(row["blocked"])
        self.assertFalse(row["terminal"])

    def test_automation_exposes_the_five_conveyor_views(self):
        rows = [
            {"repo": "a/one", "pr": 1, "next": "stage", "terminal": False, "blocked": False},
            {"repo": "a/two", "pr": 2, "next": "merge", "terminal": False, "blocked": False},
            {"repo": "a/bad", "pr": 3, "terminal": False, "blocked": True},
            {"repo": "a/done", "pr": 4, "terminal": True, "blocked": False},
        ]
        page = automation.build({}, [], {"pipeline": {"state": "ok"}, "webhook": {},
                                          "delivery": {"state": "blocked", "rows": rows}}, {})
        conveyor = page["delivery"]
        self.assertEqual(conveyor["running_now"][0]["pr"], 1)
        self.assertEqual(conveyor["next_up"][0]["pr"], 2)
        self.assertEqual(conveyor["blocked"][0]["pr"], 3)
        self.assertEqual(conveyor["completed_recently"][0]["pr"], 4)
        self.assertEqual(conveyor["pipeline_health"], "blocked")


if __name__ == "__main__":
    unittest.main()
