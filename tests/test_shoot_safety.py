"""The visual harness must never take over an unattended desktop."""

import os
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SHOOT = ROOT / "scripts" / "shoot.sh"


class ShootSafetyTest(unittest.TestCase):
    def run_unattended(self, allow=None, args=()):
        env = os.environ.copy()
        env.pop("NEXUS_OFFICE_ALLOW_VISIBLE_SHOTS", None)
        if allow is not None:
            env["NEXUS_OFFICE_ALLOW_VISIBLE_SHOTS"] = allow
        return subprocess.run(
            [str(SHOOT), *args], cwd=ROOT, env=env, text=True,
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

    def test_the_refusal_names_the_unattended_way_through(self):
        # A gate that only says no teaches a lane to bypass it. This one says
        # which path is safe without a person, so the next lane takes it.
        result = self.run_unattended()
        self.assertIn("--offscreen", result.stderr)

    def test_offscreen_is_allowed_without_a_terminal(self):
        # The quiet path activates nothing and moves no cursor, so there is
        # nobody to ask. It must get past the gate that the visible path does
        # not: it fails later, on the five second timeout while it builds, and
        # never with the refusal.
        try:
            result = self.run_unattended(args=("--offscreen",))
        except subprocess.TimeoutExpired as expired:
            self.assertNotIn("opens and drives visible Office windows",
                             (expired.stderr or b"").decode())
            return
        self.assertNotEqual(result.returncode, 2, result.stderr)
        self.assertNotIn("opens and drives visible Office windows", result.stderr)


if __name__ == "__main__":
    unittest.main()
