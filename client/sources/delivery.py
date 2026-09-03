"""Delivery progress, validated by the source-owned PR pipeline contract.

Office is a projection, never a second delivery authority. It loads the
producer's committed policy and validator, replays canonical receipts through
that validator, and only then presents a terminal claim.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import pathlib
import re
import subprocess
from types import ModuleType

from sources import _card

KEY = "delivery"
TITLE = "Delivery conveyor"
SERVICE = pathlib.Path("_meta/services/pr-pipeline")
RELATIVE = SERVICE / ".runtime/delivery"
POLICY = SERVICE / "delivery-policies.json"
VALIDATOR = SERVICE / "delivery.py"
CONVEYOR_SCHEMA = "tbs.delivery-conveyor/v1"
RECEIPT_ORDER = {
    "proposal": ("proposal",),
    "integration": ("integrated",),
    "source": ("merged", "composite"),
    "release": ("preview", "merged", "staged", "release", "buzz"),
}
HEARTBEAT_MAX_AGE_SECONDS = 15 * 60
FUTURE_CLOCK_SKEW_SECONDS = 5 * 60
UTC_SECONDS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")


class ContractError(ValueError):
    """The producer contract or one of its durable projections is unusable."""


def _root() -> pathlib.Path | None:
    value = os.environ.get("OFFICE_RUNTIME_ROOT", "").strip()
    return pathlib.Path(value).expanduser() if value else None


def _json(path: pathlib.Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractError(f"unreadable {path.name}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{path.name} is not an object")
    return value


def _producer(root: pathlib.Path) -> tuple[ModuleType, dict]:
    validator, policy = root / VALIDATOR, root / POLICY
    if not validator.is_file() or not policy.is_file():
        raise ContractError("delivery producer contract is unavailable")
    try:
        tracked = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--error-unmatch", str(VALIDATOR), str(POLICY)],
            capture_output=True, timeout=10)
        clean = subprocess.run(
            ["git", "-C", str(root), "diff", "--quiet", "HEAD", "--", str(VALIDATOR), str(POLICY)],
            capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ContractError("cannot verify committed delivery producer contract") from exc
    if tracked.returncode or clean.returncode:
        raise ContractError("delivery producer contract is not committed and clean")
    try:
        spec = importlib.util.spec_from_file_location("nexus_delivery_contract", validator)
        if spec is None or spec.loader is None:
            raise ImportError("no loader")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as exc:
        raise ContractError("delivery producer validator is unreadable") from exc
    return module, _json(policy)


def _timestamp(value, name: str, upper: dt.datetime | None = None) -> dt.datetime:
    if not isinstance(value, str) or UTC_SECONDS.fullmatch(value) is None:
        raise ContractError(f"{name} is not canonical UTC seconds")
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise ContractError(f"{name} is not a real UTC timestamp") from exc
    if upper is not None and parsed > upper:
        raise ContractError(f"{name} is later than its producer boundary")
    return parsed


def _at(path: pathlib.Path) -> str:
    return dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical_state_path(directory: pathlib.Path, path: pathlib.Path, state: dict) -> None:
    repo, pr = state.get("repo"), state.get("pr")
    if not isinstance(repo, str) or "/" not in repo or not isinstance(pr, int) or isinstance(pr, bool) or pr <= 0:
        raise ContractError("invalid delivery identity")
    head = state.get("head_sha")
    if not isinstance(head, str) or len(head) != 40:
        raise ContractError("invalid delivery head")
    expected = directory / "states" / repo.replace("/", "__") / str(pr) / f"{head}.json"
    try:
        if path.resolve(strict=True) != expected.resolve(strict=False):
            raise ContractError("state is outside its canonical path")
    except OSError as exc:
        raise ContractError("state path is unreadable") from exc


def _validated_state(directory: pathlib.Path, path: pathlib.Path, state: dict,
                     producer: ModuleType, config: dict) -> dict:
    _canonical_state_path(directory, path, state)
    try:
        policy, policy_hash = producer.policy_for(config, state["pr"], state["repo"])
        if state.get("policy_hash") != policy_hash or state.get("route") != policy.get("route"):
            raise producer.Refusal("state does not match committed delivery policy")
        return producer.validate_persisted_state(state, policy)
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise ContractError(str(exc) or "delivery proof refused") from exc


def _history(state: dict) -> list[str]:
    receipts = state.get("receipts") or {}
    names = ["review"]
    if "preview" in receipts or state.get("route") == "proposal" and "proposal" in receipts:
        names.append("preview")
    if "merged" in receipts:
        names.append("merged")
    if "integrated" in receipts:
        names.append("integrated")
    if "staged" in receipts:
        names.append("staged")
    if "release" in receipts:
        names.extend(("promoted", "live_verified"))
    if "buzz" in receipts:
        names.append("notified")
    if state.get("terminal"):
        names.append("terminal")
    return names


def _next(state: dict, producer: ModuleType, config: dict) -> str:
    try:
        policy, _ = producer.policy_for(config, state["pr"], state["repo"])
        actions = producer.next_actions(state, policy)
    except (KeyError, TypeError, ValueError):
        return "blocked"
    return str((actions[0] if actions else {}).get("action") or "complete").replace("_", " ")


def _terminal_projection(state: dict) -> dict | None:
    if state.get("terminal") is not True:
        return None
    receipts = state.get("receipts") or {}
    merged, release, buzz = (receipts.get("merged") or {}, receipts.get("release") or {},
                             receipts.get("buzz") or {})
    return {"route": state["route"], "phase": state["phase"], "head_sha": state["head_sha"],
            "merged_sha": merged.get("merged_sha"), "target": release.get("target"),
            "buzz": {"event_id": buzz.get("event_id"), "message_sha256": buzz.get("message_sha256"),
                     "sha": buzz.get("sha"), "target": buzz.get("target")} if buzz else None}


def _row(directory: pathlib.Path, path: pathlib.Path, state: dict,
         producer: ModuleType, config: dict, entry: dict, generated_at: dt.datetime) -> dict:
    validated = _validated_state(directory, path, state, producer, config)
    if any(entry.get(key) != state.get(key) for key in ("repo", "pr", "head_sha", "phase", "terminal")):
        raise ContractError("conveyor entry differs from canonical state")
    if entry.get("terminal_proof") != _terminal_projection(validated):
        raise ContractError("terminal projection differs from canonical state and Buzz proof")
    updated = entry.get("updated_at")
    _timestamp(updated, "delivery entry timestamp", generated_at)
    return {
        "repo": state["repo"], "pr": state["pr"], "head_sha": state["head_sha"],
        "route": state["route"], "phase": state.get("phase") or "bound",
        "history": _history(state), "next": _next(state, producer, config),
        "terminal": bool(validated.get("terminal")), "blocked": False,
        "problems": [], "at": str(updated),
    }


def _entry_types(row: dict) -> bool:
    identity = (isinstance(row["repo"], str) and bool(row["repo"])
                and isinstance(row["pr"], int) and not isinstance(row["pr"], bool)
                and row["pr"] > 0)
    head = isinstance(row["head_sha"], str) and len(row["head_sha"]) == 40
    flags = isinstance(row["terminal"], bool) and isinstance(row["running"], bool)
    return identity and head and flags and isinstance(row["next"], list)


def _entry_shape(entries) -> list[dict]:
    if not isinstance(entries, list):
        raise ContractError("delivery queue entries are malformed")
    if any(not isinstance(row, dict) for row in entries):
        raise ContractError("delivery queue entries are malformed")
    required = ("repo", "pr", "head_sha", "phase", "terminal", "linked_issues", "next",
                "actuated", "running", "state_path", "updated_at", "terminal_proof",
                "proof_refusal", "action_proof")
    if any(any(key not in row for key in required) for row in entries):
        raise ContractError("delivery queue entry is incomplete")
    if any(not _entry_types(row) for row in entries):
        raise ContractError("delivery queue entry has invalid types")
    return entries


def _entries(queue: dict) -> list[dict]:
    entries = _entry_shape(queue.get("entries"))
    owners = [(row.get("repo"), row.get("pr")) for row in entries]
    if len(owners) != len(set(owners)):
        raise ContractError("delivery queue contains duplicate PR ownership")
    expected = sorted(entries, key=lambda row: (row["terminal"], row["repo"], row["pr"]))
    if entries != expected:
        raise ContractError("delivery queue is outside canonical producer order")
    return entries


def _event(event: dict, generated_at: dt.datetime) -> int:
    number = event.get("sequence")
    if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
        raise ContractError("delivery queue event sequence is invalid")
    if not isinstance(event.get("type"), str) or not event["type"]:
        raise ContractError("delivery queue event type is invalid")
    _timestamp(event.get("at"), "delivery queue event timestamp", generated_at)
    return number


def _events(queue: dict, generated_at: dt.datetime) -> None:
    sequence, events = queue.get("sequence"), queue.get("events")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
        raise ContractError("delivery queue sequence is invalid")
    if not isinstance(events, list) or any(not isinstance(event, dict) for event in events):
        raise ContractError("delivery queue events are malformed")
    numbers = [_event(event, generated_at) for event in events]
    if numbers != sorted(set(numbers)) or (numbers and numbers[-1] > sequence):
        raise ContractError("delivery queue event sequence is inconsistent")


def _active(queue: dict, generated_at: dt.datetime) -> tuple[dict | None, bool]:
    active = queue.get("active_run")
    if active is not None and not isinstance(active, dict):
        raise ContractError("delivery queue active state is malformed")
    if active and (active.get("status") != "running" or not active.get("id")):
        raise ContractError("delivery queue active identity is malformed")
    heartbeat = (_timestamp(active.get("heartbeat_at"), "delivery heartbeat", generated_at)
                 if active else None)
    if active:
        started = _timestamp(active.get("started_at"), "delivery run start", heartbeat)
        if started > heartbeat:
            raise ContractError("delivery run starts after its heartbeat")
    now = dt.datetime.now(dt.timezone.utc)
    fresh = bool(heartbeat and 0 <= (now - heartbeat).total_seconds() <= HEARTBEAT_MAX_AGE_SECONDS)
    if active and not fresh:
        raise ContractError("delivery runner heartbeat is stale")
    return active if fresh else None, fresh


def _last_finished(queue: dict, generated_at: dt.datetime) -> str | None:
    last = queue.get("last_run")
    if last is None:
        return None
    if not isinstance(last, dict):
        raise ContractError("delivery queue last run is malformed")
    finished = last.get("finished_at")
    _timestamp(finished, "delivery run finish", generated_at)
    return finished


def _running(entries: list[dict], active: dict | None) -> None:
    rows = [row for row in entries if row.get("running")]
    active_id = ((active or {}).get("current_repo"), (active or {}).get("current_pr"),
                 (active or {}).get("current_head_sha"))
    if len(rows) > 1 or any(_identity(row) != active_id for row in rows):
        raise ContractError("delivery running entry disagrees with active run")
    if all(active_id) and not rows:
        raise ContractError("delivery active run has no exact running entry")


def _queue(directory: pathlib.Path) -> dict:
    path = directory / "conveyor.json"
    if not path.is_file():
        raise ContractError("delivery producer queue is missing")
    queue = _json(path)
    if queue.get("schema") != CONVEYOR_SCHEMA:
        raise ContractError("unsupported delivery conveyor")
    now = dt.datetime.now(dt.timezone.utc)
    generated_at = _timestamp(queue.get("generated_at"), "delivery queue generation timestamp",
                              now + dt.timedelta(seconds=FUTURE_CLOCK_SKEW_SECONDS))
    entries = _entries(queue)
    _events(queue, generated_at)
    active, fresh = _active(queue, generated_at)
    last_finished = _last_finished(queue, generated_at)
    _running(entries, active)
    return {"entries": entries, "active": active if fresh else None,
            "heartbeat_at": (active or {}).get("heartbeat_at") or last_finished,
            "fresh": fresh, "generated_at": generated_at}


def _identity(row: dict) -> tuple:
    return row.get("repo"), row.get("pr"), row.get("head_sha")


def _state_path(directory: pathlib.Path, entry: dict) -> pathlib.Path:
    raw = entry.get("state_path")
    if not isinstance(raw, str) or not raw:
        raise ContractError("conveyor entry has no canonical state path")
    candidate = pathlib.Path(raw)
    if not candidate.is_absolute():
        candidate = directory / candidate
    try:
        states = (directory / "states").resolve(strict=True)
        resolved = candidate.resolve(strict=True)
        relative = resolved.relative_to(states)
        cursor = states
        for part in relative.parts:
            cursor /= part
            if cursor.is_symlink():
                raise ValueError("symlinked state path")
    except (OSError, ValueError) as exc:
        raise ContractError("conveyor state path is outside its trusted root") from exc
    if not resolved.is_file():
        raise ContractError("conveyor state path is not a file")
    return resolved


def _conveyor(rows: list[dict], queue: dict) -> tuple[list[dict], list[dict]]:
    by_id = {_identity(row): row for row in rows if not row.get("terminal")}
    active_run = queue.get("active") or {}
    active = by_id.get((active_run.get("current_repo"), active_run.get("current_pr"),
                        active_run.get("current_head_sha")))
    running = [active] if active else []
    ordered = []
    for entry in queue.get("entries") or []:
        row = by_id.get(_identity(entry))
        if row and row is not active and not entry.get("running") and row not in ordered:
            ordered.append(row)
    return running, ordered


def _read_rows(directory: pathlib.Path, entries: list[dict], producer: ModuleType,
               config: dict, generated_at: dt.datetime) -> tuple[list[dict], list[dict]]:
    rows, quarantined = [], []
    for entry in entries:
        try:
            path = _state_path(directory, entry)
            rows.append(_row(directory, path, _json(path), producer, config, entry, generated_at))
        except ContractError as exc:
            name = pathlib.Path(str(entry.get("state_path") or "missing")).name
            quarantined.append({"file": name, "problem": str(exc)[:180]})
    return rows, quarantined


def _health(rows: list[dict], quarantined: list[dict], config: dict) -> tuple[str, str, bool]:
    enabled = config.get("actions_enabled") is True
    unfinished = any(not row.get("terminal") for row in rows)
    state = "blocked" if quarantined else "disabled" if unfinished and not enabled else "ok"
    detail = "delivery actuator is disabled while work is queued" if state == "disabled" else ""
    return state, detail, enabled


def read() -> dict:
    root = _root()
    if root is None:
        return {"state": "unconfigured", "detail": "OFFICE_RUNTIME_ROOT is not set", "rows": [],
                "running": [], "queued": []}
    directory = root / RELATIVE
    if not directory.is_dir():
        return {"state": "never", "detail": f"no {RELATIVE}", "rows": [],
                "running": [], "queued": []}
    try:
        producer, config = _producer(root)
        queue = _queue(directory)
    except ContractError as exc:
        return {"state": "unreachable", "detail": str(exc), "rows": [],
                "running": [], "queued": []}
    rows, quarantined = _read_rows(directory, queue["entries"], producer, config,
                                   queue["generated_at"])
    running, queued = _conveyor(rows, queue)
    state, detail, actuation_enabled = _health(rows, quarantined, config)
    return {"state": state, "detail": detail, "actuation_enabled": actuation_enabled,
            "actuation_reason": "enabled" if actuation_enabled else "committed policy disables actions",
            "rows": rows,
            "running": running, "queued": queued, "quarantined": quarantined,
            "heartbeat_at": queue.get("heartbeat_at"),
            "as_of": rows[0]["at"] if rows else str(queue.get("heartbeat_at") or "")}


def card(data: dict) -> dict:
    rows = data.get("rows") or []
    unhealthy = data.get("state") != "ok"
    done = [row for row in rows if row.get("terminal")]
    headline = (f"{len(data.get('running') or [])} running; {len(data.get('queued') or [])} next; "
                f"{len(data.get('quarantined') or [])} blocked; {len(done)} completed"
                if rows else data.get("detail") or "No delivery state yet")
    facts = [_card.fact("pipeline health", str(data.get("state") or "unknown"),
                        "bad" if unhealthy else "ok"),
             _card.fact("next up", str(len(data.get("queued") or [])), "dim"),
             _card.fact("completed recently", str(len(done)), "ok" if done else "dim")]
    return _card.build(TITLE, headline, 1 if unhealthy else 0,
                       str(data.get("as_of") or ""), facts, [])
