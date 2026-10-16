import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/fallback_snapshot_publish.py"
spec = importlib.util.spec_from_file_location("fallback_snapshot_publish", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ProjectionTest(unittest.TestCase):
    def test_large_world_projects_only_bounded_cards(self):
        world = {"generated": "2026-10-16T23:00:00Z", "stations": [],
                 "sections": {"tower": {"state": "ok", "secret": "x" * 3000000,
                                        "card": {"title": "Tower", "headline": "14 issues in retry loops"}}}}
        projected = module.project_world(world)
        self.assertEqual(projected["stations"],
                         [{"repo": "Tower", "summary": "14 issues in retry loops", "state": "ok"}])
        self.assertLess(len(str(projected)), 1000)

    def test_no_display_cards_fails_closed(self):
        with self.assertRaises(ValueError):
            module.project_world({"generated": "2026-10-16T23:00:00Z", "sections": {}})


if __name__ == "__main__":
    unittest.main()
