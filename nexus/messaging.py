"""Bounded delivery probes for Kakao and SMS provider sandboxes."""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol


CHANNELS = ("kakao", "sms")
SEND_CAP = 2
RECEIPT_STATES = {
    "accepted": "accepted",
    "queued": "accepted",
    "sent": "accepted",
    "delivered": "delivered",
    "failed": "terminal_failure",
    "rejected": "terminal_failure",
    "undeliverable": "terminal_failure",
    "expired": "terminal_failure",
}


@dataclass(frozen=True)
class Destination:
    value: str
    sandbox: bool = True


@dataclass(frozen=True)
class ProviderReceipt:
    status: str
    at: str


@dataclass(frozen=True)
class ReceiptEvidence:
    run_id: str
    channel: str
    destination: str
    provider_id: str
    accepted_at: str | None = None
    delivered_at: str | None = None
    failure_at: str | None = None
    timeout_at: str | None = None


class MessagingProvider(Protocol):
    def send(self, channel: str, destination: str, body: str,
             run_id: str) -> str: ...

    def wait_receipt(self, provider_id: str,
                     timeout_s: float) -> ProviderReceipt | None: ...


class ProbeFailure(RuntimeError):
    def __init__(self, evidence: tuple[ReceiptEvidence, ...]):
        super().__init__("messaging receipt probe failed")
        self.evidence = evidence


def receipt_state(status: str) -> str:
    """Map provider status names to the probe's stable receipt states."""
    try:
        return RECEIPT_STATES[status.strip().lower()]
    except KeyError as exc:
        raise ValueError(f"unknown receipt status: {status}") from exc


def run_messaging_probe(provider: MessagingProvider,
                        destinations: dict[str, Destination],
                        timeout_s: float = 30,
                        channels: tuple[str, ...] = CHANNELS,
                        ) -> tuple[ReceiptEvidence, ...]:
    """Send at most one sandbox delivery per channel and verify its receipts."""
    selected = _validate_probe(destinations, channels, timeout_s)
    probe_id = f"messaging-{uuid.uuid4().hex}"
    sent = _send(provider, probe_id, channels, selected)
    evidence = tuple(
        _verify_receipts(provider, probe_id, channel, destination, provider_id,
                         timeout_s)
        for channel, destination, provider_id in sent
    )
    if any(item.accepted_at is None or item.delivered_at is None
           for item in evidence):
        raise ProbeFailure(evidence)
    return evidence


def _validate_probe(destinations: dict[str, Destination],
                    channels: tuple[str, ...],
                    timeout_s: float) -> tuple[Destination, ...]:
    if (not isinstance(timeout_s, (int, float))
            or not math.isfinite(timeout_s) or timeout_s <= 0):
        raise ValueError("timeout must be a positive finite number")
    if len(channels) > SEND_CAP:
        raise ValueError(f"send cap is {SEND_CAP}")
    if len(set(channels)) != len(channels) or set(channels) != set(CHANNELS):
        raise ValueError("exactly one Kakao and one SMS delivery are required")
    missing = sorted(set(channels) - set(destinations))
    if missing:
        raise ValueError(f"missing destinations: {', '.join(missing)}")
    selected = tuple(destinations[channel] for channel in channels)
    if any(not item.sandbox or not item.value.strip() for item in selected):
        raise ValueError("every destination must be a named sandbox destination")
    return selected


def _send(provider: MessagingProvider, run_id: str, channels: tuple[str, ...],
          destinations: tuple[Destination, ...]
          ) -> list[tuple[str, Destination, str]]:
    return [
        (channel, destination,
         provider.send(channel, destination.value, f"probe {run_id}", run_id))
        for channel, destination in zip(channels, destinations)
    ]


def _verify_receipts(provider: MessagingProvider, run_id: str, channel: str,
                     destination: Destination, provider_id: str,
                     timeout_s: float) -> ReceiptEvidence:
    accepted_at = None
    deadline = time.monotonic() + timeout_s
    while True:
        remaining = deadline - time.monotonic()
        receipt = provider.wait_receipt(provider_id, remaining) if remaining > 0 else None
        if receipt is None:
            return ReceiptEvidence(
                run_id, channel, _redact(destination.value), provider_id,
                accepted_at=accepted_at,
                timeout_at=datetime.now(UTC).isoformat(),
            )
        state = receipt_state(receipt.status)
        if state == "accepted":
            accepted_at = accepted_at or receipt.at
            continue
        return ReceiptEvidence(
            run_id, channel, _redact(destination.value), provider_id,
            accepted_at=accepted_at,
            delivered_at=receipt.at if state == "delivered" else None,
            failure_at=receipt.at if state == "terminal_failure" else None,
        )


def _redact(destination: str) -> str:
    visible = destination[-2:] if len(destination) > 2 else ""
    return f"***{visible}"
