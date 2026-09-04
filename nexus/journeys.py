"""Data-driven, transactional browser journeys across product locales."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Locale:
    code: str
    route: str
    controls: tuple[str, ...]
    fallback_controls: tuple[str, ...]


@dataclass(frozen=True)
class Journey:
    entry: str
    completion: str
    steps: tuple[str, ...]
    locales: tuple[Locale, ...]


@dataclass(frozen=True)
class FailureEvidence:
    locale: str
    step: str
    route: str
    screenshot: str
    console_errors: tuple[str, ...]
    error: str


class Browser(Protocol):
    @property
    def route(self) -> str: ...

    def open(self, route: str) -> None: ...
    def set_timeout(self, timeout_s: float) -> None: ...
    def assert_control(self, text: str) -> None: ...
    def assert_control_absent(self, text: str) -> None: ...
    def act(self, step: str, run_id: str) -> None: ...
    def assert_progress(self, run_id: str) -> None: ...
    def assert_completion(self, state: str) -> None: ...
    def screenshot(self, path: Path) -> None: ...
    def console_errors(self) -> tuple[str, ...]: ...
    def cleanup(self, run_id: str) -> None: ...
    def close(self) -> None: ...


class JourneyFailure(RuntimeError):
    def __init__(self, evidence: FailureEvidence):
        super().__init__(
            f"locale={evidence.locale} step={evidence.step} "
            f"route={evidence.route}: {evidence.error}"
        )
        self.evidence = evidence


def run_journeys(journey: Journey, browser_factory, evidence_dir: Path,
                 timeout_s: float = 300) -> tuple[str, ...]:
    """Run every locale with isolated data; always remove data and close."""
    completed = []
    for locale in journey.locales:
        _run_locale(journey, locale, browser_factory(), evidence_dir, timeout_s)
        completed.append(locale.code)
    return tuple(completed)


def _run_locale(journey: Journey, locale: Locale, browser: Browser,
                evidence_dir: Path, timeout_s: float) -> None:
    run_id = f"journey-{locale.code}-{uuid.uuid4().hex}"
    started = time.monotonic()
    step = "entry"
    evidence = None
    cause = None
    try:
        browser.set_timeout(timeout_s)
        browser.open(locale.route + journey.entry)
        _assert_locale(browser, locale)
        for step in journey.steps:
            _check_timeout(started, timeout_s)
            browser.act(step, run_id)
            browser.assert_progress(run_id)
        step = "completion"
        browser.assert_completion(journey.completion)
    except Exception as exc:
        cause = exc
        evidence = _evidence(browser, locale.code, step, run_id, evidence_dir, exc)
    evidence, cause = _finish_browser(
        browser, locale.code, run_id, evidence_dir, evidence, cause
    )
    if evidence:
        raise JourneyFailure(evidence) from cause


def _finish_browser(browser: Browser, locale: str, run_id: str,
                    evidence_dir: Path, evidence: FailureEvidence | None,
                    cause: Exception | None
                    ) -> tuple[FailureEvidence | None, Exception | None]:
    operations = (
        ("cleanup", lambda: browser.cleanup(run_id)),
        ("close", browser.close),
    )
    for step, operation in operations:
        try:
            operation()
        except Exception as exc:
            if evidence is None:
                cause = exc
                evidence = _evidence(
                    browser, locale, step, run_id, evidence_dir, exc
                )
    return evidence, cause


def _assert_locale(browser: Browser, locale: Locale) -> None:
    if not browser.route.startswith(locale.route):
        raise AssertionError(f"expected route prefix {locale.route}, got {browser.route}")
    for text in locale.controls:
        browser.assert_control(text)
    for text in locale.fallback_controls:
        browser.assert_control_absent(text)


def _check_timeout(started: float, timeout_s: float) -> None:
    if time.monotonic() - started > timeout_s:
        raise TimeoutError(f"journey exceeded {timeout_s:g}s")


def _evidence(browser: Browser, locale: str, step: str, run_id: str,
              root: Path, exc: Exception) -> FailureEvidence:
    shot = root / f"{run_id}-{step}.png"
    screenshot = ""
    try:
        root.mkdir(parents=True, exist_ok=True)
        browser.screenshot(shot)
        screenshot = str(shot)
    except Exception:
        pass
    try:
        console_errors = browser.console_errors()
    except Exception as evidence_error:
        console_errors = (f"console capture failed: {evidence_error}",)
    return FailureEvidence(
        locale, step, _route(browser), screenshot, console_errors, str(exc)
    )


def _route(browser: Browser) -> str:
    try:
        return browser.route
    except Exception as exc:
        return f"route capture failed: {exc}"
