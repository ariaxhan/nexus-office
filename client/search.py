"""One search box over every desk: names, folders, paths, and the words inside.

The roster search used to be a filter on three lists the app had already
fetched. That is a good filter and a bad search: the thing a person is actually
looking for is usually a file, and the office knew where 15,000 of them were and
would not say. This is the other half.

**What it may see.** Exactly what `context.py` already hands out, and not one
byte more: the same allow-listed index of a checkout's Markdown, built by the
same walk, for the checkouts `sessions` finds by asking git for an origin. So a
search result is a file a person could already have opened by clicking through
the Context pane; it does not widen the door, it shortens the walk. Anything the
context reader refuses to list is invisible here for free, which is why this
file contains no path logic of its own.

**Three kinds of hit, in this order.**

  goto   the query IS a path. Absolute, `~`-relative, `repo:path`, or a
         relative path that lands in exactly one checkout. Typing a path you
         already know must never make you read a ranked list to find it.
  name   the filename or one of its folders contains the query.
  text   the words inside contain it.

Names before words, always. A file called `deploy.md` is what somebody typing
"deploy" wants, even when forty other files say the word.

**Why the text pass reads the disk every time.** A warm full scan of the whole
corpus is about half a second, which is faster than any cache that has to stay
honest about 15,000 files that agents rewrite all day. A stale search result is
a worse failure than a slow one: it sends a person to a paragraph that is not
there any more. The index of file NAMES is cached, because it is a directory
walk rather than the content, and it is refreshed on a short clock.
"""

from __future__ import annotations

import concurrent.futures
import os
import pathlib
import threading
import time

import context
import sessions

KEY = "search"

# Below this a search is every file in the vault, which is not a search.
MIN_QUERY = 2
MAX_QUERY = 200
# What comes back. Names and paths are cheap and specific; text is neither, so
# it gets the smaller half.
NAME_LIMIT = 40
TEXT_LIMIT = 40
# A line longer than this in a document is a wrapped paragraph, and quoting all
# of it in a result row is a wall. The match keeps its neighbourhood.
SNIPPET = 160
# Not read for the text pass. A generated file this size is a document nobody
# wrote and a scan nobody benefits from.
MAX_TEXT_BYTES = 512 * 1024
# The text pass is disk-bound, not CPU-bound: it is 15,000 opens and one
# substring each. Cold, in one thread, that is seven seconds and a search box
# that reads as hung. Fanned out it is under one.
READERS = 12
# How long the text pass may run before it stops and says so. Name hits are
# already decided by then, so this never truncates the specific half of the
# answer, only the broad one.
TEXT_DEADLINE_S = 3.0

# Folders whose Markdown is somebody else's documentation living inside a
# checkout. `context.py` skips the dependency directories a build tool makes;
# these are the ones an agent runtime makes, and one of them alone puts nine
# thousand plugin READMEs in front of the file a person is looking for.
SKIP_PARTS = ("/plugins/cache/", "/.claude/worktrees/", "/node_modules/",
              "/.venv/", "/site-packages/", "/.git/")

# The name index is a walk of ninety checkouts. Two minutes is the same clock
# `sessions` uses for finding the checkouts themselves: long enough that typing
# does not re-walk the disk, short enough that a file written this session
# becomes findable without a restart.
INDEX_TTL_S = 120
_index_lock = threading.Lock()
_index: dict[str, tuple[float, list[dict]]] = {}


def _root() -> str:
    return os.environ.get("OFFICE_RUNTIME_ROOT", "").strip()


def desks() -> dict[str, str]:
    """Every checkout this machine holds, as {owner/name: directory}.

    `sessions` owns the question of what a checkout is and where it may be
    looked for, including its own cache. Nothing here guesses a path.
    """
    root = _root()
    if not root:
        return {}
    base = pathlib.Path(root).expanduser()
    found = dict(sessions._checkouts(base))
    # The vault root is itself a checkout, and `_checkouts` only ever looks at
    # the folders UNDER the directory it is given. Leaving it out means the one
    # tree holding the doctrine every desk inherits is the one tree this search
    # cannot see, which is the opposite of what a person expects when they type
    # the name of a rule.
    own = sessions.origin_nwo(str(base))
    if own and own not in found:
        try:
            found[own] = str(base.resolve())
        except (OSError, RuntimeError):
            pass
    return found


