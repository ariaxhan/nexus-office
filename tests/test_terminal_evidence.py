"""Terminal state needs terminal evidence from the exact source and revision."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from nexus import contract, executor, landing, lanes, tower, work
from nexus import terminal
from tests import test_work  # fixture only


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class StaleCheckoutReceipt(unittest.TestCase):
    """A1: a PR merge never moves the canonical checkout; the receipt must check the landed sha."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        origin, self.checkout, other = root / "origin.git", root / "checkout", root / "other"
        git(root, "init", "--quiet", "--bare", "-b", "main", str(origin))
        git(root, "clone", "--quiet", str(origin), str(self.checkout))
        for repo in (self.checkout,):
            git(repo, "config", "user.email", "t@t"), git(repo, "config", "user.name", "t")
        (self.checkout / "a").write_text("a")
        git(self.checkout, "add", "a"), git(self.checkout, "commit", "--quiet", "-m", "a")
        git(self.checkout, "push", "--quiet", "origin", "HEAD:main")
        self.stale = git(self.checkout, "rev-parse", "HEAD")
        git(root, "clone", "--quiet", str(origin), str(other))
        git(other, "config", "user.email", "t@t"), git(other, "config", "user.name", "t")
        (other / "broken").write_text("x")
        git(other, "add", "broken"), git(other, "commit", "--quiet", "-m", "merge")
        git(other, "push", "--quiet", "origin", "HEAD:main")
        self.merged = git(other, "rev-parse", "HEAD")

    def test_check_runs_against_the_landed_sha_not_the_stale_checkout(self):
        check = {"check": "test ! -f broken"}
        done, why = contract.done_receipt({"state": "LANDED", "sha": self.merged}, check, str(self.checkout))
        self.assertFalse(done, why)
        self.assertIn(self.merged, why)
        done, why = contract.done_receipt({"state": "LANDED", "sha": self.stale}, check, str(self.checkout))
        self.assertTrue(done, why)
        self.assertIn(self.stale, why)
        self.assertEqual(1, len(git(self.checkout, "worktree", "list").splitlines()))  # throwaway removed

    def test_unreachable_sha_fails_closed(self):
        done, why = contract.done_receipt({"state": "LANDED", "sha": "0" * 40}, {"check": "true"}, str(self.checkout))
        self.assertFalse(done, why)


class TowerFixture(unittest.TestCase):
    github = test_work.WorkTests.github

    def setUp(self):
        test_work.WorkTests.setUp(self)
        work.discover(self.led, self.entry)
        self.task = self.led.tasks()[0]
        token = work._lane.set(work.TOWER_LABEL)
        self.addCleanup(lambda: work._lane.reset(token))

    def task_state(self, tid):
        return self.led.conn.execute("SELECT state FROM tasks WHERE id=?", (tid,)).fetchone()[0]


class ReopenedAfterClose(TowerFixture):
    """A2: a person reopening a closed issue must bring it back, not be skipped forever."""

    def close(self):
        self.led.set_task_state(self.task["id"], "done", decided_by="tower receipt",
                                evidence=terminal.closed("tower receipt", "closed"))
        self.led.event("work.receipt", self.task["id"], {"sha": "abc"}, "work")
        self.led.event("work.closed", self.task["id"], {"repo": self.entry["repo"]}, "work")

    def test_reopened_ready_issue_is_requeued_as_a_new_generation(self):
        self.close()
        work.discover(self.led, self.entry)  # GitHub lists it open and ready again
        queue, _ = work.selection_queue(self.led, self.entry)
        self.assertEqual(1, len(queue))
        self.assertNotEqual(self.task["id"], queue[0]["id"])
        self.assertEqual("accepted", queue[0]["state"])
        self.assertEqual("reopened after close", work.latest(self.led, "work.generation", queue[0]["id"])["reason"])

    def test_closed_issue_without_a_newer_open_capture_stays_skipped(self):
        self.close()
        self.assertEqual([], work.selection_queue(self.led, self.entry)[0])


class CloseBeforeDone(TowerFixture):
    """A3: a blocked close must not leave the task done with the issue open, nor raise out of the tick."""

    def test_blocked_close_settles_pending_then_closes_without_reflying(self):
        fid = work.claim(self.led, self.entry["repo"], 1, os.getpid(), runner=True)

        def landed(ledger, flight_id, repo, result):
            ledger.set_state(flight_id, "produced"), ledger.set_state(flight_id, "verified", evidence=terminal.landed(result, result["sha"]))
        patch("nexus.tower.land_write_flight", side_effect=landed).start()
        self.led.event("work.review", fid, {"pr": "https://pr/1", "head": "h1", "verdict": "PASS"}, "work")
        self.issues[0]["labels"] = [{"name": "hold"}]  # a person parked it mid-flight
        state = work._settle(self.led, fid, self.entry, self.task, self.issues[0],
                             {"state": "LANDED", "flight": fid, "sha": "abc", "branch": "main",
                              "pr_url": "https://pr/1", "reviewed_head": "h1"}, None)
        self.assertEqual("pending", state)
        self.assertNotEqual("done", self.task_state(self.task["id"]))
        self.assertIn("close_pending", work.latest(self.led, "work.pending", self.task["id"])["reason"])
        self.assertEqual([], self.closed)
        self.assertEqual("held", work._run_task(self.led, self.entry, self.task))  # never an error
        self.issues[0]["labels"] = [{"name": "ready"}]
        with patch("nexus.work.next_retry", return_value=0), patch("nexus.work.tower_execute") as fly:
            self.assertEqual("done", work._run_task(self.led, self.entry, self.task))
        fly.assert_not_called()
        self.assertEqual(1, len(self.closed))
        self.assertEqual("done", self.task_state(self.task["id"]))


