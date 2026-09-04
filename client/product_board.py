"""Outcome-level work state from product-owned catalogs and git history."""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
from datetime import datetime, timezone

VAULTS = pathlib.Path(os.environ.get("OFFICE_RUNTIME_ROOT", pathlib.Path.home() / "Developer/Vaults"))
TBS = VAULTS / "CodingVault" / "thinking-brain-school"
CATALOG = TBS / "repos" / "tbs-curriculum" / "catalog"
READY = {"BUILT", "STAGED", "SHIPPED", "LIVE"}
PRODUCT_NAMES = {"mommyai": "MommyAI", "superpowerai": "Superpower",
                 "littlehello": "Little Hello", "homeclass": "Homeclass",
                 "homeworship": "Home Worship"}
DOCUMENT_SUFFIXES = {".md"}
PR_NUMBER = re.compile(r"(?:pull request #|\(#)(\d+)", re.IGNORECASE)
_PR_CACHE: dict[tuple[str, str], tuple[dict, list[dict], list[str]]] = {}


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
    proof = _catalog_proof(path, rows)
    blockers = _blockers(remaining)
    changed_at, changed = _catalog_change(path)
    return {
        "id": product, "name": PRODUCT_NAMES.get(product, product),
        "status": f"{ready}/{len(rows)} built", "ready": ready, "total": len(rows),
        "remaining": len(remaining), "changed": changed,
        "blocked": ", ".join(blockers),
        "next": blockers[0] if blockers else "keep it current",
        "proof": proof, "updated_at": changed_at,
    }


def _catalog_proof(path: pathlib.Path, rows: list[dict]) -> list[dict]:
    sha = _git(CATALOG.parent, "rev-parse", "HEAD")
    remote = _remote(CATALOG.parent)
    proof = ([{"label": "catalog", "url": f"{remote}/blob/{sha}/catalog/{path.name}"}]
             if remote and sha else [])
    live = next((row for row in reversed(rows)
                 if row.get("is_published") and row.get("slug")), None)
    if live:
        proof.append({"label": "live", "url": "https://thinkingbrainschool.com/" +
                      str(live["slug"]).lstrip("/")})
    return proof


def _blockers(rows: list[dict]) -> list[str]:
    return [f"{str(row.get('slug') or '').rsplit('/', 1)[-1]} "
            f"{row.get('build_status', '')}".strip() for row in rows[:4]]


def _catalog_change(path: pathlib.Path) -> tuple[str, str]:
    newest = _git(CATALOG.parent, "log", "-1", "--format=%cI%x09%s", "--",
                  f"catalog/{path.name}")
    return tuple((newest.split("\t", 1) + [""])[:2]) if newest else ("", "")


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
            repo_name = _repo_name(remote)
            paths = _changed_paths(repo, sha)
            pr, issues, pr_paths = _pr_receipt(repo_name, subject)
            if pr_paths:
                paths = pr_paths
            files = [_artifact(repo_name, remote, sha, path) for path in paths]
            documents = [row for row in files if pathlib.Path(row["path"]).suffix.lower()
                         in DOCUMENT_SUFFIXES]
            changes.append({"id": sha, "project": name, "summary": subject,
                            "at": at, "url": f"{remote}/commit/{sha}",
                            "repo": repo_name, "pr": pr, "issues": issues,
                            "chronicles": [row for row in documents
                                           if "/chronicles/" in "/" + row["path"]],
                            "documents": documents, "files": files})
    changes.sort(key=lambda row: row["at"], reverse=True)
    return changes[:8]


def _repo_name(remote: str) -> str:
    return remote.removeprefix("https://github.com/")


def _changed_paths(repo: pathlib.Path, sha: str) -> list[str]:
    parents = _git(repo, "rev-list", "--parents", "-1", sha).split()
    if len(parents) < 2:
        return []
    raw = _git(repo, "diff", "--name-only", parents[1], sha)
    return [path for path in raw.splitlines() if path]


def _pr_receipt(repo: str, subject: str) -> tuple[dict, list[dict], list[str]]:
    match = PR_NUMBER.search(subject)
    if not match or not repo:
        return {}, [], []
    number = match.group(1)
    key = (repo, number)
    if key in _PR_CACHE:
        return _PR_CACHE[key]
    try:
        raw = subprocess.run(
            ["gh", "pr", "view", number, "-R", repo, "--json",
             "url,closingIssuesReferences,files"], capture_output=True, text=True, timeout=5,
        )
        data = json.loads(raw.stdout) if raw.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, ValueError):
        data = {}
    pr = {"label": f"PR #{number}", "url": data.get("url") or
          f"https://github.com/{repo}/pull/{number}"}
    issues = [{"label": f"issue #{row['number']}", "url": row["url"]}
              for row in data.get("closingIssuesReferences", [])
              if row.get("number") and row.get("url")]
    paths = [row["path"] for row in data.get("files", []) if row.get("path")]
    receipt = (pr, issues, paths)
    _PR_CACHE[key] = receipt
    return receipt


def _artifact(repo: str, remote: str, sha: str, path: str) -> dict:
    return {"id": f"{repo}:{path}", "name": pathlib.Path(path).name, "path": path,
            "repo": repo, "url": f"{remote}/blob/{sha}/{path}"}


def read() -> dict:
    if not CATALOG.is_dir():
        return {"state": "missing", "detail": "product catalogs are not available",
                "products": [], "changes": []}
    products = [_catalog_item(path) for path in sorted(CATALOG.glob("*.json"))]
    products = [row for row in products if row and row["remaining"] > 0]
    products.sort(key=lambda row: (-row["remaining"], row["name"]))
    return {"state": "ok", "detail": "", "products": products, "changes": _recent_changes(),
            "updated_at": datetime.now(timezone.utc).isoformat()}
