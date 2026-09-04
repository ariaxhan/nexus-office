from __future__ import annotations

import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from nexus.checkout_probe import Checkout, ProbeFailure, run_checkout_probe  # noqa: E402


class Client:
    def __init__(self, fail_create: bool = False, fail_cleanup: bool = False):
        self.fail_create = fail_create
        self.fail_cleanup = fail_cleanup
        self.request = {}
        self.expired = ""

    def create_checkout(self, request):
        self.request = request
        if self.fail_create:
            return Checkout("rejected-private-9876", request["amount"],
                            request["currency"], request["success_redirect"], False,
                            {"status": "rejected", "token": "secret-card-token"}, False)
        return Checkout("checkout-private-1234", request["amount"], request["currency"],
                        request["success_redirect"], False,
                        {"status": "open", "token": "secret-card-token"})

    def expire_checkout(self, checkout_id):
        self.expired = checkout_id
        if self.fail_cleanup:
            raise RuntimeError(f"could not expire {checkout_id}")


class CheckoutProbeTest(unittest.TestCase):
    def test_success_verifies_unique_no_charge_checkout_and_expires_it(self):
        client = Client()
        evidence = run_checkout_probe(client, currency="KRW")

        self.assertEqual(0, client.request["amount"])
        self.assertEqual("krw", client.request["currency"])
        self.assertTrue(client.request["run_id"].startswith("checkout-"))
        self.assertEqual("checkout-private-1234", client.expired)
        self.assertEqual(["verify", "cleanup"], [row.step for row in evidence])
        self.assertNotIn("secret-card-token", repr(evidence))
        self.assertIn("***1234", repr(evidence))

    def test_provider_rejection_is_redacted(self):
        client = Client(fail_create=True)
        with self.assertRaises(ProbeFailure) as raised:
            run_checkout_probe(client)

        evidence = raised.exception.evidence
        self.assertEqual("verify", evidence[0].step)
        self.assertEqual("rejected-private-9876", client.expired)
        self.assertNotIn("secret-card-token", repr(evidence))
        self.assertIn("***9876", repr(evidence))

    def test_timeout_still_expires_created_checkout(self):
        client = Client()
        with patch("nexus.checkout_probe.time.monotonic", side_effect=(0, 31)):
            with self.assertRaises(ProbeFailure) as raised:
                run_checkout_probe(client, timeout_s=30)

        self.assertEqual("checkout-private-1234", client.expired)
        self.assertIn("timed out", raised.exception.evidence[0].error)

    def test_cleanup_failure_is_reported_with_redacted_identifier(self):
        client = Client(fail_cleanup=True)
        with self.assertRaises(ProbeFailure) as raised:
            run_checkout_probe(client)

        cleanup = raised.exception.evidence[-1]
        self.assertEqual("cleanup", cleanup.step)
        self.assertNotIn("checkout-private-1234", repr(cleanup))
        self.assertIn("***1234", cleanup.error)


if __name__ == "__main__":
    unittest.main()
