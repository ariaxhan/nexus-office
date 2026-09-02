"""The agents actually running on this machine, and the way to answer one.

Every other surface in this office is about GitHub: what a repo looks like from
the outside, and what the pipeline did to it. This is the inside. A desk is a
folder, and at any moment that folder may have a Claude Code or a Codex session
sitting in it, halfway through something, waiting on a person who is not looking
at that terminal.

WHY hcom AND NOT THE TRANSCRIPT FILES
-------------------------------------
Claude Code writes JSONL under `~/.claude/projects/<slugged path>/` and Codex
writes under `~/.codex/sessions/<date>/`. Both are readable, and reading them is
the obvious thing to do, and it answers only half the question. A transcript file
says what a session SAID. It does not say whether that session is still alive,
whether it is waiting on a tool approval, or how to reach it. There are 2448
project directories on this machine and almost all of them are dead.

`hcom` already answers all three, because it holds the hooks: `list` knows which
sessions are bound to a live process and what each one is doing right now,
`transcript` reads the conversation, and `send` puts a message where the agent
will read it at its next hook. So this module is a reader of hcom, and it owns
none of that state.

The cost of that choice, stated out loud: **a session that never ran
`hcom start` is invisible here.** That is not a bug this module can fix by
reading harder, and it must not be papered over by also globbing the transcript
directories, because a row that says "running" with no way to reach it is worse
than no row. `state` says `unavailable` when hcom is absent, and the count of
what hcom can see is never presented as the count of what is running.

WHAT REACHES THE AGENT
----------------------
`hcom send`, and only that. A message lands in the agent's queue and it reads it
at its next hook, the same way another agent's message would. It is a message,
so it is refusable, orderable and logged.

`hcom term inject` is the other option and it is deliberately not wired up: it
types keystrokes into a live PTY and presses return, so a message that arrives
while the agent is mid-prompt is submitted into whatever was half-typed there.
The screen dump is read here, because looking is free; the typing is not.

DIRECTORY IS THE JOIN, AND IT IS RESOLVED LOCALLY
-------------------------------------------------
hcom knows a session's working directory. The office knows desks by
`owner/name`. Nothing knows both, so the join is `git remote get-url origin` in
that directory, cached for the life of the process: it is a local file read, it
needs no network and no token, and it is the same answer dispatch.sh derives.

A directory that is not a git repo, or whose origin is not GitHub, still gets a
row. It just has no desk, which is a true and useful thing to say about a
session running in a scratch folder.

NOTHING HERE BLOCKS THE DOOR
----------------------------
Every subprocess is a local call to a binary that answers in well under a second,
and every one of them is capped. A hung hcom is reported as its own state and
never as an empty office.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone

# The agent name is a shell-adjacent identifier that reaches argv, so it is held
# to the smallest alphabet hcom actually issues: hcom names are lowercase words,
# optionally tagged `tag-name`, optionally suffixed `:DEVICE` for a remote peer.
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}(:[A-Za-z0-9_-]{1,32})?\Z", re.ASCII)
NWO_RE = re.compile(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+\Z", re.ASCII)

# The two engines this office knows how to start. Not a list that grows by
# accident: each one is a real program that will run with Aria's credentials.
TOOLS = ("claude", "codex")

MAX_MESSAGE = 8000
MAX_PROMPT = 8000
# The transcript view is a conversation, not an archive. Ten exchanges is what
# `hcom transcript` itself defaults to and it is about a screen.
DEFAULT_EXCHANGES = 10
MAX_EXCHANGES = 50

# Every one of these is a local call. The list is the one on the hot path (it is
# read on every poll), so it gets the tightest cap.
LIST_TIMEOUT_S = 8
TRANSCRIPT_TIMEOUT_S = 20
SCREEN_TIMEOUT_S = 10
SEND_TIMEOUT_S = 15
# Launching starts a background agent and waits for hcom to report ready.
# Longer than the rest, and still a cap: a launch that hangs is a failed launch.
START_TIMEOUT_S = 90
GIT_TIMEOUT_S = 5

NO_HCOM = "hcom is not installed, so the office cannot see or reach any session"
BAD_NAME = "bad session name"
BAD_TOOL = "the engine is claude or codex"
BAD_DIR = "no such directory"
NO_MESSAGE = "a message is required"
LONG_MESSAGE = f"a message is at most {MAX_MESSAGE} characters"
LONG_PROMPT = f"a prompt is at most {MAX_PROMPT} characters"

# The statuses hcom reports, in the order a person cares about them. `blocked`
# is first because it is the only one that is definitely waiting on a human.
STATUS_ORDER = {"blocked": 0, "listening": 1, "active": 2, "unknown": 3, "inactive": 4}

# A status that means the agent will read a message. `inactive` will not, and
# saying so before the message is sent is the difference between a reply and a
# message dropped into a dead mailbox.
REACHABLE = ("active", "listening", "blocked", "unknown")

_origin_cache: dict[str, str] = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hcom_bin() -> str | None:
    """Where hcom is, or None. Looked up at call time, never at import: a test
    points this at a stub by assigning the module's PATH, and an import-time
    copy would keep finding the real one."""
    return shutil.which("hcom")


def _run(args: list[str], timeout: float,
         env: dict[str, str] | None = None) -> tuple[int, str, str]:
    """A local binary, capped, never through a shell.

    Returns (rc, stdout, stderr). A binary that is not there or does not answer
    is a non-zero rc with the reason in stderr, so no caller has to catch.
    """
    try:
        # stdin is closed, never inherited. This runs inside a long-lived server
        # whose own stdin is whatever launched it, and a child that reads from it
        # blocks forever holding a request open: the timeout above is a cap on a
        # slow answer, not a cure for a process waiting on a terminal nobody is
        # typing at.
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, env=env)
    except subprocess.TimeoutExpired:
        return 124, "", f"{args[0]} did not answer in {timeout:g}s"
    except OSError as exc:
        return 127, "", f"{type(exc).__name__}: {exc}"[:200]
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def check_name(name) -> str | None:
    """The reason this name is refused, or None. Called before the name is
    allowed anywhere near argv."""
    if not isinstance(name, str) or not NAME_RE.match(name.strip()):
        return BAD_NAME
    return None


def origin_nwo(directory: str) -> str:
    """`owner/name` for the GitHub remote of a directory, or "".

    Cached per process. The cache is keyed on the directory string rather than
    the resolved path on purpose: a session's directory is what hcom reported,
    and that is the string a person will read back on the row.
    """
    d = str(directory or "").strip()
    if not d:
        return ""
    if d in _origin_cache:
        return _origin_cache[d]
    nwo = ""
    if os.path.isdir(d):
        rc, out, _ = _run(["git", "-C", d, "remote", "get-url", "origin"], GIT_TIMEOUT_S)
        if rc == 0:
            nwo = parse_origin(out.strip())
    _origin_cache[d] = nwo
    return nwo


def parse_origin(url: str) -> str:
    """`owner/name` out of any of the shapes a GitHub remote comes in.

    ssh, ssh with a host alias (the pipeline's identity trick writes those), and
    https. Anything else is "": a remote that is not GitHub has no desk here, and
    guessing one would put a session on a desk that is not its own.
    """
    u = str(url or "").strip()
    if not u:
        return ""
    u = u[:-4] if u.endswith(".git") else u
    # git@github.com:owner/name and git@github-work:owner/name alike
    if ":" in u and not u.startswith(("http://", "https://")):
        host, _, path = u.rpartition(":")
        if "github" not in host.lower():
            return ""
        return path if NWO_RE.match(path) else ""
    if u.startswith(("http://", "https://")):
        rest = u.split("://", 1)[1]
        host, _, path = rest.partition("/")
        if "github" not in host.lower():
            return ""
        return path if NWO_RE.match(path) else ""
    return ""


def _age(created_at) -> float | None:
    try:
        return max(0.0, time.time() - float(created_at))
    except (TypeError, ValueError):
        return None


def _row(agent: dict) -> dict:
    """One hcom agent as the office reads it.

    Every field the room draws is present on every row, including the ones that
    could not be worked out, for the reason sections.py gives: a consumer must
    never have to decide whether a missing key means false or means nobody
    looked.
    """
    name = str(agent.get("name") or "").strip()
    directory = str(agent.get("directory") or "").strip()
    status = str(agent.get("status") or "unknown").strip().lower()
    tool = str(agent.get("tool") or "").strip().lower()
    return {
        "name": name,
        "tool": tool,
        "status": status,
        # hcom's own one-liner: "active: Bash", "inactive: stale". It is the
        # closest thing to "what is it doing right now" that costs nothing.
        "doing": str(agent.get("description") or "").strip()[:200],
        # The actual tool call under way, when there is one. Long, and clipped
        # hard: a bash heredoc in here would be the whole card.
        "detail": str(agent.get("status_detail") or "").strip()[:300],
        "directory": directory,
        "repo": origin_nwo(directory),
        "branch": str(((agent.get("launch_context") or {}).get("git_branch")) or "").strip(),
        "unread": int(agent.get("unread_count") or 0),
        "headless": bool(agent.get("headless")),
        # Whether a message sent now would ever be read. Said on the row rather
        # than left for the sender to infer from a status word.
        "reachable": status in REACHABLE,
        "session_id": str(agent.get("session_id") or ""),
        "age_s": (round(agent["status_age_seconds"])
                  if isinstance(agent.get("status_age_seconds"), (int, float)) else None),
        "started_at": (datetime.fromtimestamp(float(agent["created_at"]), timezone.utc)
                       .strftime("%Y-%m-%dT%H:%M:%SZ")
                       if isinstance(agent.get("created_at"), (int, float)) else ""),
    }


def _sorted(rows: list) -> list:
    """Blocked first, then listening, then active, then the dead. Within a
    status, the one that changed most recently."""
    return sorted(rows, key=lambda r: (STATUS_ORDER.get(r["status"], 3),
                                       r["age_s"] if r["age_s"] is not None else 1 << 30,
                                       r["name"]))


def read(repo: str = "") -> dict:
    """Every session hcom can see, optionally only the ones on one desk.

    States, each its own thing and never rendered the same:

      unavailable  hcom is not installed. Nothing can be seen or reached.
      unreadable   hcom is there and did not answer, or answered with
                   something that is not JSON. A tool that broke, not a quiet
                   machine.
      empty        hcom answered, and there are no sessions. A real answer.
      ok           there are sessions.
    """
    binary = hcom_bin()
    if not binary:
        return {"state": "unavailable", "detail": NO_HCOM, "sessions": [],
                "live": 0, "blocked": 0, "at": now_iso()}

    rc, out, err = _run([binary, "list", "--json"], LIST_TIMEOUT_S)
    if rc != 0 and not (out or "").strip():
        detail = (err or "hcom list failed").strip().replace("\n", " ")[:200]
        return {"state": "unreadable", "detail": detail, "sessions": [],
                "live": 0, "blocked": 0, "at": now_iso()}
    try:
        agents = json.loads(out or "[]")
    except json.JSONDecodeError:
        return {"state": "unreadable", "sessions": [], "live": 0, "blocked": 0,
                "at": now_iso(),
                "detail": "hcom list --json printed something that is not JSON"}
    if not isinstance(agents, list):
        agents = []

    rows = [_row(a) for a in agents if isinstance(a, dict) and a.get("name")]
    want = str(repo or "").strip()
    if want:
        rows = [r for r in rows if r["repo"] == want]

    rows = _sorted(rows)
    live = sum(1 for r in rows if r["status"] in ("active", "listening", "blocked"))
    blocked = sum(1 for r in rows if r["status"] == "blocked")
    return {"state": "ok" if rows else "empty", "detail": "", "sessions": rows,
            "live": live, "blocked": blocked, "at": now_iso()}


def by_desk() -> dict:
    """{nwo: [name, ...]} for every session that sits in a repo checkout.

    The roster reads this to put a count on a desk. Sessions with no GitHub
    remote are absent by construction rather than gathered under a blank key: a
    desk that is not a desk is not a desk.
    """
    out: dict[str, list] = {}
    for row in read().get("sessions") or []:
        if row["repo"] and row["status"] != "inactive":
            out.setdefault(row["repo"], []).append(row["name"])
    return out


def transcript(name: str, last: int = DEFAULT_EXCHANGES) -> tuple[int, dict]:
    """The conversation, most recent last. (http status, body).

    `--full` is not passed. An exchange in this view is a thing you read on a
    phone to decide whether to answer; the whole assistant turn is a terminal's
    job.
    """
    why = check_name(name)
    if why:
        return 400, {"error": why}
    binary = hcom_bin()
    if not binary:
        return 503, {"error": NO_HCOM}
    try:
        n = max(1, min(MAX_EXCHANGES, int(last)))
    except (TypeError, ValueError):
        n = DEFAULT_EXCHANGES

    rc, out, err = _run([binary, "transcript", name.strip(), "--last", str(n), "--json"],
                        TRANSCRIPT_TIMEOUT_S)
    if rc != 0 and not (out or "").strip():
        return 502, {"error": (err or "hcom transcript failed").strip()[:200]}
    try:
        rows = json.loads(out or "[]")
    except json.JSONDecodeError:
        return 502, {"error": "hcom transcript printed something that is not JSON"}
    if not isinstance(rows, list):
        rows = []

    exchanges = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        exchanges.append({
            "position": row.get("position"),
            "at": str(row.get("timestamp") or ""),
            "you": str(row.get("user") or ""),
            "them": str(row.get("action") or ""),
            "files": [str(f) for f in (row.get("files") or []) if isinstance(f, str)][:20],
        })
    return 200, {"name": name.strip(), "exchanges": exchanges, "at": now_iso()}


def screen(name: str) -> tuple[int, dict]:
    """What is on that agent's terminal right now, as lines.

    Read-only, always. This module never injects: see the header for why.
    `ready` and `prompt_empty` come straight from hcom and are the honest answer
    to "could I type into this", which is a different question from whether the
    office will.
    """
    why = check_name(name)
    if why:
        return 400, {"error": why}
    binary = hcom_bin()
    if not binary:
        return 503, {"error": NO_HCOM}

    rc, out, err = _run([binary, "term", name.strip(), "--json"], SCREEN_TIMEOUT_S)
    if rc != 0 and not (out or "").strip():
        return 502, {"error": (err or "hcom term failed").strip()[:200]}
    try:
        data = json.loads(out or "{}")
    except json.JSONDecodeError:
        return 502, {"error": "hcom term printed something that is not JSON"}
    if not isinstance(data, dict):
        data = {}
    lines = [str(l) for l in (data.get("lines") or []) if isinstance(l, str)]
    return 200, {"name": name.strip(), "lines": lines[-200:],
                 "ready": bool(data.get("ready")),
                 "prompt_empty": bool(data.get("prompt_empty")),
                 "at": now_iso()}


def say(body: dict) -> tuple[int, dict]:
    """Send a message to a running session. (http status, body).

    This is the whole of "responding without opening the terminal", and it is a
    message rather than a keystroke on purpose. The agent reads it at its next
    hook, which means an agent mid-tool-call gets it when that call ends rather
    than into the middle of it.

    A message to an agent hcom calls `inactive` is refused rather than sent.
    hcom would accept it and nothing would ever read it, and a reply that
    silently goes nowhere is the exact false-green this office exists to kill.
    """
    if not isinstance(body, dict):
        return 400, {"error": "expected an object"}
    # Checked BEFORE coercion, on purpose: `str(7)` is a perfectly valid name
    # shape, so coercing first would turn a malformed body into a lookup for a
    # session called "7" and report the wrong reason for the refusal.
    why = check_name(body.get("name"))
    if why:
        return 400, {"error": why}
    name = str(body.get("name")).strip()
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        return 400, {"error": NO_MESSAGE}
    text = text.strip()
    if len(text) > MAX_MESSAGE:
        return 400, {"error": LONG_MESSAGE}

    binary = hcom_bin()
    if not binary:
        return 503, {"error": NO_HCOM}

    known = {r["name"]: r for r in (read().get("sessions") or [])}
    row = known.get(name)
    if row is None:
        return 404, {"error": f"hcom does not know a session called {name}"}
    if not row["reachable"]:
        return 409, {"error": f"{name} is {row['status']}, so it would never read this"}

    # The message reaches hcom on stdin, never in argv: it is Aria's prose, it
    # can contain anything, and argv is world-readable through `ps`.
    try:
        proc = subprocess.run([binary, "send", "@" + name],
                              input=text, capture_output=True, text=True,
                              timeout=SEND_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return 504, {"error": f"hcom send did not answer in {SEND_TIMEOUT_S}s"}
    except OSError as exc:
        return 502, {"error": f"{type(exc).__name__}: {exc}"[:200]}
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "hcom send failed").strip()
        return 502, {"error": detail.replace("\n", " ")[:200]}

    return 200, {"ok": True, "name": name, "sent_at": now_iso(),
                 "result": (proc.stdout or "").strip()[:200]}


def start(body: dict) -> tuple[int, dict]:
    """Start a new Claude Code or Codex session in a desk's folder.

    This spawns a real agent with Aria's credentials from a button, so the two
    things it will not do are the two that matter:

      **The engine is one of two names**, matched exactly, never interpolated.
      **The directory is one hcom already runs something in, or one under the
      vault root.** A path from a request body is a path an attacker would like
      to choose, and `_write_ok` on the door is not the last line of defence for
      something that executes.

    It returns as soon as hcom does. hcom's own exit code says whether the agent
    came up, and exit 2 ("still launching") is reported as accepted rather than
    as failure: a terminal that is still opening has not failed.
    """
    if not isinstance(body, dict):
        return 400, {"error": "expected an object"}
    tool = str(body.get("tool") or "").strip().lower()
    if tool not in TOOLS:
        return 400, {"error": BAD_TOOL}

    directory, why = resolve_dir(body)
    if why:
        return 400, {"error": why}

    prompt = body.get("prompt")
    if prompt is not None and not isinstance(prompt, str):
        return 400, {"error": "a prompt is text"}
    prompt = (prompt or "").strip()
    if len(prompt) > MAX_PROMPT:
        return 400, {"error": LONG_PROMPT}

    binary = hcom_bin()
    if not binary:
        return 503, {"error": NO_HCOM}

    # A normal hcom terminal launch owns this subprocess until the terminal
    # closes. That makes the Office request time out even after the agent is
    # ready. Headless is hcom's background mode: it returns after readiness and
    # the agent remains reachable here, which is the point of starting it from
    # the phone instead of walking back to the Mac.
    args = [binary, tool, "--headless", "--dir", directory]
    if prompt:
        args += ["--hcom-prompt", prompt]
    rc, out, err = _run(args, START_TIMEOUT_S,
                        env=_launch_env(tool, directory))
    text = ((out or "") + "\n" + (err or "")).strip().replace("\n", " ")[:300]
    # 0 ready, 2 still coming up. Both are a window that opened.
    if rc in (0, 2):
        return 200, {"ok": True, "tool": tool, "directory": directory,
                     "starting": rc == 2, "result": text, "at": now_iso()}
    return 502, {"error": text or f"hcom {tool} exited {rc}"}


def _launch_env(tool: str, directory: str) -> dict[str, str] | None:
    """Select the locally trusted Claude account for an Office launch.

    The normal shell function routes folders under the Thinking Brain School
    workspace to its separate Claude profile. The Office daemon does not run an
    interactive zsh, so it cannot see that function. Reproduce the same fixed
    path rule here. Nothing from the request chooses a profile: the directory
    has already passed ``resolve_dir`` and the account root comes from the
    daemon's trusted runtime root.

    Returning None for every other launch preserves the daemon's environment,
    including Codex and personal Claude sessions.
    """
    if tool != "claude":
        return None
    paths = _tbs_account_paths()
    if not paths:
        return None
    tbs_root, profile = paths
    try:
        pathlib.Path(directory).resolve().relative_to(tbs_root)
    except (OSError, ValueError):
        return None
    if not profile.is_dir():
        return None
    env = os.environ.copy()
    env["CLAUDE_CONFIG_DIR"] = str(profile)
    return env


def _tbs_account_paths() -> tuple[pathlib.Path, pathlib.Path] | None:
    """Trusted workspace and profile paths, derived only from daemon config."""
    runtime = os.environ.get("OFFICE_RUNTIME_ROOT", "").strip()
    if not runtime:
        return None
    try:
        root = (pathlib.Path(runtime).expanduser().resolve() / "CodingVault" /
                "thinking-brain-school").resolve()
        return root, (root / ".claude-tbs-account").resolve()
    except OSError:
        return None


def _is_credential_dir(directory: str) -> bool:
    """A credential profile is never a desk, including through a symlink."""
    paths = _tbs_account_paths()
    if not paths:
        return False
    _, profile = paths
    try:
        pathlib.Path(directory).resolve().relative_to(profile)
        return True
    except (OSError, ValueError):
        return False


def resolve_dir(body: dict) -> tuple[str, str | None]:
    """(directory, the reason it was refused). Exactly one is falsy.

    A caller may name a desk or a path. A desk is preferred, because it is the
    office's own noun and it cannot name a folder the office does not already
    know about. A path is allowed and then checked against two things it must be
    one of, because the alternative is an endpoint that runs an agent anywhere on
    the disk.
    """
    repo = str(body.get("repo") or "").strip()
    directory = str(body.get("directory") or "").strip()

    if repo:
        if not NWO_RE.match(repo):
            return "", "bad repo"
        found = desk_dir(repo)
        if not found:
            return "", f"the office does not know where {repo} is checked out"
        if _is_credential_dir(found):
            return "", "agent credential folders are not desks"
        return found, None

    if not directory:
        return "", "name a repo or a directory"
    path = pathlib.Path(directory).expanduser()
    if not path.is_dir():
        return "", BAD_DIR
    resolved = str(path.resolve())
    if _is_credential_dir(resolved):
        return "", "agent credential folders are not desks"
    if not _allowed_dir(resolved):
        return "", "that directory is not a desk and is not under the vault"
    return resolved, None


def _allowed_dir(resolved: str) -> bool:
    """Under the vault root, or somewhere hcom already runs something.

    Two allowances rather than one, because the second is what makes this useful
    for a scratch checkout outside the vault, and it is safe for the reason that
    makes it useful: an agent is already running there, so this grants no reach
    the machine did not already have.
    """
    root = os.environ.get("OFFICE_RUNTIME_ROOT", "").strip()
    if root:
        try:
            rp = str(pathlib.Path(root).expanduser().resolve())
            if resolved == rp or resolved.startswith(rp + os.sep):
                return True
        except OSError:
            pass
    for row in read().get("sessions") or []:
        try:
            if row["directory"] and str(pathlib.Path(row["directory"]).resolve()) == resolved:
                return True
        except OSError:
            continue
    return False


NO_VAULT = ("the office has no vault to look in: OFFICE_RUNTIME_ROOT is not set "
            "on the process serving this door")


def no_vault() -> bool:
    """Whether this process was given a vault to walk at all.

    A desk that is not checked out and a door that was never told where any
    checkout lives both come out of `desk_dir` as "". They are not the same
    fact: the first is about one repo, the second is about every desk at once
    and is fixed by restarting the door with `--root`. Saying the first when the
    second is true sends a person looking at the wrong machine.
    """
    return not os.environ.get("OFFICE_RUNTIME_ROOT", "").strip()


def desk_dir(repo: str) -> str:
    """Where a desk is checked out on this machine, or "".

    Found the way everything else here is found: from what is actually on disk.
    A session already running in that repo names its directory outright; failing
    that, the vault is walked and every checkout in it is asked for its origin.
    No list, and no guessing a path from the repo name: a folder that is not
    there must read as "not checked out", never as a path that will fail later.
    """
    for row in read().get("sessions") or []:
        if row["repo"] == repo and row["directory"]:
            return row["directory"]

    root = os.environ.get("OFFICE_RUNTIME_ROOT", "").strip()
    if not root:
        return ""
    name = repo.split("/", 1)[1] if "/" in repo else repo
    base = pathlib.Path(root).expanduser()
    # The vault itself, before anything under it. `_is_checkout_of` still asks
    # git for the origin, so this reaches no directory the walk below was not
    # already allowed to reach; it just stops the one directory that holds the
    # doctrine from being the one directory the office cannot place.
    if _is_checkout_of(base, repo):
        return str(base.resolve())
    for vault in ("CodingVault", "CollabVault", ""):
        candidate = (base / vault / name) if vault else (base / name)
        if _is_checkout_of(candidate, repo):
            return str(candidate.resolve())

    # The folder is not always named after the repo (`ariacam` is `aria-cam`),
    # and it is not always one level down: `jessstrom/matra` lives three deep,
    # in `CodingVault/blinkbuild/matra-suite/matra`, because a suite repo is a
    # checkout that contains other checkouts. So the vault is walked to a bounded
    # depth and every checkout in it is asked for its origin, including the ones
    # inside another checkout. The origin is still the only thing that decides,
    # so this reaches no folder the name guess was not already allowed to reach.
    for found_repo, found_dir in _checkouts(base).items():
        if found_repo == repo:
            return found_dir
    return ""


# Deep enough for a checkout inside a suite repo inside a vault folder, and no
# deeper: past this every hit is somebody's vendored dependency, and the walk
# starts costing more than the answer is worth.
WALK_DEPTH = 4
# Folders that never hold a desk and always hold thousands of files.
WALK_SKIP = {"node_modules", "_archive", "venv", "build", "dist", "Pods",
             "DerivedData", "target", "vendor", "__pycache__"}
# The walk costs about a second over a hundred checkouts, so it is cached, but
# not for the life of the process: a repo cloned after the door started must
# become findable without a restart, and two minutes is short enough that
# nobody notices and long enough that a pane opening does not re-walk the disk.
WALK_TTL_S = 120
_checkout_cache: dict[str, tuple[float, dict[str, str]]] = {}


def _checkouts(base: pathlib.Path) -> dict[str, str]:
    """Every checkout under `base`, as {owner/name: directory}.

    Shallowest wins on a tie: two clones of one repo is a person's scratch copy
    next to the real one, and the real one is the one nearer the top.
    """
    key = str(base)
    cached = _checkout_cache.get(key)
    if cached and (time.time() - cached[0]) < WALK_TTL_S:
        return cached[1]
    found: dict[str, str] = {}
    # The vault root is itself a checkout, and the walk below only ever looks at
    # the folders UNDER the directory it was given, so it could never see it.
    # That is the whole reason "the office does not know where ariaxhan/Vaults
    # is checked out" kept coming back: every previous fix added another name
    # guess at a call site, and none of them changed the one function that
    # decides where a desk is. Asked first, so a nested clone of the same repo
    # cannot outrank the real one.
    own = origin_nwo(str(base))
    if own:
        try:
            found[own] = str(base.resolve())
        except (OSError, RuntimeError):
            pass
    stack: list[tuple[pathlib.Path, int]] = [(base, 0)]
    while stack:
        folder, depth = stack.pop(0)
        if depth > WALK_DEPTH:
            continue
        try:
            children = sorted(folder.iterdir())
        except OSError:
            continue
        for candidate in children:
            name = candidate.name
            if name.startswith(".") or name in WALK_SKIP:
                continue
            try:
                if not candidate.is_dir():
                    continue
            except OSError:
                continue
            if (candidate / ".git").exists():
                nwo = origin_nwo(str(candidate))
                if nwo and nwo not in found:
                    found[nwo] = str(candidate.resolve())
            stack.append((candidate, depth + 1))
    _checkout_cache[key] = (time.time(), found)
    return found


def _is_checkout_of(candidate: pathlib.Path, repo: str) -> bool:
    """Whether this directory is a checkout of `repo`, by its origin remote."""
    try:
        if not (candidate.is_dir() and (candidate / ".git").exists()):
            return False
    except OSError:
        return False
    return origin_nwo(str(candidate)) == repo


# ── the front page of a desk ─────────────────────────────────────────────────
# Read off this machine, never off GitHub. The office's GraphQL budget is 5000
# points an hour shared with the pipeline and with Aria's own `gh`, and a repo's
# README does not change often enough to be worth one of them.

README_NAMES = ("README.md", "readme.md", "README.markdown", "README.txt", "README")
# A README is a page a person reads, not an archive. Anything past this is a
# repo shipping its documentation as one file, and the rest is a scroll nobody
# finishes.
MAX_README = 64_000


def readme(repo: str) -> tuple[int, dict]:
    """One desk's README. (http status, body).

    Nothing a caller sends becomes part of a path: the repo is matched against
    `NWO_RE`, the folder is one `desk_dir` already vouched for, and the filename
    is one of a fixed few.

    The three ways there is no text are three different sentences, because a
    desk that is not checked out on this machine, a repo that has no README, and
    a file that would not open are three different facts, and drawing any of
    them as an empty pane is the blank screen this route exists to end.
    """
    repo = str(repo or "").strip()
    # `NWO_RE` allows dots, so `../..` is a name it accepts. Nothing downstream
    # would build a path out of it (a desk is looked up, never derived), but a
    # route that reads files refuses a dot segment where it can see one rather
    # than relying on the next function along to keep being careful.
    if not NWO_RE.match(repo) or any(part in (".", "..") for part in repo.split("/")):
        return 400, {"error": "bad repo"}
    directory = desk_dir(repo)
    if not directory:
        return 200, {"repo": repo, "state": "elsewhere", "text": "",
                     "detail": NO_VAULT if no_vault()
                     else f"{repo} is not checked out on this machine"}

    base = pathlib.Path(directory)
    try:
        root = base.resolve()
    except OSError:
        root = base
    for name in README_NAMES:
        candidate = base / name
        try:
            if not candidate.is_file():
                continue
            # `is_file()` follows a symlink, so a checkout carrying
            # `README.md -> /etc/passwd` would be served over the door. The name
            # check above is about what a caller can send; this is about what a
            # folder on this machine can point at, which is a different vector
            # and needs its own answer.
            if not candidate.resolve().is_relative_to(root):
                return 200, {"repo": repo, "state": "unreadable", "text": "", "name": name,
                             "detail": f"{name} is a link out of this checkout"}
            text = candidate.read_text(encoding="utf-8", errors="replace")
            at = datetime.fromtimestamp(candidate.stat().st_mtime, timezone.utc)
        except OSError as exc:  # noqa: BLE001 - a README that will not open is not a dead door
            return 200, {"repo": repo, "state": "unreadable", "text": "", "name": name,
                         "detail": f"could not read {name}: {exc}"[:200]}
        return 200, {"repo": repo, "state": "ok", "name": name,
                     "text": text[:MAX_README], "clipped": len(text) > MAX_README,
                     "at": at.strftime("%Y-%m-%dT%H:%M:%SZ")}

    return 200, {"repo": repo, "state": "none", "text": "",
                 "detail": "there is no README in this checkout"}
