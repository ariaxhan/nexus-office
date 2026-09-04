"""Isolated care account lifecycle and rate-limit probe."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Mapping, Protocol


@dataclass(frozen=True)
class Response:
    status: int
    identifier: str = ""
    headers: Mapping[str, str] | None = None


@dataclass(frozen=True)
class Evidence:
    at: str
    step: str
    status: int | None
    account: str
    session: str
    retry_after: str = ""
    rate_limit_reset: str = ""
    error: str = ""


class CareClient(Protocol):
    def create_account(self, run_id: str) -> Response: ...
    def record_consent(self, account_id: str) -> Response: ...
    def start_session(self, account_id: str) -> Response: ...
    def verify_session(self, account_id: str, session_id: str) -> Response: ...
    def limited_request(self, account_id: str) -> Response: ...
    def delete_account(self, account_id: str) -> Response: ...
    def access_account(self, account_id: str) -> Response: ...
    def cleanup(self, run_id: str) -> None: ...


class ProbeFailure(RuntimeError):
    def __init__(self, evidence: tuple[Evidence, ...]):
        super().__init__("care lifecycle probe failed")
        self.evidence = evidence


def run_care_probe(client: CareClient, request_threshold: int,
                   timeout_s: float = 60) -> tuple[Evidence, ...]:
    """Verify one sandbox lifecycle and its documented request threshold."""
    if request_threshold < 1 or timeout_s <= 0:
        raise ValueError("request threshold and timeout must be positive")
    run_id = f"care-{uuid.uuid4().hex}"
    started = time.monotonic()
    evidence: list[Evidence] = []
    identifiers = ["", ""]
    try:
        _run_lifecycle(client, run_id, request_threshold, timeout_s, started,
                       evidence, identifiers)
    except Exception as exc:
        if not evidence or not evidence[-1].error:
            evidence.append(_event(
                "probe", None, *identifiers, error=_safe_error(exc, identifiers)
            ))
    finally:
        _cleanup(client, run_id, evidence, identifiers)
    if any(row.error for row in evidence):
        raise ProbeFailure(tuple(evidence))
    return tuple(evidence)


def _run_lifecycle(client: CareClient, run_id: str, threshold: int,
                   timeout_s: float, started: float,
                   evidence: list[Evidence], identifiers: list[str]) -> None:
    def check_timeout() -> None:
        _check_timeout(started, timeout_s)

    identifiers[0] = _expect(evidence, "create", client.create_account(run_id),
                             {201}).identifier
    check_timeout()
    account_id = identifiers[0]
    _expect(evidence, "consent", client.record_consent(account_id),
            {200, 201, 204}, account_id)
    check_timeout()
    identifiers[1] = _expect(evidence, "session", client.start_session(account_id),
                             {201}, account_id).identifier
    check_timeout()
    session_id = identifiers[1]
    _expect(evidence, "verify", client.verify_session(account_id, session_id),
            {200}, account_id, session_id)
    check_timeout()
    _check_rate_limit(client, account_id, session_id, threshold, evidence)
    check_timeout()
    _expect(evidence, "delete", client.delete_account(account_id),
            {200, 202, 204}, account_id, session_id)
    check_timeout()
    _expect(evidence, "deleted-access", client.access_account(account_id),
            {401, 403, 404, 410}, account_id, session_id)
    check_timeout()


def _cleanup(client: CareClient, run_id: str, evidence: list[Evidence],
             identifiers: list[str]) -> None:
    try:
        client.cleanup(run_id)
        evidence.append(_event("cleanup", None, *identifiers))
    except Exception as exc:
        evidence.append(_event(
            "cleanup", None, *identifiers, error=_safe_error(exc, identifiers)
        ))


def _check_rate_limit(client: CareClient, account_id: str, session_id: str,
                      threshold: int, evidence: list[Evidence]) -> None:
    for attempt in range(threshold + 1):
        expected = {200} if attempt < threshold else {429}
        response = _expect(evidence, "rate-limit", client.limited_request(account_id),
                           expected, account_id, session_id)
    headers = {key.lower(): value for key, value in (response.headers or {}).items()}
    if not headers.get("retry-after") or not headers.get("x-ratelimit-reset"):
        raise AssertionError("rate limit response lacks retry metadata")


def _expect(evidence: list[Evidence], step: str, response: Response,
            expected: set[int], account_id: str = "", session_id: str = "") -> Response:
    error = "" if response.status in expected else (
        f"expected status {sorted(expected)}, got {response.status}"
    )
    evidence.append(_event(step, response.status, account_id or response.identifier,
                           session_id or (response.identifier if step == "session" else ""),
                           response, error))
    if error:
        raise AssertionError(error)
    if step in {"create", "session"} and not response.identifier:
        raise AssertionError(f"{step} response lacks identifier")
    return response


def _event(step: str, status: int | None, account_id: str, session_id: str,
           response: Response | None = None, error: str = "") -> Evidence:
    raw_headers = response.headers if response else None
    headers = {key.lower(): value for key, value in (raw_headers or {}).items()}
    return Evidence(datetime.now(UTC).isoformat(), step, status, _redact(account_id),
                    _redact(session_id), headers.get("retry-after", ""),
                    headers.get("x-ratelimit-reset", ""), error)


def _redact(identifier: str) -> str:
    return f"***{identifier[-4:]}" if identifier else ""


def _safe_error(exc: Exception, identifiers: list[str]) -> str:
    error = str(exc)
    for identifier in filter(None, identifiers):
        error = error.replace(identifier, _redact(identifier))
    return error


def _check_timeout(started: float, timeout_s: float) -> None:
    if time.monotonic() - started > timeout_s:
        raise TimeoutError(f"care probe exceeded {timeout_s:g}s")
