"""The historical patch verifier rejects every change outside the allowed file."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import micropatch as worker  # noqa: E402
import micropatch_tradition as harness  # noqa: E402


class Scope(unittest.TestCase):
    def test_digest_covers_new_file_at_snapshot_root(self):
        with tempfile.TemporaryDirectory() as tree:
            root = Path(tree)
            (root / "nexus").mkdir()
            (root / "nexus" / "allowed.py").write_text("pass\n")
            before = harness.digests(root)
            (root / "unauthorized.py").write_text("pass\n")
            self.assertIn("unauthorized.py", harness.digests(root).keys() - before.keys())

    def test_new_file_outside_contract_is_rejected_without_worker_receipt(self):
        before = {"nexus/allowed.py": "old"}
        after = {"nexus/allowed.py": "new", "nexus/extra.py": "created"}
        self.assertEqual(["nexus/extra.py"], worker.out_of_scope(before, after, (), "nexus/allowed.py", "/tmp/tree"))

    def test_only_allowed_file_is_accepted(self):
        before = {"nexus/allowed.py": "old"}
        after = {"nexus/allowed.py": "new"}
        self.assertEqual([], worker.out_of_scope(before, after, (), "nexus/allowed.py", "/tmp/tree"))


if __name__ == "__main__":
    unittest.main()
