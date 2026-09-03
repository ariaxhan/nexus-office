"""Exact delivery progress from the PR pipeline's durable state files."""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib

from sources import _card

KEY = "delivery"
TITLE = "Delivery conveyor"
RELATIVE = "_meta/services/pr-pipeline/.runtime/delivery"
ROUTES = {"source", "release", "proposal"}


def _root() -> pathlib.Path | None:
    value = os.environ.get("OFFICE_RUNTIME_ROOT", "").strip()
    return pathlib.Path(value).expanduser() if value else None


def _at(path: pathlib.Path) -> str:
    return dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _history(state: dict) -> list[str]:
    receipts = state.get("receipts") or {}
    history = ["review"]
    if "preview" in receipts or state.get("route") == "proposal" and "proposal" in receipts:
        history.append("preview")
    if "merged" in receipts:
        history.append("merged")
    if "staged" in receipts:
        history.append("staged")
    if "release" in receipts:
        history.extend(["promoted", "live_verified"])
    if "buzz" in receipts:
        history.append("notified")
    if state.get("terminal"):
        history.append("terminal")
    return history


def _terminal_proven(state: dict) -> bool:
    receipts, route = state.get("receipts") or {}, state.get("route")
    if not state.get("terminal"):
        return False
    for receipt in receipts.values():
        if not isinstance(receipt, dict) or any((
            receipt.get("repo") != state.get("repo"),
            receipt.get("pr") != state.get("pr"),
            receipt.get("head_sha") != state.get("head_sha"),
            receipt.get("policy_hash") != state.get("policy_hash"),
        )):
            return False
    if route == "proposal":
        proof = receipts.get("proposal") or {}
        return proof.get("outcome") == "PASS" and bool(proof.get("artifact_url") and proof.get("proof_bundle"))
    merged = receipts.get("merged") or {}
    if merged.get("outcome") != "PASS" or not merged.get("merged_sha"):
        return False
    if route == "source":
        return (receipts.get("composite") or {}).get("outcome") == "PASS"
    if route != "release":
        return False
    preview, staged = receipts.get("preview") or {}, receipts.get("staged") or {}
    release, buzz = receipts.get("release") or {}, receipts.get("buzz") or {}
    return (
        preview.get("outcome") == "PASS"
        and staged.get("outcome") == "PASS"
        and staged.get("sha") == merged.get("merged_sha")
        and release.get("outcome") == "PASS"
        and release.get("sha") == merged.get("merged_sha")
        and bool(release.get("live_receipt"))
        and buzz.get("outcome") == "PASS"
        and buzz.get("accepted") is True
    )


def _next(state: dict) -> str:
    have, route = state.get("receipts") or {}, state.get("route")
    if route == "proposal":
        return "complete" if state.get("terminal") else "verify proposal"
    if "merged" not in have:
        return "preview" if route == "release" and "preview" not in have else "merge"
    if route == "source":
        return "verify downstream proof"
    if "staged" not in have:
        return "stage"
    if "release" not in have:
        return "promote"
    if "buzz" not in have:
        return "notify Buzz"
    return "close linked issue"


def _row(path: pathlib.Path, state: dict) -> dict:
    required = ("repo", "pr", "head_sha", "policy_hash", "route", "receipts", "terminal")
    missing = [key for key in required if key not in state]
    problems = []
    if missing:
        problems.append("missing " + ", ".join(missing))
    if state.get("route") not in ROUTES:
        problems.append("unknown route")
    receipts = state.get("receipts") if isinstance(state.get("receipts"), dict) else {}
    if any((row or {}).get("outcome") == "ROLLED_BACK" for row in receipts.values() if isinstance(row, dict)):
        problems.append("release rolled back")
    if state.get("terminal") and not _terminal_proven(state):
        problems.append("terminal claim lacks exact route proof")
    return {
        "repo": str(state.get("repo") or ""), "pr": state.get("pr"),
        "head_sha": str(state.get("head_sha") or ""), "route": str(state.get("route") or ""),
        "phase": str(state.get("phase") or "review"), "history": _history(state),
        "next": _next(state), "terminal": bool(state.get("terminal")) and not problems,
        "blocked": bool(problems), "problems": problems, "at": _at(path),
    }


def read() -> dict:
    root = _root()
    if root is None:
        return {"state": "unconfigured", "detail": "OFFICE_RUNTIME_ROOT is not set", "rows": []}
    directory = root / RELATIVE
    if not directory.exists():
        return {"state": "never", "detail": f"no {RELATIVE}", "rows": []}
    rows, torn = [], []
    for path in sorted(directory.rglob("*.json")):
        try:
            value = json.loads(path.read_text())
            if not isinstance(value, dict):
                raise TypeError("not an object")
            rows.append(_row(path, value))
        except (OSError, ValueError, TypeError):
            torn.append(path.name)
    rows.sort(key=lambda row: row["at"], reverse=True)
    return {"state": "blocked" if torn or any(r["blocked"] for r in rows) else "ok",
            "rows": rows, "torn": torn, "as_of": rows[0]["at"] if rows else ""}


def card(data: dict) -> dict:
    rows = data.get("rows") or []
    blocked = [r for r in rows if r.get("blocked")]
    active = [r for r in rows if not r.get("terminal") and not r.get("blocked")]
    done = [r for r in rows if r.get("terminal")]
    headline = (f"{len(blocked)} blocked; {len(active)} moving; {len(done)} completed"
                if rows else data.get("detail") or "No delivery state yet")
    facts = [_card.fact("pipeline health", "blocked" if blocked else data.get("state", "unknown"),
                        "bad" if blocked else "ok"),
             _card.fact("next up", active[0]["next"] if active else "none", "dim"),
             _card.fact("completed recently", str(len(done)), "ok" if done else "dim")]
    return _card.build(TITLE, headline, len(blocked), data.get("as_of") or "", facts, [])