class UnverifiedLandingIsHeld(TowerFixture):
    """D1 (#210): a landed commit with no contract check and no review is held for a person, never done."""

    def test_unverified_landing_is_held_not_done_and_not_reflown(self):
        fid = work.claim(self.led, self.entry["repo"], 1, os.getpid(), runner=True)

        def landed(ledger, flight_id, repo, result):
            ledger.set_state(flight_id, "produced"), ledger.set_state(flight_id, "verified", evidence=terminal.landed(result, result["sha"]))
        patch("nexus.tower.land_write_flight", side_effect=landed).start()
        edits = []
        github = self.github
        patch("nexus.work.subprocess.run", side_effect=lambda argv, **k: edits.append(argv) or github(argv, **k)).start()
        state = work._settle(self.led, fid, self.entry, self.task, self.issues[0],
                             {"state": "LANDED", "flight": fid, "sha": "abc", "branch": "main"}, None)
        self.assertEqual("pending", state)
        self.assertNotEqual("done", self.task_state(self.task["id"]))
        self.assertEqual([], self.closed)
        pending = work.latest(self.led, "work.pending", self.task["id"])
        self.assertTrue(pending["hold"].startswith(contract.UNVERIFIED))
        self.assertTrue(any("--add-label" in a and "hold" in a for a in edits))


class DiscoveryKnowsEligibilityActs(TowerFixture):
    """#210 boundary: a held Tower issue is captured but never claimed, even with ready still on it."""

    def test_held_tower_issue_is_captured_but_never_claimed(self):
        self.issues[0]["labels"] = [{"name": "ready"}, {"name": "tower-v2"}, {"name": "hold"}]
        work.discover(self.led, self.entry)
        task = self.led.tasks()[0]
        with patch("nexus.work.tower_execute") as fly, patch("nexus.work.claim") as claim:
            self.assertEqual("held", work._run_task(self.led, self.entry, task))
        fly.assert_not_called(), claim.assert_not_called()

    def test_another_lanes_issue_gets_no_owned_disposition(self):
        self.issues[0]["labels"] = [{"name": "ready"}, {"name": "tower-v2"}]
        work.discover(self.led, self.entry)
        task = self.led.tasks()[0]
        token = work._lane.set(None)
        try:
            work.selection_queue(self.led, self.entry)
        finally:
            work._lane.reset(token)
        self.assertEqual({}, work.latest(self.led, "work.disposition", task["id"]))


class CrashIsNotNoChange(unittest.TestCase):
    """A4: a nonzero executor exit with nothing changed is a failure, never CLOSED no_change."""

    record = {"flight": "flt_x", "head": "h", "branch": "main", "write_set": ["a"]}
    crash = SimpleNamespace(returncode=3)

    def test_whole_repo_lane_crash(self):
        with patch("nexus.lease.flight_paths", return_value=([], [])), \
                patch("nexus.landing._git", return_value=SimpleNamespace(stdout="h\n", returncode=0)):
            result = executor._land({"path": "/r"}, {"labels": []}, self.record, self.crash, None, None,
                                    None, None, None)
        self.assertEqual(("FAILED", "exit_3"), (result["state"], result["reason"]))
        self.assertTrue(landing.terminal("/r", result))

    def test_write_set_lane_crash(self):
        with patch("nexus.lanes.flight_paths", return_value=([], [])):
            result = lanes.land({"path": "/r"}, {"labels": []}, self.record, self.crash,
                                None, None, None, None, None, None)
        self.assertEqual(("FAILED", "exit_3"), (result["state"], result["reason"]))

    def test_clean_exit_without_changes_is_still_no_change(self):
        with patch("nexus.lanes.flight_paths", return_value=([], [])):
            result = lanes.land({"path": "/r"}, {"labels": []}, self.record, SimpleNamespace(returncode=0),
                                None, None, None, None, None, None)
        self.assertEqual(("CLOSED", "no_change"), (result["state"], result["reason"]))


class OnlyLandedIsVerified(TowerFixture):
    """A4: tower marks verified only a LANDED result, and a crash settles as failed with no no-change comment."""

    def test_no_change_is_never_verified(self):
        fid = work.claim(self.led, self.entry["repo"], 1, os.getpid(), runner=True)
        with patch("nexus.landing.require_terminal"):
            tower.land_write_flight(self.led, fid, self.entry["path"], {"state": "CLOSED", "reason": "no_change"})
        self.assertEqual("running", self.led.flight(fid)["state"])

    def test_crash_settles_failed_without_claiming_no_change(self):
        comments = []
        real = self.github

        def gh(argv, **kw):
            if argv[:3] == ["gh", "issue", "comment"]:
                comments.append(argv[-1])
                return subprocess.CompletedProcess(argv, 0, "https://c/1", "")
            if argv[:3] == ["gh", "pr", "list"]:
                return subprocess.CompletedProcess(argv, 0, "", "")
            return real(argv, **kw)
        patch("nexus.work.subprocess.run", side_effect=gh).start()
        crash = {"state": "FAILED", "flight": "f", "reason": "exit_3"}
        with patch("nexus.executor.fly", side_effect=lambda *a, **k: dict(crash, flight=a[2])):
            self.assertEqual("failed", work.tower_execute(self.led, self.entry, self.task))
        self.assertIn("exit_3", work.latest(self.led, "work.failure", self.task["id"])["error"])
        self.assertFalse([c for c in comments if "no_change" in c or "no change" in c])
        self.assertEqual([], self.led.events(kind="work.receipt"))


if __name__ == "__main__":
    unittest.main()
