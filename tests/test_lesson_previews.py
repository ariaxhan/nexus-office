from __future__ import annotations

import json
import pathlib
import shutil
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))
import lesson_previews


def preview(**changes):
    row = {"schema_version": "tbs.lesson-preview/v2", "product": "mommyai",
           "origin": "https://candidate.example", "route": "/mommyai/paid/lesson021",
           "deployment_id": "dpl_candidate", "deployment_state": "READY",
           "deployment_sha": "a" * 40, "source_sha": "a" * 40,
           "deployment_created_at": 2000, "source_committed_at": 1000,
           "git_dirty": "0", "source_git_clean": True,
           "deployment_source_git_clean": "1", "outcome": "PASS",
           "verified_at": "2026-09-02T20:00:00Z",
           "console_errors": [], "request_failures": [],
           "screenshots": [{"width": x} for x in (375, 768, 1440)]}
    row.update(changes)
    return row


def production(**changes):
    row = {"schema_version": "tbs.lesson-production/v1", "origin": "https://live.example",
           "route": "/mommyai/paid/lesson021", "deployment_id": "dpl_live",
           "deployment_state": "READY", "source_sha": "b" * 40,
           "deployment_created_at": 3000, "verified_at": "2026-09-02T21:00:00Z",
           "outcome": "PASS"}
    row.update(changes)
    return row


class HubTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, p, prod=None, product="mommyai", lesson="L023"):
        folder = self.root / product / lesson
        folder.mkdir(parents=True)
        (folder / "preview.json").write_text(json.dumps(p))
        if prod is not None:
            (folder / "production.json").write_text(json.dumps(prod))

    def test_stale_candidate_is_red_and_its_url_is_not_exposed(self):
        self.write(preview(source_committed_at=4000))
        row = lesson_previews.build(self.root)["lessons"][0]
        self.assertEqual(row["status"], "failed")
        self.assertIn("candidate older than source", row["problems"])
        self.assertEqual(row["candidate"]["url"], "")

    def test_missing_deployment_is_red_and_its_url_is_not_exposed(self):
        self.write(preview(deployment_id=""))
        row = lesson_previews.build(self.root)["lessons"][0]
        self.assertIn("missing deployment evidence", row["problems"])
        self.assertEqual(row["candidate"]["url"], "")

    def test_verified_candidate_newer_than_production_is_linked_and_flagged(self):
        self.write(preview(deployment_created_at=4000), production(deployment_created_at=3000))
        row = lesson_previews.build(self.root)["lessons"][0]
        self.assertTrue(row["candidate_newer_than_production"])
        self.assertEqual(row["candidate"]["url"], "https://candidate.example/mommyai/paid/lesson021")
        self.assertIn("candidate newer than production", row["problems"])

    def test_noncanonical_variant_folder_is_refused(self):
        self.write(preview(), lesson="L023-layout-fix")
        self.assertEqual(lesson_previews.build(self.root)["lessons"], [])

    def test_clean_source_with_bound_transform_is_linked(self):
        digest = "c" * 64
        self.write(preview(git_dirty="1", transform_sha=digest,
                           deployment_transform_sha=digest), production())
        row = lesson_previews.build(self.root)["lessons"][0]
        self.assertEqual(row["candidate"]["url"],
                         "https://candidate.example/mommyai/paid/lesson021")

    def test_dirty_source_never_becomes_a_link(self):
        digest = "c" * 64
        self.write(preview(git_dirty="1", source_git_clean=False,
                           transform_sha=digest, deployment_transform_sha=digest), production())
        row = lesson_previews.build(self.root)["lessons"][0]
        self.assertIn("source cleanliness unproven", row["problems"])
        self.assertFalse(row["candidate"]["url"])

    def test_missing_source_clean_proof_is_red(self):
        p = preview()
        p.pop("source_git_clean")
        self.write(p, production())
        row = lesson_previews.build(self.root)["lessons"][0]
        self.assertIn("source cleanliness unproven", row["problems"])
        self.assertFalse(row["candidate"]["url"])

    def test_old_v1_receipt_is_red(self):
        self.write(preview(schema_version="tbs.lesson-preview/v1"), production())
        row = lesson_previews.build(self.root)["lessons"][0]
        self.assertIn("unsupported receipt", row["problems"])
        self.assertFalse(row["candidate"]["url"])

    def test_transform_mismatch_is_red(self):
        self.write(preview(git_dirty="1", transform_sha="c" * 64,
                           deployment_transform_sha="d" * 64), production())
        row = lesson_previews.build(self.root)["lessons"][0]
        self.assertIn("transform conflict", row["problems"])
        self.assertFalse(row["candidate"]["url"])

    def test_catalog_lesson_without_preview_is_visible(self):
        catalog = self.root / "catalog"
        catalog.mkdir()
        (catalog / "mommyai.json").write_text(json.dumps([
            {"product": "mommyai", "slug": "mommyai/code/lesson001", "title": "Free"},
            {"product": "mommyai", "slug": "mommyai/code/lesson002", "title": "Free 2"},
            {"product": "mommyai", "slug": "mommyai/paid/lesson001", "title": "Paid 1"},
        ]))
        rows = lesson_previews.build(self.root, catalog)["lessons"]
        row = next(row for row in rows if row["lesson"] == "L003")
        self.assertEqual(row["candidate"]["qa"], "MISSING")
        self.assertEqual(row["production"]["url"],
                         "https://thinkingbrainschool.com/mommyai/paid/lesson001")
        self.assertIn("missing candidate receipt", row["problems"])


