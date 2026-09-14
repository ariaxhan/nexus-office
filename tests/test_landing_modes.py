"""Tower v2 in-place landing: human bytes survive, nothing stays local-only.

A bare repo is origin; a clone is the canonical checkout. Real git, real pushes.
"""

import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nexus import landing, lease, tower  # noqa: E402
from nexus.ledger import Ledger  # noqa: E402

landing.GIT_LOCK = "/nonexistent"  # fixtures stay out of the vault mutex
lease.LANE_LOCK = os.environ["NEXUS_LANE_LOCK"] = ""  # and out of the real tbs lane locks


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


class Case(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nexus-modes-")
        self.origin = os.path.join(self.dir, "origin.git")
        self.repo = os.path.join(self.dir, "repo")
        git(self.dir, "init", "-q", "--bare", "-b", "main", self.origin)
        git(self.dir, "clone", "-q", self.origin, self.repo)
        for k, v in (("user.name", "t"), ("user.email", "t@t")):
            git(self.repo, "config", k, v)
        self.write("README", "hi\n")
        self.write("human.txt", "base\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "base")
        git(self.repo, "push", "-q", "origin", "main")
        self.comments = []

    def write(self, rel, text):
        with open(os.path.join(self.repo, rel), "w") as f:
            f.write(text)

    def read(self, rel):
        with open(os.path.join(self.repo, rel)) as f:
            return f.read()

    def comment(self, body):
        self.comments.append(body)
        return f"https://example/comment/{len(self.comments)}"

    def remote(self, branch):
        return subprocess.run(["git", "ls-remote", self.origin, f"refs/heads/{branch}"],
                              capture_output=True, text=True).stdout.split()[:1]

    def test_t3_flight_edits_baseline_dirty_path_holds_and_keeps_human_bytes(self):
        self.write("human.txt", "human edit\n")
        rec = lease.acquire(self.repo, "main", "f3", os.getpid(), 600)
        self.write("human.txt", "flight edit on top\n")
        self.write("new.txt", "flight\n")
        result = landing.direct(self.repo, rec, "t3", self.comment)
        self.assertEqual(result["state"], "HELD")
        self.assertEqual(result["reason"], "collision")
        self.assertEqual(self.remote("aria/held/f3"), [result["sha"]])
        self.assertEqual(self.read("human.txt"), "flight edit on top\n")  # not restored: may be human's
        self.assertFalse(os.path.exists(os.path.join(self.repo, "new.txt")))  # flight-only path restored
        self.assertTrue(landing.terminal(self.repo, result))

    def test_t6_killed_executor_recovers_to_held_and_releases(self):
        script = ("import os,sys,time; sys.path.insert(0,%r); from nexus import lease, landing;"
                  "landing.GIT_LOCK='/nonexistent';"
                  "lease.acquire(%r,'main','f6',os.getpid(),600);"
                  "open(os.path.join(%r,'wip.txt'),'w').write('half\\n');"
                  "print('ready',flush=True); time.sleep(60)") % (str(ROOT), self.repo, self.repo)
        proc = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
        self.assertEqual(proc.stdout.readline().strip(), "ready")
        proc.send_signal(signal.SIGKILL)
        proc.wait()
        with self.assertRaises(lease.Owned):
            lease.acquire(self.repo, "main", "next", os.getpid(), 600)
        result = lease.recover(self.repo, self.comment)
        self.assertEqual(result["state"], "HELD")
        self.assertEqual(self.remote("aria/held/f6"), [result["sha"]])
        self.assertFalse(os.path.exists(os.path.join(self.repo, "wip.txt")))
        self.assertIsNone(lease.read(self.repo))
        self.assertEqual(git(self.repo, "show", f"{result['sha']}:wip.txt"), "half")

    def test_t6b_tower_reconcile_alone_recovers_vanished_work_flight(self):
        from nexus import work
        led = Ledger(os.path.join(self.dir, "ledger.sqlite"))
        work.capture(led, "a/b", {"number": 7, "title": "t", "labels": []})
        fid = work.claim(led, "a/b", 7, os.getpid(), runner=True)
        led.event("work.executing", fid, {"repo": "a/b", "issue": 7, "path": self.repo}, "work")
        script = ("import os,sys,time; sys.path.insert(0,%r); from nexus import lease, landing;"
                  "landing.GIT_LOCK='/nonexistent';"
                  "lease.acquire(%r,'main',%r,os.getpid(),600);"
                  "open(os.path.join(%r,'wip.txt'),'w').write('half\\n');"
                  "print('ready',flush=True); time.sleep(60)") % (str(ROOT), self.repo, fid, self.repo)
        proc = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
        self.assertEqual(proc.stdout.readline().strip(), "ready")
        led.conn.execute("UPDATE flights SET pid=? WHERE id=?", (proc.pid, fid))
        led.conn.commit()
        proc.send_signal(signal.SIGKILL)
        proc.wait()
        now = time.time()
        self.write("human.txt", "unlocked human edit\n")  # after the flight died: never flight work
        os.utime(os.path.join(self.repo, "human.txt"), (now + 5, now + 5))
        report = tower.tick(led, now=now, root=os.path.join(self.dir, "flights"),
                            comment_for=lambda repo, number: self.comment)
        self.assertEqual(report["vanished"], 1)
        sha = self.remote(f"aria/held/{fid}")[0]
        self.assertEqual(git(self.repo, "show", f"{sha}:wip.txt"), "half")
        self.assertEqual(git(self.repo, "show", f"{sha}:human.txt"), "base")
        self.assertEqual(self.read("human.txt"), "unlocked human edit\n")
        self.assertFalse(os.path.exists(os.path.join(self.repo, "wip.txt")))
        self.assertIsNone(lease.read(self.repo))
        self.assertEqual(led.flight(fid)["state"], "failed")
        self.assertEqual(len(self.comments), 1)
        led.close()

    def test_t8_review_pushes_branch_before_pr_and_never_switches(self):

        self.write("human.txt", "human edit\n")
        rec = lease.acquire(self.repo, "main", "f8", os.getpid(), 600)
        self.write("src.py", "print(1)\n")
        order = []

        def pr_create(head, base, body):
            order.append(("pr", bool(self.remote(head))))
            return "https://example/pr/1"

        result = landing.review(self.repo, rec, "t8", 42, pr_create, self.comment)
        self.assertEqual(order, [("pr", True)])
        self.assertEqual(result["branch"], "aria/issue-42")
        self.assertFalse(os.path.exists(os.path.join(self.repo, "src.py")))
        self.assertEqual(self.read("human.txt"), "human edit\n")
        self.assertEqual(git(self.repo, "rev-parse", "--abbrev-ref", "HEAD"), "main")
        self.assertNotIn("checkout", git(self.repo, "reflog", "show", "HEAD"))
        self.assertTrue(landing.terminal(self.repo, result))

    def test_t11_local_only_commit_cannot_finish(self):
        self.write("x.txt", "x\n")
        git(self.repo, "add", "x.txt")
        git(self.repo, "commit", "-qm", "local only")
        sha = git(self.repo, "rev-parse", "HEAD")
        led = Ledger(os.path.join(self.dir, "ledger.sqlite"))
        plan = led.add_plan(name="p", kind="script", schedule={}, inputs={}, outputs=[], budget={},
                            resources=[], resolution_policy={})
        fid = led.create_flight(plan)
        with self.assertRaises(landing.LandingError):
            tower.land_write_flight(led, fid, self.repo, {"state": "LANDED", "flight": fid,
                                                          "sha": sha, "branch": "main"})
        with self.assertRaises(landing.LandingError):
            tower.land_write_flight(led, fid, self.repo, {"state": "HELD", "flight": fid, "sha": sha,
                                                          "branch": "aria/held/x", "comment_url": "u"})
        led.close()

    def test_t13_no_clone_worktree_stash_or_branch_switch_in_new_code(self):
        banned = re.compile(r"\"(clone|worktree|stash|switch)\"|checkout\", \"-[bB]|--autostash")
        for name in ("lease.py", "risk.py", "executor.py"):
            self.assertIsNone(banned.search((ROOT / "nexus" / name).read_text()), name)
        v2 = (ROOT / "nexus" / "landing.py").read_text().split("Tower v2")[1]
        self.assertIsNone(banned.search(v2))


if __name__ == "__main__":
    unittest.main()
