"""Consumer contract for `tbs.coordinator-decision/v1` (TBS handoff N1). The fixture is canonical:
the TBS producer tests validate against this same file, so the two sides cannot drift."""

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

from nexus import tbs_decision
from nexus.ledger import Ledger

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "tbs" / "coordinator-decision.v1.json").read_text())
SNAP = FIXTURE["snapshot_id"]


def valid(kind):
    return copy.deepcopy(FIXTURE["valid"][kind])


class Consumer(unittest.TestCase):
    def setUp(self):
        self.led = Ledger(str(Path(tempfile.mkdtemp()) / "ledger.sqlite"))

    def tasks(self):
        return self.led.conn.execute("SELECT * FROM tasks").fetchall()

    def test_execute_creates_one_candidate_task_with_the_mapped_fields(self):
        out = tbs_decision.accept(self.led, valid("execute"), SNAP)
        (task,) = self.tasks()
        o = FIXTURE["valid"]["execute"]["outcome"]
        self.assertEqual(out["task"], task["id"])
        self.assertEqual((task["state"], task["origin"], task["dedupe_key"]), ("candidate", "tbs", "tbs:" + o["key"]))
        self.assertEqual((task["title"], task["risk"], task["objective"], task["output"], task["check"], task["autonomy"]),
                         (o["title"], o["risk"], o["objective"], o["output"], o["check"], o["authority"]["mode"]))
        ev = self.led.events(kind="tbs.decision", subject=task["id"])
        self.assertEqual(json.loads(ev[0]["payload"])["snapshot_id"], SNAP)

    def test_the_same_outcome_key_never_makes_a_second_live_task(self):
        first = tbs_decision.accept(self.led, valid("execute"), SNAP)["task"]
        again = tbs_decision.accept(self.led, valid("execute"), SNAP)
        self.assertIsNone(again["task"])
        self.assertEqual(again["duplicate_of"], first)
        self.assertEqual(len(self.tasks()), 1)

    def test_wait_idle_and_escalate_create_no_task(self):
        for kind in ("wait", "idle", "escalate"):
            self.assertIsNone(tbs_decision.accept(self.led, valid(kind), SNAP, plan_id=None)["task"], kind)
        self.assertEqual(self.tasks(), [])

    def test_every_malformed_decision_is_refused_and_creates_nothing(self):
        bad = []
        d = valid("execute"); d["schema"] = "tbs.coordinator-decision/v2"; bad.append(d)
        d = valid("execute"); d["extra"] = 1; bad.append(d)
        d = valid("execute"); d["outcome"]["done"] = True; bad.append(d)
        d = valid("execute"); d["wait"] = FIXTURE["valid"]["wait"]["wait"]; bad.append(d)
        d = valid("execute"); d["snapshot_id"] = "sha256:" + "b" * 64; bad.append(d)
        d = valid("execute"); d["outcome"]["check"] = ""; bad.append(d)
        d = valid("execute"); d["outcome"]["class"] = "lesson"; bad.append(d)
        d = valid("execute"); del d["outcome"]["authority"]; bad.append(d)
        d = valid("execute"); d["outcome"]["authority"] = {"mode": "aria", "source": "x", "grant": "all"}; bad.append(d)
        d = valid("execute"); d["outcome"]["key"] = "no namespace"; bad.append(d)
        d = valid("execute"); d["outcome"]["resources"] = []; bad.append(d)
        d = valid("wait"); d["wait"].pop("wake_at"); bad.append(d)
        d = valid("idle"); d["decision"] = "done"; bad.append(d)
        for b in bad:
            out = tbs_decision.accept(self.led, b, SNAP)
            self.assertIsNotNone(out["refused"], b)
            self.assertIsNone(out["task"])
        self.assertEqual(self.tasks(), [])
        self.assertEqual(len(self.led.events(kind="tbs.decision")), len(bad))

    def test_without_an_enabled_tbs_plan_tower_launches_nothing(self):
        """Shadow mode is the default: no plan, no flight."""
        from nexus import tower
        task = tbs_decision.accept(self.led, valid("execute"), SNAP)["task"]
        tower.accept_tasks(self.led)
        self.assertEqual(self.led.task(task)["state"], "rejected_policy")
        self.assertEqual(self.led.flights(task_id=task), [])

    def test_the_consumer_imports_nothing_from_tbs(self):
        src = Path(tbs_decision.__file__).read_text()
        self.assertNotIn("thinking-brain-school", src.split('"""', 2)[2])
        self.assertFalse([m for m in sys.modules if m.startswith("tbs")])


if __name__ == "__main__":
    unittest.main()
