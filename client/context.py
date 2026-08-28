"""What a desk says about itself: its README, and the Markdown under `_meta`.

Every other read in this office is a summary somebody else already wrote. This
one hands back the bytes of a file on this machine, which makes it the one place
where a mistake is not a confusing screen but a leak. So it is built as an
allow-list with a containment proof on top, and not as a file browser with
exclusions bolted to it.

**Where it may look.** `sessions.desk_dir(repo)` and nowhere else. That function
finds a checkout from what is actually on disk: a session already running there,
the desks the office has cached, or the vault walked one level with the origin
checked. A path derived from the repo slug would name a folder that is not there
and then fail somewhere further in, or worse, name somebody else's folder that
happens to share a name.

**What it may list.** Two shapes, and no third: a root file whose name begins
`README` and whose extension is Markdown or absent, and a `.md` file at any
depth under `_meta`. That is an allow-list, so a `.env`, a key, a source file and
a database are all out by construction rather than by being remembered.

**What it may follow.** Nothing. A symlink file and a symlink directory are both
skipped, because a link planted anywhere under `_meta` is otherwise a one line
way to read any file this process can open. Every candidate is resolved and
proven still inside the checkout after that, which catches the case where the
checkout itself sits under a link.

**What it may read.** A path that exactly matches something already listed.
Matching against the index rather than re-deriving safety at read time means a
request is answered by a lookup, and a lookup cannot be tricked by an encoding.

Read only. There is no write route here, and there is no shell: a context click
opens a file and does nothing else at all.
"""

from __future__ import annotations

import os
import pathlib
import re
import stat

import sessions

KEY = "context"

# The same shape the rest of the door insists on for a repo. Checked before the
# lookup, so a name that is not a name never reaches the filesystem.
NWO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")

# The index is a list a person scans, not a search result. Five hundred is more
# entries than anyone reads and small enough that walking it is free; past that
# the answer says out loud that it was cut.
MAX_FILES = 500
# How many candidates the walk will look at before it stops. A checkout with a
# pathological `_meta` must not turn one click into a filesystem crawl.
MAX_SCAN = 20_000
# Half a megabyte of Markdown is about 250 pages. A file bigger than that is not
# a document somebody wrote, and shipping it down a loopback socket to be laid
# out by a text view is not something to do by accident.
MAX_BYTES = 512 * 1024

META = "_meta"
MD_SUFFIXES = ("", ".md", ".markdown")
README = "readme"

# Same five keys in every answer, including a refusal-free empty one, so the app
# never has to ask whether a field is there before drawing it.
BODY_KEYS = ("repo", "root", "files", "capped", "path", "title", "text", "bytes")


def _refuse(want: str) -> str:
    """Why this requested path is not a path in a checkout, or "".

    Shape only, before any lookup and before any byte. Absolute, traversing,
    empty-segmented and NUL-carrying paths are all refused here rather than
    resolved and then compared, because a check that runs after the resolve has
    already touched the thing it was supposed to refuse.
    """
    if not want:
        return ""
    if len(want) > 512:
        return "that path is too long to be a file in a checkout"
    if "\x00" in want or "\\" in want:
        return "that is not a path in this checkout"
    if want.startswith("/") or want.startswith("~"):
        return "an absolute path is not a desk's context"
    if any(part in ("", ".", "..") for part in want.split("/")):
        return "that path leaves the checkout"
    return ""


def _is_readme(name: str) -> bool:
    """A root README, in the two spellings that are Markdown.

    `README.rst` and `README.html` are readmes and are not Markdown, and this
    reader draws Markdown, so they are not context. `README` with no extension
    is Markdown by convention in every checkout in this workspace, and excluding
    it would leave the most common file in a repo unreadable.
    """
    low = name.lower()
    return low.startswith(README) and pathlib.PurePosixPath(low).suffix in MD_SUFFIXES


def _is_markdown(name: str) -> bool:
    return pathlib.PurePosixPath(name.lower()).suffix in (".md", ".markdown")


def _inside(root: str, candidate: pathlib.Path) -> str:
    """The resolved path, when it is genuinely under `root`. Else "".

    The last line of defence, and the only one that survives a link the index
    walk did not see: whatever the string looked like, this is where the
    filesystem says it lands.
    """
    try:
        real = str(candidate.resolve(strict=True))
    except (OSError, RuntimeError):
        return ""
    return real if real == root or real.startswith(root + os.sep) else ""


