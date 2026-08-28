"""The library source: what memory holds, and what it says when it cannot say.

The dangerous failure here is not a wrong number. It is a library that draws as
empty when it is actually unreadable, or a shelf of 20 presented as a shelf of
234. Both of those are false greens with furniture around them, so those are the
tests that matter.

    python3 -m unittest discover -s tests -p 'test_*.py'
"""

from __future__ import annotations

import importlib
import os
import pathlib
import sqlite3
import sys
import tempfile
import unittest

from test_sections import assert_card

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))

SCHEMA = """
CREATE TABLE learnings (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  type TEXT NOT NULL,
  insight TEXT NOT NULL,
  evidence TEXT,
  domain TEXT,
  hit_count INTEGER DEFAULT 0,
  last_hit TEXT,
  load_count INTEGER DEFAULT 0,
  archived_at TEXT,
  archived_reason TEXT
);
"""


def seed(db: pathlib.Path, rows):
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    con.executemany(
        "INSERT INTO learnings (id, ts, type, insight, evidence, domain, hit_count,"
        " last_hit, load_count, archived_at) VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def row(i, type_, hits, evidence="a commit and a test", archived=None):
    return (f"LRN-{type_}-{i:04d}", "2026-06-12T07:36:44.398Z", type_,
            f"lesson {type_} {i}", evidence, "Vaults", hits,
            "2026-08-11T05:55:33.380Z", hits * 3, archived)


class LibraryBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "_meta" / "agentdb").mkdir(parents=True)
        os.environ["OFFICE_RUNTIME_ROOT"] = str(self.root)
        os.environ.pop("AGENTDB_ROOT", None)
        # Nothing listens here, so every HTTP read in these tests is the
        # runtime-is-down path, which is the normal case in real life too.
        os.environ["OFFICE_RUNTIME_URL"] = "http://127.0.0.1:59998"
        from sources import library as srclib
        self.lib = importlib.reload(srclib)

    def tearDown(self):
        for k in ("OFFICE_RUNTIME_ROOT", "OFFICE_RUNTIME_URL", "AGENTDB_ROOT"):
            os.environ.pop(k, None)
        self.tmp.cleanup()

    @property
    def db(self):
        return self.root / "_meta" / "agentdb" / "agent.db"


class ShapeTest(LibraryBase):
    def test_the_store_reports_its_real_size_not_the_snapshot_size(self):
        # THE test for "a store growing without bound should look like it". The
        # shelf count is the number of learnings in the DB; the item count is
        # what fits in a snapshot. Collapsing the two makes growth invisible.
        seed(self.db, [row(i, "gotcha", 200 - i) for i in range(60)])
        s = self.lib.read()
        self.assertEqual(s["state"], "ok")
        shelf = next(x for x in s["shelves"] if x["type"] == "gotcha")
        self.assertEqual(shelf["count"], 60)
        # Shown and counted are the same number now, and that IS the claim: the
        # shelf carries every live learning rather than the top of it.
        self.assertEqual(shelf["shown"], 60)
        self.assertEqual(s["store"]["live"], 60)

    def test_archived_learnings_are_counted_but_not_shelved(self):
        rows = [row(i, "pattern", 5) for i in range(4)]
        rows += [row(90 + i, "pattern", 5, archived="2026-08-01T00:00:00Z") for i in range(3)]
        seed(self.db, rows)
        s = self.lib.read()
        shelf = next(x for x in s["shelves"] if x["type"] == "pattern")
        self.assertEqual(shelf["count"], 4)
        self.assertEqual(s["store"]["archived"], 3)
        self.assertEqual(s["store"]["total"], 7)

    def test_failure_and_gotcha_are_shelved_first_so_they_land_at_eye_level(self):
        # The fixture hangs the first shelf in the top bay. If preference ever
        # sorted ahead of failure, the two types that save you end up by the floor.
        seed(self.db, [row(0, "preference", 1), row(1, "pattern", 1),
                       row(2, "gotcha", 1), row(3, "failure", 1)])
        s = self.lib.read()
        self.assertEqual([x["type"] for x in s["shelves"]],
                         ["failure", "gotcha", "pattern", "preference"])

    def test_an_unknown_type_still_gets_a_shelf(self):
        seed(self.db, [row(0, "failure", 1), row(1, "hunch", 1)])
        s = self.lib.read()
        types = [x["type"] for x in s["shelves"]]
        self.assertEqual(types[0], "failure")
        self.assertIn("hunch", types)


