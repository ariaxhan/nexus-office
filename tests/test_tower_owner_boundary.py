"""A stale work inventory cannot transfer excluded projects to Tower."""

import json
from pathlib import Path
import tempfile
import unittest

from nexus import work


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


if __name__ == "__main__":
    unittest.main()
