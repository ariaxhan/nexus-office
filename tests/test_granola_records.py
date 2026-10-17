"""Issue #308: a processed Granola meeting's commitments land as dispatch records, not prose."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nexus import dispatch, granola  # noqa: E402
from nexus.ledger import Ledger  # noqa: E402

T0 = 1_800_000_000.0
RECEIPT = {
    "issues": [{"repo": "o/r", "number": 7, "owner": "jess", "title": "close the proof issue"}],
    "build_orders": [{"repo": "o/r", "number": 7, "summary": "dup of the issue"},
                     {"repo": "o/app", "number": 3, "summary": "build the MVP"}],
    "aria_items": [{"summary": "write the note", "disposition": "executed", "evidence": "_meta/note.md"},
                   {"summary": "decide pricing", "disposition": "needs-aria", "evidence": ""},
                   {"summary": "linked", "disposition": "executed", "evidence": "https://x/y"}],
}


class GranolaRecords(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nexus-granola-")
        self.led = Ledger(os.path.join(self.dir, "ledger.sqlite"))

    def tearDown(self):
        self.led.close()

    def test_each_commitment_is_one_record_with_owner_surface_and_bound(self):
        cs = granola.commitments("m1", RECEIPT, "/v")
        self.assertEqual([c["key"] for c in cs], ["granola:m1:o/r#7", "granola:m1:o/app#3", "granola:m1:aria:0"])
        self.assertEqual(cs[0]["stall_s"], granola.STALL_S["build-order"])  # also a build order
        self.assertEqual(cs[2]["verify"], ["test", "-e", "/v/_meta/note.md"])
        self.assertEqual([c["owner"] for c in cs], ["jess", "unassigned", "aria"])

    def test_file_meeting_is_idempotent_and_held_by_owner(self):
        ids = granola.file_meeting(self.led, "m1", "Demo", "2026-10-16", RECEIPT, "/v", now=T0)
        self.assertEqual(ids, granola.file_meeting(self.led, "m1", "Demo", "2026-10-16", RECEIPT, "/v", now=T0))
        rows = dispatch.records(self.led)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r["kind"] == dispatch.COMMITMENT and r["state"] == "filed" for r in rows))
        self.assertIn('Granola meeting "Demo"', rows[0]["payload"]["source"])

    def test_executed_item_advances_from_its_surface(self):
        note = os.path.join(self.dir, "_meta", "note.md")
        os.makedirs(os.path.dirname(note))
        with open(note, "w") as fh:
            fh.write("done\n")
        receipt = {"aria_items": [RECEIPT["aria_items"][0]]}
        (rid,) = granola.file_meeting(self.led, "m2", "t", "d", receipt, self.dir, now=T0)
        for k in range(4):
            dispatch.poll(self.led, now=T0 + k * (dispatch.COMMITMENT_CHECK_S + 1))
        self.assertEqual(dispatch.get(self.led, rid)["state"], "closed")

    def test_cli_files_a_receipt(self):
        path = os.path.join(self.dir, "receipt.json")
        with open(path, "w") as fh:
            json.dump(RECEIPT, fh)
        out = subprocess.run([sys.executable, "-m", "nexus", "--ledger", self.led.path, "dispatch", "granola",
                              "--meeting", "m3", "--receipt", path, "--vault", "/v"],
                             cwd=dispatch.ROOT, capture_output=True, text=True, check=True)
        self.assertEqual(len(json.loads(out.stdout)), 3)


if __name__ == "__main__":
    unittest.main()
