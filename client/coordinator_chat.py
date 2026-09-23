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
import threading
import time

import lesson_status
import office_objects

INBOX = "_meta/state/coordinator-inbox.jsonl"
RUNS = "_meta/ledgers/coordinator-runs.jsonl"
HOLDS = "_meta/ledgers/coordinator-holds.jsonl"
SUPERVISOR = "_meta/ledgers/coordinator-supervisor.jsonl"  # _meta/services/coordinators/supervise.py
MAX_RUNS = 12
MAX_LOG_BYTES = 2 * 1024 * 1024
MAX_TEXT = 8000
DEFAULT_REPO = "Thinking-Brain-School/tbs-www"
ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


CONFIG = Path(__file__).with_name("coordinators.json")
TBS_DEFAULTS = {"id": "tbs", "name": "TBS", "out": ".tbs-out", "checkouts": "tbs*", "repo": DEFAULT_REPO}


def configs():
    """Every coordinator the Office shows, from one file. The first is the default."""
    try:
        rows = json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        rows = [dict(TBS_DEFAULTS, root="thinking-brain-school")]
    out = []
    for row in rows:
        row = dict(row)
        if row["id"] == "tbs":
            row["path"] = Path(os.environ.get("OFFICE_COORDINATOR_ROOT") or lesson_status.root_path()).resolve()
        else:
            row["path"] = (Path(__file__).resolve().parents[2] / row["root"]).resolve()
        out.append(row)
    return out


def conf(root):
    for row in configs():
        if row["path"] == Path(root).resolve():
            return row
    return dict(TBS_DEFAULTS, path=Path(root))


def root_path(coordinator=None):
    rows = configs()
    for row in rows:
        if row["id"] == (coordinator or rows[0]["id"]):
            return row["path"]
    raise FileNotFoundError("unknown coordinator")


def log_glob(root):
    return conf(root)["out"] + "/coordinator-[0-9]*.log"


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
    root = root or root_path(body.get("coordinator"))
    text, request = body.get("text"), body.get("id")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        raise ValueError(f"a message is 1-{MAX_TEXT} characters")
    if not isinstance(request, str) or not ID_RE.match(request):
        raise ValueError("a message needs its request id")
    with _inbox_stream(root) as stream:
        stream.seek(0)
        for line in stream.read().splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("id") == request:
                return {"ok": True, "duplicate": True, "message": row}
        row = {"id": request, "at": now(), "from": "aria", "text": text.strip()}
        _write(stream, [row])
    return {"ok": True, "duplicate": False, "message": row}


def _inbox_stream(root):
    """The inbox's only open: no symlinks, owner-only, exclusively locked until closed."""
    path = root / INBOX
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)
    stream = os.fdopen(fd, "a+", encoding="utf-8")
    fcntl.flock(stream, fcntl.LOCK_EX)
    return stream


