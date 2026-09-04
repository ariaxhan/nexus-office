from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from nexus.journeys import Journey, JourneyFailure, Locale, run_journeys  # noqa: E402


LOCALES = (
    Locale("kr", "/kr", ("시작", "다음", "완료"), ("Start", "Next", "Complete")),
    Locale("en", "/en", ("Start", "Next", "Complete"), ("시작", "다음", "완료")),
    Locale("es", "/es", ("Comenzar", "Siguiente", "Completar"), ("Start", "Next", "Complete")),
)
JOURNEY = Journey("/lesson", "complete", ("start", "respond", "finish"), LOCALES)


class Browser:
    def __init__(self, fail=None):
        self.route = ""
        self.fail = fail
        self.progress = []
        self.cleaned = []
        self.closed = False
        self.absent = []
        self.timeout_s = None

    def open(self, route):
        self.route = route

    def set_timeout(self, timeout_s):
        self.timeout_s = timeout_s

    def assert_control(self, text):
        if self.fail == text:
            raise AssertionError(f"missing localized control: {text}")

    def assert_control_absent(self, text):
        self.absent.append(text)

    def act(self, step, run_id):
        self.route = self.route.rsplit("/", 1)[0] + f"/{step}"
        self.progress.append((run_id, step))

    def assert_progress(self, run_id):
        if not self.progress or self.progress[-1][0] != run_id:
            raise AssertionError("progress did not persist")

    def assert_completion(self, state):
        if state != "complete" or not self.route.endswith("/finish"):
            raise AssertionError("completion state missing")

    def screenshot(self, path):
        path.write_bytes(b"png")

    def console_errors(self):
        return ("localized control missing",) if self.fail else ()

    def cleanup(self, run_id):
        self.cleaned.append(run_id)

    def close(self):
        self.closed = True


class JourneyProbeTest(unittest.TestCase):
    def test_all_locale_variants_complete_with_unique_sandbox_data(self):
        browsers = []

        def factory():
            browser = Browser()
            browsers.append(browser)
            return browser

        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(("kr", "en", "es"),
                             run_journeys(JOURNEY, factory, pathlib.Path(root)))
        run_ids = [browser.cleaned[0] for browser in browsers]
        self.assertEqual(3, len(set(run_ids)))
        self.assertTrue(all(browser.closed for browser in browsers))
        self.assertTrue(all(len(browser.progress) == 3 for browser in browsers))
        self.assertEqual([locale.fallback_controls for locale in LOCALES],
                         [tuple(browser.absent) for browser in browsers])
        self.assertTrue(all(browser.timeout_s == 300 for browser in browsers))

    def test_localized_failure_names_locale_step_route_and_evidence(self):
        browsers = []

        def factory():
            locale = LOCALES[len(browsers)]
            browser = Browser("Siguiente" if locale.code == "es" else None)
            browsers.append(browser)
            return browser

        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(JourneyFailure) as raised:
                run_journeys(JOURNEY, factory, pathlib.Path(root))
            evidence = raised.exception.evidence
            self.assertEqual(("es", "entry", "/es/lesson"),
                             (evidence.locale, evidence.step, evidence.route))
            self.assertTrue(pathlib.Path(evidence.screenshot).exists())
            self.assertEqual(("localized control missing",), evidence.console_errors)
        self.assertTrue(all(browser.cleaned and browser.closed for browser in browsers))

    def test_cleanup_failure_has_evidence_and_still_closes(self):
        class CleanupFailureBrowser(Browser):
            def cleanup(self, run_id):
                super().cleanup(run_id)
                raise RuntimeError("cleanup failed")

        browser = CleanupFailureBrowser()
        journey = Journey("/lesson", "complete", ("finish",), (LOCALES[0],))

        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(JourneyFailure) as raised:
                run_journeys(journey, lambda: browser, pathlib.Path(root))
            self.assertEqual("cleanup", raised.exception.evidence.step)
            self.assertTrue(pathlib.Path(raised.exception.evidence.screenshot).exists())
        self.assertTrue(browser.closed)


if __name__ == "__main__":
    unittest.main()
