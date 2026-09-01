"""Every agent actually running on this machine, read from the outside.

`sessions.py` next door is the room you can ANSWER: it reads hcom, hcom holds
the hooks, and a session that never ran `hcom start` is invisible there. That is
the right trade for a surface with a send button, and it is the wrong trade for
the question "what is running right now", because most sessions never join hcom.

So this module answers the other half, and it is read only on purpose. It has no
send, no start, no keystroke, and nothing it exposes can be posted to. It is a
window, and a window is allowed to be pointed at things you cannot reach.

WHERE LIVENESS COMES FROM
-------------------------
Processes, not files. `pgrep -x claude` and `pgrep -x codex` are the list; a
transcript on disk proves only that a session once existed, and there are 3500
project directories on this machine whose sessions are all dead. The cwd comes
from `lsof -a -p <pid> -d cwd`, which is the same thing a shell would tell you.

A process with no transcript still gets a row, with state `unknown`. Dropping it
would make the count on the page disagree with the count in a terminal, and the
whole point of this surface is that those two agree.

HOW A PROCESS FINDS ITS TRANSCRIPT
----------------------------------
By directory, which is the only thing the two sides share.

Claude Code writes `~/.claude/projects/<slug>/<sessionId>.jsonl`, where the slug
is the cwd with every `/` and `.` turned into `-`. Two things bite here, both
measured rather than guessed:

  * a worktree under `/private/var/folders/...` has BOTH a fully dashed slug dir
    and one that kept its dots, sitting side by side on disk, and only one of
    them is being written to. Every candidate is tried and the one holding the
    newest jsonl wins.
  * macOS reports the cwd as `/private/var/...` while some sessions were logged
    under `/var/...`. That prefix is tried too.

Codex writes `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` and states its own
cwd in the first line, so the join is a read rather than a guess. Only today's
and yesterday's date folders are scanned: a session started before that is not
one this page is claiming to be live about, and walking the whole tree on every
poll is how a five second refresh becomes a spinning disk. A rollout written by
a Codex SUBAGENT is skipped while a top-level one exists for that directory: the
subagent runs inside the same process, and showing its thread as the session's
would answer "what is this agent doing" with someone else's transcript.

NOTHING HERE BLOCKS THE DOOR
----------------------------
Every subprocess is capped at two seconds and never runs through a shell. A
failure is `unreadable` with the reason on it, never an empty list: "nothing is
running" is a claim, and this module may only make it when it actually asked and
was actually told nothing.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone

import sessions

# engine-pid, and nothing else may ever be a key. It is the only thing a caller
# hands back to reach a transcript, and it never becomes a path: the path comes
# off the row this process built, so a key is a lookup and not a filename.
KEY_RE = re.compile(r"[a-z]+-[0-9]+\Z", re.ASCII)

ENGINES = ("claude", "codex")

CLAUDE_PROJECTS = pathlib.Path.home() / ".claude" / "projects"
CODEX_SESSIONS = pathlib.Path.home() / ".codex" / "sessions"

# Every one of these is a local binary answering about local state. Two seconds
# is already ten times what any of them takes; past that something is wrong and
# the honest answer is to say so rather than to hold the page.
PROBE_TIMEOUT_S = 2.0

# A transcript touched inside this window is a session mid-turn. Longer than a
# tool call, shorter than a person's attention: two minutes.
WORKING_S = 120

# The pid to cwd and cwd to transcript joins cost three subprocesses and a
# directory walk, and neither answer changes on the scale a person polls at.
JOIN_CACHE_S = 10

DEFAULT_LIMIT = 400
MAX_LIMIT = 1000
# A single tool result can be a megabyte of build output. It is clipped, and the
# line says it was clipped, which is a different thing from a page that silently
# hands back a third of what was said.
MAX_TEXT = 40000
# Past this a file is not a conversation, it is an artifact. Read the tail.
MAX_READ_BYTES = 64 * 1024 * 1024

TITLE_MAX = 80
LAST_LINE_MAX = 200

BAD_KEY = "a session key is engine-pid"
NO_SESSION = "no session is running under that key"
NO_TRANSCRIPT = "that session has no transcript on this machine"

_joins: dict = {"at": 0.0, "cwd": {}, "tx": {}}
# One parsed transcript, keyed on (path, mtime, size). A reader polls the same
# file every few seconds and it changes rarely; re-parsing megabytes on every
# poll for the four lines that arrived is the obvious way to make a read-only
# page expensive.
_parsed: dict = {"key": None, "lines": []}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso(stamp: float) -> str:
    return datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run(args: list[str], timeout: float = PROBE_TIMEOUT_S) -> tuple[int, str, str]:
    """A local binary, capped, never through a shell.

    The one seam the tests move: everything this module learns about the machine
    arrives through here, so a test that replaces it is testing the real reading
    code against a machine it made up.
    """
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return 124, "", f"{args[0]} did not answer in {timeout:g}s"
    except OSError as exc:
        return 127, "", f"{type(exc).__name__}: {exc}"[:200]
    return proc.returncode, proc.stdout or "", proc.stderr or ""


# ── the machine ──────────────────────────────────────────────────────────────

def pids(engine: str) -> tuple[list, str]:
    """([pid, ...], reason it could not be asked).

    `pgrep` exits 1 for "nothing matched", which is an answer and not a fault.
    Anything else is a fault, and it is returned rather than flattened into an
    empty list, because an empty list here would be a lie about the machine.
    """
    rc, out, err = _run(["pgrep", "-x", engine])
    if rc not in (0, 1):
        return [], (err or f"pgrep -x {engine} failed ({rc})").strip().replace("\n", " ")[:200]
    found = []
    for word in (out or "").split():
        if word.isdigit():
            found.append(int(word))
    return found, ""


def cwd_of(pid: int) -> str:
    """The working directory of one process, or "".

    `-Fn` is lsof's machine format: one field per line, the ones that matter
    prefixed `n`. A process that ended between the pgrep and here answers with
    nothing, which is a blank cwd and a row that still says it was seen.
    """
    rc, out, _ = _run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"])
    if rc != 0 and not out.strip():
        return ""
    for line in reversed((out or "").splitlines()):
        if line.startswith("n") and len(line) > 1:
            return line[1:].strip()
    return ""


def _etimes(all_pids: list) -> dict:
    """{pid: seconds alive}. One `ps` for every process rather than one each."""
    if not all_pids:
        return {}
    rc, out, _ = _run(["ps", "-o", "pid=,etime=", "-p", ",".join(str(p) for p in all_pids)])
    if rc != 0 and not out.strip():
        return {}
    ages = {}
    for line in (out or "").splitlines():
        parts = line.split()
        if len(parts) < 2 or not parts[0].isdigit():
            continue
        seconds = _etime_seconds(parts[1])
        if seconds is not None:
            ages[int(parts[0])] = seconds
    return ages


def _etime_seconds(text: str):
    """`ps` elapsed time to seconds. [[dd-]hh:]mm:ss, and nothing else."""
    body = str(text or "").strip()
    days = 0
    if "-" in body:
        head, _, body = body.partition("-")
        if not head.isdigit():
            return None
        days = int(head)
    bits = body.split(":")
    if not 2 <= len(bits) <= 3 or not all(b.isdigit() for b in bits):
        return None
    while len(bits) < 3:
        bits.insert(0, "0")
    hours, minutes, secs = (int(b) for b in bits)
    return days * 86400 + hours * 3600 + minutes * 60 + secs


# ── the join: a directory to a transcript ────────────────────────────────────

def claude_slug(cwd: str) -> str:
    """The directory name Claude Code writes a project's sessions under.

    Every `/` and every `.` becomes `-`, leading slash included, which is why a
    slug starts with a dash. Pure, and tested as such: it is the one piece of
    this file that is a fact about another program's file layout.
    """
    return re.sub(r"[/.]", "-", str(cwd or ""))


def claude_dirs(cwd: str) -> list:
    """Every directory Claude Code might have written this cwd's sessions to.

    Three candidates, all of them observed on this machine rather than imagined:
    the dashed slug, the slug that kept its dots (worktree paths carry both, side
    by side), and the same pair with the `/private` that macOS prepends to a
    temp path removed. Order is not significance: the caller picks by mtime.
    """
    here = str(cwd or "").strip()
    if not here:
        return []
    roots = [here]
    if here.startswith("/private/"):
        roots.append(here[len("/private"):])
    out = []
    for root in roots:
        for name in (claude_slug(root), root.replace("/", "-")):
            if name and name not in out:
                out.append(name)
    return out


def claude_transcript(cwd: str) -> str:
    """The newest jsonl Claude Code has for this directory, or ""."""
    best, best_at = "", -1.0
    for name in claude_dirs(cwd):
        folder = CLAUDE_PROJECTS / name
        try:
            entries = list(folder.glob("*.jsonl"))
        except OSError:
            continue
        for entry in entries:
            try:
                when = entry.stat().st_mtime
            except OSError:
                continue
            if when > best_at:
                best, best_at = str(entry), when
    return best


def _codex_day_dirs() -> list:
    today = datetime.now()
    days = [today, today - timedelta(days=1)]
    return [CODEX_SESSIONS / d.strftime("%Y") / d.strftime("%m") / d.strftime("%d")
            for d in days]


def _codex_head(path: pathlib.Path) -> dict:
    """The first line of a rollout, which is its `session_meta`."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            first = handle.readline()
    except OSError:
        return {}
    try:
        obj = json.loads(first or "{}")
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def codex_transcripts() -> dict:
    """{cwd: newest rollout path} across today and yesterday.

    A rollout that a subagent wrote loses to a top-level one for the same
    directory whatever its mtime, because the subagent's thread is not the
    session a person is looking at.
    """
    best: dict = {}
    for folder in _codex_day_dirs():
        try:
            entries = sorted(folder.glob("rollout-*.jsonl"))
        except OSError:
            continue
        for entry in entries:
            try:
                when = entry.stat().st_mtime
            except OSError:
                continue
            head = _codex_head(entry)
            payload = head.get("payload") if isinstance(head.get("payload"), dict) else {}
            where = str((payload or {}).get("cwd") or "").strip()
            if not where:
                continue
            source = (payload or {}).get("source")
            sub = bool(isinstance(source, dict) and source.get("subagent"))
            was = best.get(where)
            # A top-level rollout beats a subagent one outright; between two of
            # the same kind, the one written most recently wins.
            if was is None or (not sub, when) > (not was["sub"], was["at"]):
                best[where] = {"path": str(entry), "at": when, "sub": sub}
    return {where: found["path"] for where, found in best.items()}


