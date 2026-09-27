"""The #187 decision benchmark: extraction, the deterministic tier, scoring."""

import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import decision_bench as bench  # noqa: E402


def ledger(events):
    db = sqlite3.connect(":memory:")
    db.execute("create table events (id integer primary key, ts real, kind text, "
               "subject text, payload text, source text)")
    for kind, subject, payload in events:
        db.execute("insert into events (ts, kind, subject, payload, source) "
                   "values (0, ?, ?, ?, 'test')", (kind, subject, json.dumps(payload)))
    return db


def issue(n, labels, title="t", body="b"):
    return {"html_url": f"https://github.com/o/r/issues/{n}", "title": title,
            "body": body, "labels": [{"name": l} for l in labels]}


class Extract(unittest.TestCase):
    def test_gold_comes_from_human_labels_and_untriaged_is_dropped(self):
        db = ledger([("work.issue", "a", issue(1, ["waiting on human"])),
                     ("work.issue", "b", issue(2, ["ready"])),
                     ("work.issue", "c", issue(3, []))])
        cases = {c["id"].rsplit("/", 1)[1]: c for c in bench.extract(db)}
        self.assertEqual(cases["1"]["gold"], 1)
        self.assertEqual(cases["2"]["gold"], 0)
        self.assertNotIn("3", cases)

    def test_labels_never_leak_into_the_candidate_text(self):
        db = ledger([("work.issue", "a", issue(1, ["waiting on human"], "Title", "Body"))])
        self.assertNotIn("waiting", bench.extract(db)[0]["text"])

    def test_lane_outcome_uses_the_first_attempt(self):
        db = ledger([("work.issue", "a", issue(5, [])),
                     ("work.lane", "f1", {"repo": "o/r", "issue": 5, "state": "HELD"}),
                     ("work.lane", "f2", {"repo": "o/r", "issue": 5, "state": "CLOSED"})])
        (case,) = bench.extract(db)
        self.assertEqual((case["task"], case["gold"]), ("lane_held", 1))


class Rules(unittest.TestCase):
    def test_banner_is_the_deterministic_signal(self):
        p, cost = bench.rules({"task": "needs_human", "text": "Blocked on you: pick one"})
        self.assertGreater(p, 0.5)
        self.assertEqual(cost, 0.0)

    def test_rules_abstain_where_no_rule_exists(self):
        self.assertEqual(bench.rules({"task": "lane_held", "text": "x"})[0], 0.5)


class Score(unittest.TestCase):
    def test_a_failed_call_is_wrong_not_skipped(self):
        cases = [{"id": "1", "task": "t", "gold": 1}, {"id": "2", "task": "t", "gold": 0}]

        def ask(case):
            if case["id"] == "2":
                raise RuntimeError("timeout")
            return 0.9, 0.001
        (row,) = bench.score(list(bench.run(cases, "x", ask)))
        self.assertEqual((row["accuracy"], row["errors"]), (0.5, 1))

    def test_confidence_band_measures_escalation_usefulness(self):
        rows = [{"candidate": "x", "task": "t", "gold": g, "p": p, "latency_s": 1}
                for g, p in [(1, 0.95), (0, 0.05), (1, 0.5), (0, 0.6)]]
        (row,) = bench.score(rows)
        self.assertEqual((row["confident_share"], row["confident_accuracy"]), (0.5, 1.0))

    def test_parse_p_rejects_invalid_probabilities_and_prose(self):
        self.assertEqual(bench._parse_p('{"p": 0.4}'), 0.4)
        self.assertEqual(bench._parse_p('{"p": 1e-2}'), 0.01)
        self.assertEqual(bench._parse_p('{"p": 2.5E-1}'), 0.25)
        with self.assertRaises(ValueError):
            bench._parse_p('{"p": -1e-2}')
        with self.assertRaises(ValueError):
            bench._parse_p('{"p": 1.4}')
        with self.assertRaises(ValueError):
            bench._parse_p("probably yes")


if __name__ == "__main__":
    unittest.main()
