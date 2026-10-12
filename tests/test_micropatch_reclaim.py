"""A cancelled or killed micro-patch worker leaves no snapshot behind once reconciled."""

import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import micropatch as worker  # noqa: E402

# A worker that takes a snapshot, says where it is, and then hangs until it is stopped.
HOLDER = f"""
import sys, time
sys.path.insert(0, {str(SCRIPTS)!r})
import micropatch as mp
mp.terminate_cleanly()
with mp.snapshot("HEAD", {{}}) as tree:
    print(tree, flush=True)
    time.sleep(120)
"""


class Reclaim(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old, tempfile.tempdir = tempfile.tempdir, self.tmp

    def tearDown(self):
        tempfile.tempdir = self.old
        subprocess.run(["rm", "-rf", self.tmp])

    def stopped(self, sig):
        env = dict(os.environ, TMPDIR=self.tmp)
        proc = subprocess.Popen([sys.executable, "-c", HOLDER], stdout=subprocess.PIPE, text=True, env=env)
        tree = Path(proc.stdout.readline().strip())
        self.assertTrue(tree.is_dir())
        proc.send_signal(sig)
        proc.wait(timeout=30)
        return tree

    def test_sigterm_removes_the_live_snapshot(self):
        tree = self.stopped(signal.SIGTERM)
        self.assertFalse(tree.exists())
        self.assertEqual([], worker.leaked())

    def test_kill_9_is_reclaimed_by_the_next_run(self):
        tree = self.stopped(signal.SIGKILL)
        self.assertTrue(tree.exists())  # a crash cannot clean up after itself
        worker.reclaim()
        self.assertEqual([], worker.leaked())

    def test_live_owner_keeps_its_snapshot(self):
        live = Path(self.tmp, worker.PREFIX + "live")
        live.mkdir()
        (live / worker.OWNER).write_text(str(os.getpid()))
        worker.reclaim()
        self.assertTrue(live.exists())

    def test_unowned_stale_snapshot_is_reclaimed(self):
        stale = Path(self.tmp, worker.PREFIX + "stale")
        stale.mkdir()
        old = time.time() - 7200
        os.utime(stale, (old, old))
        worker.reclaim()
        self.assertFalse(stale.exists())


if __name__ == "__main__":
    unittest.main()
