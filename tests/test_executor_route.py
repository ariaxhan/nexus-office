"""Issues whose METHOD names Antigravity are authored through the router's Antigravity class."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nexus import executor  # noqa: E402

ENTRY = {"repo": "o/r", "path": "/tmp/r"}


class Route(unittest.TestCase):
    def issue(self, body, labels=()):
        return {"number": 1, "title": "t", "body": body, "labels": [{"name": n} for n in labels]}

    def test_method_naming_antigravity_routes_to_antigravity(self):
        argv, prompt, road, _ = executor.plan(ENTRY, self.issue("- METHOD: English authored by Antigravity via ./tbs-agy"))
        self.assertEqual(["run", "customer-copy-antigravity", "--", "-p"], argv[1:5])
        self.assertIn("/tmp/r", prompt)
        self.assertIsNone(road)

    def test_copy_authority_label_routes_to_antigravity(self):
        self.assertEqual("customer-copy-antigravity", executor.plan(ENTRY, self.issue("", ["copy-authority"]))[0][2])

    def test_plain_issue_and_non_method_mention_stay_claude(self):
        for body in ("fix a bug", "Background: Antigravity wrote this once.\n- METHOD: edit the parser"):
            self.assertEqual("code-judgment", executor.plan(ENTRY, self.issue(body))[0][2], body)


if __name__ == "__main__":
    unittest.main()
