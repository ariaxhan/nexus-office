"""Stable output contract between live probes and repair reconciliation."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping

SCHEMA = "nexus.live-probe-repairs/v1"
STATES = {"pass", "fail"}
REQUIRED = ("check_id", "owner", "repair_issue", "state")


def build_probe_output(checks: Iterable[Mapping]) -> dict:
    """Return one validated, deterministically ordered repair row per check."""
    rows = [_repair_row(check) for check in checks]
    ids = [row["check_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate check_id")
    return {"schema_version": SCHEMA,
            "checks": sorted(rows, key=lambda row: row["check_id"])}


def encode_probe_output(checks: Iterable[Mapping]) -> str:
    """Serialize the contract identically for identical check state."""
    return json.dumps(build_probe_output(checks), sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False) + "\n"


def _repair_row(check: Mapping) -> dict:
    missing = [field for field in REQUIRED if field not in check]
    if missing:
        raise ValueError(f"missing probe fields: {', '.join(missing)}")
    check_id = _text(check["check_id"], "check_id")
    owner = _text(check["owner"], "owner")
    if owner.count("/") != 1 or not all(owner.split("/")):
        raise ValueError("owner must be a GitHub owner/repository")
    issue = check["repair_issue"]
    if isinstance(issue, bool) or not isinstance(issue, int) or issue < 1:
        raise ValueError("repair_issue must be a positive integer")
    state = _text(check["state"], "state")
    if state not in STATES:
        raise ValueError("state must be pass or fail")
    return {"check_id": check_id, "owner": owner,
            "repair_issue": issue, "state": state}


def _text(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()
