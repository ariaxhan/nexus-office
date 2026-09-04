"""Keep GitHub repair issues aligned with deterministic live proof."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Iterable, Mapping

from .probes import CHECK_OWNERS, SCHEMA, build_probe_output

MARKER_PREFIX = "nexus-repair-check:"
REPAIR_LABELS = {"p0", "ready"}


class RepairError(RuntimeError):
    pass


def reconcile_repairs(
    failures: Iterable[Mapping],
    registry: Mapping[str, str],
    request: Callable[[list[str]], object] | None = None,
) -> list[dict]:
    """Create or refresh the repair issue for every failed check."""
    call = request or _gh
    rows = [_failure(failure, registry) for failure in failures]
    ids = [row["check_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise RepairError("duplicate failed check_id")
    return [_reconcile(row, call) for row in rows]


def reconcile_probe_output(
    output: Mapping,
    request: Callable[[list[str]], object] | None = None,
) -> list[dict]:
    """Close passing repairs and requeue failed ones from one complete proof."""
    if not isinstance(output, Mapping) or output.get("schema_version") != SCHEMA:
        raise RepairError(f"schema_version must be {SCHEMA}")
    checks = output.get("checks")
    if not isinstance(checks, list):
        raise RepairError("checks must be an array")
    try:
        rows = build_probe_output(checks)["checks"]
        rows = [_canonical_proof(row) for row in rows]
    except (TypeError, ValueError) as exc:
        raise RepairError(str(exc)) from exc
    call = request or _gh
    return [_apply_proof(row, call) for row in rows]


def _canonical_proof(proof: dict) -> dict:
    check_id = proof["check_id"]
    owner = CHECK_OWNERS.get(check_id)
    if owner is None:
        raise ValueError(f"missing owner for check_id: {check_id}")
    if proof["owner"] != owner:
        raise ValueError(
            f"owner for check_id {check_id} must be canonical repository {owner}"
        )
    return {**proof, "owner": owner}


def _apply_proof(proof: dict, call: Callable[[list[str]], object]) -> dict:
    path = f"repos/{proof['owner']}/issues/{proof['repair_issue']}"
    issue = call(["GET", path])
    if not isinstance(issue, Mapping) or issue.get("number") != proof["repair_issue"]:
        raise RepairError("GitHub issue response did not match repair_issue")
    labels = {label.get("name") for label in issue.get("labels", [])
              if isinstance(label, Mapping) and label.get("name")}
    passing = proof["state"] == "pass"
    wanted_state = "closed" if passing else "open"
    wanted_labels = labels - REPAIR_LABELS if passing else labels | REPAIR_LABELS
    if issue.get("state") == wanted_state and labels == wanted_labels:
        return _proof_result(proof, "unchanged")
    args = ["PATCH", path, "state=" + wanted_state]
    if labels != wanted_labels:
        if wanted_labels:
            args.extend("labels[]=" + label for label in sorted(wanted_labels))
        else:
            args.append("labels[]")
    call(args)
    return _proof_result(proof, "closed" if passing else "requeued")


def _proof_result(proof: dict, action: str) -> dict:
    return {"check_id": proof["check_id"], "repository": proof["owner"],
            "issue": proof["repair_issue"], "action": action}


def _failure(failure: Mapping, registry: Mapping[str, str]) -> dict:
    check_id = _text(failure.get("check_id"), "check_id")
    try:
        repository = _repository(registry[check_id])
    except KeyError as exc:
        raise RepairError(f"check_id is absent from the repair registry: {check_id}") from exc
    evidence = _text(failure.get("evidence"), "evidence")
    title = str(failure.get("title") or f"Repair failed check: {check_id}").strip()
    return {"check_id": check_id, "repository": repository,
            "title": title, "evidence": evidence}


def _reconcile(failure: dict, call: Callable[[list[str]], object]) -> dict:
    marker = _marker(failure["check_id"])
    issue = _repair_issue(failure, marker, call)
    body = f"{marker}\n\n## Current failure evidence\n\n{failure['evidence']}\n"
    if issue is None:
        issue = call(["POST", f"repos/{failure['repository']}/issues",
                      "title=" + failure["title"], "body=" + body,
                      "labels[]=ready", "labels[]=p0"])
        return _result(failure, issue, "created")
    current_labels = {label["name"] for label in issue.get("labels", [])}
    unchanged = (issue.get("state") == "open" and issue.get("body") == body
                 and REPAIR_LABELS <= current_labels)
    if unchanged:
        return _result(failure, issue, "unchanged")
    args = ["PATCH", f"repos/{failure['repository']}/issues/{issue['number']}",
            "state=open", "body=" + body]
    args.extend("labels[]=" + label for label in sorted(current_labels | REPAIR_LABELS))
    return _result(failure, call(args), "updated")


def _repair_issue(failure: dict, marker: str,
                  call: Callable[[list[str]], object]) -> dict | None:
    matches = [issue for issue in _issues(failure["repository"], call)
               if marker in (issue.get("body") or "")]
    if len(matches) > 1:
        numbers = ", ".join(str(issue["number"]) for issue in matches)
        raise RepairError(f"multiple repair issues for {failure['check_id']}: {numbers}")
    return matches[0] if matches else None


def _issues(repository: str, call: Callable[[list[str]], object]) -> list[dict]:
    value = call(["GET", f"repos/{repository}/issues?state=all&per_page=100",
                  "--paginate"])
    if not isinstance(value, list):
        raise RepairError("GitHub issue listing was not an array")
    return [issue for issue in value if "pull_request" not in issue]


def _result(failure: dict, issue: Mapping, action: str) -> dict:
    return {"check_id": failure["check_id"], "repository": failure["repository"],
            "issue": issue["number"], "action": action}


def _marker(check_id: str) -> str:
    if "--" in check_id or ">" in check_id:
        raise RepairError("check_id cannot contain -- or >")
    return f"<!-- {MARKER_PREFIX}{check_id} -->"


def _repository(value) -> str:
    repository = _text(value, "repair repository")
    if repository.count("/") != 1 or not all(repository.split("/")):
        raise RepairError("repair repository must be owner/name")
    return repository


def _text(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RepairError(f"{field} must be a non-empty string")
    return value.strip()


def _gh(args: list[str]):
    command = ["gh", "api", "--method", args[0], args[1]]
    for value in args[2:]:
        if value != "--paginate":
            command.extend(["-F" if value.endswith("[]") else "-f", value])
    if "--paginate" in args:
        command.extend(["--paginate", "--slurp"])
    proc = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if proc.returncode:
        raise RepairError(proc.stderr.strip() or "GitHub request failed")
    value = json.loads(proc.stdout)
    return sum(value, []) if "--paginate" in args else value
