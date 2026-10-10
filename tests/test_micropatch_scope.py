"""The historical patch verifier rejects every change outside the allowed file."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import micropatch as worker  # noqa: E402


class Scope(unittest.TestCase):
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
