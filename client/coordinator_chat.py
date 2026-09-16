"""The one conversation with the TBS coordinator: its run logs out, one inbox file in.

All state is on disk in the thinking-brain-school tree, so an Office restart or a
coordinator crash loses nothing. Reads are bounded; the inbox write is an atomic,
locked, idempotent append keyed by the page's request id.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import glob
import json
import os
from pathlib import Path
import re
import subprocess

import lesson_status
import office_objects

INBOX = "_meta/state/coordinator-inbox.jsonl"
RUNS = "_meta/ledgers/coordinator-runs.jsonl"
HOLDS = "_meta/ledgers/coordinator-holds.jsonl"
LOG_GLOB = ".tbs-out/coordinator-[0-9]*.log"
MAX_RUNS = 12
MAX_LOG_BYTES = 2 * 1024 * 1024
MAX_TEXT = 8000
DEFAULT_REPO = "Thinking-Brain-School/tbs-www"
ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


def root_path():
    return Path(os.environ.get("OFFICE_COORDINATOR_ROOT") or lesson_status.root_path()).resolve()


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rows(path):
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


# ── inbox ────────────────────────────────────────────────────────────────────
def say(body, root=None):
    root = root or root_path()
    text, request = body.get("text"), body.get("id")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        raise ValueError(f"a message is 1-{MAX_TEXT} characters")
    if not isinstance(request, str) or not ID_RE.match(request):
        raise ValueError("a message needs its request id")
    path = root / INBOX
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "a+", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.seek(0)
        for line in stream.read().splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("id") == request:
                return {"ok": True, "duplicate": True, "message": row}
        row = {"id": request, "at": now(), "from": "aria", "text": text.strip()}
        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return {"ok": True, "duplicate": False, "message": row}


def mark_read(ids, run_start, root=None):
    """What a run appends after reading: one row per message id."""
    root = root or root_path()
    with open(root / INBOX, "a", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        for value in ids:
            stream.write(json.dumps({"read": value, "at": now(), "run_start": run_start}) + "\n")


def inbox(root):
    messages, read = [], {}
    for row in _rows(root / INBOX):
        if "read" in row:
            read.setdefault(row["read"], row)
        elif row.get("id") and isinstance(row.get("text"), str):
            messages.append(row)
    for row in messages:
        seen = read.get(row["id"])
        row["read_at"] = seen.get("at") if seen else None
    return messages


def unread(root=None):
    return [row for row in inbox(root or root_path()) if not row["read_at"]]


# ── run output ───────────────────────────────────────────────────────────────
def _tool_line(block):
    name, data = block.get("name") or "tool", block.get("input") or {}
    detail = data.get("command") or data.get("file_path") or data.get("pattern") or data.get("description") or ""
    return f"{name}: {' '.join(str(detail).split())[:160]}".rstrip(": ")


def _json_line(line):
    if not line.startswith("{"):
        return None
    try:
        row = json.loads(line)
    except ValueError:
        return None
    return row if isinstance(row, dict) and "type" in row else None


def _assistant_events(row):
    for block in (row.get("message") or {}).get("content") or []:
        if block.get("type") == "text" and block.get("text", "").strip():
            yield {"kind": "text", "text": block["text"].strip()}
        elif block.get("type") == "tool_use":
            yield {"kind": "tool", "text": _tool_line(block)}


def _result_event(row, events):
    text = str(row.get("result") or row.get("subtype") or "").strip()
    if events and events[-1]["text"] == text:
        return None  # the result repeats the final assistant text
    return {"kind": "result", "text": text}


def parse_log(raw):
    """stream-json -> assistant text + one line per tool call; plain text logs pass through."""
    events, plain = [], []
    for line in raw.splitlines():
        row = _json_line(line)
        if row is None:
            if not line.startswith("PASS tbs-claude:"):  # the launcher's own banner
                plain.append(line)
        elif row["type"] == "assistant":
            events.extend(_assistant_events(row))
        elif row["type"] == "result":
            events.append(_result_event(row, events))
    if "".join(plain).strip():
        events.insert(0, {"kind": "text", "text": "\n".join(plain).strip()})
    return [event for event in events if event and event["text"]]


def _read_tail(path):
    try:
        size = path.stat().st_size
        with open(path, "rb") as stream:
            if size > MAX_LOG_BYTES:
                stream.seek(size - MAX_LOG_BYTES)
                stream.readline()
            return stream.read().decode("utf-8", errors="replace"), size > MAX_LOG_BYTES
    except OSError:
        return "", False


def _stamp(path):
    match = re.search(r"coordinator-(\d{8}T\d{6}Z)-(\w+)\.log$", path.name)
    if not match:
        return None, ""
    return dt.datetime.strptime(match[1], "%Y%m%dT%H%M%SZ").strftime("%Y-%m-%dT%H:%M:%SZ"), match[2]


def runs(root):
    ledger = _rows(root / RUNS)
    ends = {os.path.basename(row.get("log") or ""): row for row in ledger if row.get("event") == "end"}
    holds = _rows(root / HOLDS)
    logs = sorted(glob.glob(str(root / LOG_GLOB)))[-MAX_RUNS:]
    out = []
    for name in logs:
        path = Path(name)
        start, mode = _stamp(path)
        if not start:
            continue
        end = ends.get(path.name)
        start = (end or {}).get("start") or start
        raw, truncated = _read_tail(path)
        live = end is None and _live(root, start)
        events = parse_log(raw)
        if not (events or end or live):
            continue  # a launcher that died before claude wrote a byte: nothing to say
        try:
            stopped = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except OSError:
            stopped = start
        out.append({"stopped": stopped, "start": start, "mode": mode, "log": str(path.relative_to(root)),
                    "live": live, "end": end,
                    "truncated": truncated, "events": events,
                    "holds": [row for row in holds if row.get("run_start") == start]})
    return out


def _live(root, start):
    """The newest log without an end row is live only while the launcher's lock pid is."""
    try:
        pid = int((root / ".tbs-out/coordinator.lock/pid").read_text().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    logs = sorted(glob.glob(str(root / LOG_GLOB)))
    return bool(logs) and _stamp(Path(logs[-1]))[0] == start


def last_skip(root):
    """The newest skip, but only while it is still what the launcher last did.

    A skip older than the newest start describes a tick that has since been superseded;
    printing it as the current status left the page reading "daily-cap" for 16 hours while
    four runs came and went.
    """
    rows = _rows(root / RUNS)
    skips = [row for row in rows if row.get("event") == "skip"]
    starts = [row for row in rows if row.get("event") == "start"]
    if not skips:
        return None
    if starts and (starts[-1].get("at") or "") > (skips[-1].get("at") or ""):
        return None
    return skips[-1]


# ── changes ──────────────────────────────────────────────────────────────────
def checkouts(root):
    base = root.parent
    found = {"thinking-brain-school": root}
    for path in sorted(base.glob("tbs*")):
        if (path / ".git").exists():
            found[path.name] = path
    return found


def _git(path, *args):
    try:
        return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True,
                              timeout=10, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


REMOTES, SHAS, CHANGES = {}, {}, {}


def _remote(path):
    if str(path) in REMOTES:
        return REMOTES[str(path)]
    url = _git(path, "remote", "get-url", "origin").strip()
    match = re.search(r"github\.com[:/]([^/]+/[^/.]+?)(?:\.git)?$", url)
    REMOTES[str(path)] = match[1] if match else ""
    return REMOTES[str(path)]


def changes(root, start, end, final=False):
    key = (str(root), start, end)
    if final and key in CHANGES:
        return CHANGES[key]
    commits = []
    for name, path in checkouts(root).items():
        repo = _remote(path)
        for line in _git(path, "log", "--all", f"--since={start}", f"--until={end}",
                         "--format=%H%x09%cI%x09%s", "-n", "40").splitlines():
            sha, at, subject = (line.split("\t", 2) + ["", ""])[:3]
            commits.append({"checkout": name, "repo": repo, "sha": sha, "at": at, "subject": subject})
    publishes = []
    for name in glob.glob(str(root / "_meta/receipts/publish/*/release.json")):
        try:
            data = json.loads(Path(name).read_text())
        except (OSError, ValueError):
            continue
        finished = data.get("finishedAt", "")
        if isinstance(data, dict) and start <= finished <= end:
            publishes.append({"outcome": data.get("outcome"), "at": finished,
                              "path": str(Path(name).relative_to(root))})
    result = {"commits": sorted(commits, key=lambda row: row["at"]),
              "publishes": sorted(publishes, key=lambda row: row["at"])}
    if final:
        CHANGES[key] = result
    return result


ISSUE_CMD = re.compile(r"gh issue (create|close|comment|reopen)\b(?:\s+(\d+))?.*?(?:-R|--repo)\s+([\w.-]+/[\w.-]+)")


def issue_actions(events):
    out = []
    for event in events:
        if event["kind"] != "tool":
            continue
        match = ISSUE_CMD.search(event["text"])
        if match:
            out.append({"action": match[1], "repo": match[3], "number": int(match[2]) if match[2] else None})
    return out


# ── links ────────────────────────────────────────────────────────────────────
TOKEN = re.compile(
    r"(?P<url>https?://[^\s<>)\]`'\"]+)"
    r"|(?P<issue>(?:(?P<owner>[\w.-]+)/)?(?P<name>[A-Za-z][\w.-]*)?#(?P<number>\d{1,6}))\b"
    r"|(?P<path>(?:(?P<prefix>[\w.-]+):)?(?P<rel>/?[\w.@-]+(?:/[\w.@-]+)*\.[A-Za-z0-9]{1,8}|(?:[\w.@-]+/)+[\w.@-]+)(?::(?P<line>\d+))?)"
    r"|(?P<sha>\b[0-9a-f]{7,40}\b)")
GH_ISSUE = re.compile(r"^https://github\.com/([\w.-]+/[\w.-]+)/(?:issues|pull)/(\d+)")


class Linker:
    def __init__(self, root):
        self.root = root
        self.checkouts = checkouts(root)
        self.repos = {name: _remote(path) for name, path in self.checkouts.items()}
        self.object_roots = None

    def _repo(self, owner, name):
        if owner and name:
            return f"{owner}/{name}"
        if not name:
            return DEFAULT_REPO
        full = self.repos.get(name)
        return full or None

    def _file(self, prefix, rel):
        base = self.checkouts.get(prefix) if prefix else self.root
        if rel.startswith("/"):
            if prefix:
                return None
            absolute = Path(rel)
            base = next((path for path in sorted(self.checkouts.values(), key=lambda p: -len(str(p)))
                         if absolute.is_relative_to(path)), None)
            if not base:
                return None
            rel = str(absolute.relative_to(base))
        if not base or ".." in Path(rel).parts:
            return None
        path = (base / rel)
        if not path.is_file() or not office_objects.permitted(rel):
            return None
        if self.object_roots is None:
            try:
                self.object_roots = {row["path"]: key for key, row in office_objects.roots().items()}
            except (ValueError, OSError):
                self.object_roots = {}
        key = self.object_roots.get(str(base.resolve()))
        return office_objects.encode(key, rel) if key else None

    def _sha(self, sha):
        if sha not in SHAS:
            SHAS[sha] = self._find_sha(sha)
        return SHAS[sha]

    def _find_sha(self, sha):
        for name, path in self.checkouts.items():
            if _git(path, "cat-file", "-t", sha).strip() == "commit":
                return name
        return None

    def segments(self, text):
        out, at = [], 0
        for match in TOKEN.finditer(text):
            segment = self._segment(match)
            if not segment:
                continue
            if match.start() > at:
                out.append({"text": text[at:match.start()]})
            out.append(segment)
            at = match.end()
        if at < len(text):
            out.append({"text": text[at:]})
        return out

    def _segment(self, match):
        raw = match[0]
        if match["url"]:
            issue = GH_ISSUE.match(raw)
            if issue:
                return {"text": raw, "kind": "issue", "repo": issue[1], "number": int(issue[2])}
            return {"text": raw, "kind": "url", "href": raw}
        if match["issue"]:
            repo = self._repo(match["owner"], match["name"])
            return {"text": raw, "kind": "issue", "repo": repo, "number": int(match["number"])} if repo else None
        if match["path"]:
            identity = self._file(match["prefix"], match["rel"])
            return {"text": raw, "kind": "file", "id": identity, "path": match["rel"]} if identity else None
        if match["sha"] and re.search(r"[a-f]", raw):
            checkout = self._sha(raw)
            return {"text": raw, "kind": "sha", "sha": raw, "checkout": checkout} if checkout else None
        return None


def commit(sha, checkout, root=None):
    root = root or root_path()
    if not SHA_RE.match(sha or ""):
        raise ValueError("not a commit sha")
    path = checkouts(root).get(checkout)
    if not path:
        raise FileNotFoundError("unknown checkout")
    show = _git(path, "show", "--stat=200", "--format=%H%n%an%n%cI%n%B%n--", sha)
    if not show:
        raise FileNotFoundError("commit not in this checkout")
    linker = Linker(root)
    files = _git(path, "show", "--name-only", "--format=", sha).split()
    prefix = "" if path == root else checkout
    return {"sha": show.split("\n", 1)[0], "repo": _remote(path), "checkout": checkout,
            "text": show,
            "files": [{"path": rel, "id": linker._file(prefix, rel)} for rel in files]}


# ── the conversation ────────────────────────────────────────────────────────
def _linked_changes(linker, found, issues):
    return {"commits": [dict(row, segments=linker.segments(row["subject"])) for row in found["commits"]], "issues": issues,
            "publishes": [dict(row, id=linker._file("", row["path"])) for row in found["publishes"]]}


def read(root=None):
    root = root or root_path()
    linker = Linker(root)
    items = []
    for run in runs(root):
        end_at = (run["end"] or {}).get("at") or (now() if run["live"] else run["stopped"])
        issues = issue_actions(run["events"])
        items.append({"kind": "run", "at": run["start"], "mode": run["mode"], "live": run["live"],
                      "log": run["log"], "end": run["end"], "truncated": run["truncated"],
                      "events": [dict(event, segments=linker.segments(event["text"]) if event["kind"] != "tool" else [{"text": event["text"]}])
                                 for event in run["events"]],
                      "holds": [dict(row, segments=linker.segments(" ".join(str(part) for part in (f"{row.get('repo')}#{row.get('issue')}" if row.get("issue") else row.get("repo"), row.get("blocker"), row.get("next")) if part)))
                                for row in run["holds"]],
                      "changes": _linked_changes(linker, changes(root, run["start"], end_at, final=not run["live"]), issues)})
    for row in inbox(root):
        items.append({"kind": "message", "at": row["at"], "id": row["id"], "read_at": row["read_at"],
                      "segments": linker.segments(row["text"])})
    items.sort(key=lambda item: item["at"])
    return {"items": items, "last_skip": last_skip(root), "unread": sum(1 for row in inbox(root) if not row["read_at"]),
            "as_of": now()}
