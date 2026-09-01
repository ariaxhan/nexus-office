"""What a desk says about itself: every Markdown file in the checkout.

Every other read in this office is a summary somebody else already wrote. This
one hands back the bytes of a file on this machine, which makes it the one place
where a mistake is not a confusing screen but a leak. So it is built as an
allow-list with a containment proof on top, and not as a file browser with
exclusions bolted to it.

**Where it may look.** `sessions.desk_dir(repo)` and nowhere else. That function
finds a checkout from what is actually on disk: a session already running there,
or the vault walked to a bounded depth with every checkout's origin asked. A path derived from the repo slug would name a folder that is not there
and then fail somewhere further in, or worse, name somebody else's folder that
happens to share a name.

**What it may list.** Two shapes, and no third: a root file whose name begins
`README` and whose extension is Markdown or absent, and a `.md` or `.markdown`
file at any depth in the checkout. That is an allow-list, so a `.env`, a key, a
source file and a database are all out by construction rather than by being
remembered. `.git`, `node_modules`, `vendor` and the usual build caches are not walked:
nothing in them is a document somebody wrote for this repo, and a checkout's
`node_modules` alone can hold ten thousand READMEs that are not its own.
Neither is a folder that is itself a checkout. A repo that contains another repo
contains that repo's documents, not its own: `matra` keeps 2,872 Markdown files
under `.claude/worktrees`, every one of them a second copy of a file the desk
already lists, and they buried the README on the desk they were listed under.

**What it may follow.** Nothing. A symlink file and a symlink directory are both
skipped, because a link planted anywhere under `_meta` is otherwise a one line
way to read any file this process can open. Every candidate is resolved and
proven still inside the checkout after that, which catches the case where the
checkout itself sits under a link.

**What it may read.** A path that exactly matches something already listed.
Matching against the index rather than re-deriving safety at read time means a
request is answered by a lookup, and a lookup cannot be tricked by an encoding.

Writes use the same allow-list and containment proof. They replace one indexed
Markdown file atomically, and only when its current text still matches what the
editor opened. There is no shell.
"""

from __future__ import annotations

import os
import pathlib
import re
import stat
import tempfile
import threading

import sessions

KEY = "context"

# The same shape the rest of the door insists on for a repo. Checked before the
# lookup, so a name that is not a name never reaches the filesystem.
NWO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")

# Folders that are never walked. Not documents of this repo: a dependency's
# README, a build product, a cache, or git's own object store.
SKIP_DIRS = frozenset({
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".build", "build",
    "dist", "DerivedData", ".next", ".turbo", ".cache", "target", "Pods",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "vendor",
})
# Half a megabyte of Markdown is about 250 pages. A file bigger than that is not
# a document somebody wrote, and shipping it down a loopback socket to be laid
# out by a text view is not something to do by accident.
MAX_BYTES = 512 * 1024

MD_SUFFIXES = ("", ".md", ".markdown")
README = "readme"

# Same five keys in every answer, including a refusal-free empty one, so the app
# never has to ask whether a field is there before drawing it.
BODY_KEYS = ("repo", "root", "files", "capped", "path", "title", "text", "bytes")

# One verified index per checkout. Asking for the index refreshes it; opening a
# listed document reuses it. The old path rebuilt and returned the whole tree for
# every click, which made a local file read proportional to the entire vault.
_INDEX_CACHE: dict[str, tuple[list[dict], bool]] = {}
_INDEX_LOCK = threading.Lock()


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
            "bytes": int(st.st_size),
            # When it last changed. A desk holds hundreds of documents in folder
            # order, so the one written an hour ago is invisible without this.
            "mtime": int(st.st_mtime)}


def index(root: str) -> tuple[list[dict], bool]:
    """Everything this desk offers to show, and whether the list was cut.

    Root READMEs first, then every other Markdown by path, so the front page is
    the first row and the tree under it reads in folder order. The index is
    complete: truncating it makes later paths fail the exact-match read
    permission even though they are valid documents in the checkout.
    """
    base = pathlib.Path(root)
    found: list[tuple[int, str, dict]] = []
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        here = pathlib.Path(dirpath)
        # `followlinks=False` already declines to descend into a linked
        # directory; dropping it from the listing as well means nothing
        # downstream can be handed one by accident.
        dirnames[:] = sorted(d for d in dirnames
                             if d not in SKIP_DIRS
                             and not (here / d).is_symlink()
                             and not (here / d / ".git").exists())
        at_root = here == base
        for fname in sorted(filenames):
            readme = at_root and _is_readme(fname)
            if not readme and not _is_markdown(fname):
                continue
            candidate = here / fname
            rel = candidate.relative_to(base).as_posix()
            entry = _entry(root, candidate, rel)
            if entry:
                found.append((0 if readme else 1, rel, entry))
    found.sort(key=lambda row: (row[0], row[1]))
    files = [row[2] for row in found]
    return files, False


