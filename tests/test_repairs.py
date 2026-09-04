from __future__ import annotations

import pathlib
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from nexus.probes import SCHEMA  # noqa: E402
from nexus.repairs import RepairError, _gh, reconcile_probe_output  # noqa: E402


CHECK = "nexus.core.delivery-loop"
OWNER = "ariaxhan/nexus-office"


def proof(state="pass", *, check_id=CHECK, owner=OWNER, issue=93):
    return {"schema_version": SCHEMA, "checks": [{
        "check_id": check_id, "owner": owner,
        "repair_issue": issue, "state": state,
    }]}


class Github:
    def __init__(self, issue):
        self.issue = issue
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        return self.issue if args[0] == "GET" else {"number": self.issue["number"]}


class ProbeRepairTest(unittest.TestCase):
    def test_pass_closes_issue_and_removes_repair_labels(self):
        github = Github({"number": 93, "state": "open", "labels": [
            {"name": "ready"}, {"name": "p0"},
        ]})

        self.assertEqual("closed", reconcile_probe_output(proof(), github)[0]["action"])
        self.assertEqual([
            ["GET", "repos/ariaxhan/nexus-office/issues/93"],
            ["PATCH", "repos/ariaxhan/nexus-office/issues/93", "state=closed", "labels[]"],
        ], github.calls)

    def test_fail_reopens_issue_and_restores_repair_labels(self):
        github = Github({"number": 93, "state": "closed", "labels": [{"name": "keep"}]})

        self.assertEqual("requeued", reconcile_probe_output(proof("fail"), github)[0]["action"])
        self.assertEqual(
            ["PATCH", "repos/ariaxhan/nexus-office/issues/93", "state=open",
             "labels[]=keep", "labels[]=p0", "labels[]=ready"], github.calls[-1])

    def test_identical_proof_is_idempotent(self):
        cases = (
            ("pass", {"number": 93, "state": "closed", "labels": [{"name": "keep"}]}),
            ("fail", {"number": 93, "state": "open", "labels": [
                {"name": "keep"}, {"name": "p0"}, {"name": "ready"},
            ]}),
        )
        for state, issue in cases:
            with self.subTest(state=state):
                github = Github(issue)
                self.assertEqual("unchanged", reconcile_probe_output(proof(state), github)[0]["action"])
                self.assertEqual(1, len(github.calls))

    def test_whole_input_is_validated_before_first_request(self):
        output = proof()
        output["checks"].append({
            "check_id": "tbs.core.health", "owner": "wrong/repo",
            "repair_issue": 10, "state": "pass",
        })
        github = Github({"number": 93, "state": "open", "labels": []})

        with self.assertRaisesRegex(RepairError, "must be canonical repository"):
            reconcile_probe_output(output, github)
        self.assertEqual([], github.calls)

    def test_check_owner_is_bound_to_canonical_registry(self):
        github = Github({"number": 93, "state": "open", "labels": []})
        with self.assertRaisesRegex(RepairError, "must be canonical repository"):
            reconcile_probe_output(proof(owner="other/repo"), github)
        with self.assertRaisesRegex(RepairError, "missing owner for check_id: unknown"):
            reconcile_probe_output(proof(check_id="unknown"), github)
        self.assertEqual([], github.calls)

    def test_mismatched_issue_response_stops_without_patch(self):
        github = Github({"number": 94, "state": "open", "labels": []})
        with self.assertRaisesRegex(RepairError, "did not match repair_issue"):
            reconcile_probe_output(proof(), github)
        self.assertEqual(1, len(github.calls))

    def test_pass_preserves_unrelated_labels(self):
        github = Github({"number": 93, "state": "open", "labels": [
            {"name": "ready"}, {"name": "p0"}, {"name": "keep"},
        ]})
        reconcile_probe_output(proof(), github)
        self.assertEqual(
            ["PATCH", "repos/ariaxhan/nexus-office/issues/93", "state=closed", "labels[]=keep"],
            github.calls[-1])

    def test_github_failure_is_reported_without_live_request(self):
        failed = subprocess.CompletedProcess([], 1, stdout="", stderr="rate limited")
        with patch("nexus.repairs.subprocess.run", return_value=failed):
            with self.assertRaisesRegex(RepairError, "rate limited"):
                _gh(["GET", "repos/ariaxhan/nexus-office/issues/93"])

    def test_empty_label_array_uses_gh_typed_field_without_live_request(self):
        ok = subprocess.CompletedProcess([], 0, stdout='{"number":93}', stderr="")
        with patch("nexus.repairs.subprocess.run", return_value=ok) as run:
            _gh(["PATCH", "repos/ariaxhan/nexus-office/issues/93",
                 "state=closed", "labels[]"])
        self.assertEqual(
            ["gh", "api", "--method", "PATCH",
             "repos/ariaxhan/nexus-office/issues/93",
             "-f", "state=closed", "-F", "labels[]"],
            run.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
