"""No-charge checkout probe for a payment-provider sandbox."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Mapping, Protocol


@dataclass(frozen=True)
class Checkout:
    identifier: str
    amount: int
    currency: str
    success_redirect: str
    live_charge: bool
    response: Mapping[str, object]
    accepted: bool = True


@dataclass(frozen=True)
class Evidence:
    at: str
    step: str
    checkout: str
    request: Mapping[str, object]
    response: Mapping[str, object]
    error: str = ""


class CheckoutClient(Protocol):
    def create_checkout(self, request: Mapping[str, object]) -> Checkout: ...
    def expire_checkout(self, checkout_id: str) -> None: ...


class ProbeFailure(RuntimeError):
    def __init__(self, evidence: tuple[Evidence, ...]):
        super().__init__("checkout probe failed")
        self.evidence = evidence


def run_checkout_probe(client: CheckoutClient, *, amount: int = 0,
                       currency: str = "usd",
                       success_redirect: str = "https://example.invalid/probe/success",
                       timeout_s: float = 30) -> tuple[Evidence, ...]:
    """Create, verify, and expire one uniquely identified sandbox checkout."""
    if amount < 0 or not currency.strip() or not success_redirect.strip() or timeout_s <= 0:
        raise ValueError("amount, currency, redirect, and timeout must be valid")
    request = {
        "run_id": f"checkout-{uuid.uuid4().hex}",
        "amount": amount,
        "currency": currency.lower(),
        "success_redirect": success_redirect,
    }
    deadline = time.monotonic() + timeout_s
    evidence: list[Evidence] = []
    checkout: Checkout | None = None
    try:
        checkout = client.create_checkout(request)
        _verify(checkout, request, deadline)
        evidence.append(_event("verify", checkout, request))
    except Exception as exc:
        evidence.append(_event("create" if checkout is None else "verify",
                               checkout, request, _safe_error(exc, checkout)))
    finally:
        if checkout is not None:
            try:
                client.expire_checkout(checkout.identifier)
                evidence.append(_event("cleanup", checkout, request))
            except Exception as exc:
                evidence.append(_event("cleanup", checkout, request,
                                       _safe_error(exc, checkout)))
    if any(row.error for row in evidence):
        raise ProbeFailure(tuple(evidence))
    return tuple(evidence)


def _verify(checkout: Checkout, request: Mapping[str, object], deadline: float) -> None:
    expected = (request["amount"], request["currency"], request["success_redirect"])
    actual = (checkout.amount, checkout.currency.lower(), checkout.success_redirect)
    if not checkout.identifier:
        raise AssertionError("provider response lacks checkout identifier")
    if not checkout.accepted:
        raise AssertionError("provider rejected checkout")
    if actual != expected:
        raise AssertionError(f"checkout fields differ: expected {expected}, got {actual}")
    if checkout.live_charge:
        raise AssertionError("checkout created a live charge")
    if time.monotonic() > deadline:
        raise TimeoutError("checkout probe timed out")


def _event(step: str, checkout: Checkout | None, request: Mapping[str, object],
           error: str = "") -> Evidence:
    identifier = checkout.identifier if checkout else ""
    response = checkout.response if checkout else {}
    return Evidence(datetime.now(UTC).isoformat(), step, _redact(identifier),
                    _redact_mapping(request), _redact_mapping(response), error)


def _redact_mapping(values: Mapping[str, object]) -> dict[str, object]:
    safe = {"run_id", "amount", "currency", "success_redirect", "status"}
    return {key: value if key in safe else "[redacted]" for key, value in values.items()}


def _redact(value: str) -> str:
    return f"***{value[-4:]}" if value else ""


def _safe_error(exc: Exception, checkout: Checkout | None) -> str:
    error = str(exc)
    if checkout and checkout.identifier:
        error = error.replace(checkout.identifier, _redact(checkout.identifier))
    return error
