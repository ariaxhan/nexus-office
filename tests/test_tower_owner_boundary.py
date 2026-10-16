"""A stale work inventory cannot transfer excluded projects to Tower."""

import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from nexus import intake, tower, work
from nexus.ledger import Ledger


class TowerOwnerBoundary(unittest.TestCase):
    def test_configured_enabled_repo_is_still_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            repos = ("ariaxhan/modelmind", "ariaxhan/modelmind-site",
                     "ariaxhan/our4cuts", "ariaxhan/paper-rooms",
                     "persist-os/heycontent-web", "ariaxhan/thinking-brain-school",
                     "Thinking-Brain-School/tbs-www", "ariaxhan/other")
            path.write_text(json.dumps({"repositories": [
                {"repo": repo, "path": directory, "enabled": True,
                 "provider": "local", "account": "test", "risk": {"low": True},
                 "executor": ["true"], "verify": ["true"]} for repo in repos]}))
            rows = {row["repo"]: row for row in work.registry(path)}
            for repo in repos[:-1]:
                row = rows[repo.lower()]
                self.assertFalse(row["enabled"], repo)
                self.assertFalse(work.tower_row(row), repo)
            self.assertTrue(rows["ariaxhan/other"]["enabled"])
            self.assertTrue(work.tower_row(rows["ariaxhan/other"]))

    def test_intake_does_not_turn_tbs_coordinator_claims_into_tower_faults(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(str(Path(directory) / "ledger.sqlite"))
            self.addCleanup(ledger.close)
            when = intake.SINCE + 100
            issue = {"number": 7, "title": "TBS work", "state": "open", "labels": [{"name": "ready"}],
                     "created_at": intake._iso(when), "updated_at": intake._iso(when)}
            with patch("time.time", return_value=when):
                work.capture(ledger, "thinking-brain-school/tbs-www", issue)
            self.assertEqual([], intake.walks(ledger, when + 10,
                             {("thinking-brain-school/tbs-www", 8): {"activity": when, "running": True}}))

    def test_orphan_pass_does_not_touch_excluded_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(str(Path(directory) / "ledger.sqlite"))
            self.addCleanup(ledger.close)
            path = Path(directory) / "tbs"
            path.mkdir()
            row = {"repo": "thinking-brain-school/tbs-www", "path": str(path), "enabled": False}
            with patch.dict(os.environ, {"NEXUS_WORK_REGISTRY": "fixture"}), \
                 patch("nexus.lease.indexed", return_value=[str(path)]), \
                 patch("nexus.work.registry", return_value=[row]), \
                 patch("nexus.tower._release_orphan_lease") as release:
                self.assertEqual(0, tower._reconcile_orphan_leases(ledger, time.time()))
            release.assert_not_called()

    def test_coordinator_document_scan_skips_tbs(self):
        rows = [{"repo": "thinking-brain-school/tbs-www", "path": "/tmp/tbs", "enabled": True},
                {"repo": "ariaxhan/other", "path": "/tmp/other", "enabled": True}]
        with patch.dict(os.environ, {"NEXUS_WORK_REGISTRY": "fixture"}), \
             patch("nexus.work.registry", return_value=rows), \
             patch("nexus.coordinator.published", return_value={}) as published:
            self.assertEqual({}, intake.coordinator_claims())
        published.assert_called_once_with("/tmp/other")

    def test_explicit_tbs_claim_cannot_enter_tower_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(str(Path(directory) / "ledger.sqlite"))
            self.addCleanup(ledger.close)
            claims = {("thinking-brain-school/tbs-www", 2): {"activity": time.time(), "running": True}}
            self.assertEqual(0, intake.sync_claims(ledger, claims))
            self.assertEqual([], ledger.events(kind="intake.claim"))


if __name__ == "__main__":
    unittest.main()
