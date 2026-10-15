"""Receipt rows summarize into success, escalation and disk numbers, never from the model's own claim."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import micropatch_report as report  # noqa: E402

ROWS = [
    {"result": "verified", "executor_ok": False, "wall_s": 10.0, "disk_bytes": 300, "leaked": [], "model_tokens": 100},
    {"result": "escalated", "executor_ok": True, "wall_s": 30.0, "disk_bytes": 900, "leaked": ["x"], "model_tokens": 300},
    {"result": "verified", "executor_ok": True, "wall_s": 20.0, "disk_bytes": 600, "leaked": [], "model_tokens": 200},
]


class Summarize(unittest.TestCase):
    def test_counts_come_from_result_not_executor_claim(self):
        s = report.summarize(ROWS)
        self.assertEqual(3, s["cases"])
        self.assertEqual(2, s["verified"])
        self.assertEqual(1, s["escalated"])
        self.assertAlmostEqual(2 / 3, s["success_rate"])
        self.assertAlmostEqual(1 / 3, s["escalation_rate"])

    def test_wall_tokens_and_disk(self):
        s = report.summarize(ROWS)
        self.assertEqual(20.0, s["median_wall_s"])
        self.assertEqual(600, s["total_model_tokens"])
        self.assertEqual(900, s["peak_disk_bytes"])
        self.assertEqual(1, s["residual_leaks"])

    def test_empty(self):
        s = report.summarize([])
        self.assertEqual(0, s["cases"])
        self.assertEqual(0.0, s["success_rate"])
        self.assertEqual(0, s["peak_disk_bytes"])


class Load(unittest.TestCase):
    def test_reads_every_jsonl_row_and_skips_blank_lines(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d, "a.jsonl"), Path(d, "b.jsonl")
            a.write_text(json.dumps(ROWS[0]) + "\n\n" + json.dumps(ROWS[1]) + "\n")
            b.write_text(json.dumps(ROWS[2]) + "\n")
            self.assertEqual(ROWS, report.load([str(a), str(b)]))

    def test_corrupt_row_is_an_error_not_a_skipped_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            a = Path(d, "a.jsonl")
            a.write_text(json.dumps(ROWS[0]) + "\n{not json\n")
            with self.assertRaises(json.JSONDecodeError):
                report.load([str(a)])


class ByClass(unittest.TestCase):
    def test_groups_rows_by_commit_class(self):
        rows = [dict(ROWS[0], commit="a"), dict(ROWS[1], commit="b"), dict(ROWS[2], commit="c")]
        classes = {"a": {"class": "lifecycle"}, "b": {"class": "lifecycle"}, "c": {"class": "wiring"}}
        out = report.by_class(rows, classes)
        self.assertEqual(["lifecycle", "wiring"], sorted(out))
        self.assertEqual(2, out["lifecycle"]["cases"])
        self.assertEqual(1, out["lifecycle"]["verified"])
        self.assertEqual(1, out["wiring"]["verified"])

    def test_unknown_or_missing_commit_is_unclassified(self):
        rows = [dict(ROWS[0], commit="zzz"), dict(ROWS[1])]
        out = report.by_class(rows, {})
        self.assertEqual({"unclassified"}, set(out))
        self.assertEqual(2, out["unclassified"]["cases"])


class Main(unittest.TestCase):
    def test_prints_overall_and_by_class_when_classes_given(self):
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as d:
            rows = Path(d, "r.jsonl")
            rows.write_text(json.dumps(dict(ROWS[0], commit="a")) + "\n")
            classes = Path(d, "c.json")
            classes.write_text(json.dumps({"a": {"class": "wiring"}}))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                report.main(["--classes", str(classes), str(rows)])
            got = json.loads(out.getvalue())
            self.assertEqual(1, got["all"]["verified"])
            self.assertEqual(1, got["by_class"]["wiring"]["cases"])

    def test_without_classes_prints_overall_only(self):
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as d:
            rows = Path(d, "r.jsonl")
            rows.write_text(json.dumps(ROWS[1]) + "\n")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                report.main([str(rows)])
            self.assertEqual({"all"}, set(json.loads(out.getvalue())))


if __name__ == "__main__":
    unittest.main()
