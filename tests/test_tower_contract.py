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
        # a legacy row written before terminal evidence existed; the guard now refuses this write
        self.led.conn.execute("UPDATE tasks SET state='done', decided_by='old exit-code inference' WHERE id=?",
                              (self.task["id"],))
        done = self.led.conn.execute("SELECT * FROM tasks WHERE id=?", (self.task["id"],)).fetchone()
        self.assertEqual("reopened", work._run_task(self.led, self.entry, done))
        self.assertEqual([], self.closed)
        newest = self.led.conn.execute("SELECT state FROM tasks WHERE dedupe_key=? ORDER BY created_at DESC LIMIT 1",
                                       (self.task["dedupe_key"],)).fetchone()[0]
        self.assertEqual("accepted", newest)


class TowerYield(unittest.TestCase):
    """2026-09-14 ledger: waits counted as failures, review re-run on unchanged heads, p0 behind stale waits."""
    github = test_work.WorkTests.github

    def setUp(self):
        test_work.WorkTests.setUp(self)
        self.entry["repo"] = "thinking-brain-school/tbs-landing"
        self.issues = [dict(number=60, title="copy", state="open", body=BLOCK.replace(
            '["thinking-brain-school/tbs-landing#66"]', "[]"), labels=[{"name": "ready"}])]
        self.open_pr = ""
        self.pr = dict(headRefName="aria/issue-60", headRefOid="h1", baseRefName="main", labels=[], files=[],
                       mergeCommit={"oid": "m1"})
        github = self.github

        def gh(argv, **kw):
            if argv[:3] == ["gh", "pr", "list"]:
                return subprocess.CompletedProcess(argv, 0, self.open_pr, "")
            if argv[:3] == ["gh", "pr", "view"]:
                return subprocess.CompletedProcess(argv, 0, json.dumps(self.pr), "")
            if argv[:2] == ["gh", "pr"] or argv[:3] == ["gh", "issue", "comment"]:
                return subprocess.CompletedProcess(argv, 1 if argv[2] == "merge" else 0, "https://c/1", "")
            return github(argv, **kw)
        patch("nexus.work.subprocess.run", side_effect=gh).start()
        work.discover(self.led, self.entry)
        self.task = self.led.tasks()[0]
        self.token = work._lane.set(work.TOWER_LABEL)
        self.addCleanup(lambda: work._lane.reset(self.token))

    def test_lane_lock_is_a_bounded_wait_not_a_failure(self):
        from nexus import lease
        with patch("nexus.executor.fly", side_effect=lease.Owned("lane_lock:LANE HELD: tbs@main")):
            self.assertEqual("pending", work.tower_execute(self.led, self.entry, self.task))
        waiting = work.latest(self.led, "work.pending", self.task["id"])
        self.assertTrue(waiting["reason"].startswith("wait: lane_lock"))
        self.assertLessEqual(waiting["next_retry"], work.time.time() + work.WAIT_S + 1)
        self.assertEqual([], self.led.events(kind="work.failure"))

    def test_held_push_failure_is_a_wait(self):
        from nexus import landing
        with patch("nexus.executor.fly", side_effect=landing.LandingError("held_push_failed", "aria/held/f")):
            self.assertEqual("pending", work.tower_execute(self.led, self.entry, self.task))
        self.assertIn("wait: held_push_failed", work.latest(self.led, "work.pending", self.task["id"])["reason"])
        self.assertEqual([], self.led.events(kind="work.failure"))

    def test_short_budget_never_starts_an_executor(self):
        token = work._deadline.set(work.time.monotonic() + 30)
        try:
            with patch("nexus.executor.fly") as fly:
                self.assertEqual("backoff", work.tower_execute(self.led, self.entry, self.task))
        finally:
            work._deadline.reset(token)
        fly.assert_not_called()
        self.assertEqual([], self.led.flights())

    def test_open_pr_is_reviewed_not_rebuilt(self):
        self.open_pr = "https://github.com/x/pull/9\n"
        with patch("nexus.executor.fly") as fly, patch("nexus.work.tower_review", return_value="pending") as review:
            self.assertEqual("pending", work.tower_execute(self.led, self.entry, self.task))
        fly.assert_not_called()
        review.assert_called_once_with(self.led, unittest.mock.ANY, self.task, "https://github.com/x/pull/9")

    def test_review_runs_once_per_head(self):
        patch("nexus.tower.land_write_flight").start()
        patch("nexus.work._sensitive_hold", return_value=None).start()
        with patch("nexus.executor.review", return_value=("FAIL", "bug")) as review:
            work.tower_review(self.led, self.entry, self.task, "https://pr/9")
            work.tower_review(self.led, self.entry, self.task, "https://pr/9")
            self.assertEqual(1, review.call_count)  # unchanged head: cached verdict
            self.pr["headRefOid"] = "h2"
            work.tower_review(self.led, self.entry, self.task, "https://pr/9")
            self.assertEqual(2, review.call_count)  # new head: fresh review
        cached = [e for e in self.led.events(kind="work.review") if json.loads(e["payload"])["cached"]]
        self.assertEqual(1, len(cached))

    def test_p0_preempts_a_wait_recorded_before_it_was_p0(self):
        fid = work.claim(self.led, self.entry["repo"], 60, os.getpid(), runner=True)
        work.pending(self.led, fid, {"reason": "not_done", "retry_at": work.time.time() + 3600})
        self.assertGreater(work.next_retry(self.led, self.task), work.time.time())
        self.issues[0]["labels"].append({"name": "p0"})
        work.discover(self.led, self.entry)
        self.assertEqual(0, work.next_retry(self.led, self.task))
        self.assertTrue(work.has_p0(self.led, self.entry["repo"]))
        fid = work.claim(self.led, self.entry["repo"], 60, os.getpid(), runner=True)
        work.pending(self.led, fid, {"reason": "not_done", "retry_at": work.time.time() + 3600})
        self.assertGreater(work.next_retry(self.led, self.task), work.time.time())  # its own p0 wait holds


if __name__ == "__main__":
    unittest.main()
