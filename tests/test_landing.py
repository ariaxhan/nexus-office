"""Landing for script flights: hangar clone, commit, push, reconcile, fast-forward.

A bare repo stands in for GitHub and a clone of it stands in for the human's
checkout. Nothing here is mocked: real clones, real pushes, real crashes.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nexus import flights as fl  # noqa: E402
from nexus import landing as ld  # noqa: E402
from nexus import tower  # noqa: E402
from nexus.ledger import Ledger  # noqa: E402


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          check=True).stdout.strip()


class LandingCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nexus-landing-")
        self.root = os.path.join(self.dir, "flights")
        self.remote = os.path.join(self.dir, "remote.git")
        self.human = os.path.join(self.dir, "human")
        git(self.dir, "init", "--quiet", "--bare", "-b", "main", self.remote)
        git(self.dir, "clone", "--quiet", self.remote, self.human)
        git(self.human, "config", "user.name", "person")
        git(self.human, "config", "user.email", "p@x")
        with open(os.path.join(self.human, "README"), "w") as f:
            f.write("hi\n")
        git(self.human, "add", "README")
        git(self.human, "commit", "--quiet", "-m", "init")
        git(self.human, "push", "--quiet", "-u", "origin", "main")
        self.led = Ledger(os.path.join(self.dir, "ledger.sqlite"))

    def tearDown(self):
        for flight in self.led.flights():
            fl.kill(flight["pid"])
        self.led.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def plan(self, cmd="date +%s%N > brief.md", outputs=("brief.md",), **budget):
        b = {"timeout_s": 30, "max_retries": 2, "concurrency": 1, **budget}
        return self.led.add_plan(
            "brief", schedule={"every": 0.1},
            inputs={"cmd": cmd, "target": {"repo": self.human, "branch": "main"}},
            outputs=list(outputs), budget=b, resolution_policy={"may_accept": True})

    def tick(self, **kw):
        return tower.tick(self.led, root=self.root, **kw)

    def run_until(self, predicate, timeout=20.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.tick()
            if predicate():
                return True
            time.sleep(0.1)
        return False


    def produced_by_hand(self):
        """A produced flight with a real hangar, built without racing the tick."""
        plan = self.plan()
        flight = self.led.create_flight(plan)
        workspace = fl.workspace_path(self.root, flight)
        hangar = ld.clone_hangar(self.human, "main", workspace)
        with open(os.path.join(hangar, "brief.md"), "w") as f:
            f.write("by hand\n")
        self.led.set_state(flight, "running", expect="queued", workspace=workspace,
                           started_at=time.time())
        self.led.set_state(flight, "produced", expect="running",
                           result={"ok": True, "artifacts": [{"kind": "file", "ref": "repo/brief.md"}],
                                   "error": None, "cost": {}})
        return self.led.flight(flight)

    def landed(self):
        return self.led.flights(states=("landed",))

    def test_output_lands_as_a_commit_and_the_human_tree_fast_forwards(self):
        self.plan()
        self.assertTrue(self.run_until(lambda: self.landed()), "nothing landed")
        flight = self.landed()[0]
        landing = [r for r in self.led.landings() if r["flight_id"] == flight["id"]][0]
        self.assertEqual("applied", landing["state"])
        tip = git(self.dir, "--git-dir", self.remote, "rev-parse", "refs/heads/main")
        self.assertEqual(landing["applied_sha"], tip, "remote tip is not the applied sha")
        self.assertEqual(tip, git(self.human, "rev-parse", "HEAD"), "human tree not fast-forwarded")
        self.assertTrue(os.path.exists(os.path.join(self.human, "brief.md")))
        self.assertEqual("nexus tower", git(self.human, "log", "-1", "--format=%an"))
        self.tick()
        self.assertFalse(os.path.isdir(flight["workspace"]), "hangar outlived the landing")
        self.assertEqual([], self.led.integrity_check())
        self.assertEqual([], self.led.leases())
        task = self.led.task(flight["task_id"])
        self.assertEqual("done", task["state"])

    def test_dirty_human_tree_is_left_alone_but_the_landing_still_applies(self):
        with open(os.path.join(self.human, "README"), "a") as f:
            f.write("uncommitted\n")
        self.plan()
        self.assertTrue(self.run_until(lambda: self.landed()))
        before = git(self.human, "rev-parse", "HEAD")
        landing = self.led.landings(states=("applied",))[0]
        self.assertNotEqual(before, landing["applied_sha"])
        with open(os.path.join(self.human, "README")) as f:
            self.assertTrue(f.read().endswith("uncommitted\n"))
        ev = self.led.events(kind="landing.human_tree")[-1]
        self.assertIn('"dirty"', ev["payload"])

    def test_push_rejected_fails_the_flight_with_a_code_and_the_retry_lands(self):
        # the flight's hangar is cloned from the human tree; the remote moves on
        # underneath it before the push, so the first attempt is non-fast-forward.
        plan = self.plan(cmd="sleep 1.5; echo x > brief.md")
        self.assertTrue(self.run_until(lambda: self.led.flights(states=("running",))))
        time.sleep(0.5)  # hangar cloned; now advance the remote behind its back
        other = os.path.join(self.dir, "other")
        git(self.dir, "clone", "--quiet", self.remote, other)
        git(other, "config", "user.name", "o")
        git(other, "config", "user.email", "o@x")
        with open(os.path.join(other, "OTHER"), "w") as f:
            f.write("moved\n")
        git(other, "add", "OTHER")
        git(other, "commit", "--quiet", "-m", "moved")
        git(other, "push", "--quiet", "origin", "main")

        self.assertTrue(self.run_until(lambda: self.led.flights(plan_id=plan, states=("failed",))))
        failed = self.led.flights(plan_id=plan, states=("failed",))[0]
        self.assertIn('"push_rejected"', failed["result"])
        self.assertTrue(self.led.landings(states=("refused",)))
        self.assertTrue(self.run_until(lambda: self.landed()), "retry never landed")
        tip = git(self.dir, "--git-dir", self.remote, "rev-parse", "refs/heads/main")
        self.assertEqual(tip, self.led.landings(states=("applied",))[0]["applied_sha"])
        self.assertEqual([], self.led.integrity_check())

    def test_crash_after_applying_before_push_is_reconciled_by_pushing_again(self):
        # freeze the flight at `applying` as if tower died between the two writes
        flight = self.produced_by_hand()
        self.led.set_state(flight["id"], "verified", expect="produced")
        hangar = ld.hangar_path(flight["workspace"])
        sha, _ = ld.commit_outputs(hangar, ["brief.md"], "m")
        landing = self.led.create_landing(flight["id"], ld.target_key(self.human, "main"),
                                          expected_sha=sha, state="verified")
        self.led.start_applying(landing, sha)
        # nothing pushed yet; a restart reconciles by pushing from the hangar
        self.tick()
        self.assertEqual("applied", self.led.landing(landing)["state"])
        self.assertEqual(sha, git(self.dir, "--git-dir", self.remote, "rev-parse", "refs/heads/main"))
        self.assertEqual("landed", self.led.flight(flight["id"])["state"])

    def test_crash_after_push_before_applied_is_reconciled_from_the_remote_tip(self):
        flight = self.produced_by_hand()
        self.led.set_state(flight["id"], "verified", expect="produced")
        hangar = ld.hangar_path(flight["workspace"])
        sha, _ = ld.commit_outputs(hangar, ["brief.md"], "m")
        landing = self.led.create_landing(flight["id"], ld.target_key(self.human, "main"),
                                          expected_sha=sha, state="verified")
        self.led.start_applying(landing, sha)
        ld.push(hangar, "main")
        shutil.rmtree(flight["workspace"])  # even the hangar is gone
        self.tick()
        self.assertEqual("applied", self.led.landing(landing)["state"])
        self.assertEqual("landed", self.led.flight(flight["id"])["state"])

    def test_applying_with_no_hangar_and_no_push_is_refused_not_guessed(self):
        flight = self.produced_by_hand()
        self.led.set_state(flight["id"], "verified", expect="produced")
        landing = self.led.create_landing(flight["id"], ld.target_key(self.human, "main"),
                                          expected_sha="0" * 40, state="verified")
        self.led.start_applying(landing, "0" * 40)
        shutil.rmtree(flight["workspace"])
        self.tick()
        self.assertEqual("refused", self.led.landing(landing)["state"])
        self.assertIn('"landing_lost"', self.led.flight(flight["id"])["result"])

    def test_flight_without_a_target_still_ends_at_produced(self):
        self.led.add_plan("plain", schedule={"every": 0.1}, inputs={"cmd": "echo hi > out.txt"},
                          outputs=["out.txt"], budget={"timeout_s": 30, "concurrency": 1},
                          resolution_policy={"may_accept": True})
        self.assertTrue(self.run_until(lambda: self.led.flights(states=("produced",))))
        self.assertEqual([], self.led.landings())


if __name__ == "__main__":
    unittest.main()


class UndeclaredOutputs(LandingCase):
    def test_hangar_artifacts_are_what_git_sees_changed(self):
        self.led.add_plan(
            "brief", schedule={"every": 0.1},
            inputs={"cmd": "mkdir -p deep; echo a > deep/a.md; echo b >> README",
                    "target": {"repo": self.human, "branch": "main"}},
            outputs=[], budget={"timeout_s": 30, "concurrency": 1},
            resolution_policy={"may_accept": True})
        self.assertTrue(self.run_until(lambda: self.landed()))
        flight = self.landed()[0]
        refs = sorted(os.path.basename(a["ref"]) for a in self.led.artifacts(flight["id"]))
        self.assertEqual(["README", "a.md"], refs)
        tip = git(self.dir, "--git-dir", self.remote, "rev-parse", "refs/heads/main")
        self.assertEqual(tip, self.led.landings(states=("applied",))[0]["applied_sha"])
        with open(os.path.join(self.human, "deep", "a.md")) as f:
            self.assertEqual("a\n", f.read())