def _write(stream, rows):
    stream.write("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    stream.flush()
    os.fsync(stream.fileno())


def mark_read(ids, run_start, root=None):
    """What a run appends after reading: one row per message id."""
    root = root or root_path()
    with _inbox_stream(root) as stream:
        _write(stream, [{"read": value, "at": now(), "run_start": run_start} for value in ids])


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
        # Another agent's writer can omit `at`. The message is still real, so it
        # is kept and dated "unknown", which sorts oldest. Reading row["at"]
        # without this took the whole coordinator endpoint down with a 500.
        if not isinstance(row.get("at"), str):
            row["at"] = ""
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
    at = row.get("timestamp") if isinstance(row.get("timestamp"), str) else None
    for block in (row.get("message") or {}).get("content") or []:
        if block.get("type") == "text" and block.get("text", "").strip():
            yield {"kind": "text", "text": block["text"].strip(), "at": at}
        elif block.get("type") == "tool_use":
            yield {"kind": "tool", "text": _tool_line(block), "at": at}


def _result_event(row, events):
    text = str(row.get("result") or row.get("subtype") or "").strip()
    if events and events[-1]["text"] == text:
        return None  # the result repeats the final assistant text
    # a result row carries no timestamp of its own; the last real one it followed is its time
    at = events[-1]["at"] if events else None
    return {"kind": "result", "text": text, "at": at}


def _append(events, event):
    """A deduped result is nothing, not a None in the list.

    `_result_event` compares against `events[-1]`, so a None left in the list
    made the NEXT result row raise TypeError and took the whole coordinator
    endpoint down with a 500. Two result rows in one log is enough.
    """
    if event:
        events.append(event)


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
            _append(events, _result_event(row, events))
    if "".join(plain).strip():
        events.insert(0, {"kind": "text", "text": "\n".join(plain).strip(), "at": None})
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
    starts = sorted(row["at"] for row in ledger if row.get("event") == "start" and isinstance(row.get("at"), str))
    holds = _rows(root / HOLDS)
    logs = sorted(glob.glob(str(root / log_glob(root))))[-MAX_RUNS:]
    out = []
    for name in logs:
        path = Path(name)
        start, mode = _stamp(path)
        if not start:
            continue
        end = ends.get(path.name)
        # the ledger start precedes the log's own stamp; holds are keyed by it, live or not
        start = (end or {}).get("start") or next((at for at in reversed(starts) if at <= start), start)
        raw, truncated = _read_tail(path)
        live = end is None and _live(root, path)
        events = parse_log(raw)
        for event in events:  # a line with no timestamp of its own dates to when the run began
            if not event.get("at"):
                event["at"] = start
        if not (events or end or live):
            continue  # a launcher that died before claude wrote a byte: nothing to say
        try:
            stopped = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except OSError:
            stopped = start
        out.append({"stopped": stopped, "start": start, "mode": mode, "log": str(path.relative_to(root)),
                    "live": live, "end": end,
                    "truncated": truncated, "events": events, "shas": set(HEX.findall(raw)),
                    "holds": [row for row in holds if row.get("run_start") == start]})
    return out


def _live(root, log):
    """The newest log without an end row is live only while the launcher's lock pid is."""
    try:
        pid = int((root / conf(root)["out"] / "coordinator.lock/pid").read_text().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    logs = sorted(glob.glob(str(root / log_glob(root))))
    return bool(logs) and Path(logs[-1]) == log


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
    pattern = conf(root)["checkouts"]
    found = {root.name: root}
    for path in sorted(base.glob(pattern)) if pattern else []:
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
SHA_MISS_TTL = 60
LIVE_TTL = 15  # a live run's commits are re-read at most this often
READ_LOCK = threading.Lock()  # overlapping polls wait for one build instead of each spawning git


def _remote(path):
    if str(path) in REMOTES:
        return REMOTES[str(path)]
    url = _git(path, "remote", "get-url", "origin").strip()
    match = re.search(r"github\.com[:/]([^/]+/[^/.]+?)(?:\.git)?$", url)
    REMOTES[str(path)] = match[1] if match else ""
    return REMOTES[str(path)]


HEX = re.compile(r"\b[0-9a-f]{7,40}\b")


def changes(root, start, end, final=False, shas=None):
    """Commits in the run's window that its own log names (git output, pushes); others' work stays out."""
    key = (str(root), start, end if final else "live")
    cached = CHANGES.get(key)
    if cached and (final or time.monotonic() - cached[0] < LIVE_TTL):
        return cached[1]
    commits = []
    for name, path in checkouts(root).items():
        repo = _remote(path)
        for line in _git(path, "log", "--all", f"--since={start}", f"--until={end}",
                         "--format=%H%x09%cI%x09%s", "-n", "40").splitlines():
            sha, at, subject = (line.split("\t", 2) + ["", ""])[:3]
            if shas is not None and sha[:7] not in {value[:7] for value in shas}:
                continue
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
    CHANGES[key] = (time.monotonic(), result)
    return result


ISSUE_CMD = re.compile(r"\bgh\b.*?\bissue (create|close|comment|reopen)\b(?:\s+(\d+))?")
ISSUE_REPO = re.compile(r"(?:-R|--repo)[\s=]+([\w.-]+/[\w.-]+)")


def issue_actions(events):
    out = []
    for event in events:
        if event["kind"] != "tool":
            continue
        match, repo = ISSUE_CMD.search(event["text"]), ISSUE_REPO.search(event["text"])
        if match and repo:
            out.append({"action": match[1], "repo": repo[1], "number": int(match[2]) if match[2] else None})
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
        self.default_repo = conf(root).get("repo") or DEFAULT_REPO
        self.repos = {name: _remote(path) for name, path in self.checkouts.items()}
        self.object_roots = None

    def _repo(self, owner, name):
        if owner and name:
            return f"{owner}/{name}"
        if not name:
            return self.default_repo
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
        checkout, at = SHAS.get(sha, (None, 0))
        if not checkout and time.monotonic() - at > SHA_MISS_TTL:  # a miss may arrive with the next fetch
            checkout, at = self._find_sha(sha), time.monotonic()
            SHAS[sha] = (checkout, at)
        return checkout

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


def commit(sha, checkout, root=None, coordinator=None):
    root = root or root_path(coordinator)
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


def read(root=None, coordinator=None):
    with READ_LOCK:
        return _read(root or root_path(coordinator))


def _read(root):
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
                      "changes": _linked_changes(linker, changes(root, run["start"], end_at, final=not run["live"], shas=run["shas"]), issues)})
    messages = inbox(root)
    for row in messages:
        items.append({"kind": "message", "at": row["at"], "id": row["id"], "read_at": row["read_at"],
                      "segments": linker.segments(row["text"])})
    items.sort(key=lambda item: item["at"])
    return {"items": items, "last_skip": last_skip(root), "unread": sum(1 for row in messages if not row["read_at"]),
            "as_of": now()}