def _files(root: str, refresh: bool = False) -> tuple[list[dict], bool]:
    if not refresh:
        with _INDEX_LOCK:
            cached = _INDEX_CACHE.get(root)
        if cached is not None:
            return cached
    scanned = index(root)
    with _INDEX_LOCK:
        _INDEX_CACHE[root] = scanned
    return scanned


def _listed(root: str, want: str) -> tuple[dict | None, bool]:
    """The verified entry, refreshing once when a direct open names a new file."""
    files, capped = _files(root)
    hit = next((f for f in files if f["path"] == want), None)
    if hit is not None:
        return hit, capped
    files, capped = _files(root, refresh=True)
    return next((f for f in files if f["path"] == want), None), capped


def _current_entry(root: str, target: pathlib.Path, want: str) -> dict | None:
    """Recheck mutable filesystem facts a cached index cannot promise."""
    entry = _entry(root, target, want)
    if entry is None:
        return None
    boundary = pathlib.Path(root)
    parent = target.parent
    while parent != boundary:
        if (parent / ".git").exists():
            return None
        parent = parent.parent
    return entry


def read(repo: str, path: str = "") -> tuple[int, dict]:
    """(status, body). A fresh index, or one document from its cached index."""
    repo = str(repo or "").strip()
    if not NWO_RE.match(repo):
        return 400, {"error": "bad repo"}

    want = str(path or "").strip()
    refusal = _refuse(want)
    if refusal:
        return 400, {"error": refusal}

    found = sessions.desk_dir(repo)
    if not found:
        return 404, {"error": sessions.NO_VAULT if sessions.no_vault()
                     else f"the office does not know where {repo} is checked out"}
    try:
        root = str(pathlib.Path(found).resolve(strict=True))
    except (OSError, RuntimeError):
        return 404, {"error": f"{repo} is not checked out at {found} any more"}
    if not os.path.isdir(root):
        return 404, {"error": f"{repo} is not checked out at {found} any more"}

    files, capped = _files(root, refresh=True) if not want else ([], False)
    body = {"repo": repo, "root": root, "files": files, "capped": capped,
            "path": "", "title": "", "text": "", "bytes": 0}
    if not want:
        return 200, body

    hit, capped = _listed(root, want)
    body["capped"] = capped
    if hit is None:
        # On disk and not in the index is still not context. Being listed is the
        # whole permission, so this answers the same way as a file that is not
        # there at all.
        return 404, {"error": "that file is not in this desk's context"}

    target = pathlib.Path(root) / want
    current = _current_entry(root, target, want)
    if current is None:
        _files(root, refresh=True)
        return 404, {"error": "that file is not in this desk's context"}
    hit = current
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
    try:
        body["bytes"] = os.stat(real).st_size
    except OSError:
        body["bytes"] = hit["bytes"]
    return 200, body


def write(body: dict) -> tuple[int, dict]:
    """Replace one indexed Markdown file without clobbering a newer version.

    `expected` is the exact text the editor opened. An agent or another editor
    changing the file first turns this save into a conflict instead of making
    the last network request silently win.
    """
    if not isinstance(body, dict):
        return 400, {"error": "bad write"}
    repo, want = body.get("repo"), body.get("path")
    text, expected = body.get("text"), body.get("expected")
    if not all(isinstance(value, str) for value in (repo, want, text, expected)):
        return 400, {"error": "repo, path, text and expected must be text"}
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_BYTES:
        return 400, {"error": "that document is too large to save here"}

    code, current = read(repo, want)
    if code != 200:
        return code, current
    if current["text"] != expected:
        return 409, {"error": "that file changed on disk; reopen it before saving"}
    if text == expected:
        return 200, current

    target = pathlib.Path(current["root"]) / want
    real = _inside(current["root"], target)
    if not real or target.is_symlink():
        return 409, {"error": "that file changed on disk; reopen it before saving"}
    try:
        mode = stat.S_IMODE(os.stat(real).st_mode)
        fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.",
                                         suffix=".office-save", dir=str(target.parent))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, mode)
            # Staging and fsync take time. Re-read at the last possible point so
            # a writer that landed after the first comparison wins rather than
            # being silently replaced by this older editor revision.
            if pathlib.Path(real).read_text(encoding="utf-8", errors="replace") != expected:
                return 409, {"error": "that file changed on disk; reopen it before saving"}
            os.replace(temporary, real)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
    except PermissionError:
        return 403, {"error": "that file is not writable"}
    except OSError as exc:
        return 409, {"error": f"could not save that file: {exc}"[:200]}
    code, saved = read(repo, want)
    if code == 200:
        saved["files"], saved["capped"] = _files(saved["root"], refresh=True)
    return code, saved
