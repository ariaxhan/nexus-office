"""The tap on the shoulder, and the four ways it must stay quiet.

The interesting tests here are not "it posted". They are the ones about the post
that must NOT happen: the relay answering 200 instead of 202 and the caller being
told the truth about it, an unset secret, a dead network, and the same event
replayed six times by a GitHub redelivery. A notifier that gets any of those
wrong either lies about delivery or turns one event into a storm.

    python3 -m unittest discover -s tests -p 'test_*.py'
"""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import pathlib
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))

import buzz  # noqa: E402

ENV = ("OFFICE_BUZZ_SECRET", "OFFICE_BUZZ_HOOK_URL", "OFFICE_BUZZ_MIN_S")


class _Resp:
    """Just enough of an http.client.HTTPResponse for a `with` block."""

    def __init__(self, status):
        self.status = status

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class BuzzTest(unittest.TestCase):
    def setUp(self):
        for key in ENV:
            os.environ.pop(key, None)
        os.environ["OFFICE_BUZZ_SECRET"] = "shhh-not-a-real-secret"
        # Reloaded per test so the throttle and the said-once log start empty:
        # both are process memory on purpose, and a test that inherited them
        # would pass for the wrong reason.
        self.buzz = importlib.reload(buzz)

    def tearDown(self):
        for key in ENV:
            os.environ.pop(key, None)

    # ── helpers ──────────────────────────────────────────────────────────────

    @contextlib.contextmanager
    def relay(self, status=202, boom=None):
        """A fake relay. Yields (calls, stderr); calls holds every Request made."""
        calls = []

        def fake(req, timeout=None):
            calls.append((req, timeout))
            if boom is not None:
                raise boom
            return _Resp(status)

        err = io.StringIO()
        with mock.patch("urllib.request.urlopen", fake), contextlib.redirect_stderr(err):
            yield calls, err

    # ── the contract ─────────────────────────────────────────────────────────

    def test_202_is_the_only_success(self):
        with self.relay(202) as (calls, _):
            self.assertTrue(self.buzz.notify("gate", "a gate is raised"))
        self.assertEqual(len(calls), 1)

    def test_200_is_a_failure_and_says_so(self):
        with self.relay(200) as (calls, err):
            self.assertFalse(self.buzz.notify("gate", "a gate is raised"))
        self.assertEqual(len(calls), 1)
        self.assertIn("200", err.getvalue())

    def test_the_body_field_is_board(self):
        """`text` is a relay built-in registered after webhook fields, so a body
        field called `text` posts an empty message with no error."""
        with self.relay() as (calls, _):
            self.buzz.notify("gate", "a gate is raised: blinkbuild wants Bash")
        req, timeout = calls[0]
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(list(body), ["board"])
        self.assertEqual(body["board"], "a gate is raised: blinkbuild wants Bash")
        self.assertNotIn("text", body)
        self.assertEqual(timeout, 20)

    def test_the_request_is_the_researched_call(self):
        with self.relay() as (calls, _):
            self.buzz.notify("gate", "hello")
        req, _ = calls[0]
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.full_url, self.buzz.DEFAULT_URL)
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.assertEqual(req.get_header("X-webhook-secret"), "shhh-not-a-real-secret")

    def test_the_url_is_overridable(self):
        os.environ["OFFICE_BUZZ_HOOK_URL"] = "https://example.invalid/hooks/abc"
        with self.relay() as (calls, _):
            self.buzz.notify("gate", "hello")
        self.assertEqual(calls[0][0].full_url, "https://example.invalid/hooks/abc")

    # ── the ways it stays quiet ──────────────────────────────────────────────

    def test_no_secret_means_false_and_no_request(self):
        os.environ.pop("OFFICE_BUZZ_SECRET")
        with self.relay() as (calls, err):
            self.assertFalse(self.buzz.notify("gate", "a gate is raised"))
        self.assertEqual(calls, [])
        self.assertIn("buzz not configured", err.getvalue())

    def test_unconfigured_is_logged_once_not_every_event(self):
        os.environ.pop("OFFICE_BUZZ_SECRET")
        with self.relay() as (_, err):
            for _i in range(5):
                self.buzz.notify("gate", "a gate is raised", subject=str(_i))
        self.assertEqual(err.getvalue().count("buzz not configured"), 1)

    def test_a_blank_url_override_falls_back_to_the_default(self):
        """A wrapper that exports the variable empty must not silently disable
        the only channel this room has."""
        os.environ["OFFICE_BUZZ_HOOK_URL"] = "   "
        with self.relay() as (calls, _):
            self.assertTrue(self.buzz.notify("gate", "hello"))
        self.assertEqual(calls[0][0].full_url, self.buzz.DEFAULT_URL)

    def test_a_network_error_is_false_and_logged_never_raised(self):
        with self.relay(boom=urllib.error.URLError("no route to host")) as (calls, err):
            self.assertFalse(self.buzz.notify("gate", "a gate is raised"))
        self.assertEqual(len(calls), 1)
        self.assertIn("unreachable", err.getvalue())

    def test_an_http_error_is_false_and_logged(self):
        boom = urllib.error.HTTPError("https://x.invalid", 401, "no", {}, None)
        with self.relay(boom=boom) as (_, err):
            self.assertFalse(self.buzz.notify("gate", "a gate is raised"))
        self.assertIn("401", err.getvalue())

    def test_the_log_never_carries_the_secret(self):
        with self.relay(boom=urllib.error.URLError("shhh-not-a-real-secret in a repr")) as (_, err):
            self.buzz.notify("gate", "a gate is raised")
        self.assertNotIn("shhh-not-a-real-secret", err.getvalue())

    def test_empty_text_posts_nothing(self):
        with self.relay() as (calls, _):
            self.assertFalse(self.buzz.notify("gate", "   "))
        self.assertEqual(calls, [])

    # ── the throttle ─────────────────────────────────────────────────────────

    def test_the_same_kind_and_subject_fires_once_per_window(self):
        with self.relay() as (calls, _):
            self.assertTrue(self.buzz.notify("landed", "landed: a #1", subject="a"))
            self.assertFalse(self.buzz.notify("landed", "landed: a #2", subject="a"))
            self.assertFalse(self.buzz.notify("landed", "landed: a #3", subject="a"))
        self.assertEqual(len(calls), 1)

    def test_a_different_subject_is_a_different_tap(self):
        with self.relay() as (calls, _):
            self.assertTrue(self.buzz.notify("landed", "landed: a", subject="a"))
            self.assertTrue(self.buzz.notify("landed", "landed: b", subject="b"))
        self.assertEqual(len(calls), 2)

    def test_a_different_kind_on_one_subject_is_a_different_tap(self):
        with self.relay() as (calls, _):
            self.assertTrue(self.buzz.notify("landed", "landed: a", subject="a"))
            self.assertTrue(self.buzz.notify("refused", "refused: a", subject="a"))
        self.assertEqual(len(calls), 2)

    def test_the_window_is_configurable_and_zero_disables_it(self):
        os.environ["OFFICE_BUZZ_MIN_S"] = "0"
        with self.relay() as (calls, _):
            self.assertTrue(self.buzz.notify("landed", "one", subject="a"))
            self.assertTrue(self.buzz.notify("landed", "two", subject="a"))
        self.assertEqual(len(calls), 2)

    def test_a_junk_window_falls_back_to_the_default(self):
        os.environ["OFFICE_BUZZ_MIN_S"] = "soon"
        self.assertEqual(self.buzz._min_s(), float(self.buzz.DEFAULT_MIN_S))

    def test_a_failed_post_still_consumes_the_window(self):
        """A relay that is failing under a redelivery storm is the moment you
        least want sixty retries."""
        with self.relay(500) as (calls, _):
            self.assertFalse(self.buzz.notify("landed", "one", subject="a"))
            self.assertFalse(self.buzz.notify("landed", "two", subject="a"))
        self.assertEqual(len(calls), 1)

    # ── what the taps say ────────────────────────────────────────────────────

    def test_gate_raised_names_the_bot_the_permission_and_the_target(self):
        text = self.buzz.gate_raised({
            "bot": "blinkbuild",
            "permission": "Bash",
            "target": "npx playwright install --with-deps chromium",
        })
        self.assertEqual(
            text,
            "a gate is raised: blinkbuild wants Bash on "
            "npx playwright install --with-deps chromium",
        )

    def test_gate_raised_survives_an_empty_gate(self):
        self.assertEqual(
            self.buzz.gate_raised({}),
            "a gate is raised: an agent wants permission on something it did not name",
        )

    def test_gate_raised_trims_the_target_to_eighty(self):
        text = self.buzz.gate_raised({"bot": "b", "permission": "Bash", "target": "x" * 300})
        target = text.split(" on ", 1)[1]
        self.assertEqual(len(target), 80)
        self.assertTrue(target.endswith("..."))

    def test_pr_landed_reads_as_a_sentence(self):
        self.assertEqual(
            self.buzz.pr_landed("ariaxhan/nexus-office", 24, "webhooks over Funnel"),
            "landed: ariaxhan/nexus-office #24 webhooks over Funnel",
        )

    def test_lane_refused_reads_as_a_sentence(self):
        self.assertEqual(
            self.buzz.lane_refused("ariaxhan/nexus-office", 24, "the tree was dirty"),
            "a lane refused on ariaxhan/nexus-office #24: the tree was dirty",
        )

    def test_lane_refused_without_an_issue_drops_the_hash(self):
        self.assertEqual(
            self.buzz.lane_refused("ariaxhan/nexus-office", "", "no account holds push here"),
            "a lane refused on ariaxhan/nexus-office: no account holds push here",
        )

    def test_every_shape_stays_under_two_hundred_characters(self):
        long = "y" * 500
        for text in (
            self.buzz.gate_raised({"bot": long, "permission": long, "target": long}),
            self.buzz.pr_landed(long, long, long),
            self.buzz.lane_refused(long, long, long),
        ):
            self.assertLess(len(text), 200, text)
            self.assertNotIn("\n", text)

    def test_no_shape_leaks_an_em_dash(self):
        for text in (
            self.buzz.gate_raised({"bot": "b", "permission": "p", "target": "t"}),
            self.buzz.pr_landed("r", 1, "t"),
            self.buzz.lane_refused("r", 1, "d"),
        ):
            self.assertNotIn("—", text)


