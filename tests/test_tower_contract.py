"""Every Tower path reads the contract; done needs a receipt; no_change is never done."""

import json
import os
import subprocess
import unittest
from unittest.mock import patch

from nexus import work
from tests import test_work  # fixture only; importing the module keeps its tests out of this file

BLOCK = ('```tbs-contract\ndepends_on: ["thinking-brain-school/tbs-landing#66"]\nwrite_set: ["src/a.jsx"]\n'
         'route: "claude"\ncheck: null\nacceptance: "x"\n```')


class TowerContract(unittest.TestCase):
    github = test_work.WorkTests.github

    def setUp(self):
        test_work.WorkTests.setUp(self)
        self.entry["repo"] = "thinking-brain-school/tbs-landing"
        self.issues = [dict(number=60, title="copy", state="open", body=BLOCK, labels=[{"name": "ready"}])]
        self.dep_state = "open"
        github = self.github

        def gh(argv, **kw):
            if argv[:2] == ["gh", "api"] and argv[2].endswith("/issues/66"):
                return subprocess.CompletedProcess(argv, 0, self.dep_state, "")
            if argv[:3] == ["gh", "issue", "comment"]:
                return subprocess.CompletedProcess(argv, 0, "https://c/1", "")
            return github(argv, **kw)
        patch("nexus.work.subprocess.run", side_effect=gh).start()
        patch("nexus.contract._gh", side_effect=lambda argv: gh(argv)).start()
        self.fly = patch("nexus.work.tower_execute", wraps=None, return_value="done").start()
        work.discover(self.led, self.entry)
        self.task = self.led.tasks()[0]
        self.token = work._lane.set(work.TOWER_LABEL)
        self.addCleanup(lambda: work._lane.reset(self.token))

    def test_open_dependency_is_never_selected_by_fallback_or_wave(self):
        self.assertEqual("blocked", work._run_task(self.led, self.entry, self.task))
        self.fly.assert_not_called()
        with patch("nexus.lanes.read_plan", return_value=[[{"repo": self.entry["repo"], "number": 60,
                                                              "write_set": ["src/a.jsx"], "depends_on": [], "window": "any"}]]), \
                patch("nexus.work.eligible", return_value=[self.entry]):
            self.assertIsNone(work.wave_candidates(self.led, [self.entry], (3, 3)))
        self.dep_state = "closed"
        with patch("nexus.work.next_retry", return_value=0):
            self.assertEqual("done", work._run_task(self.led, self.entry, self.task))
        self.fly.assert_called_once()

    def test_issue_without_contract_is_not_eligible_in_tbs_repos(self):
        self.issues[0]["body"] = "Depends on: #66"
        self.assertEqual("blocked", work._run_task(self.led, self.entry, self.task))
        self.fly.assert_not_called()

    def test_no_change_stays_pending_with_a_comment(self):
        fid = work.claim(self.led, self.entry["repo"], 60, os.getpid(), runner=True)
        patch("nexus.tower.land_write_flight").start()
        state = work._settle(self.led, fid, self.entry, self.task, self.issues[0],
                             {"state": "CLOSED", "flight": fid, "reason": "no_change"}, None)
        self.assertEqual("pending", state)
        self.assertNotEqual("done", self.led.conn.execute("SELECT state FROM tasks WHERE id=?", (self.task["id"],)).fetchone()[0])
        self.assertIn("not_done", work.latest(self.led, "work.pending", self.task["id"])["reason"])
        self.assertEqual([], self.closed)

    def test_done_without_receipt_is_reopened_never_closed(self):
        self.led.set_task_state(self.task["id"], "done", decided_by="old exit-code inference")
        done = self.led.conn.execute("SELECT * FROM tasks WHERE id=?", (self.task["id"],)).fetchone()
        self.assertEqual("reopened", work._run_task(self.led, self.entry, done))
        self.assertEqual([], self.closed)
        newest = self.led.conn.execute("SELECT state FROM tasks WHERE dedupe_key=? ORDER BY created_at DESC LIMIT 1",
                                       (self.task["dedupe_key"],)).fetchone()[0]
        self.assertEqual("accepted", newest)


if __name__ == "__main__":
    unittest.main()