def _join(engine: str, pid: int, cwds: dict, codex_index: dict) -> str:
    where = cwds.get(pid) or ""
    if not where:
        return ""
    cached = _joins["tx"].get((engine, where))
    if cached is not None:
        return cached
    if engine == "claude":
        found = claude_transcript(where)
    else:
        found = codex_index.get(where) or ""
        if not found and where.startswith("/private/"):
            found = codex_index.get(where[len("/private"):]) or ""
    _joins["tx"][(engine, where)] = found
    return found


# ── one transcript, digested for a row ───────────────────────────────────────

def _digest(path: str, engine: str) -> dict:
    """title, last_line and turns for one transcript, without parsing all of it.

    The whole file is walked, and only the lines that could possibly matter are
    handed to the JSON parser: a ten megabyte transcript is a few milliseconds of
    substring scanning and a few dozen parses, where parsing every line of it on
    every five second poll would not be.
    """
    out = {"title": "", "last_line": "", "turns": 0}
    try:
        raw = pathlib.Path(path).read_bytes()[-MAX_READ_BYTES:]
    except OSError:
        return out
    lines = raw.splitlines()
    title_from_user = ""
    for chunk in lines:
        if engine == "claude" and b'"ai-title"' in chunk:
            obj = _loads(chunk)
            if obj.get("aiTitle"):
                out["title"] = str(obj["aiTitle"])[:TITLE_MAX]
        if not _maybe_user(chunk, engine):
            continue
        said = _first_text(_lines_of(_loads(chunk), engine), "user")
        if not said:
            continue
        out["turns"] += 1
        if not title_from_user and not _preamble(said):
            title_from_user = said[:TITLE_MAX]
    for chunk in reversed(lines):
        parsed = _lines_of(_loads(chunk), engine)
        said = _first_text(parsed, "agent")
        if said:
            out["last_line"] = said[:LAST_LINE_MAX]
            break
    if not out["title"]:
        out["title"] = title_from_user
    return out


