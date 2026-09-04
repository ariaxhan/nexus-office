from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from nexus.probes import (  # noqa: E402
    CHECK_OWNERS,
    assign_repair_owners,
    build_check_registry,
)
from nexus.repairs import reconcile_repairs  # noqa: E402


class CheckRegistryTest(unittest.TestCase):
    def test_every_core_check_has_one_declared_repair_repository(self):
        tbs = "Thinking-Brain-School/tbs-www"
        self.assertEqual({
            "tbs.core.browser": tbs,
            "tbs.core.authentication": tbs,
            "tbs.core.asset": tbs,
            "tbs.core.redirect": tbs,
            "tbs.core.paid-gate": tbs,
            "tbs.core.entitlement": tbs,
            "tbs.core.progress": tbs,
            "tbs.core.health": tbs,
            "tbs.core.tls": tbs,
            "nexus.core.delivery-loop": "ariaxhan/nexus-office",
        }, dict(CHECK_OWNERS))

    def test_duplicate_check_id_is_rejected_before_dict_conversion(self):
        with self.assertRaisesRegex(ValueError, "duplicate check_id: same"):
            build_check_registry((("same", "owner/repo"),
                                  ("same", "owner/other")))

    def test_missing_check_id_or_owner_is_rejected(self):
        for rows, message in (
            ((("", "owner/repo"),), "check_id must be a non-empty string"),
            ((("check", ""),), "owner must be a non-empty string"),
        ):
            with self.subTest(rows=rows):
                with self.assertRaisesRegex(ValueError, message):
                    build_check_registry(rows)

    def test_owner_must_be_one_github_repository(self):
        for owner in ("owner", "owner/repo/extra", "owner /repo", "owner/ repo"):
            with self.subTest(owner=owner):
                with self.assertRaisesRegex(ValueError, "GitHub owner/repository"):
                    build_check_registry((("check", owner),))

    def test_registry_is_immutable(self):
        with self.assertRaises(TypeError):
            CHECK_OWNERS["new.check"] = "owner/repo"


class RepairOwnerAssignmentTest(unittest.TestCase):
    def test_wording_and_evidence_do_not_change_check_identity_or_owner(self):
        first = assign_repair_owners(({
            "check_id": "tbs.core.browser",
            "title": "old words",
            "evidence": "old evidence",
        },))[0]
        changed = assign_repair_owners(({
            "check_id": "tbs.core.browser",
            "title": "new words",
            "evidence": "new evidence",
            "owner": "wrong/repository",
        },))[0]

        self.assertEqual(first["check_id"], changed["check_id"])
        self.assertEqual(first["owner"], changed["owner"])
        self.assertEqual("Thinking-Brain-School/tbs-www", changed["owner"])
        self.assertNotEqual(first["evidence"], changed["evidence"])

    def test_missing_unknown_and_duplicate_result_ids_are_rejected(self):
        cases = (
            (({},), "missing probe fields: check_id"),
            (({"check_id": "unknown"},), "missing owner for check_id: unknown"),
            (({"check_id": "tbs.core.tls"},
              {"check_id": "tbs.core.tls"}), "duplicate check_id: tbs.core.tls"),
        )
        for results, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    assign_repair_owners(results)

    def test_registry_output_feeds_repair_reconciliation(self):
        assigned = assign_repair_owners(({
            "check_id": "nexus.core.delivery-loop",
            "title": "Repair delivery loop",
            "evidence": "delivery receipt missing",
        },))
        calls = []

        def request(args):
            calls.append(args)
            if args[0] == "GET":
                return []
            return {"number": 7}

        result = reconcile_repairs(assigned, CHECK_OWNERS, request)

        self.assertEqual("ariaxhan/nexus-office", result[0]["repository"])
        self.assertEqual("created", result[0]["action"])
        self.assertIn("repos/ariaxhan/nexus-office/issues", calls[-1][1])


if __name__ == "__main__":
    unittest.main()