class ShelvingTest(LibraryBase):
    def test_items_carry_type_and_hits_and_provenance(self):
        seed(self.db, [row(0, "failure", 46, evidence="chronicle 2026-08-24")])
        item = self.lib.read()["shelves"][0]["items"][0]
        self.assertEqual(item["type"], "failure")
        self.assertEqual(item["hits"], 46)
        self.assertTrue(item["provenance"]["sourced"])
        self.assertEqual(item["provenance"]["evidence"], "chronicle 2026-08-24")
        self.assertEqual(item["provenance"]["domain"], "Vaults")
        self.assertTrue(item["provenance"]["learned_at"])

    def test_a_memory_with_no_evidence_is_flagged_as_unsourced(self):
        # A memory you cannot source is a rumour. It must not borrow the
        # authority of the ones that can be sourced by looking identical.
        seed(self.db, [row(0, "gotcha", 3, evidence=""),
                       row(1, "gotcha", 2, evidence=None)])
        items = self.lib.read()["shelves"][0]["items"]
        self.assertEqual([i["provenance"]["sourced"] for i in items], [False, False])

    def test_every_live_learning_is_carried_most_recalled_first(self):
        """The old shelf kept the top twenty of each type and counted the rest.
        A memory you cannot reach from the room is a memory the room does not
        have, so the shelf now carries all of them and the order is the only
        thing the hit count decides."""
        seed(self.db, [row(i, "gotcha", i) for i in range(40)])
        items = self.lib.read()["shelves"][0]["items"]
        self.assertEqual(len(items), 40)
        self.assertEqual([i["hits"] for i in items], list(range(39, -1, -1)))
        self.assertEqual(len({i["id"] for i in items}), 40, "no learning twice")

    def test_long_bodies_are_clipped_so_the_snapshot_stays_small(self):
        long = ("x" * 5000, "y" * 5000)
        seed(self.db, [("LRN-long", "2026-06-12T00:00:00Z", "failure", long[0],
                        long[1], "Vaults", 9, "", 0, None)])
        item = self.lib.read()["shelves"][0]["items"][0]
        self.assertLessEqual(len(item["insight"]), self.lib.INSIGHT_CHARS)
        self.assertLessEqual(len(item["provenance"]["evidence"]), self.lib.EVIDENCE_CHARS)


class TruncationTest(LibraryBase):
    """What is left behind, now that the answer is "nothing".

    These used to prove a per-type cap of twenty was admitted honestly. The cap
    is gone, so what they prove now is that nothing quietly took its place: the
    accounting still exists, and it still says zero, which is a claim that can
    be checked rather than a key that was deleted.
    """

    def test_nothing_is_left_off_the_shelf_however_many_there_are(self):
        seed(self.db, [row(i, "gotcha", i) for i in range(55)])
        s = self.lib.read()
        cap = s["capped"]
        self.assertEqual(cap["shown"], 55)
        self.assertEqual(cap["omitted"], 0)
        self.assertEqual(cap["note"], "")
        self.assertEqual(len(s["shelves"][0]["items"]), 55)

    def test_a_body_is_still_clipped_even_though_the_list_is_not(self):
        """Carrying every learning is not carrying every byte of every one. The
        clip is what keeps 632 lessons a snapshot rather than a download."""
        seed(self.db, [row(i, "failure", i) for i in range(3)])
        cap = self.lib.read()["capped"]
        self.assertEqual(cap["insight_chars"], self.lib.INSIGHT_CHARS)

    def test_a_snapshot_costs_a_bounded_amount_per_learning(self):
        """The size claim, stated as arithmetic rather than as a wish.

        Every one of these 1000 lessons is longer than both clips, so this is
        the worst case the store can produce: the shelf item and its card row
        both carry the clipped body, and nothing else scales with the count.
        A per-learning ceiling is the honest invariant, because the total is
        now allowed to grow, and only the price of one lesson is fixed.

        Measured against the live vault on 2026-08-27: 630 learnings, 840,619
        bytes, 0.80 MiB. Under a megabyte, over a loopback socket, every ten
        minutes, which is the price of the library being readable.
        """
        import json
        seed(self.db, [(f"LRN-{t}-{i:04d}", "2026-06-12T07:36:44.398Z", t,
                        "l" * (self.lib.INSIGHT_CHARS + 60),
                        "e" * (self.lib.EVIDENCE_CHARS + 60), "Vaults", i,
                        "2026-08-11T05:55:33.380Z", i, None)
                       for t in ("failure", "gotcha", "pattern", "preference")
                       for i in range(250)])
        section = self.lib.read()
        section["card"] = self.lib.card(section)
        size = len(json.dumps(section).encode())
        per = size / 1000
        self.assertLess(per, 1900, f"{per:.0f} bytes per learning, worst case")
        self.assertEqual(section["capped"]["shown"], 1000)


