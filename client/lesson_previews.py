"""Generate the lesson preview hub from durable preview and production receipts."""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import re
import urllib.parse

SCHEMA = "tbs.lesson-preview/v1"
PRODUCTION_SCHEMA = "tbs.lesson-production/v1"
CANONICAL = re.compile(r"L\d{3}\Z")
WIDTHS = {375, 768, 1440}
PRODUCTION_ORIGIN = "https://thinkingbrainschool.com"


def _receipts_root() -> pathlib.Path | None:
    explicit = os.environ.get("OFFICE_LESSON_RECEIPTS_ROOT", "").strip()
    if explicit:
        return pathlib.Path(explicit).expanduser()
    runtime = os.environ.get("OFFICE_RUNTIME_ROOT", "").strip()
    if not runtime:
        return None
    return (pathlib.Path(runtime).expanduser() / "CodingVault" /
            "thinking-brain-school" / "_meta" / ".runtime" / "lesson-previews")


def _json(path: pathlib.Path) -> tuple[dict | None, str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"{type(exc).__name__}: {exc}"[:180]
    return (value, "") if isinstance(value, dict) else (None, "receipt is not an object")


def _millis(value) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    if not value:
        return None
    try:
        return int(dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def _https_origin(value) -> bool:
    parsed = urllib.parse.urlparse(str(value or ""))
    return parsed.scheme == "https" and bool(parsed.netloc) and not parsed.username


def _candidate(receipt: dict) -> tuple[list[str], list[str]]:
    bad, warn = [], []
    if receipt.get("schema_version") != SCHEMA:
        bad.append("unsupported receipt")
    if receipt.get("outcome") != "PASS":
        bad.append("QA failed")
    if str(receipt.get("git_dirty", "")).lower() not in {"0", "false"}:
        bad.append("dirty source")
    if receipt.get("deployment_state") != "READY" or not _https_origin(receipt.get("origin")):
        bad.append("candidate unreachable")
    if not receipt.get("deployment_id") or not receipt.get("source_sha"):
        bad.append("missing deployment evidence")
    if receipt.get("deployment_sha") != receipt.get("source_sha"):
        bad.append("source/deployment conflict")
    transform = receipt.get("transform_sha")
    if transform and transform != receipt.get("deployment_transform_sha"):
        bad.append("transform conflict")
    source_at = _millis(receipt.get("source_committed_at"))
    deployed_at = _millis(receipt.get("deployment_created_at"))
    if source_at and (not deployed_at or deployed_at < source_at):
        bad.append("candidate older than source")
    if receipt.get("console_errors") or receipt.get("request_failures"):
        bad.append("browser errors")
    shots = {x.get("width") for x in receipt.get("screenshots", []) if isinstance(x, dict)}
    if not WIDTHS.issubset(shots):
        bad.append("missing responsive QA")
    images = receipt.get("images")
    if isinstance(images, list) and any(not x.get("naturalWidth") for x in images if isinstance(x, dict)):
        bad.append("image failed to decode")
    if not receipt.get("verified_at"):
        bad.append("missing checked time")
    return list(dict.fromkeys(bad)), warn


def _production(receipt: dict | None, candidate: dict) -> tuple[dict, list[str], bool]:
    route = str((receipt or {}).get("route") or candidate.get("route") or "")
    origin = str((receipt or {}).get("origin") or PRODUCTION_ORIGIN)
    url = origin.rstrip("/") + route if route.startswith("/") and _https_origin(origin) else ""
    bad = []
    if receipt is None:
        bad.append("missing production receipt")
        return {"url": url, "source_sha": "", "deployment_id": "", "checked_at": ""}, bad, False
    if receipt.get("schema_version") != PRODUCTION_SCHEMA or receipt.get("outcome") != "PASS":
        bad.append("production QA failed")
    if receipt.get("deployment_state") != "READY" or not url:
        bad.append("production unreachable")
    if not receipt.get("source_sha") or not receipt.get("deployment_id"):
        bad.append("missing production evidence")
    candidate_at = _millis(candidate.get("deployment_created_at"))
    production_at = _millis(receipt.get("deployment_created_at"))
    newer = bool(candidate_at and production_at and candidate_at > production_at)
    return {"url": url, "source_sha": str(receipt.get("source_sha") or ""),
            "deployment_id": str(receipt.get("deployment_id") or ""),
            "checked_at": str(receipt.get("verified_at") or "")}, bad, newer


def build(root: pathlib.Path | None = None) -> dict:
    root = root or _receipts_root()
    if root is None:
        return {"state": "unconfigured", "detail": "lesson receipt root is not configured", "lessons": []}
    if not root.is_dir():
        return {"state": "missing", "detail": f"lesson receipt root is missing: {root}", "lessons": []}
    lessons = []
    for path in sorted(root.glob("*/L[0-9][0-9][0-9]/preview.json")):
        lesson = path.parent.name
        if not CANONICAL.fullmatch(lesson):
            continue
        receipt, error = _json(path)
        if receipt is None:
            lessons.append({"product": path.parent.parent.name, "lesson": lesson,
                            "status": "failed", "problems": [error], "candidate": {}, "production": {}})
            continue
        product = str(receipt.get("product") or path.parent.parent.name)
        conflicts = []
        if product != path.parent.parent.name:
            conflicts.append("product/path conflict")
        prod_path = path.with_name("production.json")
        prod, prod_error = _json(prod_path) if prod_path.exists() else (None, "")
        candidate_bad, _ = _candidate(receipt)
        production, production_bad, candidate_newer = _production(prod, receipt)
        problems = conflicts + candidate_bad + production_bad
        if prod_error:
            problems.append(prod_error)
        verified = not conflicts and not candidate_bad
        candidate_url = str(receipt.get("origin") or "").rstrip("/") + str(receipt.get("route") or "") if verified else ""
        if candidate_newer:
            production_bad.append("candidate newer than production")
            problems.append("candidate newer than production")
        lessons.append({
            "product": product, "lesson": lesson,
            "status": "failed" if problems else "current",
            "problems": list(dict.fromkeys(problems)),
            "candidate_newer_than_production": candidate_newer,
            "candidate": {"url": candidate_url, "source_sha": str(receipt.get("source_sha") or ""),
                          "deployment_id": str(receipt.get("deployment_id") or ""),
                          "qa": str(receipt.get("outcome") or "MISSING"),
                          "checked_at": str(receipt.get("verified_at") or "")},
            "production": production,
        })
    return {"schema_version": "nexus.lesson-preview-hub/v1", "state": "ok",
            "root": str(root), "lessons": lessons,
            "counts": {"total": len(lessons), "failed": sum(x["status"] == "failed" for x in lessons)}}
