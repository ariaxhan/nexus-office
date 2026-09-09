"""Local lesson evidence and append-only OPTIONS steering; no production credentials."""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import quote

PRODUCTS = {"superpowerai": "superkidsai", "mommyai": "mommyai"}
PREVIEW_ORIGIN = "https://tbs-lesson-previews.vercel.app"
PUBLICATION_SOURCE = "_meta/receipts/prod-probe/published.json"


def root_path():
    return Path(os.environ.get("OFFICE_LESSON_ROOT") or
                Path(__file__).resolve().parents[2] / "thinking-brain-school").expanduser().resolve()


def _read(path):
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _json(path, default):
    try:
        return json.loads(_read(path))
    except (ValueError, OSError):
        return default


def _publication(root):
    data = _json(root / PUBLICATION_SOURCE, {})
    checked = data.get("checked_at", "") if isinstance(data, dict) else ""
    try:
        timestamp = dt.datetime.fromisoformat(checked.replace("Z", "+00:00"))
        age = (dt.datetime.now(dt.timezone.utc) - timestamp).total_seconds()
        if age < 0:
            raise ValueError("future receipt")
    except (ValueError, TypeError, AttributeError):
        return {}, {"state": "unknown", "checked_at": "", "source": PUBLICATION_SOURCE,
                    "detail": "Publication receipt missing or invalid"}
    stale = age > 13 * 3600
    return data, {"state": "stale" if stale else "current", "checked_at": checked,
                  "source": PUBLICATION_SOURCE,
                  "detail": ("Stale: publication check older than 13 hours" if stale else "Production curriculum catalog") + f"; checked {checked}"}


def _published(publication, product, lesson):
    data, evidence = publication
    values = data.get(product, {})
    value = values.get(lesson) if isinstance(values, dict) else None
    if type(value) is not bool:
        return {**evidence, "present": None, "state": "unknown", "detail": "Publication lesson missing or invalid"}
    return {**evidence, "present": value}


def _identity(product, lesson):
    if not isinstance(product, str) or product not in PRODUCTS or not isinstance(lesson, str) or not re.fullmatch(r"L\d{3}", lesson):
        raise ValueError("unknown product or lesson")
    if not 1 <= int(lesson[1:]) <= 96:
        raise ValueError("lesson must be L001 through L096")


