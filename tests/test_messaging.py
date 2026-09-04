from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from nexus.messaging import (  # noqa: E402
    Destination,
    ProbeFailure,
    ProviderReceipt,
    receipt_state,
    run_messaging_probe,
)


DESTINATIONS = {
    "kakao": Destination("kakao-sandbox-1234"),
    "sms": Destination("+15550000123"),
}


class Provider:
    def __init__(self, receipts=None):
        self.sent = []
        self.receipts = receipts or {
            "kakao-id": [ProviderReceipt("accepted", "2026-09-03T10:00:00Z"),
                          ProviderReceipt("delivered", "2026-09-03T10:00:01Z")],
            "sms-id": [ProviderReceipt("queued", "2026-09-03T10:00:02Z"),
                        ProviderReceipt("delivered", "2026-09-03T10:00:03Z")],
        }

    def send(self, channel, destination, body, run_id):
        self.sent.append((channel, destination, body, run_id))
        return f"{channel}-id"

    def wait_receipt(self, provider_id, timeout_s):
        rows = self.receipts.get(provider_id, [])
        return rows.pop(0) if rows else None


class MessagingProbeTest(unittest.TestCase):
    def test_both_channels_deliver_with_redacted_evidence(self):
        provider = Provider()
        evidence = run_messaging_probe(provider, DESTINATIONS, timeout_s=8)

        self.assertEqual(["kakao", "sms"], [row.channel for row in evidence])
        self.assertEqual(["kakao-id", "sms-id"],
                         [row.provider_id for row in evidence])
        self.assertEqual(["***34", "***23"],
                         [row.destination for row in evidence])
        self.assertTrue(all(row.accepted_at and row.delivered_at for row in evidence))
        run_ids = {row[3] for row in provider.sent}
        self.assertEqual(1, len(run_ids))
        self.assertTrue(next(iter(run_ids)).startswith("messaging-"))
        self.assertTrue(all(row[3] in row[2] for row in provider.sent))

    def test_terminal_failure_is_mapped_for_each_channel(self):
        self.assertEqual("terminal_failure", receipt_state("failed"))
        provider = Provider({
            f"{channel}-id": [ProviderReceipt("accepted", f"{channel}-accepted"),
                              ProviderReceipt("rejected", f"{channel}-failed")]
            for channel in DESTINATIONS
        })

        with self.assertRaises(ProbeFailure) as raised:
            run_messaging_probe(provider, DESTINATIONS)
        self.assertEqual(["kakao-failed", "sms-failed"],
                         [row.failure_at for row in raised.exception.evidence])

    def test_timeout_retains_identifiers_and_acceptance(self):
        provider = Provider({
            f"{channel}-id": [ProviderReceipt("accepted", f"{channel}-accepted")]
            for channel in DESTINATIONS
        })

        with self.assertRaises(ProbeFailure) as raised:
            run_messaging_probe(provider, DESTINATIONS)
        self.assertEqual(["kakao-id", "sms-id"],
                         [row.provider_id for row in raised.exception.evidence])
        self.assertTrue(all(row.accepted_at for row in raised.exception.evidence))
        self.assertTrue(all(row.timeout_at for row in raised.exception.evidence))

    def test_delivery_without_acceptance_fails(self):
        provider = Provider({
            f"{channel}-id": [ProviderReceipt("delivered", f"{channel}-delivered")]
            for channel in DESTINATIONS
        })

        with self.assertRaises(ProbeFailure) as raised:
            run_messaging_probe(provider, DESTINATIONS)
        self.assertTrue(all(row.delivered_at for row in raised.exception.evidence))
        self.assertTrue(all(row.accepted_at is None for row in raised.exception.evidence))

    def test_short_destinations_are_fully_redacted(self):
        destinations = {
            "kakao": Destination("k"),
            "sms": Destination("1"),
        }
        evidence = run_messaging_probe(Provider(), destinations)
        self.assertEqual(["***", "***"], [row.destination for row in evidence])

    def test_invalid_timeout_and_missing_destination_send_nothing(self):
        provider = Provider()
        with self.assertRaisesRegex(ValueError, "timeout"):
            run_messaging_probe(provider, DESTINATIONS, timeout_s=0)
        with self.assertRaisesRegex(ValueError, "missing destinations: sms"):
            run_messaging_probe(provider, {"kakao": DESTINATIONS["kakao"]})
        self.assertEqual([], provider.sent)

    def test_send_cap_and_sandbox_gate_apply_before_sending(self):
        provider = Provider()
        with self.assertRaisesRegex(ValueError, "send cap"):
            run_messaging_probe(
                provider, DESTINATIONS, channels=("kakao", "sms", "email")
            )
        self.assertEqual([], provider.sent)

        destinations = dict(DESTINATIONS)
        destinations["sms"] = Destination("+15550000999", sandbox=False)
        with self.assertRaisesRegex(ValueError, "sandbox"):
            run_messaging_probe(provider, destinations)
        self.assertEqual([], provider.sent)


if __name__ == "__main__":
    unittest.main()
