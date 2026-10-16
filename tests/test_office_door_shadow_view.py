"""The Office Watch payload reads the door shadow without an ask."""

from contextlib import closing
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))

import office_faults  # noqa: E402
from nexus import faults  # noqa: E402
from nexus.ledger import Ledger  # noqa: E402


class DoorViewCase(unittest.TestCase):
    def test_detected_fault_is_visible_as_shadow_not_human_ask(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "ledger.sqlite")
            with closing(Ledger(str(path))) as ledger:
                fault_id = faults.detect(ledger, "phone_path_failed", office_faults.DOOR_SOURCE)
                ledger.event("watchdog.observed", "office-door/2026-10-16T22:30Z",
                             {"source": office_faults.DOOR_SOURCE,
                              "classification": "phone_path_failed", "fault_id": fault_id,
                              "late": False}, office_faults.DOOR_SOURCE, 1792189880)
            with patch.object(office_faults.run_board, "LEDGER", path):
                result = office_faults.snapshot()
            self.assertEqual(0, result["count"])
            self.assertEqual("phone_path_failed", result["door_shadow"]["classification"])
            self.assertEqual("detected", result["door_shadow"]["fault"]["state"])
            self.assertEqual([], result["items"])

    def test_latest_observation_wins(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "ledger.sqlite")
            with closing(Ledger(str(path))) as ledger:
                for minute, classification in ((0, "phone_path_failed"), (30, "healthy")):
                    ledger.event("watchdog.observed", f"office-door/2026-10-16T22:{minute:02}Z",
                                 {"source": office_faults.DOOR_SOURCE,
                                  "classification": classification, "fault_id": None,
                                  "late": False}, office_faults.DOOR_SOURCE, 1792188000 + minute * 60)
            with patch.object(office_faults.run_board, "LEDGER", path):
                result = office_faults.snapshot()
            self.assertEqual("healthy", result["door_shadow"]["classification"])

    def test_bad_observation_is_skipped_and_empty_is_safe(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "ledger.sqlite")
            with closing(Ledger(str(path))) as ledger:
                ledger.event("watchdog.observed", "bad", [], "test", 1792189000)
            with patch.object(office_faults.run_board, "LEDGER", path):
                result = office_faults.snapshot()
            self.assertIsNone(result["door_shadow"])
            self.assertEqual(0, result["count"])
