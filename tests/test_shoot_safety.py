"""The visual harness must never take over an unattended desktop."""

import os
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SHOOT = ROOT / "scripts" / "shoot.sh"


class ShootSafetyTest(unittest.TestCase):
    def run_unattended(self, allow=None):
        env = os.environ.copy()
        env.pop("NEXUS_OFFICE_ALLOW_VISIBLE_SHOTS", None)
        if allow is not None:
            env["NEXUS_OFFICE_ALLOW_VISIBLE_SHOTS"] = allow
        return subprocess.run(
            [str(SHOOT)], cwd=ROOT, env=env, text=True,
            stdin=subprocess.DEVNULL, capture_output=True, timeout=5,
        )

    def test_unattended_run_is_refused(self):
        result = self.run_unattended()
        self.assertEqual(result.returncode, 2)
        self.assertIn("opens and drives visible Office windows", result.stderr)

    def test_environment_variable_cannot_bypass_missing_terminal(self):
        result = self.run_unattended("1")
        self.assertEqual(result.returncode, 2)
        self.assertIn("opens and drives visible Office windows", result.stderr)


if __name__ == "__main__":
    unittest.main()
