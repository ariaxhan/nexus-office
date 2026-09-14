"""`nexus plans list` says whether each plan is enabled, and why one is not."""

import contextlib
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nexus import cli  # noqa: E402
from nexus.ledger import Ledger  # noqa: E402


class PlansListCase(unittest.TestCase):
    def test_shows_enabled_and_disabled_with_reason(self):
        path = os.path.join(tempfile.mkdtemp(prefix="nexus-cli-"), "ledger.sqlite")
        led = Ledger(path)
        led.add_plan("runs", inputs={"cmd": "true"})
        led.add_plan("nexus-work", inputs={"cmd": "true"})
        self.assertEqual(cli.main(["--ledger", path, "plans", "disable", "nexus-work",
                                   "--reason", "retired by code-work"]), 0)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["--ledger", path, "plans", "list"]), 0)
        lines = {line.split("  ")[1]: line for line in out.getvalue().splitlines()}
        self.assertIn("  enabled  ", lines["runs"])
        self.assertIn("  disabled (retired by code-work)  ", lines["nexus-work"])


if __name__ == "__main__":
    unittest.main()