if __name__ == "__main__":
    unittest.main()


class WatchTest(unittest.TestCase):
    def test_only_a_hand_that_was_not_there_before_is_new(self):
        gates = [{"id": "a"}, {"id": "b"}, {"id": ""}]
        self.assertEqual(buzz.new_gate_ids({"a"}, gates), ["b"])
        self.assertEqual(buzz.new_gate_ids(set(), []), [])

    def test_receipts_are_tailed_from_where_we_left_off_and_a_rotated_file_reads_from_the_top(self):
        import tempfile, os, json
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "r.jsonl")
            with open(path, "w") as fh:
                fh.write(json.dumps({"outcome": "landed", "repo": "a/b", "issue": "1"}) + "\n")
            rows, off = buzz.new_receipts(path, 0)
            self.assertEqual(len(rows), 1)
            rows, off2 = buzz.new_receipts(path, off)
            self.assertEqual(rows, [])
            self.assertEqual(off2, off)
            with open(path, "a") as fh:
                fh.write("not json\n" + json.dumps({"outcome": "refused", "repo": "a/b", "issue": "2"}) + "\n")
            rows, off3 = buzz.new_receipts(path, off2)
            self.assertEqual([r["outcome"] for r in rows], ["refused"])
            with open(path, "w") as fh:
                fh.write(json.dumps({"outcome": "survey", "repo": "a/b"}) + "\n")
            rows, _ = buzz.new_receipts(path, off3)
            self.assertEqual([r["outcome"] for r in rows], ["survey"])