if __name__ == "__main__":
    unittest.main()


class ReceiptErrorDisclosureTests(unittest.TestCase):
    """The rows are published as a public static file, so a problem string may
    say what went wrong and never where on the builder's disk it went wrong."""

    def test_unreadable_receipt_does_not_publish_the_local_path(self):
        root = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        lesson = root / "MommyAI" / "L001"
        lesson.mkdir(parents=True)
        (lesson / "preview.json").symlink_to(root / "nowhere" / "gone.json")
        row, = lesson_previews.build(root, None)["lessons"]
        self.assertEqual(row["status"], "failed")
        problem, = row["problems"]
        self.assertNotIn(str(root), problem)
        self.assertNotIn("preview.json", problem)
        self.assertIn("FileNotFoundError", problem)

    def test_malformed_receipt_still_names_the_parse_failure(self):
        root = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        lesson = root / "MommyAI" / "L001"
        lesson.mkdir(parents=True)
        (lesson / "preview.json").write_text("{not json", encoding="utf-8")
        row, = lesson_previews.build(root, None)["lessons"]
        problem, = row["problems"]
        self.assertIn("JSONDecodeError", problem)
        self.assertNotIn(str(root), problem)


class InventoryNumberingTests(unittest.TestCase):
    """Lessons are numbered per product. `product` comes off the row, so two
    catalog files may name the same product and neither may erase the other."""

    def _catalog(self, files):
        root = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        for name, rows in files.items():
            (root / name).write_text(json.dumps(rows), encoding="utf-8")
        return root

    def test_two_files_naming_one_product_keep_every_lesson(self):
        root = self._catalog({
            "wave-one.json": [{"product": "TBS", "slug": "one", "title": "1"},
                              {"product": "TBS", "slug": "two", "title": "2"}],
            "wave-two.json": [{"product": "TBS", "slug": "three", "title": "3"}],
        })
        inventory = lesson_previews._inventory(root)
        self.assertEqual(sorted(inventory), [("TBS", "L001"), ("TBS", "L002"),
                                             ("TBS", "L003")])
        self.assertEqual([inventory[("TBS", f"L{n:03d}")]["route"]
                          for n in (1, 2, 3)], ["/one", "/two", "/three"])

    def test_one_file_per_product_numbers_exactly_as_before(self):
        root = self._catalog({
            "mommyai.json": [{"product": "MommyAI", "slug": "a", "title": "a"},
                             {"product": "MommyAI", "slug": "", "title": "no slug"},
                             {"product": "MommyAI", "slug": "c", "title": "c"}],
        })
        inventory = lesson_previews._inventory(root)
        # The slug-less row still burns its number: L002 stays absent so that
        # L003 keeps pointing at the third lesson in the catalog.
        self.assertEqual(sorted(inventory),
                         [("MommyAI", "L001"), ("MommyAI", "L003")])
        self.assertEqual(inventory[("MommyAI", "L003")]["route"], "/c")

    def test_distinct_products_are_numbered_independently(self):
        root = self._catalog({
            "all.json": [{"product": "MommyAI", "slug": "a", "title": "a"},
                         {"product": "SuperPower", "slug": "b", "title": "b"}],
        })
        self.assertEqual(sorted(lesson_previews._inventory(root)),
                         [("MommyAI", "L001"), ("SuperPower", "L001")])
