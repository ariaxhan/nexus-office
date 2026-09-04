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


SANDBOX_PREFIX = "sandbox."


def sandbox_failures(report: Mapping) -> list[dict]:
    """Repair rows for a sandbox probe report, one per failed probe.

    The report is the JSON a sandbox flight prints: `run_id`, `ok`, `probes`
    (name to redacted evidence rows) and, when the runner itself broke, `error`.
    A probe failed when any of its rows carries an `error`; its check id is
    `sandbox.<name>`, stable across runs, so repairs reconcile by id. The rows
    feed `nexus.repairs.reconcile_repairs` unchanged.
    """
    if report.get("ok"):
        return []
    run_id = str(report.get("run_id") or "")
    failures = []
    if report.get("error"):
        failures.append(_sandbox_failure("run", run_id, [{"error": report["error"]}]))
    for name, rows in sorted((report.get("probes") or {}).items()):
        errors = [row for row in rows if isinstance(row, Mapping) and row.get("error")]
        if errors:
            failures.append(_sandbox_failure(name, run_id, errors))
    return failures


def _sandbox_failure(name: str, run_id: str, rows: list) -> dict:
    evidence = json.dumps(rows, sort_keys=True, indent=2, ensure_ascii=False, default=str)
    return {"check_id": SANDBOX_PREFIX + name,
            "title": f"Repair failed sandbox probe: {name}",
            "evidence": f"run `{run_id}`\n\n```json\n{evidence}\n```"}
