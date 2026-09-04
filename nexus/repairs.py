"""One GitHub repair issue for each failed deterministic check."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Iterable, Mapping

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
    matches = [issue for issue in _issues(failure["repository"], call)
               if marker in (issue.get("body") or "")]
    if len(matches) > 1:
        numbers = ", ".join(str(issue["number"]) for issue in matches)
        raise RepairError(f"multiple repair issues for {failure['check_id']}: {numbers}")
    body = f"{marker}\n\n## Current failure evidence\n\n{failure['evidence']}\n"
    if not matches:
        issue = call(["POST", f"repos/{failure['repository']}/issues",
                      "title=" + failure["title"], "body=" + body,
                      "labels[]=" + "ready", "labels[]=" + "p0"])
        return _result(failure, issue, "created")
    issue = matches[0]
    current_labels = {label["name"] for label in issue.get("labels", [])}
    labels = current_labels | REPAIR_LABELS
    unchanged = (issue.get("state") == "open" and issue.get("body") == body
                 and REPAIR_LABELS <= current_labels)
    if unchanged:
        return _result(failure, issue, "unchanged")
    args = ["PATCH", f"repos/{failure['repository']}/issues/{issue['number']}",
            "state=open", "body=" + body]
    args.extend("labels[]=" + label for label in sorted(labels))
    return _result(failure, call(args), "updated")


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
            command.extend(["-f", value])
    if "--paginate" in args:
        command.extend(["--paginate", "--slurp"])
    proc = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if proc.returncode:
        raise RepairError(proc.stderr.strip() or "GitHub request failed")
    value = json.loads(proc.stdout)
    return sum(value, []) if "--paginate" in args else value