# The cheap prefilter. A line that cannot be a user message is never handed to
# the JSON parser, which is the whole reason a ten megabyte transcript digests
# in milliseconds. Both spacings are checked because these files are written by
# two different serialisers.
def _maybe_user(chunk: bytes, engine: str) -> bool:
    if engine == "claude":
        return b'"type":"user"' in chunk or b'"type": "user"' in chunk
    return (b'"response_item"' in chunk
            and (b'"role":"user"' in chunk or b'"role": "user"' in chunk))


# What a session was ASKED is the title. Both engines open a session by feeding
# the agent its own instructions as a user message, and a roster where every
# Codex row is titled "# AGENTS.md instructions" tells a person nothing.
PREAMBLES = ("# agents.md", "<instructions>", "<environment_context>",
             "<user_instructions>", "<system-reminder>", "caveat:")


def _preamble(said: str) -> bool:
    head = said.strip().lower()
    return any(head.startswith(mark) for mark in PREAMBLES)


def _loads(chunk) -> dict:
    try:
        obj = json.loads(chunk)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


def _lines_of(obj: dict, engine: str) -> list:
    return parse_claude_line(obj) if engine == "claude" else parse_codex_line(obj)


def _first_text(rows: list, who: str) -> str:
    for row in rows:
        if row.get("who") == who and row.get("kind") == "text":
            said = " ".join(str(row.get("text") or "").split())
            if said:
                return said
    return ""