def _entry(root: str, candidate: pathlib.Path, rel: str) -> dict | None:
    """One index row, or None when this candidate is not context after all."""
    if candidate.is_symlink():
        return None
    try:
        st = candidate.stat()
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_BYTES:
        return None
    if not _inside(root, candidate):
        return None
    parent = str(pathlib.PurePosixPath(rel).parent)
    return {"path": rel, "name": candidate.name,
            "group": "root" if parent == "." else parent,
            "bytes": int(st.st_size)}


def index(root: str) -> tuple[list[dict], bool]:
    """Everything this desk offers to show, and whether the list was cut.

    Sorted before it is cut, so which five hundred you get is a fact about the
    checkout rather than a fact about the order the filesystem happened to hand
    them over in.
    """
    base = pathlib.Path(root)
    found: list[tuple[int, str, dict]] = []
    scanned = 0

    try:
        names = sorted(p.name for p in base.iterdir())
    except OSError:
        return [], False
    for name in names:
        if not _is_readme(name):
            continue
        scanned += 1
        entry = _entry(root, base / name, name)
        if entry:
            found.append((0, name, entry))

    meta = base / META
    if meta.is_dir() and not meta.is_symlink():
        for dirpath, dirnames, filenames in os.walk(meta, followlinks=False):
            here = pathlib.Path(dirpath)
            # `followlinks=False` already declines to descend into a linked
            # directory; dropping it from the listing as well means nothing
            # downstream can be handed one by accident.
            dirnames[:] = sorted(d for d in dirnames if not (here / d).is_symlink())
            for fname in sorted(filenames):
                if not _is_markdown(fname):
                    continue
                scanned += 1
                if scanned > MAX_SCAN:
                    break
                candidate = here / fname
                rel = candidate.relative_to(base).as_posix()
                entry = _entry(root, candidate, rel)
                if entry:
                    found.append((1, rel, entry))
            if scanned > MAX_SCAN:
                break

    found.sort(key=lambda row: (row[0], row[1]))
    files = [row[2] for row in found]
    capped = len(files) > MAX_FILES or scanned > MAX_SCAN
    return files[:MAX_FILES], capped


def read(repo: str, path: str = "", desks: dict | None = None) -> tuple[int, dict]:
    """(status, body). The index of one desk, and one file out of it.

    The index rides along with the file so the pane draws from one call: an app
    that has to ask twice is an app whose list and whose document can disagree
    about which desk they belong to.
    """
    repo = str(repo or "").strip()
    if not NWO_RE.match(repo):
        return 400, {"error": "bad repo"}

    want = str(path or "").strip()
    refusal = _refuse(want)
    if refusal:
        return 400, {"error": refusal}

    found = sessions.desk_dir(repo, desks)
    if not found:
        return 404, {"error": f"the office does not know where {repo} is checked out"}
    try:
        root = str(pathlib.Path(found).resolve(strict=True))
    except (OSError, RuntimeError):
        return 404, {"error": f"{repo} is not checked out at {found} any more"}
    if not os.path.isdir(root):
        return 404, {"error": f"{repo} is not checked out at {found} any more"}

    files, capped = index(root)
    body = {"repo": repo, "root": root, "files": files, "capped": capped,
            "path": "", "title": "", "text": "", "bytes": 0}
    if not want:
        return 200, body

    hit = next((f for f in files if f["path"] == want), None)
    if hit is None:
        # On disk and not in the index is still not context. Being listed is the
        # whole permission, so this answers the same way as a file that is not
        # there at all.
        return 404, {"error": "that file is not in this desk's context"}

    target = pathlib.Path(root) / want
    real = _inside(root, target)
    if not real:
        return 400, {"error": "that path leaves the checkout"}
    try:
        # `errors="replace"` because a stray byte in a document is a smudge on
        # one character, and refusing the whole file over it would hide a page
        # somebody is trying to read.
        text = pathlib.Path(real).read_text(encoding="utf-8", errors="replace")
    except PermissionError:
        return 403, {"error": "that file is not readable"}
    except OSError as exc:
        return 404, {"error": f"could not read that file: {exc}"[:200]}

    body["path"] = want
    body["title"] = hit["name"]
    body["text"] = text
    body["bytes"] = hit["bytes"]
    return 200, body
