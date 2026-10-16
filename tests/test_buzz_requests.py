"""Issue #309: Buzz requests are records at the channel; thread state is both sides, as data."""

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nexus import buzz_requests as br  # noqa: E402
from nexus import dispatch  # noqa: E402
from nexus.ledger import Ledger  # noqa: E402

T0 = 1_800_000_000.0
ROOT_ID, REPLY_ID, MUSE_ID = "a" * 64, "b" * 64, "c" * 64


def post(pid, author, text, at, reply_to=None, channel="general"):
    return {"id": pid, "channel": channel, "author": author, "author_id": author.lower(), "at": at,
            "thread": ROOT_ID, "reply_to": reply_to, "text": text}


ASK = post(ROOT_ID, "TBS agent", "@Aria can you approve the Tuesday schedule?", "2026-10-16T10:00:00Z")
ARIA = post(REPLY_ID, "Aria", "yes, ship it", "2026-10-16T10:05:00Z", reply_to=ROOT_ID)
MUSE = post(MUSE_ID, "TBS agent", "@Aria one more question?", "2026-10-16T10:09:00Z", reply_to=ROOT_ID)


class BuzzRequestCase(unittest.TestCase):
    def setUp(self):
        self.led = Ledger(os.path.join(tempfile.mkdtemp(prefix="nexus-buzz-"), "ledger.sqlite"))

    def tearDown(self):
        self.led.close()

    def test_request_post_in_general_produces_one_record(self):
        self.assertEqual(br.listen(self.led, [ASK], now=T0)["filed"], 1)
        self.assertEqual(br.listen(self.led, [ASK], now=T0 + 1)["filed"], 0)
        rec = br.get(self.led, ROOT_ID)
        self.assertEqual((rec["channel"], rec["author"], rec["state"]), ("general", "TBS agent", "unanswered"))
        self.assertEqual([p["id"] for p in rec["thread_state"]["posts"]], [ROOT_ID])

    def test_non_requests_and_read_only_channel_file_nothing(self):
        chatter = post("d" * 64, "TBS agent", "deploy finished", "2026-10-16T10:00:00Z")
        decision = post("e" * 64, "TBS agent", "@Aria pick one?", "2026-10-16T10:00:00Z", channel="human-decisions")
        own = post("f" * 64, "Aria", "@Aria note to self?", "2026-10-16T10:00:00Z")
        self.assertEqual(br.listen(self.led, [chatter, decision, own], now=T0)["filed"], 0)

    def test_aria_reply_in_thread_flips_state_from_thread_data(self):
        br.listen(self.led, [ASK], now=T0)
        report = br.listen(self.led, [ASK, ARIA], now=T0 + 60)
        self.assertEqual(report["changed"], 1)
        rec = br.get(self.led, ROOT_ID)
        self.assertEqual(rec["state"], "answered")
        self.assertEqual(rec["thread_state"]["aria_last_at"], ARIA["at"])
        # the requester asks again: unanswered is computed from both sides, not asserted
        br.listen(self.led, [ASK, ARIA, MUSE], now=T0 + 120)
        self.assertEqual(br.get(self.led, ROOT_ID)["state"], "unanswered")
        walk = [__import__("json").loads(e["payload"])["to"] for e in self.led.events("buzz.request", ROOT_ID)]
        self.assertEqual(walk, ["unanswered", "answered", "unanswered"])

    def test_conversion_files_dispatch_record_to_owning_lane(self):
        br.listen(self.led, [ASK, ARIA], now=T0)
        rid = br.convert(self.led, ROOT_ID, "lane-tbs", "publish the schedule", ["/usr/bin/true"], now=T0 + 1)
        rec = br.get(self.led, ROOT_ID)
        self.assertEqual((rec["state"], rec["work_order"]), ("converted", rid))
        order = dispatch.get(self.led, rid)
        self.assertEqual((order["queue"], order["kind"], order["state"]), ("lane-tbs", "buzz-request", "filed"))
        self.assertEqual(order["payload"]["post_id"], ROOT_ID)
        self.assertEqual(order["payload"]["thread_state"]["aria_last_at"], ARIA["at"])
        launched = []
        dispatch.poll(self.led, now=T0 + 2, workers={"lane-tbs": {"argv": ["w", "{record_id}"]}},
                      launch=lambda argv, env, log: launched.append(argv) or 7, stop=lambda r: True)
        self.assertEqual(launched, [["w", rid]])
        with self.assertRaises(br.BuzzRequestError):
            br.convert(self.led, ROOT_ID, "lane-tbs", "again", ["/usr/bin/true"], now=T0 + 3)
        # thread keeps updating as data; a converted record stays converted
        br.listen(self.led, [ASK, ARIA, MUSE], now=T0 + 4)
        rec = br.get(self.led, ROOT_ID)
        self.assertEqual((rec["state"], len(rec["thread_state"]["posts"])), ("converted", 3))

    def test_reply_flips_state_after_root_leaves_the_read_window(self):
        br.listen(self.led, [ASK], now=T0)
        report = br.listen(self.led, [ARIA], now=T0 + 60)  # root scrolled past the 100-post window
        self.assertEqual(report["changed"], 1)
        rec = br.get(self.led, ROOT_ID)
        self.assertEqual(rec["state"], "answered")
        self.assertEqual([p["id"] for p in rec["thread_state"]["posts"]], [ROOT_ID, REPLY_ID])
        br.listen(self.led, [MUSE], now=T0 + 120)
        rec = br.get(self.led, ROOT_ID)
        self.assertEqual((rec["state"], len(rec["thread_state"]["posts"])), ("unanswered", 3))

    def test_conversion_that_loses_its_race_leaves_no_order(self):
        br.listen(self.led, [ASK], now=T0)
        stale = br.get
        winner = []

        def racing_get(led, post_id):
            snapshot = stale(led, post_id)
            if not winner:  # another converter lands between our read and our write
                br.get = stale
                winner.append(br.convert(self.led, ROOT_ID, "lane-tbs", "first", ["/usr/bin/true"], now=T0 + 1))
            return snapshot

        br.get = racing_get
        try:
            with self.assertRaises(br.BuzzRequestError):
                br.convert(self.led, ROOT_ID, "lane-tbs", "second", ["/usr/bin/true"], now=T0 + 2)
        finally:
            br.get = stale
        self.assertEqual([r["record_id"] for r in dispatch.records(self.led)], winner)
        self.assertEqual(br.get(self.led, ROOT_ID)["work_order"], winner[0])

    def test_store_refuses_asserted_states(self):
        br.listen(self.led, [ASK], now=T0)
        for sql in ("UPDATE buzz_requests SET state='converted'", "DELETE FROM buzz_requests",
                    "UPDATE buzz_requests SET state='answered'"):
            with self.assertRaises(sqlite3.DatabaseError, msg=sql):
                self.led.conn.execute(sql)


if __name__ == "__main__":
    unittest.main()