# ── the two parsers ──────────────────────────────────────────────────────────

def _clip(text) -> tuple[str, bool]:
    body = str(text or "")
    if len(body) <= MAX_TEXT:
        return body, False
    return body[:MAX_TEXT], True


def _line(at: str, who: str, kind: str, text) -> dict:
    body, cut = _clip(text)
    return {"at": at, "who": who, "kind": kind, "text": body, "truncated": cut}


def _tool_line(at: str, name, payload) -> dict:
    try:
        shown = json.dumps(payload, separators=(",", ":"), default=str) \
            if not isinstance(payload, str) else payload
    except (TypeError, ValueError):
        shown = str(payload)
    return _line(at, "tool", "tool", f"{name or 'tool'} {shown}".strip())


def parse_claude_line(obj) -> list:
    """One record of a Claude Code jsonl as reader lines. Pure.

    A record can be several lines: an assistant turn is a list of blocks and a
    turn that thought, called a tool and then spoke is three things a person
    reads separately. Records that are not conversation (attachments, mode
    changes, file snapshots, the title) produce nothing.
    """
    if not isinstance(obj, dict):
        return []
    kind = obj.get("type")
    at = str(obj.get("timestamp") or "")
    if kind == "system":
        body = obj.get("content")
        text = body if isinstance(body, str) else str(obj.get("subtype") or "")
        return [_line(at, "system", "text", text)] if text else []
    if kind not in ("user", "assistant"):
        return []
    message = obj.get("message")
    if not isinstance(message, dict):
        return []
    who = "user" if kind == "user" else "agent"
    content = message.get("content")
    if isinstance(content, str):
        return [_line(at, who, "text", content)] if content.strip() else []
    if not isinstance(content, list):
        return []
    out = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text" and str(block.get("text") or "").strip():
            out.append(_line(at, who, "text", block.get("text")))
        elif btype == "thinking" and str(block.get("thinking") or "").strip():
            out.append(_line(at, "agent", "thinking", block.get("thinking")))
        elif btype == "tool_use":
            out.append(_tool_line(at, block.get("name"), block.get("input")))
        elif btype == "tool_result":
            body = block.get("content")
            if isinstance(body, list):
                body = "\n".join(str(part.get("text") or "") for part in body
                                 if isinstance(part, dict))
            out.append(_line(at, "result", "result", body))
    return out