def _files(repo: str, root: str) -> list[dict]:
    """The context index for one desk, cached on a short clock."""
    now = time.time()
    with _index_lock:
        cached = _index.get(root)
        if cached and (now - cached[0]) < INDEX_TTL_S:
            return cached[1]
    try:
        found, _ = context.index(root)
    except OSError:
        found = []
    rows = [dict(row, repo=repo, root=root) for row in found
            if not _vendored(row["path"])]
    with _index_lock:
        _index[root] = (time.time(), rows)
    return rows


def _vendored(rel: str) -> bool:
    """Whether this document belongs to something vendored into the checkout."""
    padded = "/" + rel + "/"
    return any(part in padded for part in SKIP_PARTS)


def corpus() -> list[dict]:
    """Every listable document on this machine, with its desk on each row."""
    rows: list[dict] = []
    for repo, root in desks().items():
        rows.extend(_files(repo, root))
    return rows


def forget() -> None:
    """Drop the name index. For tests, and for a caller that just wrote a file."""
    with _index_lock:
        _index.clear()


# ── the query ────────────────────────────────────────────────────────────────

def _clean(q) -> str:
    return str(q or "").strip()[:MAX_QUERY]


def _looks_like_path(q: str) -> bool:
    return "/" in q or q.startswith("~") or ":" in q


def _resolve_path(q: str, rows: list[dict]) -> list[dict]:
    """The file this query names outright, when it names one. Else [].

    Four spellings, because all four are things a person actually has in the
    clipboard: an absolute path, a `~` path, `owner/repo:relative/path.md`, and
    a bare relative path that only one checkout has.

    Every one of them ends at the same place: a row that is already in the
    index. A path that resolves to a real file the context reader would refuse
    is not a hit here either, which is the whole reason this matches against the
    index instead of the filesystem.
    """
    q = q.strip()
    if not q:
        return []

    # owner/repo:path
    if ":" in q:
        repo, _, rel = q.partition(":")
        rel = rel.strip().lstrip("./")
        hits = [r for r in rows if r["repo"] == repo.strip() and r["path"] == rel]
        if hits:
            return hits[:1]

    # An absolute or `~` path, and a path written from the vault root, which is
    # how every path in a chronicle, a plan or a commit message is written.
    # Both end as one resolved string compared against the index.
    bases = [q] if (q.startswith("~") or q.startswith("/")) else []
    root = _root()
    if root and not bases:
        bases = [os.path.join(root, q)]
    for spelling in bases:
        try:
            real = str(pathlib.Path(spelling).expanduser().resolve())
        except (OSError, RuntimeError):
            continue
        for row in rows:
            if os.path.join(row["root"], row["path"]) == real:
                return [row]

    # A bare relative path. Exact tail match on the desk-relative path, so
    # `_meta/reference/glossary.md` finds itself and `glossary.md` does not
    # pretend to be a path when it is a name.
    if "/" in q:
        want = q.lstrip("./")
        hits = [r for r in rows if r["path"] == want]
        if hits:
            return hits
    return []


