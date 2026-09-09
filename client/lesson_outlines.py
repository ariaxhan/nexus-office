"""Plain-text lesson outlines from the lesson repository; approval runs its own CLI."""
from __future__ import annotations

from pathlib import Path
import re
import subprocess

import lesson_status

APPROVE = "bin/lesson-outline.py"
HEADER_KEYS = ("status", "product", "lesson", "decision", "source", "value", "template", "lane", "drafted")
LESSON_RE = re.compile(r"L\d{3}-[a-z0-9][a-z0-9-]*")


def _options(folder):
    """The idea behind the outline. decision-proposed: is the factory's own proposal, waiting on Aria;
    approving the outline is what promotes it to decision: (bin/lesson-outline.py approve)."""
    path = folder.parent / f"{folder.name[:4]}-OPTIONS.md"
    text = lesson_status._read(path) if path.is_file() else ""
    keys = dict(re.findall(r"(?m)^(decision-proposed|decision|value):\s*(.*)$", text))
    return {"decision": keys.get("decision") or keys.get("decision-proposed", ""),
            "proposed": bool(keys.get("decision-proposed") and not keys.get("decision")),
            "value": keys.get("value", ""), "options": str(path.name) if path.is_file() else ""}


def _parse(text):
    head, body = {}, []
    lines = text.splitlines()
    index = 0
    for index, line in enumerate(lines):
        match = re.match(r"^([a-z]+):\s*(.*)$", line)
        if not match or match.group(1) not in HEADER_KEYS:
            break
        head[match.group(1)] = match.group(2).strip()
    else:
        index = len(lines)
    body = "\n".join(lines[index:]).strip("\n")
    status = head.get("status", "unknown")
    return {**{key: head.get(key, "") for key in HEADER_KEYS},
            "status": status, "approved": status.startswith("approved"), "body": body}


def _entry(root, path):
    folder = path.parent
    product = {"superkidsai": "superpowerai", "mommyai": "mommyai"}.get(folder.parent.name, "")
    head = _parse(lesson_status._read(path))
    idea = _options(folder)
    proposed = idea["proposed"] or head["source"] == "proposed"
    return {**head, "product_dir": folder.parent.name, "product": product, "lesson": folder.name,
            "decision": idea["decision"] or head["decision"], "proposed": proposed and not head["approved"],
            "value": head["value"] or idea["value"], "options": idea["options"],
            "queued": head["approved"], "source": str(path.relative_to(root))}


def build(root=None):
    root = root or lesson_status.root_path()
    paths = sorted(root.glob("proposed/tbs-curriculum/*/L*/OUTLINE.md"))
    outlines = [_entry(root, path) for path in paths if path.is_file() and LESSON_RE.fullmatch(path.parent.name)]
    return {"state": "ok" if root.is_dir() else "missing", "outlines": outlines,
            "checked_at": lesson_status.dt.datetime.now(lesson_status.dt.timezone.utc).isoformat(timespec="seconds")}


def approve(body, root=None):
    root = root or lesson_status.root_path()
    product, lesson = body.get("product"), body.get("lesson")
    if product not in lesson_status.PRODUCTS or not isinstance(lesson, str) or not LESSON_RE.fullmatch(lesson):
        raise ValueError("unknown product or lesson")
    path = root / "proposed/tbs-curriculum" / lesson_status.PRODUCTS[product] / lesson / "OUTLINE.md"
    if not path.is_file():
        raise FileNotFoundError(f"no outline for {product} {lesson}")
    if _parse(lesson_status._read(path))["approved"]:
        return 409, {"error": "already approved", "status": _parse(lesson_status._read(path))["status"]}
    run = subprocess.run(["python3", APPROVE, "approve", product, lesson, "--by", "aria"],
                         cwd=root, capture_output=True, text=True, timeout=30)
    entry = _entry(root, path)
    if run.returncode != 0 or not entry["approved"]:
        return 500, {"error": (run.stderr or run.stdout or "approve failed").strip()[:300], "status": entry["status"]}
    return 200, {"ok": True, "status": entry["status"], "output": run.stdout.strip()[:300]}
