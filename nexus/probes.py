"""Stable output contract between live probes and repair reconciliation."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from types import MappingProxyType

SCHEMA = "nexus.live-probe-repairs/v1"
STATES = {"pass", "fail"}
REQUIRED = ("check_id", "owner", "repair_issue", "state")

# IDs describe what is checked, not its current wording or evidence. Keep this
# as rows until validation so a duplicate cannot be hidden by a dict literal.
_CHECK_OWNER_ROWS = (
    ("tbs.core.browser", "Thinking-Brain-School/tbs-www"),
    ("tbs.core.authentication", "Thinking-Brain-School/tbs-www"),
    ("tbs.core.asset", "Thinking-Brain-School/tbs-www"),
    ("tbs.core.redirect", "Thinking-Brain-School/tbs-www"),
    ("tbs.core.paid-gate", "Thinking-Brain-School/tbs-www"),
    ("tbs.core.entitlement", "Thinking-Brain-School/tbs-www"),
    ("tbs.core.progress", "Thinking-Brain-School/tbs-www"),
    ("tbs.core.health", "Thinking-Brain-School/tbs-www"),
    ("tbs.core.tls", "Thinking-Brain-School/tbs-www"),
    ("nexus.core.delivery-loop", "ariaxhan/nexus-office"),
)


def _text(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _owner(value) -> str:
    owner = _text(value, "owner")
    parts = owner.split("/")
    if (len(parts) != 2 or not all(parts)
            or any(any(char.isspace() for char in part) for part in parts)):
        raise ValueError("owner must be a GitHub owner/repository")
    return owner


def build_check_registry(rows: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Validate check-to-repository declarations without hiding duplicates."""
    registry = {}
    for raw_id, raw_owner in rows:
        check_id = _text(raw_id, "check_id")
        if check_id in registry:
            raise ValueError(f"duplicate check_id: {check_id}")
        registry[check_id] = _owner(raw_owner)
    return registry


CHECK_OWNERS = MappingProxyType(build_check_registry(_CHECK_OWNER_ROWS))


def assign_repair_owners(results: Iterable[Mapping],
                         registry: Mapping = CHECK_OWNERS) -> list[dict]:
    """Copy probe results and attach their canonical repair repositories."""
    owners = build_check_registry(registry.items())
    assigned = []
    seen = set()
    for result in results:
        if "check_id" not in result:
            raise ValueError("missing probe fields: check_id")
        check_id = _text(result["check_id"], "check_id")
        if check_id in seen:
            raise ValueError(f"duplicate check_id: {check_id}")
        if check_id not in owners:
            raise ValueError(f"missing owner for check_id: {check_id}")
        seen.add(check_id)
        assigned.append({**result, "check_id": check_id,
                         "owner": owners[check_id]})
    return assigned


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
    owner = _owner(check["owner"])
    issue = check["repair_issue"]
    if isinstance(issue, bool) or not isinstance(issue, int) or issue < 1:
        raise ValueError("repair_issue must be a positive integer")
    state = _text(check["state"], "state")
    if state not in STATES:
        raise ValueError("state must be pass or fail")
    return {"check_id": check_id, "owner": owner,
            "repair_issue": issue, "state": state}
