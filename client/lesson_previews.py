"""Generate the lesson preview hub from durable preview and production receipts."""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import re
import urllib.parse

SCHEMA = "tbs.lesson-preview/v2"
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


def _catalog_root() -> pathlib.Path | None:
    runtime = os.environ.get("OFFICE_RUNTIME_ROOT", "").strip()
    if not runtime:
        return None
    return pathlib.Path(runtime).expanduser() / "CodingVault" / "tbs-curriculum" / "catalog"


def _json(path: pathlib.Path) -> tuple[dict | None, str]:
    """Read one receipt. The problem string is published verbatim in the static
    export, which is why `build` drops `root`; an OSError stringifies with the
    filename it failed on, so it would put the builder's absolute local path
    back into the same public file. Say what went wrong, never where."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return None, f"{type(exc).__name__}: {exc.strerror or 'unreadable'}"[:180]
    except json.JSONDecodeError as exc:
        return None, f"{type(exc).__name__}: {exc.msg} (line {exc.lineno})"[:180]
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


def _source_proven(receipt: dict) -> bool:
    """Both the source worktree and the thing that was deployed said clean."""
    return (receipt.get("source_git_clean") is True and
            str(receipt.get("deployment_source_git_clean", "")) == "1")


def _transform_problem(receipt: dict) -> str:
    """A dirty worktree may only ship with a transform digest that the
    deployment agrees with. A clean one may not ship a transform at all, and
    any other `git_dirty` is a receipt we cannot reason about."""
    dirty = str(receipt.get("git_dirty", ""))
    transform = str(receipt.get("transform_sha") or "")
    deployed = str(receipt.get("deployment_transform_sha") or "")
    if dirty == "0":
        return "unexpected transform" if transform or deployed else ""
    if dirty != "1":
        return "invalid transformed-worktree state"
    agrees = re.fullmatch(r"[0-9a-f]{64}", transform) and transform == deployed
    return "" if agrees else "transform conflict"


def _reachable(receipt: dict) -> bool:
    return (receipt.get("deployment_state") == "READY" and
            _https_origin(receipt.get("origin")) and
            str(receipt.get("route") or "").startswith("/"))


def _older_than_source(receipt: dict) -> bool:
    source_at = _millis(receipt.get("source_committed_at"))
    deployed_at = _millis(receipt.get("deployment_created_at"))
    return bool(source_at and (not deployed_at or deployed_at < source_at))


def _responsive(receipt: dict) -> bool:
    shots = {x.get("width") for x in receipt.get("screenshots", []) if isinstance(x, dict)}
    return WIDTHS.issubset(shots)


def _images_decoded(receipt: dict) -> bool:
    images = receipt.get("images")
    if not isinstance(images, list):
        return True
    return all(x.get("naturalWidth") for x in images if isinstance(x, dict))


def _schema(receipt: dict) -> str:
    return "" if receipt.get("schema_version") == SCHEMA else "unsupported receipt"


def _qa(receipt: dict) -> str:
    return "" if receipt.get("outcome") == "PASS" else "QA failed"


def _cleanliness(receipt: dict) -> str:
    return "" if _source_proven(receipt) else "source cleanliness unproven"


def _reach(receipt: dict) -> str:
    return "" if _reachable(receipt) else "candidate unreachable"


def _evidence(receipt: dict) -> str:
    have = receipt.get("deployment_id") and receipt.get("source_sha")
    return "" if have else "missing deployment evidence"


def _sha_agreement(receipt: dict) -> str:
    same = receipt.get("deployment_sha") == receipt.get("source_sha")
    return "" if same else "source/deployment conflict"


def _freshness(receipt: dict) -> str:
    return "candidate older than source" if _older_than_source(receipt) else ""


def _browser(receipt: dict) -> str:
    broke = receipt.get("console_errors") or receipt.get("request_failures")
    return "browser errors" if broke else ""


def _responsive_qa(receipt: dict) -> str:
    return "" if _responsive(receipt) else "missing responsive QA"


def _images(receipt: dict) -> str:
    return "" if _images_decoded(receipt) else "image failed to decode"


def _checked(receipt: dict) -> str:
    return "" if receipt.get("verified_at") else "missing checked time"


# Order is the order a reader should hear the problems in, worst provenance
# question first. Each check answers with the problem or with nothing.
CANDIDATE_CHECKS = (_schema, _qa, _cleanliness, _transform_problem, _reach,
                    _evidence, _sha_agreement, _freshness, _browser,
                    _responsive_qa, _images, _checked)


def _candidate(receipt: dict) -> tuple[list[str], list[str]]:
    bad = [problem for problem in (check(receipt) for check in CANDIDATE_CHECKS) if problem]
    return list(dict.fromkeys(bad)), []


def _production_url(receipt: dict | None, candidate: dict) -> str:
    """Production lives at one origin and the lesson's own route. A receipt may
    name a different origin; anything that is not an https origin and a rooted
    route resolves to no link at all rather than to a guess."""
    route = str((receipt or {}).get("route") or candidate.get("route") or "")
    origin = str((receipt or {}).get("origin") or PRODUCTION_ORIGIN)
    if not route.startswith("/") or not _https_origin(origin):
        return ""
    return origin.rstrip("/") + route


def _production_problems(receipt: dict, url: str) -> list[str]:
    bad = []
    if receipt.get("schema_version") != PRODUCTION_SCHEMA or receipt.get("outcome") != "PASS":
        bad.append("production QA failed")
    if receipt.get("deployment_state") != "READY" or not url:
        bad.append("production unreachable")
    if not receipt.get("source_sha") or not receipt.get("deployment_id"):
        bad.append("missing production evidence")
    return bad


def _production(receipt: dict | None, candidate: dict) -> tuple[dict, list[str], bool]:
    url = _production_url(receipt, candidate)
    if receipt is None:
        return ({"url": url, "source_sha": "", "deployment_id": "", "checked_at": ""},
                ["missing production receipt"], False)
    candidate_at = _millis(candidate.get("deployment_created_at"))
    production_at = _millis(receipt.get("deployment_created_at"))
    newer = bool(candidate_at and production_at and candidate_at > production_at)
    return ({"url": url, "source_sha": str(receipt.get("source_sha") or ""),
             "deployment_id": str(receipt.get("deployment_id") or ""),
             "checked_at": str(receipt.get("verified_at") or "")},
            _production_problems(receipt, url), newer)


def _inventory(catalog_root: pathlib.Path | None) -> dict[tuple[str, str], dict]:
    inventory: dict[tuple[str, str], dict] = {}
    counts: dict[str, int] = {}
    if catalog_root is None or not catalog_root.is_dir():
        return inventory
    for path in sorted(catalog_root.glob("*.json")):
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(rows, list):
            continue
        for item in rows:
            if not isinstance(item, dict):
                continue
            product = str(item.get("product") or path.stem)
            # Numbered per product, not per file. `product` is read off the row,
            # so two catalog files can name the same one; a file-positional
            # counter would then mint the same key twice and the second file
            # would silently overwrite the first's lessons, route and all.
            counts[product] = counts.get(product, 0) + 1
            slug = str(item.get("slug") or "")
            route = f"/{slug}" if slug and not slug.startswith("/") else slug
            lesson = f"L{counts[product]:03d}"
            if route and CANONICAL.fullmatch(lesson):
                inventory[(product, lesson)] = {"product": product, "lesson": lesson,
                                                "route": route, "title": str(item.get("title") or "")}
    return inventory


def _unreadable_row(path: pathlib.Path, lesson: str, error: str) -> dict:
    return {"product": path.parent.parent.name, "lesson": lesson,
            "status": "failed", "problems": [error], "candidate": {}, "production": {}}


def _receipt_row(path: pathlib.Path, lesson: str, receipt: dict, product: str) -> dict:
    """One lesson that has a candidate receipt. The candidate link is exposed
    only when nothing about its provenance is in question; a production problem
    is worth saying but is not the candidate's fault, so it does not withhold
    the link."""
    conflicts = ["product/path conflict"] if product != path.parent.parent.name else []
    prod_path = path.with_name("production.json")
    prod, prod_error = _json(prod_path) if prod_path.exists() else (None, "")
    candidate_bad, _ = _candidate(receipt)
    production, production_bad, candidate_newer = _production(prod, receipt)
    problems = conflicts + candidate_bad + production_bad
    if prod_error:
        problems.append(prod_error)
    if candidate_newer:
        problems.append("candidate newer than production")
    verified = not conflicts and not candidate_bad
    url = (str(receipt.get("origin") or "").rstrip("/") +
           str(receipt.get("route") or "")) if verified else ""
    return {"product": product, "lesson": lesson,
            "status": "failed" if problems else "current",
            "problems": list(dict.fromkeys(problems)),
            "candidate_newer_than_production": candidate_newer,
            "candidate": {"url": url, "source_sha": str(receipt.get("source_sha") or ""),
                          "deployment_id": str(receipt.get("deployment_id") or ""),
                          "qa": str(receipt.get("outcome") or "MISSING"),
                          "checked_at": str(receipt.get("verified_at") or "")},
            "production": production}


def _gap_row(item: dict) -> dict:
    """A catalog lesson with no candidate receipt at all. It is still listed,
    because a lesson nobody has previewed is the fact the page is for."""
    production, production_bad, _ = _production(None, item)
    return {"product": item["product"], "lesson": item["lesson"],
            "title": item["title"], "status": "failed",
            "problems": ["missing candidate receipt"] + production_bad,
            "candidate_newer_than_production": False,
            "candidate": {"url": "", "source_sha": "", "deployment_id": "",
                          "qa": "MISSING", "checked_at": ""},
            "production": production}


def _rows(root: pathlib.Path) -> tuple[list[dict], set[tuple[str, str]]]:
    lessons, seen = [], set()
    for path in sorted(root.glob("*/L[0-9][0-9][0-9]/preview.json")):
        lesson = path.parent.name
        if not CANONICAL.fullmatch(lesson):
            continue
        receipt, error = _json(path)
        if receipt is None:
            lessons.append(_unreadable_row(path, lesson, error))
            continue
        product = str(receipt.get("product") or path.parent.parent.name)
        seen.add((product, lesson))
        lessons.append(_receipt_row(path, lesson, receipt, product))
    return lessons, seen


def build(root: pathlib.Path | None = None, catalog_root: pathlib.Path | None = None) -> dict:
    root = root or _receipts_root()
    if root is None:
        return {"state": "unconfigured", "detail": "lesson receipt root is not configured", "lessons": []}
    if not root.is_dir():
        return {"state": "missing", "detail": f"lesson receipt root is missing: {root}", "lessons": []}
    lessons, seen = _rows(root)
    inventory = _inventory(catalog_root if catalog_root is not None else _catalog_root())
    lessons += [_gap_row(item) for key, item in inventory.items() if key not in seen]
    lessons.sort(key=lambda row: (row["product"], row["lesson"]))
    return {"schema_version": "nexus.lesson-preview-hub/v1", "state": "ok",
            "root": str(root), "lessons": lessons,
            "counts": {"total": len(lessons), "failed": sum(x["status"] == "failed" for x in lessons)}}
