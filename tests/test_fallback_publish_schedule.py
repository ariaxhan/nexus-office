"""The fallback publisher must use the host's working calendar timer."""
import plistlib
from pathlib import Path
import unittest


PLIST = (Path(__file__).resolve().parents[1] / "ops" / "launchd"
         / "com.aria.office-fallback-publish.plist")


class FallbackPublishScheduleTest(unittest.TestCase):
    def test_five_minute_calendar_without_interval(self):
        spec = plistlib.loads(PLIST.read_bytes())
        self.assertEqual(spec["Label"], "com.aria.office-fallback-publish")
        self.assertEqual(spec["StartCalendarInterval"],
                         [{"Minute": minute} for minute in range(0, 60, 5)])
        self.assertNotIn("StartInterval", spec)
        self.assertTrue(spec["RunAtLoad"])
        self.assertEqual(spec["ProgramArguments"][0], "/usr/bin/python3")
        self.assertTrue(spec["ProgramArguments"][1].endswith(
            "/scripts/fallback_snapshot_publish.py"))


if __name__ == "__main__":
    unittest.main()