def _snippet(text: str, at: int) -> tuple[int, str]:
    """(1-based line number, the line with the match, trimmed around it)."""
    line_no = text.count("\n", 0, at) + 1
    start = text.rfind("\n", 0, at) + 1
    end = text.find("\n", at)
    if end == -1:
        end = len(text)
    line = text[start:end].strip()
    if len(line) <= SNIPPET:
        return line_no, line
    # Keep the match itself in shot rather than the first 160 characters of a
    # paragraph that happens to contain it further down.
    offset = at - start
    left = max(0, offset - SNIPPET // 3)
    cut = line[left:left + SNIPPET].strip()
    return line_no, ("…" + cut if left else cut)


def _name_score(row: dict, needle: str) -> int | None:
    """How well this row's NAME or folders match, lower is better. None is no."""
    name = row["name"].lower()
    path = row["path"].lower()
    if name == needle or os.path.splitext(name)[0] == needle:
        return 0
    if name.startswith(needle):
        return 1
    # A match that starts a word beats one buried in the middle of another
    # word. Without this, typing "shoot" answers with four hundred files called
    # `troubleshooting.md` before it answers with `shoot.sh`, and every one of
    # them is a true substring match and none of them is the thing.
    if _at_boundary(name, needle):
        return 2
    if _at_boundary(path, needle):
        return 3
    if needle in name:
        return 4
    if needle in path:
        return 5
    if needle in row["repo"].lower():
        return 6
    return None


# What separates one word from the next in a filename somebody typed.
BREAKS = set(" -_./,()[]0123456789")


def _at_boundary(haystack: str, needle: str) -> bool:
    """Whether `needle` starts a word anywhere in `haystack`."""
    at = haystack.find(needle)
    while at >= 0:
        if at == 0 or haystack[at - 1] in BREAKS:
            return True
        at = haystack.find(needle, at + 1)
    return False


def _row(row: dict, kind: str, rank: int,
         line: int = 0, snippet: str = "") -> dict:
    return {"kind": kind, "repo": row["repo"], "path": row["path"],
            "name": row["name"], "group": row["group"], "mtime": row["mtime"],
            "line": line, "snippet": snippet, "rank": rank}


def run(q, limit: int = NAME_LIMIT + TEXT_LIMIT) -> dict:
    """Everything on this machine that matches `q`.

    Never raises for a caller: an unreadable checkout is one desk missing from
    the answer, not a search that failed. The shape is the same for a refusal,
    an empty result and a full one, so the app draws it without asking whether
    a field is there.
    """
    started = time.time()
    query = _clean(q)
    body = {"q": query, "results": [], "counts": {"goto": 0, "name": 0, "text": 0},
            "files": 0, "desks": 0, "ms": 0, "truncated": False, "said": ""}

    if len(query) < MIN_QUERY:
        body["said"] = ("say at least two characters" if query
                        else "")
        return body
    if sessions.no_vault():
        body["said"] = sessions.NO_VAULT
        return body

    rows = corpus()
    body["files"] = len(rows)
    body["desks"] = len({r["root"] for r in rows})
    needle = query.lower()

    results: list[dict] = []
    seen: set[tuple[str, str]] = set()

    if _looks_like_path(query):
        for row in _resolve_path(query, rows):
            key = (row["repo"], row["path"])
            if key in seen:
                continue
            seen.add(key)
            results.append(_row(row, "goto", 0))

    named: list[tuple[int, int, dict]] = []
    for row in rows:
        if (row["repo"], row["path"]) in seen:
            continue
        score = _name_score(row, needle)
        if score is not None:
            # Newest first inside one score band: two files with the same name
            # is the normal case in a vault, and the one somebody touched today
            # is the one they mean.
            named.append((score, -row["mtime"], row))
    named.sort(key=lambda t: (t[0], t[1], t[2]["repo"], t[2]["path"]))
    for score, _, row in named[:NAME_LIMIT]:
        seen.add((row["repo"], row["path"]))
        results.append(_row(row, "name", score))
    body["counts"]["name"] = min(len(named), NAME_LIMIT)

    text_hits, timed_out = _text_pass(rows, needle, seen)
    body["counts"]["text"] = len(text_hits)
    results.extend(text_hits[:TEXT_LIMIT])

    body["counts"]["goto"] = sum(1 for r in results if r["kind"] == "goto")
    body["truncated"] = (len(named) > NAME_LIMIT or len(text_hits) > TEXT_LIMIT
                         or timed_out)
    if timed_out:
        body["said"] = "stopped early: not every document was read"
    body["results"] = results[:max(1, int(limit or 0) or NAME_LIMIT + TEXT_LIMIT)]
    body["ms"] = int((time.time() - started) * 1000)
    return body


def _text_pass(rows: list[dict], needle: str,
               seen: set[tuple[str, str]]) -> tuple[list[dict], bool]:
    """(hits, whether it ran out of time). Every document whose words match.

    Read as bytes and matched as bytes: decoding 135 MB of Markdown to find a
    substring costs several times what the search itself does, and a needle from
    a text field is ordinary UTF-8. Only a file that matched is decoded, and only
    to find the line to quote.
    """
    probe = needle.encode("utf-8")
    candidates = [row for row in rows
                  if (row["repo"], row["path"]) not in seen
                  and row["bytes"] <= MAX_TEXT_BYTES]
    deadline = time.time() + TEXT_DEADLINE_S
    ran_out = threading.Event()

    def scan(row: dict) -> dict | None:
        if time.time() > deadline:
            ran_out.set()
            return None
        full = os.path.join(row["root"], row["path"])
        try:
            with open(full, "rb") as handle:
                raw = handle.read(MAX_TEXT_BYTES)
        except OSError:
            return None
        if raw.lower().find(probe) < 0:
            return None
        text = raw.decode("utf-8", "replace")
        # A byte offset is not a character offset once anything is not ASCII,
        # so the line is found in the decoded text rather than trusted from the
        # byte scan.
        at = text.lower().find(needle)
        if at < 0:
            return None
        line, snippet = _snippet(text, at)
        return _row(row, "text", 7, line=line, snippet=snippet)

    hits: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=READERS) as pool:
        for found in pool.map(scan, candidates):
            if found is not None:
                hits.append(found)
    hits.sort(key=lambda r: (-r["mtime"], r["repo"], r["path"]))
    return hits, ran_out.is_set()
