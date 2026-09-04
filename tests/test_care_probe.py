from __future__ import annotations

import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from nexus.care_probe import ProbeFailure, Response, run_care_probe  # noqa: E402


class Client:
    def __init__(self, fail_at=""):
        self.calls = []
        self.fail_at = fail_at
        self.rate_calls = 0

    def reply(self, step, status, identifier="", headers=None):
        self.calls.append(step)
        if self.fail_at == step:
            raise RuntimeError(f"{step} failed")
        return Response(status, identifier, headers)

    def create_account(self, run_id):
        self.run_id = run_id
        return self.reply("create", 201, "account-private-1234")

    def record_consent(self, account_id):
        return self.reply("consent", 204)

    def start_session(self, account_id):
        return self.reply("session", 201, "session-private-5678")

    def verify_session(self, account_id, session_id):
        return self.reply("verify", 200)

    def limited_request(self, account_id):
        self.rate_calls += 1
        if self.rate_calls <= 2:
            return self.reply("rate-limit", 200)
        return self.reply("rate-limit", 429, headers={
            "Retry-After": "60", "X-RateLimit-Reset": "1788490860"
        })

    def delete_account(self, account_id):
        return self.reply("delete", 204)

    def access_account(self, account_id):
        return self.reply("deleted-access", 404)

    def cleanup(self, run_id):
        self.calls.append("cleanup")


class CareProbeTest(unittest.TestCase):
    def test_lifecycle_rate_limit_evidence_and_cleanup(self):
        client = Client()
        evidence = run_care_probe(client, request_threshold=2)

        self.assertEqual(
            ["create", "consent", "session", "verify", "rate-limit", "rate-limit",
             "rate-limit", "delete", "deleted-access", "cleanup"],
            client.calls,
        )
        self.assertEqual("429", str(evidence[6].status))
        self.assertEqual(("60", "1788490860"),
                         (evidence[6].retry_after, evidence[6].rate_limit_reset))
        self.assertTrue(all(row.at.endswith("+00:00") for row in evidence))
        serialized = repr(evidence)
        self.assertNotIn("account-private", serialized)
        self.assertNotIn("session-private", serialized)
        self.assertIn("***1234", serialized)
        self.assertIn("***5678", serialized)

    def test_failure_and_timeout_still_cleanup(self):
        failed = Client(fail_at="consent")
        with self.assertRaises(ProbeFailure):
            run_care_probe(failed, request_threshold=2)
        self.assertEqual("cleanup", failed.calls[-1])

        timed = Client()
        with patch("nexus.care_probe.time.monotonic", side_effect=(0, 2)):
            with self.assertRaises(ProbeFailure) as raised:
                run_care_probe(timed, request_threshold=2, timeout_s=1)
        self.assertEqual("cleanup", timed.calls[-1])
        self.assertEqual(["create", "cleanup"], timed.calls)
        self.assertIn("timed out", raised.exception.evidence[-2].error)

    def test_errors_redact_created_identifiers(self):
        class LeakingClient(Client):
            def record_consent(self, account_id):
                raise RuntimeError(f"consent failed for {account_id}")

        with self.assertRaises(ProbeFailure) as raised:
            run_care_probe(LeakingClient(), request_threshold=2)
        self.assertNotIn("account-private-1234", repr(raised.exception.evidence))
        self.assertIn("***1234", raised.exception.evidence[-2].error)

    def test_unexpected_rate_status_or_metadata_fails_after_cleanup(self):
        client = Client()
        client.rate_calls = -1
        with self.assertRaises(ProbeFailure):
            run_care_probe(client, request_threshold=2)
        self.assertEqual("cleanup", client.calls[-1])

        class NoMetadata(Client):
            def limited_request(self, account_id):
                self.rate_calls += 1
                return self.reply("rate-limit", 200 if self.rate_calls <= 2 else 429)

        client = NoMetadata()
        with self.assertRaises(ProbeFailure) as raised:
            run_care_probe(client, request_threshold=2)
        self.assertIn("retry metadata", raised.exception.evidence[-2].error)
        self.assertEqual("cleanup", client.calls[-1])


if __name__ == "__main__":
    unittest.main()
