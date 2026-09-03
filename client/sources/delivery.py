"""Exact delivery progress from the PR pipeline's durable state files."""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import re
import urllib.parse

from sources import _card

KEY = "delivery"
TITLE = "Delivery conveyor"
RELATIVE = "_meta/services/pr-pipeline/.runtime/delivery"
ROUTES = {"source", "release", "proposal"}
RECEIPTS = {
    "proposal": {"proposal"},
    "source": {"merged", "composite"},
    "release": {"preview", "merged", "staged", "release", "buzz"},
}


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _sha(value, size: int) -> bool:
    return _text(value) and len(value) == size and re.fullmatch(r"[0-9a-fA-F]+", value) is not None


def _url(value) -> bool:
    if not _text(value):
        return False
    parsed = urllib.parse.urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _bindings(receipt: dict, state: dict) -> bool:
    return all((
        receipt.get("repo") == state.get("repo"),
        receipt.get("pr") == state.get("pr"),
        receipt.get("head_sha") == state.get("head_sha"),
        receipt.get("policy_hash") == state.get("policy_hash"),
    ))


def _receipt_shape(kind: str, receipt: dict) -> bool:
    if receipt.get("outcome") != "PASS":
        return False
    if kind == "proposal":
        return _url(receipt.get("artifact_url")) and _text(receipt.get("proof_bundle"))
    if kind == "preview":
        return _text(receipt.get("deployment_id")) and _text(receipt.get("proof_bundle"))
    if kind == "merged":
        return _sha(receipt.get("merged_sha"), 40)
    if kind == "staged":
        return _sha(receipt.get("sha"), 40)
    if kind == "release":
        return _sha(receipt.get("sha"), 40) and _text(receipt.get("live_receipt"))
    if kind == "buzz":
        return receipt.get("accepted") is True
    if kind == "composite":
        proofs = receipt.get("proofs")
        return (isinstance(proofs, list) and bool(proofs)
                and all(isinstance(proof, dict) and bool(proof) for proof in proofs))
    return False


def _root() -> pathlib.Path | None:
    value = os.environ.get("OFFICE_RUNTIME_ROOT", "").strip()
    return pathlib.Path(value).expanduser() if value else None


def _at(path: pathlib.Path) -> str:
    return dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _history(state: dict) -> list[str]:
    receipts = state.get("receipts") if isinstance(state.get("receipts"), dict) else {}
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
    receipts, route = state.get("receipts"), state.get("route")
    if not state.get("terminal"):
        return False
    if not isinstance(receipts, dict) or route not in RECEIPTS or set(receipts) != RECEIPTS[route]:
        return False
    for kind, receipt in receipts.items():
        if not isinstance(receipt, dict) or not _bindings(receipt, state) or not _receipt_shape(kind, receipt):
            return False
    if route == "proposal":
        return True
    merged = receipts.get("merged") or {}
    if merged.get("outcome") != "PASS" or not merged.get("merged_sha"):
        return False
    if route == "source":
        return True
    if route != "release":
        return False
    preview, staged = receipts.get("preview") or {}, receipts.get("staged") or {}
    release, buzz = receipts.get("release") or {}, receipts.get("buzz") or {}
    return (
        staged.get("sha") == merged.get("merged_sha")
        and release.get("sha") == merged.get("merged_sha")
    )


def _next(state: dict) -> str:
    have = state.get("receipts") if isinstance(state.get("receipts"), dict) else {}
    route = state.get("route")
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
    if not isinstance(state.get("receipts"), dict):
        raise TypeError("receipts is not an object")
    if not isinstance(state.get("terminal"), bool):
        raise TypeError("terminal is not boolean")
    problems = []
    if missing:
        problems.append("missing " + ", ".join(missing))
    if state.get("route") not in ROUTES:
        problems.append("unknown route")
    receipts = state.get("receipts") if isinstance(state.get("receipts"), dict) else {}
    if not isinstance(state.get("pr"), int) or isinstance(state.get("pr"), bool) or state.get("pr", 0) <= 0:
        problems.append("invalid PR number")
    if not _text(state.get("repo")) or "/" not in state.get("repo", ""):
        problems.append("invalid repo")
    if not _sha(state.get("head_sha"), 40) or not _sha(state.get("policy_hash"), 64):
        problems.append("invalid exact binding")
    allowed = RECEIPTS.get(state.get("route"), set())
    if any(kind not in allowed for kind in receipts):
        problems.append("receipt belongs to another route")
    if any(not isinstance(receipt, dict) or not _bindings(receipt, state)
           or not _receipt_shape(kind, receipt) for kind, receipt in receipts.items()):
        problems.append("malformed or mismatched receipt")
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