class AbsenceTest(LibraryBase):
    def test_an_absent_agentdb_is_absent_not_empty(self):
        # No file. The room must draw an unopenable library, not an empty one.
        s = self.lib.read()
        self.assertEqual(s["state"], "absent")
        self.assertNotIn("shelves", s)
        self.assertIn(str(self.db), s["detail"])

    def test_an_unconfigured_vault_says_so_rather_than_looking_empty(self):
        os.environ.pop("OFFICE_RUNTIME_ROOT")
        from sources import library as srclib
        lib = importlib.reload(srclib)
        self.assertEqual(lib.read()["state"], "unconfigured")

    def test_a_corrupt_database_is_an_error_not_an_empty_library(self):
        self.db.write_bytes(b"this is not a database, it is a pile of bytes")
        s = self.lib.read()
        self.assertEqual(s["state"], "error")
        self.assertTrue(s["detail"])

    def test_a_genuinely_empty_store_is_ok_with_no_shelves(self):
        # The third state, and the one that must be distinguishable from the
        # other two: the store opened fine and there is nothing in it.
        seed(self.db, [])
        s = self.lib.read()
        self.assertEqual(s["state"], "ok")
        self.assertEqual(s["shelves"], [])
        self.assertEqual(s["store"]["live"], 0)

    def test_the_db_path_is_absolute_and_taken_from_the_vault_root(self):
        # agentdb resolves its DB from the working directory, and a sub-repo with
        # its own _meta/ routes to a different brain. The root is passed in.
        p = self.lib._db_path()
        self.assertTrue(p.is_absolute())
        self.assertEqual(p, self.db.resolve())

    def test_agentdb_root_overrides_the_vault_root(self):
        other = pathlib.Path(self.tmp.name) / "other"
        (other / "_meta" / "agentdb").mkdir(parents=True)
        os.environ["AGENTDB_ROOT"] = str(other)
        from sources import library as srclib
        lib = importlib.reload(srclib)
        self.assertEqual(lib._db_path(), (other / "_meta/agentdb/agent.db").resolve())


class RuntimeDownTest(LibraryBase):
    def test_a_down_runtime_leaves_the_review_cart_unknown_not_empty(self):
        # "The memory server is not running" and "nothing is waiting for review"
        # must never render the same. One is a setup fact; the other is good news.
        seed(self.db, [row(0, "failure", 1)])
        s = self.lib.read()
        self.assertEqual(s["review"]["state"], "down")
        self.assertIn("detail", s["review"])
        self.assertNotIn("count", s["review"])

    def test_a_down_runtime_does_not_take_the_shelves_down_with_it(self):
        # The on-disk half is the reliable half. The library still stands.
        seed(self.db, [row(i, "failure", i) for i in range(5)])
        s = self.lib.read()
        self.assertEqual(s["state"], "ok")
        self.assertEqual(s["shelves"][0]["count"], 5)
        self.assertEqual(s["semantic"]["state"], "down")

    def test_a_hanging_runtime_does_not_hang_the_snapshot(self):
        # A snapshot is pushed every ten minutes. A server that accepts the
        # connection and then says nothing must cost seconds, not the push.
        import socket
        import threading
        import time
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)
        held = []
        stop = threading.Event()

        def hold():
            while not stop.is_set():
                try:
                    srv.settimeout(0.3)
                    c, _ = srv.accept()
                except OSError:
                    continue
                held.append(c)  # accepted, then deliberately never answered

        t = threading.Thread(target=hold, daemon=True)
        t.start()
        os.environ["OFFICE_RUNTIME_URL"] = f"http://127.0.0.1:{srv.getsockname()[1]}"
        from sources import library as srclib
        lib = importlib.reload(srclib)
        seed(self.db, [row(0, "gotcha", 1)])
        began = time.monotonic()
        s = lib.read()
        elapsed = time.monotonic() - began
        stop.set()
        for c in held:
            c.close()
        srv.close()
        # Two endpoints, each bounded by HTTP_TIMEOUT, plus slack.
        self.assertLess(elapsed, lib.HTTP_TIMEOUT * 2 + 3, f"took {elapsed:.1f}s")
        self.assertIn(s["review"]["state"], ("down", "error"))
        self.assertEqual(s["state"], "ok")


class SectionsTest(LibraryBase):
    def test_the_library_lands_in_the_snapshot_under_its_key(self):
        seed(self.db, [row(0, "failure", 1)])
        import sections
        got = importlib.reload(sections).read_all()
        self.assertIn("library", got)
        self.assertEqual(got["library"]["state"], "ok")