# ── the overview: one row per coordinator ───────────────────────────────────
THRASH_RUNS = 4  # this many ended runs in a row that shipped nothing is thrashing
OVERVIEW_TTL = 15
OVERVIEW = {}
LANE_RE = re.compile(r"^\s*LANE\s+\S+\s*\|.*$", re.M)
OVERVIEW_LOG_BYTES = 512 * 1024


def _age(at):
    try:
        then = dt.datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return None
    return int((dt.datetime.now(dt.timezone.utc) - then).total_seconds())


def _tail(path, limit):
    try:
        size = path.stat().st_size
        with open(path, "rb") as stream:
            stream.seek(max(0, size - limit))
            if size > limit:
                stream.readline()
            return stream.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _window_commits(root, start, end):
    """Commits in the coordinator's checkouts inside one run window.

    Scheduled `sync:` snapshot commits land every few hours whatever the coordinator does, so
    they are not shipping; counting them would hide a coordinator that does nothing.
    """
    out = []
    for name, path in checkouts(root).items():
        for line in _git(path, "log", "--all", f"--since={start}", f"--until={end}",
                         "--format=%H%x09%ct%x09%s", "-n", "40").splitlines():
            sha, at, subject = (line.split("\t", 2) + ["0", ""])[:3]
            if subject.startswith("sync: "):
                continue
            stamp = dt.datetime.fromtimestamp(int(at or 0), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            out.append({"checkout": name, "sha": sha, "at": stamp, "subject": subject})
    return out


def summary(row):
    root = row["path"]
    ledger = _rows(root / RUNS)
    starts = [r for r in ledger if r.get("event") == "start" and isinstance(r.get("at"), str)]
    ends = [r for r in ledger if r.get("event") == "end" and isinstance(r.get("at"), str)]
    logs = sorted(glob.glob(str(root / log_glob(root))))
    latest = Path(logs[-1]) if logs else None
    live = bool(latest) and not any(os.path.basename(r.get("log") or "") == latest.name for r in ends) and _live(root, latest)
    last_end = ends[-1] if ends else None
    recent = ends[-THRASH_RUNS:]
    windows = [_window_commits(root, r.get("start") or r["at"], r["at"]) for r in recent]
    shipped = [(r.get("prod_changes") or 0) + len(found) for r, found in zip(recent, windows)]
    thrashing = len(recent) >= THRASH_RUNS and not any(shipped)
    doing, lanes = "", []
    if latest:
        events = parse_log(_tail(latest, OVERVIEW_LOG_BYTES))
        texts = [e["text"] for e in events if e["kind"] in ("text", "result")]
        doing = texts[-1][:600] if texts else ""
        lanes = [m.strip()[:200] for m in LANE_RE.findall("\n".join(texts))][-12:]
        if not lanes:
            lanes = [e["text"] for e in events if e["kind"] == "tool"
                     and (e["text"].startswith(("Agent", "Task")) or "codex-lane submit" in e["text"])][-12:]
    found = [c for window in windows for c in window]
    if live and starts:
        found += _window_commits(root, starts[-1]["at"], now())
    commits = sorted({c["sha"]: c for c in found}.values(), key=lambda c: c["at"], reverse=True)[:6]
    last_start = starts[-1]["at"] if starts else None
    last_skip_row = last_skip(root)
    health = "running" if live else "thrashing" if thrashing else "ok"
    if not live and last_end and last_end.get("rc") not in (0, None):
        health = "failing"
    age = _age((last_end or {}).get("at") or last_start)
    if not live and (age is None or age > row.get("stall_s", 6 * 3600)):
        health = "stalled"
    return {"id": row["id"], "name": row["name"], "live": live, "health": health,
            "last_start": last_start, "last_end": last_end, "age_s": age,
            "shipped_recent": shipped, "thrashing": thrashing,
            "working_on": doing, "lanes": lanes, "commits": commits,
            "unread": len(unread(root)), "last_skip": last_skip_row,
            "supervisor": (_rows(root / SUPERVISOR) or [None])[-1],
            "prompt": row.get("prompt") or "docs/coordinator.md"}


def overview():
    cached = OVERVIEW.get("rows")
    if cached and time.monotonic() - cached[0] < OVERVIEW_TTL:
        return cached[1]
    rows = []
    for row in configs():
        try:
            rows.append(summary(row))
        except Exception as exc:  # noqa: BLE001 - one broken tree must not hide the others
            rows.append({"id": row["id"], "name": row["name"], "health": "error", "error": str(exc)[:200]})
    result = {"coordinators": rows, "as_of": now()}
    OVERVIEW["rows"] = (time.monotonic(), result)
    return result
