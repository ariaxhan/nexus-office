"""Tower reconcile recovers and releases a stale checkout lease; a live one is left alone."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nexus import lease, tower  # noqa: E402
from nexus.ledger import Ledger  # noqa: E402

lease.LANE_LOCK = os.environ["NEXUS_LANE_LOCK"] = ""


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


class LeaseReconcile(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nexus-lease-")
        self.repo = os.path.join(self.dir, "repo")
        git(self.dir, "init", "-q", "-b", "main", self.repo)
        for k, v in (("user.name", "t"), ("user.email", "t@t")):
            git(self.repo, "config", k, v)
        with open(os.path.join(self.repo, "README"), "w") as f:
            f.write("hi\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "base")
        self.led = Ledger(os.path.join(self.dir, "ledger.sqlite"))

    def tearDown(self):
        self.led.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def plant(self, pid, expires):
        record = {"flight": "flt_stale", "pid": pid, "expires": expires, "branch": "main",
                  "head": git(self.repo, "rev-parse", "HEAD"), "baseline": {}}
        with open(lease.path(self.repo), "w") as f:
            json.dump(record, f)

    def tick(self):
        return tower.tick(self.led, root=os.path.join(self.dir, "flights"), checkouts=[self.repo])

    def events(self):
        return [e["kind"] for e in self.led.events()]


    def test_dead_pid_lease_is_released_after_one_reconcile(self):
        dead = subprocess.Popen(["true"])
        dead.wait()
        self.plant(dead.pid, time.time() + 3600)
        self.assertEqual(self.tick()["stale_leases"], 1)
        self.assertIsNone(lease.read(self.repo))
        self.assertIn("checkout_lease.recovered", self.events())

    def test_expired_lease_is_released_even_with_a_live_pid(self):
        self.plant(os.getpid(), time.time() - 1)
        self.tick()
        self.assertIsNone(lease.read(self.repo))

    def test_live_unexpired_lease_is_untouched(self):
        self.plant(os.getpid(), time.time() + 3600)
        self.assertEqual(self.tick()["stale_leases"], 0)
        self.assertEqual(lease.read(self.repo)["flight"], "flt_stale")


if __name__ == "__main__":
    unittest.main()
