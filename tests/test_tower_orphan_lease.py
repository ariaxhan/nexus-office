"""A dead orphan lease may lose ownership without losing anyone's checkout bytes."""

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from nexus import landing, lease, tower
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



class UnrunnableClassification(unittest.TestCase):
    """A checkout git cannot read is classified once, not errored every tick."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.led = Ledger(str(Path(self.tmp.name) / "ledger.sqlite"))
        self.addCleanup(self.led.close)
        self.plain = Path(self.tmp.name) / "plain"
        self.plain.mkdir()

    def reconcile(self, *paths):
        with patch.object(lease, "indexed", return_value=[str(p) for p in paths]), \
             patch.dict(os.environ, {"NEXUS_WORK_REGISTRY": ""}):
            return tower._reconcile_orphan_leases(self.led, time.time())

    def test_unreadable_checkout_classifies_once_not_every_tick(self):
        for _ in range(3):
            self.assertEqual(self.reconcile(self.plain), 0)
        un = self.led.events(kind="work.repo_unrunnable")
        self.assertEqual(len(un), 1)
        self.assertEqual(un[0]["subject"], str(self.plain))
        self.assertIn("not a git repository", json.loads(un[0]["payload"])["error"])
        self.assertEqual(self.led.events(kind="work.orphan_lease_error"), [])
        self.assertEqual(self.led.events(kind="work.repo_runnable"), [])

    def test_changed_error_reclassifies(self):
        self.assertEqual(self.reconcile(self.plain), 0)
        with patch.object(lease, "read", side_effect=landing.LandingError("injected_failure", "a different failure")):
            self.assertEqual(self.reconcile(self.plain), 0)
        un = self.led.events(kind="work.repo_unrunnable")
        self.assertEqual(len(un), 2)
        self.assertEqual(json.loads(un[-1]["payload"])["error"], "injected_failure: a different failure")

    def test_classification_clears_and_orphan_releases_once_checkout_is_a_repo(self):
        self.assertEqual(self.reconcile(self.plain), 0)
        self.assertEqual(len(self.led.events(kind="work.repo_unrunnable")), 1)
        for args in (("init", "-q"), ("config", "user.email", "test@example.invalid"),
                     ("config", "user.name", "Test")):
            subprocess.run(("git", *args), cwd=self.plain, check=True)
        (self.plain / "base.txt").write_text("base\n")
        subprocess.run(("git", "add", "."), cwd=self.plain, check=True)
        subprocess.run(("git", "commit", "-qm", "base"), cwd=self.plain, check=True)
        head = subprocess.check_output(("git", "rev-parse", "HEAD"), cwd=self.plain, text=True).strip()
        row = dict(flight="flt_missing", pid=2147483647, expires=time.time() - 1,
                   head=head, baseline=lease.dirty(str(self.plain)))
        Path(lease.path(str(self.plain))).write_text(json.dumps(row))
        with patch.object(lease, "lane_lock", return_value=(0, "")):
            self.assertEqual(self.reconcile(self.plain), 1)
        self.assertEqual(len(self.led.events(kind="work.orphan_lease")), 1)
        self.assertEqual(len(self.led.events(kind="work.repo_runnable")), 1)
        self.assertFalse(Path(lease.path(str(self.plain))).exists())
        # A fresh failure after the clear classifies again, once.
        (self.plain / ".git").rename(Path(self.tmp.name) / "gitdir-moved")
        self.assertEqual(self.reconcile(self.plain), 0)
        self.assertEqual(self.reconcile(self.plain), 0)
        self.assertEqual(len(self.led.events(kind="work.repo_unrunnable")), 2)


if __name__ == "__main__":
    unittest.main()