class CardTest(LibraryBase):
    def card(self):
        return self.lib.card(self.lib.read())

    def test_the_card_counts_the_shelves_and_the_recalls(self):
        seed(self.db, [row(0, "failure", 3), row(1, "failure", 4),
                       row(2, "gotcha", 1),
                       row(3, "pattern", 2, archived="2026-01-01T00:00:00Z")])
        card = self.card()
        assert_card(self, card)
        self.assertEqual(card["headline"], "3 learnings, 10 recalls")
        self.assertEqual(card["needs"], 0)
        facts = {f["label"]: f["value"] for f in card["facts"]}
        self.assertEqual(facts["live"], "3")
        self.assertEqual(facts["archived"], "1")
        self.assertIn("failure 2", facts["by type"])

    def test_a_closed_runtime_leaves_the_review_queue_unknown_not_empty(self):
        seed(self.db, [row(0, "failure", 1)])
        facts = {f["label"]: f["value"] for f in self.card()["facts"]}
        self.assertIn("unknown", facts["review queue"])
        # ... and a closed runtime is the normal case, so it needs nobody.
        self.assertEqual(self.card()["needs"], 0)

    def test_a_vault_that_has_never_learned_anything_is_not_a_broken_one(self):
        card = self.card()
        assert_card(self, card)
        self.assertIn("no agentdb on disk", card["headline"])
        self.assertEqual(card["needs"], 0)


class CardRowTest(LibraryBase):
    """The shelf, as the generic table every renderer already knows how to draw.

    The point of #40 is that a person can READ the library from the room. A count
    on a card is not memory you can reach, so every live learning becomes a row,
    and the row carries its provenance with it: a lesson without the evidence
    beside it is a rumour with a badge on.
    """

    def card(self):
        return self.lib.card(self.lib.read())

    def test_every_live_learning_becomes_a_row_even_past_the_old_cap(self):
        seed(self.db, [row(i, "gotcha", i) for i in range(45)]
                      + [row(i, "failure", i) for i in range(3)])
        card = self.card()
        assert_card(self, card)
        self.assertEqual(len(card["rows"]), 48)
        self.assertEqual(len({r["id"] for r in card["rows"]}), 48)

    def test_an_archived_learning_is_never_a_row(self):
        seed(self.db, [row(0, "failure", 4),
                       row(1, "failure", 9, archived="2026-01-01T00:00:00Z")])
        card = self.card()
        self.assertEqual([r["id"] for r in card["rows"]], ["LRN-failure-0000"])

    def test_the_rows_are_grouped_by_type_at_eye_level_first(self):
        seed(self.db, [row(0, "preference", 1), row(1, "pattern", 2),
                       row(2, "gotcha", 3), row(3, "failure", 4)])
        groups = [r["group"] for r in self.card()["rows"]]
        self.assertEqual(groups, ["failure", "gotcha", "pattern", "preference"])

    def test_a_row_carries_the_lesson_its_evidence_its_hits_and_its_record(self):
        seed(self.db, [row(7, "failure", 46, evidence="chronicle 2026-08-24")])
        r = self.card()["rows"][0]
        self.assertEqual(r["title"], "lesson failure 7")
        self.assertEqual(r["subtitle"], "chronicle 2026-08-24")
        self.assertEqual(r["badge"], "46×")
        self.assertEqual(r["id"], "LRN-failure-0007")
        self.assertIn("Vaults", r["detail"])
        self.assertIn("2026-06-12", r["detail"], "when it was learned")
        self.assertEqual(r["url"], "", "a learning is not a place to go")

    def test_an_unsourced_row_says_so_rather_than_borrowing_authority(self):
        seed(self.db, [row(0, "gotcha", 3, evidence=""),
                       row(1, "gotcha", 2, evidence="a real commit")])
        rows = {r["id"]: r for r in self.card()["rows"]}
        weak = rows["LRN-gotcha-0000"]
        self.assertIn("no evidence", weak["subtitle"])
        self.assertEqual(weak["tone"], "dim")
        self.assertEqual(rows["LRN-gotcha-0001"]["tone"], "")

    def test_the_row_bodies_are_clipped_the_same_way_the_shelf_items_are(self):
        seed(self.db, [("LRN-long", "2026-06-12T00:00:00Z", "failure", "x" * 5000,
                        "y" * 5000, "Vaults", 9, "", 0, None)])
        r = self.card()["rows"][0]
        self.assertLessEqual(len(r["title"]), self.lib.INSIGHT_CHARS)
        self.assertLessEqual(len(r["subtitle"]), self.lib.EVIDENCE_CHARS)

    def test_a_store_that_will_not_open_has_no_rows_to_show(self):
        self.db.write_bytes(b"not a database")
        card = self.card()
        assert_card(self, card)
        self.assertNotIn("rows", card)

    def test_the_shelf_and_the_rows_never_disagree_about_what_is_there(self):
        seed(self.db, [row(i, t, i) for t in ("failure", "pattern")
                       for i in range(25)])
        section = self.lib.read()
        card = self.lib.card(section)
        self.assertEqual(len(card["rows"]),
                         sum(len(s["items"]) for s in section["shelves"]))
        self.assertEqual(len(card["rows"]), section["store"]["live"])


if __name__ == "__main__":
    unittest.main()