def parse_codex_line(obj) -> list:
    """One record of a Codex rollout as reader lines. Pure.

    `reasoning` is skipped because it is encrypted, so there is nothing to show.
    `event_msg` is skipped because `task_complete` repeats the assistant message
    that is already in the transcript, and a reader that shows a turn twice is a
    reader nobody trusts about ordering.
    """
    if not isinstance(obj, dict):
        return []
    at = str(obj.get("timestamp") or "")
    if obj.get("type") != "response_item":
        return []
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return []
    ptype = str(payload.get("type") or "")
    if ptype == "message":
        role = str(payload.get("role") or "")
        who = {"user": "user", "assistant": "agent", "developer": "system"}.get(role)
        if not who:
            return []
        text = _codex_text(payload.get("content"))
        return [_line(at, who, "text", text)] if text.strip() else []
    if ptype.endswith("_call_output"):
        return [_line(at, "result", "result", _codex_text(payload.get("output")))]
    if ptype.endswith("_call"):
        return [_tool_line(at, payload.get("name"),
                           payload.get("input") if payload.get("input") is not None
                           else payload.get("arguments"))]
    return []


def _codex_text(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(parts)


# ── the roster ───────────────────────────────────────────────────────────────

def _fresh_joins(found: dict) -> tuple[dict, dict]:
    """The pid to cwd and cwd to transcript joins, at most ten seconds old."""
    now = time.monotonic()
    if now - _joins["at"] > JOIN_CACHE_S:
        _joins["at"] = now
        _joins["cwd"] = {}
        _joins["tx"] = {}
    cwds = _joins["cwd"]
    for engine in ENGINES:
        for pid in found.get(engine, []):
            if pid not in cwds:
                cwds[pid] = cwd_of(pid)
    want_codex = any(found.get("codex"))
    index = codex_transcripts() if want_codex and not _joins.get("codex_index") else \
        (_joins.get("codex_index") or {})
    if want_codex:
        _joins["codex_index"] = index
    return cwds, index


def read() -> dict:
    """Every Claude Code and Codex process on this machine, with its transcript.

    States:

      unreadable  the machine could not be asked. `pgrep` is missing, or it
                  failed, and the reason is on the answer. Never an empty list:
                  "nothing is running" is a claim this may only make after it
                  actually asked and was actually told nothing.
      empty       asked, and nothing is running.
      ok          there are sessions.
    """
    found, faults = {}, []
    for engine in ENGINES:
        got, why = pids(engine)
        found[engine] = got
        if why:
            faults.append(why)
    if faults:
        return {"state": "unreadable", "detail": " · ".join(faults)[:300],
                "sessions": [], "working": 0, "as_of": now_iso()}

    cwds, codex_index = _fresh_joins(found)
    ages = _etimes([p for engine in ENGINES for p in found[engine]])
    now = time.time()

    made = []
    for engine in ENGINES:
        for pid in found[engine]:
            where = cwds.get(pid) or ""
            path = _join(engine, pid, cwds, codex_index)
            touched = None
            if path:
                try:
                    touched = os.stat(path).st_mtime
                except OSError:
                    path, touched = "", None
            digest = _digest(path, engine) if path else {"title": "", "last_line": "", "turns": 0}
            age = ages.get(pid)
            made.append((touched or 0.0, {
                "key": f"{engine}-{pid}",
                "engine": engine,
                "pid": pid,
                "cwd": where,
                "repo": sessions.origin_nwo(where) if where else "",
                "started": _iso(now - age) if age is not None else "",
                "last_activity": _iso(touched) if touched else "",
                "title": digest["title"],
                "last_line": digest["last_line"],
                "turns": digest["turns"],
                "transcript": path,
                "state": ("working" if touched and now - touched <= WORKING_S
                          else ("idle" if touched else "unknown")),
            }))

    # Working first, then quiet, then the ones with nothing on disk; inside a
    # group, the one that moved most recently. A session with no transcript
    # is last and still present: it is running, and the count on this page has
    # to be the count a terminal would give.
    order = {"working": 0, "idle": 1, "unknown": 2}
    made.sort(key=lambda pair: (order.get(pair[1]["state"], 3), -pair[0], pair[1]["key"]))
    rows = [row for _, row in made]
    return {"state": "ok" if rows else "empty", "detail": "", "sessions": rows,
            "working": sum(1 for r in rows if r["state"] == "working"),
            "as_of": now_iso()}


# ── one transcript, whole, paged ─────────────────────────────────────────────

def check_key(key) -> str | None:
    if not isinstance(key, str) or not KEY_RE.match(key.strip()):
        return BAD_KEY
    return None


def _row(key: str):
    for row in read().get("sessions") or []:
        if row["key"] == key:
            return row
    return None


def _all_lines(path: str, engine: str) -> list:
    """The whole transcript as reader lines, cached against its own mtime.

    The path never comes from a caller. It comes off the row this process built
    from `pgrep`, which is why there is no traversal to defend against here:
    there is no user string anywhere on the way to this open.
    """
    try:
        stat = os.stat(path)
    except OSError:
        return []
    stamp = (path, stat.st_mtime, stat.st_size)
    if _parsed["key"] == stamp:
        return _parsed["lines"]
    out = []
    try:
        with open(path, "rb") as handle:
            for chunk in handle:
                out.extend(_lines_of(_loads(chunk), engine))
    except OSError:
        return []
    _parsed["key"] = stamp
    _parsed["lines"] = out
    return out


def transcript(key: str, offset=0, limit=DEFAULT_LIMIT) -> tuple[int, dict]:
    """One session's whole conversation, a page at a time. (http status, body).

    A negative offset means "the end", which is what a reader wants on open: it
    is the only way to ask for the newest page without first asking how many
    lines there are and then asking again.
    """
    why = check_key(key)
    if why:
        return 400, {"error": why}
    row = _row(key.strip())
    if row is None:
        return 404, {"error": NO_SESSION}
    if not row["transcript"]:
        return 404, {"error": NO_TRANSCRIPT, "key": row["key"]}

    limit = _int(limit, DEFAULT_LIMIT)
    limit = max(1, min(MAX_LIMIT, limit))
    lines = _all_lines(row["transcript"], row["engine"])
    total = len(lines)
    start = _int(offset, 0)
    if start < 0:
        start = max(0, total - limit)
    start = max(0, min(start, total))
    return 200, {"key": row["key"], "engine": row["engine"], "state": row["state"],
                 "title": row["title"], "cwd": row["cwd"], "repo": row["repo"],
                 "transcript": row["transcript"], "total": total, "offset": start,
                 "lines": lines[start:start + limit], "as_of": now_iso()}


def _int(value, fallback: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return fallback
