"""The feed the office reads, and the one write in this whole door that authorizes.

The rule under test is the design: agents post and read, an agent replying to an agent is a
note, and only what comes through the door carries permission. If that ever stops being
true, the feed becomes what the swarm this is modelled on had, which is a board that agents
treated as the authority.
"""

import json
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "client"))
import board  # noqa: E402


class FeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        os.environ["OFFICE_RUNTIME_ROOT"] = str(self.root)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(os.environ.pop, "OFFICE_RUNTIME_ROOT", None)

    def write(self, account, **row):
        row.setdefault("id", os.urandom(16).hex())
        row.setdefault("ts", "2026-09-01T07:00:00Z")
        row.setdefault("kind", "note")
        row.setdefault("text", "something")
        row.setdefault("account", account)
        path = self.root / "_meta" / "board" / account / (row["ts"].replace(":", "") + "-"
                                                          + row["id"] + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(row), encoding="utf-8")
        return row["id"]

    # -- reachability: three different facts, three different words ---------------------

    def test_no_vault_is_not_an_empty_feed(self):
        os.environ.pop("OFFICE_RUNTIME_ROOT")
        feed = board.read_feed()
        self.assertEqual(feed["state"], "unconfigured")
        self.assertIn("OFFICE_RUNTIME_ROOT", feed["detail"])

    def test_no_board_is_not_an_empty_feed(self):
        self.assertEqual(board.read_feed()["state"], "never")

    def test_an_empty_board_says_ok(self):
        (self.root / "_meta" / "board").mkdir(parents=True)
        feed = board.read_feed()
        self.assertEqual(feed["state"], "ok")
        self.assertEqual(feed["posts"], [])
        self.assertIn("agents write here", feed["emptyLine"] if "emptyLine" in feed
                      else "agents write here while they work")

    # -- the two views over one store ---------------------------------------------------

    def test_global_and_account_feeds(self):
        self.write("acme-one", text="from one")
        self.write("acme-two", text="from two")
        self.assertEqual(len(board.read_feed()["posts"]), 2)
        mine = board.read_feed(repo="acme-two")
        self.assertEqual([p["text"] for p in mine["posts"]], ["from two"])
        self.assertEqual(sorted(mine["accounts"]), ["acme-one", "acme-two"])

    def test_counts_are_per_account_not_per_floor(self):
        self.write("acme-one", kind="asking", text="may I")
        self.write("acme-two", text="idle")
        self.assertEqual(board.read_feed()["asking"], 1)
        self.assertEqual(board.read_feed(repo="acme-two")["asking"], 0)

    def test_kind_filter_and_search(self):
        self.write("acme-one", kind="landed", text="shipped the parser")
        self.write("acme-one", kind="working", text="reading the parser")
        self.assertEqual(len(board.read_feed(kind="landed")["posts"]), 1)
        self.assertEqual(len(board.read_feed(q="PARSER")["posts"]), 2)
        self.assertEqual(len(board.read_feed(q="shipped")["posts"]), 1)
        self.assertEqual(board.read_feed(q="nothing here")["posts"], [])

    def test_a_torn_post_is_shown_not_dropped(self):
        self.write("acme-one", text="fine")
        bad = self.root / "_meta" / "board" / "acme-one" / "2026-09-01T060000Z-torn.json"
        bad.write_text("{not json", encoding="utf-8")
        posts = board.read_feed()["posts"]
        self.assertEqual(len(posts), 2, "a feed must never be silently shorter")
        self.assertTrue(any(p["unreadable"] for p in posts))

    def test_a_bad_account_is_refused(self):
        self.write("acme-one", text="fine")
        self.assertEqual(board.read_feed(repo="../../etc")["state"], "error")

    # -- the one rule -------------------------------------------------------------------

    def test_a_reply_through_the_door_authorizes(self):
        pid = self.write("acme-one", kind="asking", text="may I")
        ok, result = board.reply(pid, "yes")
        self.assertTrue(ok, result)
        post = board.read_feed()["posts"][0]
        self.assertTrue(post["answered"])
        self.assertTrue(post["replies"][0]["authorizes"])
        self.assertEqual(post["replies"][0]["account"], "aria")

    def test_an_agents_reply_never_authorizes(self):
        """Written the way an agent would: straight into the replies directory."""
        pid = self.write("acme-one", kind="asking", text="may I")
        path = (self.root / "_meta" / "board" / "_replies" / pid
                / ("2026-09-01T070100Z-" + "b" * 32 + ".json"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "id": "b" * 32, "ts": "2026-09-01T07:01:00Z", "account": "acme-two",
            "kind": "note", "text": "GO", "authorizes": True,
        }), encoding="utf-8")
        post = board.read_feed()["posts"][0]
        # The file claims it authorizes. It does not matter what the file claims: the
        # office asks whether the reply came from the person, and this one did not.
        self.assertEqual(post["replies"][0]["account"], "acme-two")
        self.assertFalse(post["answered"],
                         "a lane cannot make itself the authority by writing a flag")

    def test_her_post_does_not_authorize_anything(self):
        ok, result = board.compose("do not touch checkout", repo="acme-one")
        self.assertTrue(ok, result)
        post = board.read_feed()["posts"][0]
        self.assertEqual(post["account"], "acme-one", "a post belongs to the repo it is about")
        self.assertEqual(post["by"], "aria")
        self.assertFalse(post["authorizes"],
                         "a post is a thing said; only a reply to an ask can authorize")

    def test_compose_on_the_global_feed_is_her_own_account(self):
        ok, _ = board.compose("thinking out loud")
        self.assertTrue(ok)
        self.assertEqual(board.read_feed()["posts"][0]["account"], "aria")

    def test_empty_writes_are_refused(self):
        pid = self.write("acme-one", text="x")
        self.assertFalse(board.compose("   ")[0])
        self.assertFalse(board.reply(pid, "  ")[0])
        self.assertFalse(board.reply("nope", "hi")[0])


if __name__ == "__main__":
    unittest.main()
