"""Issue #310: dual-write to the Cloudflare shadow; Studio authoritative, shadow judged by its own rules."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from nexus import dispatch, faults, shadow  # noqa: E402
from nexus.ledger import Ledger  # noqa: E402

T0 = 1_800_000_000.0
DONE = {"outcome": "done", "artifacts": ["file:/tmp/x"], "verification": "test -f /tmp/x",
        "evidence": ["exists"], "next": "none"}
JUDGE = """
import { judgeDispatch, judgeFault, invalid, sha256 } from %s;
const envs = JSON.parse(await new Promise((ok) => { let s = ""; process.stdin.on("data", (d) => s += d).on("end", () => ok(s)); }));
const prev = {}, out = [];
for (const e of envs) {
  const record = JSON.parse(e.record_json);
  const bad = invalid(e) || ((await sha256(e.record_json)) !== e.sha256 ? "sha256" : null);
  const pairs = e.namespace === "office.dispatch" ? judgeDispatch(record, e.transitions)
    : [judgeFault(prev[e.record_id] || null, record, e.transitions[0].seq)];
  prev[e.record_id] = record;
  out.push({ event_id: e.event_id, bad, pairs });
}
console.log(JSON.stringify(out));
"""


class Proc:
    def __init__(self, rc):
        self.returncode, self.stdout, self.stderr = rc, "", ""


def judge(envelopes):
    """The deployed Worker's own rules, run by node on what the Studio would send."""
    url = json.dumps("file://" + os.path.join(ROOT, "shadow", "src", "parity.js"))
    out = subprocess.run(["node", "--input-type=module", "-e", JUDGE % url], input=json.dumps(envelopes),
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


class ShadowCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nexus-shadow-")
        self.led = Ledger(os.path.join(self.dir, "ledger.sqlite"))
        self.sent = []

    def tearDown(self):
        self.led.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def send(self, records, token):
        self.sent.append(records)
        return {"accepted": [{"event_id": r["event_id"], "fresh": True} for r in records], "refused": []}

    def walk(self):
        rid = dispatch.file_control_request(self.led, "broker-studio", "lane x", "click Allow", "no shell route",
                                            "grant reads allowed", ["/usr/bin/true"], now=T0)
        workers = {"broker-studio": {"argv": ["/usr/bin/true"], "bound_s": 60}}
        dispatch.poll(self.led, now=T0 + 1, workers=workers, launch=lambda *a: 4242)
        dispatch.submit_receipt(self.led, rid, DONE, now=T0 + 2)
        dispatch.poll(self.led, now=T0 + 3, workers=workers, run=lambda *a, **k: Proc(0), stop=lambda r: True)
        return rid

    def test_envelope_is_content_addressed_and_byte_identical(self):
        rid = self.walk()
        envs, _ = shadow.collect(self.led, 0, now=T0)
        env = next(e for e in envs if e["record_id"] == rid)
        self.assertEqual(env["record_json"], shadow.canonical(dispatch.get(self.led, rid)))
        self.assertEqual(env["sha256"], hashlib.sha256(env["record_json"].encode()).hexdigest())
        self.assertEqual(env["event_id"], f"office.dispatch:{rid}:{env['revision']}")
        again, _ = shadow.collect(self.led, 0, now=T0 + 99)
        self.assertEqual(next(e for e in again if e["record_id"] == rid)["event_id"], env["event_id"])

    def test_studio_outcomes_name_each_step(self):
        rid = self.walk()
        env = next(e for e in shadow.collect(self.led, 0)[0] if e["record_id"] == rid)
        self.assertEqual([t["studio_outcome"] for t in env["transitions"]],
                         ["filed", "dispatch", "launched", "receipt", "verify_pass", "close"])

    def test_shadow_rules_match_the_studio_on_a_full_walk(self):
        rid = self.walk()
        fid = faults.report(self.led, "door down", "watchdog", "detected", now=T0)["fault_id"]
        faults.report(self.led, "door down", "watchdog", "repairing", fault_id=fid, action="restart", now=T0 + 1)
        first = shadow.collect(self.led, 0)[0]
        faults.report(self.led, "door down", "watchdog", "verified_fixed", fault_id=fid, surface="GET /health",
                      now=T0 + 2)
        second = shadow.collect(self.led, max(e["transitions"][-1]["seq"] for e in first))[0]
        verdicts = judge(first + second)
        self.assertTrue(all(v["bad"] is None for v in verdicts))
        pairs = [p for v in verdicts for p in v["pairs"]]
        self.assertTrue(pairs and all(p["match"] for p in pairs), pairs)
        self.assertIn(rid, {v["event_id"].split(":")[1] for v in verdicts})
        self.assertEqual({p["to"] for p in pairs if p["lens"] == "fault"}, {"repairing", "verified_fixed"})

    def test_drifted_studio_decision_is_a_mismatch(self):
        rid = self.walk()
        env = next(e for e in shadow.collect(self.led, 0)[0] if e["record_id"] == rid)
        env["transitions"][1]["studio_outcome"] = "escalate:decision"  # a rule the shadow does not hold
        pairs = judge([env])[0]["pairs"]
        self.assertFalse(pairs[1]["match"])
        self.assertTrue(all(p["match"] for i, p in enumerate(pairs) if i != 1))

    def test_tampered_record_is_refused_on_hash(self):
        rid = self.walk()
        env = next(e for e in shadow.collect(self.led, 0)[0] if e["record_id"] == rid)
        env["record_json"] = env["record_json"].replace("broker-studio", "broker-macbook", 1)
        self.assertEqual(judge([env])[0]["bad"], "sha256")

    def test_first_push_starts_at_now_then_copies_from_creation(self):
        self.walk()
        start = shadow.push(self.led, send=self.send, token="t")
        self.assertEqual(start["sent"], 0)
        self.assertEqual(self.sent, [])
        rid = dispatch.file_control_request(self.led, "broker-studio", "lane y", "click", "none", "s",
                                            ["/usr/bin/true"], now=T0 + 10)
        report = shadow.push(self.led, send=self.send, token="t")
        self.assertEqual([r["record_id"] for r in self.sent[0]], [rid])
        self.assertEqual(report["fresh"], 1)
        self.assertEqual(shadow.push(self.led, send=self.send, token="t")["sent"], 0)

    def test_failed_send_keeps_the_cursor(self):
        shadow.push(self.led, send=self.send, token="t")
        dispatch.file_control_request(self.led, "broker-studio", "lane y", "click", "none", "s",
                                      ["/usr/bin/true"], now=T0)

        def down(records, token):
            raise OSError("network down")
        with self.assertRaises(OSError):
            shadow.push(self.led, send=down, token="t")
        self.assertEqual(shadow.push(self.led, send=self.send, token="t")["sent"], 1)

    def test_no_token_and_non_studio_ledger_never_send(self):
        real, shadow.TOKEN_FILE = shadow.TOKEN_FILE, os.path.join(self.dir, "absent")
        try:
            self.assertEqual(shadow.push(self.led, send=self.send), {"skipped": "no shadow token"})
        finally:
            shadow.TOKEN_FILE = real
        self.assertIsNone(shadow.tick(self.led))
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
