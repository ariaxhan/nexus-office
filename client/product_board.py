"""Outcome-level work state from product-owned catalogs and git history."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
from datetime import datetime, timezone

VAULTS = pathlib.Path(os.environ.get("OFFICE_RUNTIME_ROOT", pathlib.Path.home() / "Developer/Vaults"))
TBS = VAULTS / "CodingVault" / "thinking-brain-school"
CATALOG = TBS / "repos" / "tbs-curriculum" / "catalog"
READY = {"BUILT", "STAGED", "SHIPPED", "LIVE"}
PRODUCT_NAMES = {"mommyai": "MommyAI", "superpowerai": "Superpower",
                 "littlehello": "Little Hello", "homeclass": "Homeclass",
                 "homeworship": "Home Worship"}


def _git(repo: pathlib.Path, *args: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                              text=True, timeout=4).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _remote(repo: pathlib.Path) -> str:
    raw = _git(repo, "remote", "get-url", "origin")
    if raw.startswith("git@github.com:"):
        raw = "https://github.com/" + raw.removeprefix("git@github.com:")
    return raw.removesuffix(".git") if raw.startswith("https://github.com/") else ""


def _catalog_item(path: pathlib.Path) -> dict | None:
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(rows, list) or not rows:
        return None
    product = path.stem
    remaining = [row for row in rows
                 if not row.get("is_published")
                 and str(row.get("build_status") or "") not in READY]
    ready = len(rows) - len(remaining)
    sha = _git(CATALOG.parent, "rev-parse", "HEAD")
    remote = _remote(CATALOG.parent)
    proof = []
    if remote and sha:
        proof.append({"label": "catalog", "url": f"{remote}/blob/{sha}/catalog/{path.name}"})
    live = next((row for row in reversed(rows)
                 if row.get("is_published") and row.get("slug")), None)
    if live:
        proof.append({"label": "live", "url": "https://thinkingbrainschool.com/" +
                      str(live["slug"]).lstrip("/")})
    blockers = [f"{str(row.get('slug') or '').rsplit('/', 1)[-1]} {row.get('build_status', '')}".strip()
                for row in remaining[:4]]
    newest = _git(CATALOG.parent, "log", "-1", "--format=%cI%x09%s", "--", f"catalog/{path.name}")
    changed_at, changed = (newest.split("\t", 1) + [""])[:2] if newest else ("", "")
    return {
        "id": product, "name": PRODUCT_NAMES.get(product, product),
        "status": f"{ready}/{len(rows)} built", "ready": ready, "total": len(rows),
        "remaining": len(remaining), "changed": changed,
        "blocked": ", ".join(blockers),
        "next": blockers[0] if blockers else "keep it current",
        "proof": proof, "updated_at": changed_at,
    }


def _recent_changes() -> list[dict]:
    changes = []
    for name in ("tbs-curriculum", "tbs-www", "tbs-landing"):
        repo = TBS / "repos" / name
        remote = _remote(repo)
        if not remote:
            continue
        raw = _git(repo, "log", "--since=24 hours ago", "--format=%H%x09%cI%x09%s", "-8")
        for line in raw.splitlines():
            parts = line.split("\t", 2)
            if len(parts) != 3:
                continue
            sha, at, subject = parts
            changes.append({"id": sha, "project": name, "summary": subject,
                            "at": at, "url": f"{remote}/commit/{sha}"})
    changes.sort(key=lambda row: row["at"], reverse=True)
    return changes[:8]


def read() -> dict:
    if not CATALOG.is_dir():
        return {"state": "missing", "detail": "product catalogs are not available",
                "products": [], "changes": []}
    products = [_catalog_item(path) for path in sorted(CATALOG.glob("*.json"))]
    products = [row for row in products if row and row["remaining"] > 0]
    products.sort(key=lambda row: (-row["remaining"], row["name"]))
    return {"state": "ok", "detail": "", "products": products, "changes": _recent_changes(),
            "updated_at": datetime.now(timezone.utc).isoformat()}
