"""A dead orphan lease may lose ownership without losing anyone's checkout bytes."""

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from nexus import lease, tower
from nexus.ledger import Ledger


class OrphanLeaseRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        for args in (("init", "-q"), ("config", "user.email", "test@example.invalid"),
                     ("config", "user.name", "Test")):
            subprocess.run(("git", *args), cwd=self.repo, check=True)
        (self.repo / "human.txt").write_text("before\n")
        subprocess.run(("git", "add", "."), cwd=self.repo, check=True)
        subprocess.run(("git", "commit", "-qm", "base"), cwd=self.repo, check=True)
        (self.repo / "human.txt").write_text("human edit\n")
        self.led = Ledger(str(Path(self.tmp.name) / "ledger.sqlite"))
        self.addCleanup(self.led.close)
        self.head = subprocess.check_output(("git", "rev-parse", "HEAD"), cwd=self.repo, text=True).strip()

    def record(self, pid):
        row = dict(flight="flt_missing", pid=pid, expires=time.time() - 1,
                   head=self.head, baseline=lease.dirty(str(self.repo)))
        Path(lease.path(str(self.repo))).write_text(json.dumps(row))
        return row

    def test_dead_orphan_releases_ownership_and_preserves_dirty_file(self):
        self.record(2147483647)
        with patch.object(lease, "indexed", return_value=[str(self.repo)]), \
             patch.object(lease, "lane_lock", return_value=(0, "")):
            self.assertEqual(tower._reconcile_orphan_leases(self.led, time.time()), 1)
        self.assertFalse(Path(lease.path(str(self.repo))).exists())
        self.assertEqual((self.repo / "human.txt").read_text(), "human edit\n")
        events = self.led.events(kind="work.orphan_lease")
        self.assertEqual(len(events), 1)
        self.assertEqual(json.loads(events[0]["payload"])["outcome"],
                         "ownership_released_files_preserved")

    def test_live_pid_is_not_released_even_if_expired(self):
        self.record(os.getpid())
        with patch.object(lease, "indexed", return_value=[str(self.repo)]):
            self.assertEqual(tower._reconcile_orphan_leases(self.led, time.time()), 0)
        self.assertTrue(Path(lease.path(str(self.repo))).exists())


if __name__ == "__main__":
    unittest.main()
