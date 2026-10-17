"""Claim-before-edit (#311): two scratch lanes, the real pre-commit guard, expiry and release."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nexus import claims  # noqa: E402

CLI = ROOT / "nexus" / "claims.py"


def git(cwd, *args, env=None):
    return subprocess.run(["git", "-C", str(cwd), *args], text=True, capture_output=True,
                          env=dict(os.environ, **(env or {})))


class Claims(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.a, self.b = self.tmp / "a", self.tmp / "b"
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.a)], check=True)
        for k, v in (("user.name", "Fixture"), ("user.email", "fixture@example.invalid")):
            git(self.a, "config", k, v)
        (self.a / "nexus").mkdir()
        shutil.copy(CLI, self.a / "nexus" / "claims.py")
        for name in ("claimed.py", "unclaimed.py"):
            (self.a / name).write_text("base\n")
        git(self.a, "add", ".")
        self.assertEqual(git(self.a, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "base").returncode, 0)
        self.assertEqual(git(self.a, "worktree", "add", "-q", "--detach", str(self.b)).returncode, 0)
        (self.a / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\necho next-hook-ran >&2\n")
        (self.a / ".git" / "hooks" / "pre-commit").chmod(0o755)
        out = subprocess.run(["bash", str(ROOT / "scripts" / "install-lane-claim-hook.sh"), str(self.a)],
                             text=True, capture_output=True)
        self.assertEqual(out.returncode, 0, out.stderr)

    def cli(self, cwd, *args, lane=None):
        env = dict(os.environ)
        env.pop("TBS_LANE_ID", None)
        if lane:
            env["TBS_LANE_ID"] = lane
        return subprocess.run([sys.executable, str(CLI), *args, "--root", str(cwd)], cwd=cwd, text=True,
                              capture_output=True, env=env)

    def commit(self, cwd, path, lane):
        (cwd / path).write_text(f"edit by {lane}\n")
        git(cwd, "add", path)
        env = {"TBS_LANE_ID": lane} if lane else {}
        out = git(cwd, "commit", "-m", f"{lane} {path}", env=env)
        if out.returncode:
            git(cwd, "reset", "-q", "HEAD", "--", path)
        return out

    def test_two_lanes_refusals_from_the_guard(self):
        made = self.cli(self.a, "claim", "claimed.py", "--work-order", "ariaxhan/nexus-office#1", lane="lane-a")
        self.assertEqual(made.returncode, 0, made.stderr)
        row = json.loads(made.stdout)
        self.assertEqual((row["file"], row["lane_id"], row["work_order"]), ("claimed.py", "lane-a",
                                                                           "ariaxhan/nexus-office#1"))
        other = self.commit(self.b, "claimed.py", "lane-b")
        self.assertNotEqual(other.returncode, 0)
        self.assertIn("REFUSED: claimed.py: claimed by another live lane", other.stderr)
        self.assertIn('"lane_id": "lane-a"', other.stderr)
        unclaimed = self.commit(self.b, "unclaimed.py", "lane-b")
        self.assertNotEqual(unclaimed.returncode, 0)
        self.assertIn("REFUSED: unclaimed.py: no claim for lane lane-b", unclaimed.stderr)
        person = self.commit(self.b, "claimed.py", None)  # a person is bound by others' claims too
        self.assertNotEqual(person.returncode, 0)
        own = self.commit(self.a, "claimed.py", "lane-a")
        self.assertEqual(own.returncode, 0, own.stderr)
        self.assertIn("next-hook-ran", own.stderr)  # the displaced hook still runs
        self.assertIn("lane-claim-guard", (self.a / ".git/hooks/pre-commit").read_text())

    def test_conflicting_claim_refused_and_expiry_releases(self):
        self.assertEqual(self.cli(self.a, "claim", "claimed.py", "--work-order", "1", "--ttl", "1",
                                  lane="lane-a").returncode, 0)
        refused = self.cli(self.b, "claim", "claimed.py", "--work-order", "2", lane="lane-b")
        self.assertEqual(refused.returncode, 3)
        self.assertIn("REFUSED: existing claim", refused.stderr)
        time.sleep(1.1)
        after = self.cli(self.b, "claim", "claimed.py", "--work-order", "2", lane="lane-b")
        self.assertEqual(after.returncode, 0, after.stderr)
        released = [json.loads(l) for l in (claims.store(self.a) / "releases.jsonl").read_text().splitlines()]
        self.assertEqual(released[-1]["release_reason"], "expired")
        done = self.cli(self.b, "release", "--all", lane="lane-b")
        self.assertIn('"release_reason": "clean_finish"', done.stdout)
        self.assertEqual(self.cli(self.a, "list").stdout, "")

    def test_non_git_tree_keeps_claims_beside_it(self):
        tree = self.tmp / "battery"
        tree.mkdir()
        (tree / "run.py").write_text("x\n")
        rows = claims.claim(tree, ["run.py"], "nexus-office#243", "ariaxhan/nexus-office#243", ttl=60)
        self.assertEqual(rows[0]["file"], "run.py")
        self.assertTrue((tree / ".lane-claims").is_dir())
        self.assertEqual(claims.check(tree, ["run.py"], "other")[0][1], "claimed by another live lane")

    def test_path_outside_the_tree_is_an_error(self):
        with self.assertRaises(ValueError):
            claims.claim(self.a, ["../escape.py"], "lane-a", "1")


if __name__ == "__main__":
    unittest.main()
