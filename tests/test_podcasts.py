"""Podcast manifest states and safe, playable local destinations."""

from __future__ import annotations

import importlib
import json
import pathlib
import sys
import tempfile
import unittest

from test_sections import assert_card

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))


class PodcastsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name) / "podcasts"
        self.root.mkdir()
        import sources.podcasts as podcasts
        self.podcasts = importlib.reload(podcasts)
        self.podcasts.ROOT = self.root
        self.podcasts.MANIFEST = self.root / "manifest.json"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, episodes, **extra):
        manifest = {"version": 1, "updated_at": "2026-09-04T12:00:00Z",
                    "episodes": episodes, **extra}
        self.podcasts.MANIFEST.write_text(json.dumps(manifest), encoding="utf-8")

    def episode(self, audio, **extra):
        return {"id": "2026-09-04/morning-briefing", "date": "2026-09-04",
                "type": "morning-briefing", "title": "The morning briefing",
                "audio_path": str(audio), "duration_s": 1802, "word_count": 3900,
                "generated_at": "2026-09-04T11:58:00Z", **extra}

    def test_missing_manifest_is_not_an_empty_library(self):
        data = self.podcasts.read()
        self.assertEqual(data["state"], "missing")
        card = self.podcasts.card(data)
        assert_card(self, card)
        self.assertEqual(card["needs"], 1)

    def test_malformed_json_and_schema_are_distinct_from_empty(self):
        self.podcasts.MANIFEST.write_text("{broken", encoding="utf-8")
        self.assertEqual(self.podcasts.read()["state"], "malformed")
        self.podcasts.MANIFEST.write_text(json.dumps({"episodes": {}}), encoding="utf-8")
        self.assertEqual(self.podcasts.read()["state"], "malformed")
        self.podcasts.MANIFEST.write_text(json.dumps({"episodes": ["not an object"]}), encoding="utf-8")
        self.assertEqual(self.podcasts.read()["state"], "malformed")

    def test_valid_empty_manifest_is_a_calm_empty_state(self):
        self.write([])
        data = self.podcasts.read()
        self.assertEqual(data["state"], "empty")
        card = self.podcasts.card(data)
        assert_card(self, card)
        self.assertEqual(card["needs"], 0)
        self.assertNotIn("rows", card)

    def test_existing_mp3_beneath_root_gets_a_file_uri(self):
        audio = self.root / "2026-09-04" / "morning.mp3"
        audio.parent.mkdir()
        audio.write_bytes(b"ID3")
        self.write([self.episode(audio)])

        data = self.podcasts.read()
        self.assertEqual(data["state"], "ok")
        self.assertEqual(data["episodes"][0]["audio_url"], audio.resolve().as_uri())
        card = self.podcasts.card(data)
        assert_card(self, card)
        self.assertEqual(card["needs"], 0)
        self.assertEqual(card["rows"][0]["url"], audio.resolve().as_uri())
        self.assertEqual(card["rows"][0]["badge"], "30m")

    def test_missing_outside_relative_and_non_mp3_audio_never_get_links(self):
        outside = pathlib.Path(self.tmp.name) / "outside.mp3"
        outside.write_bytes(b"ID3")
        wrong = self.root / "notes.txt"
        wrong.write_text("not audio")
        paths = [self.root / "missing.mp3", outside, pathlib.Path("relative.mp3"), wrong]
        self.write([self.episode(path, id=f"episode-{i}") for i, path in enumerate(paths)])

        data = self.podcasts.read()
        self.assertTrue(all(not item["audio_url"] for item in data["episodes"]))
        card = self.podcasts.card(data)
        assert_card(self, card)
        self.assertEqual(card["needs"], 4)
        self.assertTrue(all(not row["url"] for row in card["rows"]))

    def test_symlink_escape_is_rejected(self):
        outside = pathlib.Path(self.tmp.name) / "outside.mp3"
        outside.write_bytes(b"ID3")
        link = self.root / "escape.mp3"
        link.symlink_to(outside)
        self.write([self.episode(link)])
        self.assertFalse(self.podcasts.read()["episodes"][0]["audio_url"])

    def test_manifest_order_is_preserved_newest_first(self):
        files = [self.root / f"{name}.mp3" for name in ("new", "old")]
        for path in files:
            path.write_bytes(b"ID3")
        self.write([self.episode(files[0], id="new"), self.episode(files[1], id="old")])
        card = self.podcasts.card(self.podcasts.read())
        self.assertEqual([row["id"] for row in card["rows"]], ["new", "old"])


if __name__ == "__main__":
    unittest.main()