def _safe(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("path outside lesson repository")
    return path


def _report_link(relative):
    return "/api/lesson-report?path=" + quote(str(relative), safe="")


def report(relative, root=None):
    root = root or root_path()
    if not isinstance(relative, str) or not relative.startswith(("findings/continuity/", "findings/playthrough/", "proposed/tbs-curriculum/")):
        raise ValueError("unsupported report")
    path = _safe(root, relative)
    if path.suffix != ".md":
        raise ValueError("reports must be markdown")
    return path.read_text(encoding="utf-8")


def steer(body, root=None):
    root = root or root_path()
    product, lesson, text = body.get("product"), body.get("lesson"), body.get("text")
    _identity(product, lesson)
    if not isinstance(text, str) or not text.strip() or len(text) > 8000:
        raise ValueError("steering must contain 1–8000 characters")
    # A single dated line cannot inject a new decision or Markdown section.
    text = " ".join(text.split())
    relative = Path("proposed/tbs-curriculum") / PRODUCTS[product] / f"{lesson}-OPTIONS.md"
    path = _safe(root, relative)
    if path != root.resolve() / relative:
        raise ValueError("steering path must not contain symlinks")
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW
    with os.fdopen(os.open(path, flags, 0o644), "a+", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.seek(0)
        existing = stream.read()
        # Reopen the heading at EOF if another section follows the previous one.
        headings = re.findall(r"^##\s+(.+?)\s*$", existing, re.M)
        heading = "" if headings and headings[-1].lower() == "steering" else "\n## steering\n"
        line = f"- {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')} - {text}"
        stream.write("\n" + heading + line + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return 200, {"ok": True, "line": line, "report_url": _report_link(relative)}


def _main(root, repo):
    try:
        sha = subprocess.run(["git", "-C", str(root / "repos" / repo), "rev-parse", "origin/main"],
                             capture_output=True, text=True, timeout=5, check=True).stdout.strip()
        paths = subprocess.run(["git", "-C", str(root / "repos" / repo), "ls-tree", "-r", "--name-only", sha],
                               capture_output=True, text=True, timeout=5, check=True).stdout.splitlines()
        return {"sha": sha[:10], "paths": set(paths), "error": ""}
    except (OSError, subprocess.SubprocessError):
        return {"sha": "", "paths": set(), "error": "local origin/main unavailable"}


def _ledger(root, kind):
    relative = f"findings/{kind}/ledger.jsonl"
    rows, errors = {}, []
    for line in _read(root / relative).splitlines():
        try:
            row = json.loads(line)
            _identity(row.get("product"), row.get("lesson", "L001"))
            rows[(row["product"], row.get("lesson", "*"))] = row
        except (ValueError, AttributeError, TypeError):
            errors.append(f"{relative}: invalid ledger row")
    return rows, errors


def _count(value):
    if isinstance(value, list):
        return len(value)
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _finding_url(root, row, kind):
    relative = row.get("report") or row.get("report_path") or ""
    if not isinstance(relative, str):
        return ""
    if relative.startswith(str(root) + "/"):
        relative = relative[len(str(root)) + 1:]
    url = ""
    try:
        if relative.startswith(f"findings/{kind}/") and _safe(root, relative).is_file() and relative.endswith(".md"):
            url = _report_link(relative)
    except ValueError:
        pass
    return url


def _finding(root, row, kind):
    if row is None:
        return {"state": "unknown", "detail": "No ledger row", "report_url": ""}
    return {"state": row.get("verdict") or row.get("status") or row.get("outcome") or "recorded",
            "scope": "Product-wide check" if not row.get("lesson") else "Lesson check",
            "defects": _count(row.get("defects", row.get("defects_count"))),
            "gaps": _count(row.get("gaps", row.get("gaps_count"))),
            "checked_at": row.get("at") or row.get("checked_at") or row.get("timestamp") or "",
            "report_url": _finding_url(root, row, kind), "source": f"findings/{kind}/ledger.jsonl"}


def _drafts(folder, lesson):
    sources = []
    for path in sorted(folder.glob(f"{lesson}-*")):
        if not path.is_dir():
            continue
        if (path / "lesson.kr.json").is_file() and (path / "lesson.en.json").is_file():
            sources.append(path)
        sources.extend(p for p in path.glob("_build_*") if p.is_dir())
    return sources


def _lesson(root, product, number, previews, main, ledgers, publication=None):
    lesson = f"L{number:03d}"
    folder = root / "proposed/tbs-curriculum" / PRODUCTS[product]
    options = folder / f"{lesson}-OPTIONS.md"
    content = _read(options)
    decision = re.search(r"^decision:[ \t]*(.*)$", content, re.M | re.I)
    drafts = _drafts(folder, lesson)
    preview = previews.get((product, lesson), {})
    preview_path = preview.get("path", "")
    preview_url = PREVIEW_ORIGIN + preview_path if re.fullmatch(r"/[a-zA-Z0-9/_-]+", preview_path) else ""
    if product == "superpowerai":
        page = f"superpowerai/paid/lesson{number:03d}/index.html"
    else:
        page = f"src/pages/lessons/LessonPaid{number - 2:02d}.jsx" if number > 2 else ""
    on_main = page in main["paths"]
    media = root / "media" / ("spa" if product == "superpowerai" else "mommyai") / lesson / "receipt.json"
    state = "unplanned"
    for exists, name in [(options.exists(), "planned"), (bool(drafts), "drafted"), (bool(preview), "previewed"), (on_main, "on main")]:
        if exists:
            state = name
    return {"product": product, "lesson": lesson, "title": preview.get("title", ""), "state": state,
            "planned": {"present": options.is_file(), "decision": decision.group(1).strip() if decision else "",
                        "source": str(options.relative_to(root)), "report_url": _report_link(options.relative_to(root)) if options.is_file() else ""},
            "drafted": {"present": bool(drafts), "sources": [str(p.relative_to(root)) for p in drafts]},
            "previewed": {"present": bool(preview), "url": preview_url, "checked_at": preview.get("updated", ""), "source": "preview-hub/lessons.json"},
            "on_main": {"present": on_main, "sha": main["sha"], "source": page, "detail": main["error"] or ("No paid route for L001/L002" if not page else "Local origin/main snapshot")},
            "published": _published(publication or _publication(root), product, lesson),
            "media": {"present": media.is_file(), "source": str(media.relative_to(root))},
            "continuity": _finding(root, ledgers["continuity"].get((product, lesson), ledgers["continuity"].get((product, "*"))), "continuity"),
            "playthrough": _finding(root, ledgers["playthrough"].get((product, lesson)), "playthrough")}


def build(root=None):
    root = root or root_path()
    previews = {}
    for row in _json(root / "preview-hub/lessons.json", []):
        if isinstance(row, dict):
            previews[(row.get("product"), row.get("lesson"))] = row
    publication = _publication(root)
    ledgers, gaps = {}, []
    if publication[1]["state"] != "current":
        gaps.append(publication[1]["detail"])
    for kind in ("continuity", "playthrough"):
        ledgers[kind], errors = _ledger(root, kind)
        gaps.extend(errors)
        if not (root / f"findings/{kind}/ledger.jsonl").is_file():
            gaps.append(f"findings/{kind}/ledger.jsonl missing")
    lessons = []
    for product, repo in [("superpowerai", "tbs-www"), ("mommyai", "tbs-landing")]:
        main = _main(root, repo)
        lessons.extend(_lesson(root, product, n, previews, main, ledgers, publication) for n in range(1, 97))
    return {"state": "ok" if root.is_dir() else "missing", "detail": "Lesson repository unavailable" if not root.is_dir() else "",
            "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "lessons": lessons, "gaps": list(dict.fromkeys(gaps))}
