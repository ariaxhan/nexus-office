#!/usr/bin/env python3
"""GitHub webhooks, arriving on the one public path this machine exposes.

The office polls GitHub every five minutes because that is what a GraphQL budget
of 5000 points an hour buys. Five minutes is fine for a room you glance at and
useless for the two moments that actually want a person: somebody just commented
on an issue the bot is sitting on, and a PR just merged. This is the other half:
GitHub tells us, instead of us asking.

WHAT IS PUBLIC, AND WHAT IS NOT
-------------------------------
Tailscale Funnel puts exactly one path on the open internet: `POST /webhook`.
Everything else on the door stays tailnet-only behind `Tailscale-User-Login`,
and loopback stays this machine. So `/webhook` is the ONE route that cannot
pass `_identity_ok` (Funnel traffic carries no tailnet login: the whole point
of Funnel is that the sender is a stranger) and cannot pass `_write_ok` (GitHub
sends no `Origin` and never will).

What replaces those two checks is stronger than either: an HMAC-SHA256 over the
RAW REQUEST BYTES, compared in constant time. `raw` is not a detail. Re-serialising
the parsed JSON and signing that is the classic way to make a signature check
that passes for a body nobody sent, because two different byte strings can parse
to the same object and only one of them was signed.

Unsigned is never accepted. With no `OFFICE_WEBHOOK_SECRET` the route answers
503 and does nothing, because a receiver that falls back to "no secret, no
check" is a public endpoint that runs your pipeline for anyone who finds it.

THE FEEDBACK LOOP THIS EXISTS TO NOT HAVE
-----------------------------------------
The pipeline comments on issues. A comment is a webhook. A webhook triggers the
pipeline. That is a machine talking to itself forever, at whatever rate GitHub
will deliver. So `should_trigger` refuses twice over: a comment or issue written
by one of OUR logins never triggers, and neither does one carrying the bot's own
marker. Both, because either alone has a hole: the marker misses a bare comment,
and the login list misses a bot whose token we do not know about.

Note what is deliberately NOT suppressed: a `pull_request` event. The office
merges PRs as one of our own logins, so suppressing our logins there would mean
the one event we most want to hear about (the merge) is the one we drop.

AT-LEAST-ONCE, AND THE REDELIVER BUTTON
--------------------------------------
GitHub retries on any non-2xx and gives the request 10 seconds, so the door
answers 200 before it does anything and remembers what it handled on a bounded
on-disk set. Delivery ids are the only dedup key that survives a restart.

The subtlety is the redeliver button. `X-GitHub-Delivery` is the SAME id on a
manual redelivery, so a set that drops everything it has seen turns the one
recovery control a person has into a no-op. What is stored is therefore the
id WITH ITS OUTCOME, and only an accepted one is a duplicate: a delivery that
was refused, or that was never finished, comes back and is handled fresh.

Refusals are deliberately not written down. The set is bounded, so an unsigned
poster who could put entries in it could evict the real ones and make GitHub's
next redelivery run everything twice. Absence IS the record of a refusal.

  webhook-seen.json    the last 2000 accepted deliveries, written whole
  webhook-events.jsonl every parsed event, trimmed to the last 5000 lines
  webhook-runs.jsonl   every dispatch this triggered, with its exit code

ONE DRAINER, NEVER A POOL
-------------------------
`dispatch.sh` takes ONE global lock for the whole pipeline, not one per repo
(`LOCK="$STATE/pid"`, dispatch.sh:77-78), and a second run finding it held says
so and EXITS 0 (dispatch.sh:472-476). Exit zero, no queue, nothing done. So
running several at once does not parallelise anything; it silently drops every
event but the first, wearing a green exit code.

This is therefore a mailbox with a single serial drainer. Before spawning it
reads that pidfile, and after a run it checks the tell of a silent no-op (exit
0, under two seconds, lock still held by another pid) and puts the repo back for
the next drain rather than believing it.

Configuration:

  OFFICE_WEBHOOK_SECRET      the shared secret GitHub signs with (required)
  OFFICE_TRIGGER_DEBOUNCE_S  collect a repo's events for this long (default 20)
  OFFICE_TRIGGER_REQUEUE_S   wait this long after finding the pipeline busy (60)
  OFFICE_DISPATCH            the runner script (default: under the runtime root)
  OFFICE_STATE               where the three files above live
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import os
import pathlib
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

ISO = "%Y-%m-%dT%H:%M:%SZ"


def _env_path(name: str):
    v = os.environ.get(name, "").strip()
    return pathlib.Path(v).expanduser() if v else None


# The same directory office-sync.py keeps its desk cache in. A module constant
# rather than a call so a test can point it somewhere disposable by assignment,
# which is how every other state path in this project is redirected.
STATE = _env_path("OFFICE_STATE") or (pathlib.Path.home() / ".local/state/nexus-office")

# The shared secret GitHub signs with. It lives HERE, in the module that does the
# verifying, and every other reader asks this one: the door, the status route and
# the card on the wall all have to agree about whether webhooks are configured,
# and two of them reading the environment separately is two answers waiting to
# disagree. Empty means the route answers 503; it never means "skip the check".
SECRET = os.environ.get("OFFICE_WEBHOOK_SECRET", "").strip().encode()

# The marker the pipeline leaves on its own words. Read exactly the way
# office-sync.py reads it, and it must stay that way: this file and that one are
# both deciding "did the bot say this", and two different answers to that
# question is the feedback loop.
BOT_MARKER = os.environ.get("OFFICE_MARKER", "pipeline-bot")

# What GitHub sends and we know what to do with. Anything else is delivered,
# logged and dropped: a webhook we do not understand is not an error, it is a
# subscription somebody widened.
KINDS = ("issues", "issue_comment", "pull_request", "ping")

# Only these four move a pull request somewhere worth looking. `labeled`,
# `assigned`, `review_requested` and the rest are bookkeeping, and running the
# pipeline on them would spend a dispatch on somebody tidying a board.
PR_ACTIONS = {"closed", "opened", "reopened", "synchronize"}

# owner/name, and nothing that could be a path or a second query. Same shape
# office-sync.py's NWO_RE enforces, restated here so this file can refuse a
# malformed repo without importing the module that fetches.
NWO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z", re.ASCII)

# The issue a PR says it closes. GitHub's own keywords, the same three
# office-sync.py reads off a PR body, because both are answering "which issue
# does merging this finish".
CLOSES_RE = re.compile(r"(?:closes|fixes|resolves)\s+#(\d+)", re.I)

SEEN_FILE = "webhook-seen.json"
EVENTS_FILE = "webhook-events.jsonl"
RUNS_FILE = "webhook-runs.jsonl"

# Bounds, so three append-only files cannot become a disk problem. The seen set
# is the one that matters: it is read on every delivery, so it is held in memory
# and only ever written whole.
SEEN_MAX = 2000
EVENTS_KEEP = 5000
EVENTS_MAX = 6000

DEBOUNCE_S = float(os.environ.get("OFFICE_TRIGGER_DEBOUNCE_S", "") or 20)
# How long to wait before trying a repo again after finding the pipeline's one
# global lock held. Not a retry storm: the run holding it takes minutes, so
# asking again in a minute costs nothing and asking again in a second is noise.
REQUEUE_S = float(os.environ.get("OFFICE_TRIGGER_REQUEUE_S", "") or 60)
# The tell of a run that did nothing. dispatch.sh's lock guard logs one line and
# exits 0, so a real sweep and a silent no-op differ only in how long they took
# and whether somebody else still holds the lock.
SILENT_EXIT_S = 2.0
# dispatch.sh:77-78. `PIPELINE_STATE_DIR` is its own override, honoured here so a
# test can point both at the same disposable place.
LOCK_REL = ".runtime/pid"
# A pipeline lane can genuinely take half an hour. Past that it is wedged, and a
# wedged run holding a slot forever is how a debounce turns into a queue nobody
# drains.
DISPATCH_TIMEOUT_S = 30 * 60
# How long the owner/name to local checkout map is trusted. Walking the vault
# costs a find and one `git config` per repo; a clone appearing is not something
# that needs to be noticed inside ten minutes.
MAP_TTL_S = 10 * 60
DISPATCH_REL = "_meta/services/issue-pipeline/dispatch.sh"
CONFIG_REL = "_meta/services/issue-pipeline/pipeline-config.py"
LOG_LINES = 20

# The Trigger this process is running, if it is running one. `sources/webhook.py`
# reads it to show what is waiting on a debounce, which is a fact that exists
# only in memory: a queue is by definition the thing that has not happened yet,
# so there is no file to read it out of.
RUNNING_TRIGGER = None

# Bad signatures with nothing valid since. A run of them is the shape of a
# secret rotated on one side only, which otherwise looks exactly like a quiet
# morning: every delivery refused, nothing arriving, nothing wrong on screen.
# Counted in memory rather than logged to a file, because an unsigned poster must
# not be able to grow a file on this disk by posting to a public path.
BAD_SIGNATURES = 0


def note_signature(ok: bool) -> int:
    """Fold one signature check into the run. Returns the run afterwards."""
    global BAD_SIGNATURES
    BAD_SIGNATURES = 0 if ok else BAD_SIGNATURES + 1
    return BAD_SIGNATURES


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime(ISO)


def log(msg: str) -> None:
    print(f"[webhook] {msg}", file=sys.stderr, flush=True)


# ── the signature ────────────────────────────────────────────────────────────

def verify(secret: bytes, raw_body: bytes, header: str) -> bool:
    """True when `header` is GitHub's signature over exactly these bytes.

    Over the RAW bytes, never over a re-serialised parse: `{"a":1}` and
    `{"a": 1}` are the same object and different messages, and only one of them
    was signed. Compared with `compare_digest`, because a byte-at-a-time `==` on
    a secret-derived value leaks where it stopped matching.
    """
    if not secret:
        return False
    got = (header or "").strip()
    if not got.startswith("sha256="):
        return False
    want = "sha256=" + hmac.new(bytes(secret), raw_body or b"", hashlib.sha256).hexdigest()
    return hmac.compare_digest(want.encode(), got.encode("utf-8", "replace"))


def sign(secret: bytes, raw_body: bytes) -> str:
    """The header GitHub would send for these bytes. Here so a test, a probe and
    a registration script all sign the one way rather than three."""
    return "sha256=" + hmac.new(bytes(secret), raw_body or b"", hashlib.sha256).hexdigest()


# ── what arrived ─────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class Event:
    """One delivery, reduced to the facts anything downstream acts on.

    Everything else in a GitHub payload is left behind on purpose. A webhook
    body is 30KB of things that will change; these ten fields are what the
    office decides with, and reducing at the door means a payload shape moving
    breaks the parse rather than something three files away.
    """

    delivery: str
    event: str
    action: str
    repo: str
    number: int | None
    login: str
    merged: bool
    at: str
    body_marker: bool
    # The issue this PR says it closes, when its body names one. Carried because
    # a merged PR's receipt is about the ISSUE that is now finished, and going
    # back to GitHub for a body we already had in the payload would be a request
    # spent on a fact we were handed.
    closes: int | None = None

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def _num(v):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def parse(event: str, delivery: str, body: dict) -> Event | None:
    """One delivery into an Event, or None when it is not ours to act on.

    None is not an error. It is "this subscription is wider than this code", and
    the door answers 200 to it exactly like anything else, because arguing with
    GitHub about what it sends costs a retry storm and changes nothing.
    """
    event = str(event or "").strip()
    if event not in KINDS or not isinstance(body, dict):
        return None

    repo = str(((body.get("repository") or {}) if isinstance(body.get("repository"), dict) else {}).get("full_name") or "")
    if event != "ping" and not NWO_RE.match(repo):
        # A repo name that is not owner/name reaches `gh` and the local repo map
        # later on. It stops here instead.
        return None

    action = str(body.get("action") or "")
    sender = body.get("sender") if isinstance(body.get("sender"), dict) else {}
    login = str((sender or {}).get("login") or "")

    issue = body.get("issue") if isinstance(body.get("issue"), dict) else {}
    comment = body.get("comment") if isinstance(body.get("comment"), dict) else {}
    pull = body.get("pull_request") if isinstance(body.get("pull_request"), dict) else {}

    number = None
    merged = False
    marker = False
    closes = None

    if event == "issue_comment":
        number = _num((issue or {}).get("number"))
        # The comment's author, not the sender, when the payload names one. They
        # are the same account in practice; when they are not, the words are the
        # thing being judged and the author wrote them.
        login = str(((comment or {}).get("user") or {}).get("login") or "") or login
        marker = BOT_MARKER in str((comment or {}).get("body") or "")
    elif event == "issues":
        number = _num((issue or {}).get("number"))
        marker = BOT_MARKER in str((issue or {}).get("body") or "")
    elif event == "pull_request":
        number = _num((pull or {}).get("number")) or _num(body.get("number"))
        merged = action == "closed" and bool((pull or {}).get("merged"))
        found = CLOSES_RE.findall(str((pull or {}).get("body") or ""))
        closes = _num(found[0]) if found else None
        # A PR body is written by the pipeline and says so. Reading the marker
        # off it would mean every pipeline PR merge is suppressed, which is the
        # one event this whole path exists to hear, so PR bodies are not read
        # for the marker at all.

    return Event(delivery=str(delivery or ""), event=event, action=action, repo=repo,
                 number=number, login=login, merged=merged, at=now_iso(),
                 body_marker=marker, closes=closes)


def should_trigger(ev, our_logins) -> bool:
    """Does this event mean the pipeline should look at that repo again?

    Two refusals matter more than the yes.

    A comment or issue written by one of our own logins, or carrying the bot's
    marker, never triggers. The pipeline comments; a comment is a webhook; a
    webhook would run the pipeline. That is a machine answering itself forever,
    and the debounce would not save it because each round genuinely is new work.

    A `ping` never triggers. It is GitHub saying hello when the hook is
    registered, and running the whole pipeline to say hello back is a strange
    way to find out the URL is right.
    """
    if ev is None or ev.event == "ping":
        return False
    if ev.event in ("issue_comment", "issues"):
        if ev.body_marker:
            return False
        mine = {str(l).strip().lower() for l in (our_logins or ()) if str(l).strip()}
        if ev.login and ev.login.strip().lower() in mine:
            return False
        return True
    if ev.event == "pull_request":
        return ev.action in PR_ACTIONS
    return False


# ── the mailbox ──────────────────────────────────────────────────────────────

class Mailbox:
    """Which deliveries have been handled, and what arrived.

    The seen set is the point of this class, and it stores an OUTCOME rather
    than a bare id. GitHub delivers at least once and retries anything that is
    not a 2xx, so "have I already run the pipeline for this delivery" has to
    survive a restart. But `X-GitHub-Delivery` is the same id when a person
    presses redeliver, so dropping everything already seen would disable the one
    recovery control there is. Only an ACCEPTED delivery is a duplicate.

    A refusal is stored nowhere on purpose. The set is bounded at 2000, so
    anything an unsigned poster could add to it, they could use to evict the
    real entries and make the next redelivery run everything twice.
    """

    OK = "ok"

    def __init__(self, state_dir):
        self.dir = pathlib.Path(state_dir)
        self.seen_path = self.dir / SEEN_FILE
        self.events_path = self.dir / EVENTS_FILE
        self.runs_path = self.dir / RUNS_FILE
        self.lock = threading.Lock()
        self._rows = None     # loaded on first use, oldest first
        self._out = None      # delivery id -> outcome
        self._inflight = set()

    # -- the seen set ------------------------------------------------------
    def _load(self) -> None:
        if self._rows is not None:
            return
        rows = []
        try:
            raw = json.loads(self.seen_path.read_text())
            got = raw.get("ids") if isinstance(raw, dict) else None
            for r in got or []:
                if isinstance(r, dict) and isinstance(r.get("id"), str):
                    rows.append({"id": r["id"], "outcome": str(r.get("outcome") or self.OK),
                                 "at": str(r.get("at") or "")})
        except Exception:
            # Unreadable reads as empty, which costs at most one repeated
            # dispatch. The other way round (refusing to work) costs every
            # delivery until somebody notices.
            rows = []
        self._rows = rows[-SEEN_MAX:]
        self._out = {r["id"]: r["outcome"] for r in self._rows}

    def outcome(self, delivery: str):
        """What happened to this delivery last time, or None if it is new."""
        if not delivery:
            return None
        with self.lock:
            self._load()
            return self._out.get(delivery)

    def seen(self, delivery: str) -> bool:
        """True only for a delivery that was ACCEPTED. A refused one is new
        again, which is what makes GitHub's redeliver button work."""
        return self.outcome(delivery) == self.OK

    def claim(self, delivery: str) -> bool:
        """True when this delivery is ours to handle now.

        False when it was already handled, or is being handled on another
        thread this instant. Two GitHub retries can land at once, and a check
        that is not also a claim would let both through.
        """
        if not delivery:
            return False
        with self.lock:
            self._load()
            if self._out.get(delivery) == self.OK or delivery in self._inflight:
                return False
            self._inflight.add(delivery)
            return True

    def settle(self, delivery: str, ok: bool = True) -> None:
        """Close a claim. `ok` writes it down; anything else releases it, so a
        redelivery of work that did not finish is handled fresh."""
        if not delivery:
            return
        with self.lock:
            self._load()
            self._inflight.discard(delivery)
            if not ok or self._out.get(delivery) == self.OK:
                return
            self._rows.append({"id": delivery, "outcome": self.OK, "at": now_iso()})
            self._out[delivery] = self.OK
            while len(self._rows) > SEEN_MAX:
                self._out.pop(self._rows.pop(0)["id"], None)
            self._write_seen()

    def remember(self, delivery: str) -> None:
        """Claim and settle in one move, for a caller with nothing to undo."""
        self.claim(delivery)
        self.settle(delivery, True)

    def _write_seen(self) -> None:
        """Whole file, then moved into place. A torn seen set reads as "nothing
        handled", which would re-run every delivery it could still see."""
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.seen_path.with_name(self.seen_path.name + ".tmp")
            tmp.write_text(json.dumps({"ids": self._rows}))
            tmp.replace(self.seen_path)
        except Exception as exc:  # noqa: BLE001 - a cache, not the delivery
            log(f"could not write the seen set: {exc}")

    def count(self) -> int:
        with self.lock:
            self._load()
            return len(self._rows)

    # -- the log -----------------------------------------------------------
    def append(self, ev, trigger: bool = False) -> None:
        row = ev.as_dict() if hasattr(ev, "as_dict") else dict(ev)
        row["trigger"] = bool(trigger)
        self._append(self.events_path, row)

    def record_run(self, row: dict) -> None:
        self._append(self.runs_path, dict(row))

    def _append(self, path: pathlib.Path, row: dict) -> None:
        with self.lock:
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row) + "\n")
                self._trim(path)
            except Exception as exc:  # noqa: BLE001 - a log, never the answer
                log(f"could not write {path.name}: {exc}")

    def _trim(self, path: pathlib.Path) -> None:
        """Rewrite at the ceiling, not at every line: trimming on each append
        would rewrite the whole file for every delivery."""
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        if len(lines) <= EVENTS_MAX:
            return
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text("\n".join(lines[-EVENTS_KEEP:]) + "\n", encoding="utf-8")
        tmp.replace(path)

    def tail(self, path: pathlib.Path, n: int = 20) -> list:
        rows = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in lines[-n:]:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
        return rows

    def last_events(self, n: int = 20) -> list:
        return self.tail(self.events_path, n)

    def last_runs(self, n: int = 20) -> list:
        return self.tail(self.runs_path, n)


# ── owner/name to a checkout on this machine ─────────────────────────────────
# Two halves, and each is taken from the place that already owns it.
#
# WHICH DIRECTORIES ARE REPOS: dispatch.sh's own walk, restated. Every `.git`
# under the vault, pruned so a gitdir inside another repo's `.git/` is
# unreachable, minus any checkout its own parent repo gitignores (vendored,
# cached, a managed clone). dispatch.sh:249-330.
#
# WHICH REPO A CHECKOUT IS: `pipeline-config.py origin <path>`, which is that
# subsystem's own answer to "where do this repo's issues live"
# (pipeline-config.py:12, 411-418). NOT parsed out of `--list-repos`: that
# prints a human report, and a parser over it has already dropped a repo
# silently once, when the status vocabulary grew `PARKED` mid-build.

# The fallback, for a machine with no pipeline installed. Same expression
# pipeline-config.py:146 uses, so the two cannot disagree about a URL shape:
#   git@github.com:o/n.git   https://github.com/o/n   ssh://git@github.com/o/n.git
_REMOTE_RE = re.compile(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?/?$")


def normalise_remote(url: str) -> str:
    """owner/name out of a remote URL, or "" when it is not a GitHub one.

    The host check is not decoration. This map answers "which checkout is the
    repo GitHub just told me about", and a GitLab remote at the same owner/name
    would answer it wrongly and run the pipeline against the wrong tree.
    """
    text = str(url or "").strip()
    if not text or "github.com" not in text.lower():
        return ""
    m = _REMOTE_RE.search(text)
    if not m:
        return ""
    nwo = f"{m.group(1)}/{m.group(2)}"
    return nwo if NWO_RE.match(nwo) else ""


def _git(args, cwd=None, timeout=15):
    try:
        p = subprocess.run(["git", *args], capture_output=True, text=True,
                           timeout=timeout, cwd=str(cwd) if cwd else None)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return p.returncode, p.stdout


def find_checkouts(root) -> list:
    """Every working tree under `root`, by dispatch.sh's own two skip rules."""
    base = pathlib.Path(root).expanduser()
    try:
        base = base.resolve()
    except OSError:
        return []
    if not base.is_dir():
        return []

    found = []
    for here, dirs, _files in os.walk(base, followlinks=False):
        if ".git" in dirs or ".git" in _files:
            found.append(pathlib.Path(here))
            # Prune AT .git, exactly as `find -name .git -prune` does, so a
            # gitdir under .git/modules is unreachable rather than filtered.
            dirs[:] = [d for d in dirs if d != ".git"]
        else:
            dirs[:] = [d for d in dirs if d != ".git"]
    return sorted(found)


def _drop_ignored(repos: list) -> list:
    """Drop a checkout its nearest enclosing repo gitignores.

    One `check-ignore` per PARENT, never per repo: asking one big repo the same
    question seventy times is how a walk becomes a wait. Same reasoning, and the
    same shape, as dispatch.sh:305-315.
    """
    if not repos:
        return []
    by_parent = {}
    for repo in repos:
        best = None
        for cand in repos:
            if cand == repo:
                continue
            try:
                repo.relative_to(cand)
            except ValueError:
                continue
            if best is None or len(str(cand)) > len(str(best)):
                best = cand
        if best is not None:
            by_parent.setdefault(best, []).append(repo)

    ignored = set()
    for parent, kids in by_parent.items():
        try:
            p = subprocess.run(["git", "-C", str(parent), "check-ignore", "--stdin"],
                               input="\n".join(str(k) for k in kids),
                               capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        for line in (p.stdout or "").splitlines():
            line = line.strip()
            if line:
                ignored.add(pathlib.Path(line))
    return [r for r in repos if r not in ignored]


def origin_of(repo, config=None) -> str:
    """owner/name for one checkout, asked of the pipeline's own resolver.

    `pipeline-config.py origin` is the single answer that subsystem gives to
    "where do this repo's issues live". Asking it, rather than re-deriving it,
    means a repo whose origin convention changes changes in one place.

    Falls back to reading the remote directly when the script is not installed,
    so this module still works on a machine with no pipeline.
    """
    if config is not None and pathlib.Path(config).exists():
        try:
            p = subprocess.run([sys.executable, str(config), "origin", str(repo)],
                               capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return ""
        nwo = (p.stdout or "").strip()
        return nwo if NWO_RE.match(nwo) else ""
    rc, url = _git(["-C", str(repo), "config", "--get", "remote.origin.url"])
    return normalise_remote(url) if rc == 0 else ""


def build_repo_map(root, config=None) -> dict:
    """{owner/name (lowercased): the shallowest checkout of it}."""
    out = {}
    for repo in _drop_ignored(find_checkouts(root)):
        nwo = origin_of(repo, config)
        if not nwo:
            continue
        key = nwo.lower()
        # Shallowest wins. Two checkouts of one repo happen (a clone under a
        # client folder, the canonical one at the top); the shorter path is the
        # one a person means.
        if key not in out or len(str(repo)) < len(str(out[key])):
            out[key] = repo
    return out


# ── acting on it ─────────────────────────────────────────────────────────────

class Trigger:
    """Collect a repo's events, then run the pipeline against it. One at a time.

    **Debounce.** A push, a PR opened and a comment inside two seconds are three
    deliveries carrying one piece of news: look at this repo. Without the window
    that is three dispatches over one working tree.

    **One drainer, serial.** Not a choice about load. `dispatch.sh` holds ONE
    global lock for the whole pipeline (dispatch.sh:77-78) and a second run
    finding it held logs a line and EXITS 0 (dispatch.sh:472-476). So a pool
    would not run two sweeps, it would silently drop all but one of them behind
    a green exit code. The lock is read before spawning, and the tell of a
    silent no-op is checked after, and either way the repo goes back in the
    queue instead of being marked done.
    """

    def __init__(self, mailbox, root=None, dispatch=None, debounce_s=None,
                 refresh=None, receipts=None, timeout_s=DISPATCH_TIMEOUT_S,
                 runner=None, requeue_s=None, lock_path=None, config=None):
        self.mailbox = mailbox
        self.root = pathlib.Path(root).expanduser() if root else None
        self.dispatch = pathlib.Path(dispatch).expanduser() if dispatch else None
        if self.dispatch is None:
            env = _env_path("OFFICE_DISPATCH")
            if env is not None:
                self.dispatch = env
            elif self.root is not None:
                self.dispatch = self.root / DISPATCH_REL
        self.config = pathlib.Path(config).expanduser() if config else (
            self.root / CONFIG_REL if self.root else None)
        # dispatch.sh's own lock, at its own override. Read, never written: this
        # process must never look like the run holding it.
        if lock_path is not None:
            self.lock_path = pathlib.Path(lock_path).expanduser()
        else:
            state = _env_path("PIPELINE_STATE_DIR")
            self.lock_path = ((state / "pid") if state is not None
                              else (self.dispatch.parent / LOCK_REL if self.dispatch else None))

        self.debounce_s = DEBOUNCE_S if debounce_s is None else float(debounce_s)
        self.requeue_s = REQUEUE_S if requeue_s is None else float(requeue_s)
        self.refresh = refresh
        self.receipts = receipts if receipts is not None else _env_path("OFFICE_RECEIPTS")
        self.timeout_s = timeout_s
        # Swappable so a test can watch what would have run without running it.
        self.runner = runner or self._run_dispatch

        self.cv = threading.Condition()
        self.pending = {}     # repo -> [Event] waiting on a debounce window
        self.due = {}         # repo -> monotonic deadline
        self.retry = {}       # repo -> the delivery a requeued dispatch is for
        self.stopped = False
        self.acts = 0         # dispatch attempts
        self.requeued = 0     # attempts that found the pipeline already running
        self._map = None
        self._map_at = 0.0
        self._map_lock = threading.Lock()

        self.thread = threading.Thread(target=self._drain, daemon=True,
                                       name="webhook-drainer")
        self.thread.start()

        global RUNNING_TRIGGER
        RUNNING_TRIGGER = self

    # -- the queue ---------------------------------------------------------
    def notice(self, ev) -> None:
        """One event in. Returns at once: this is called after the 200 has gone
        out, and nothing here may take a second, let alone thirty minutes."""
        if ev is None or not NWO_RE.match(ev.repo or ""):
            return
        with self.cv:
            if self.stopped:
                return
            self.pending.setdefault(ev.repo, []).append(ev)
            # A fixed window, not a sliding one. Sliding means a repo somebody
            # is actively commenting on never gets looked at.
            self.due.setdefault(ev.repo, time.monotonic() + self.debounce_s)
            self.cv.notify_all()

    def queued(self) -> list:
        with self.cv:
            return sorted(set(self.pending) | set(self.retry))

    def stop(self) -> None:
        """Put the drainer down. For a test, and for a clean shutdown."""
        with self.cv:
            self.stopped = True
            self.pending.clear()
            self.due.clear()
            self.retry.clear()
            self.cv.notify_all()

    cancel = stop

    def _next(self):
        """The repo whose window has closed, or None. Blocks until there is one."""
        with self.cv:
            while True:
                if self.stopped:
                    return None, []
                if not self.due:
                    self.cv.wait()
                    continue
                now = time.monotonic()
                ready = sorted(r for r, t in self.due.items() if t <= now)
                if not ready:
                    self.cv.wait(max(0.01, min(self.due.values()) - now))
                    continue
                repo = ready[0]
                self.due.pop(repo, None)
                return repo, self.pending.pop(repo, [])

    def _drain(self) -> None:
        """The single serial drainer. Everything below happens one at a time,
        because the thing it drives can only ever be running once."""
        while True:
            repo, events = self._next()
            if repo is None:
                return
            try:
                again = self.act(repo, events)
            except Exception as exc:  # noqa: BLE001 - one bad act, not a dead office
                log(f"{repo}: the trigger failed: {type(exc).__name__}: {exc}")
                again = False
            if again:
                with self.cv:
                    if self.stopped:
                        return
                    self.due[repo] = time.monotonic() + self.requeue_s
                    self.cv.notify_all()

    # -- the act -----------------------------------------------------------
    def act(self, repo: str, events: list) -> bool:
        """One debounce window's worth of work. True means put the repo back.

        Receipts and the desk refresh are about the DELIVERY, so they happen
        once, here, whether or not the dispatch gets to run. The dispatch is
        about the pipeline, so it is the only part that can be requeued.
        """
        self.acts += 1
        latest = events[-1] if events else None
        delivery = latest.delivery if latest is not None else self.retry.get(repo, "")

        # The receipt goes FIRST, and deliberately not last. It is a fact about
        # the delivery, not about the run: writing it after a thirty-minute
        # dispatch would leave the desk showing "in pr" for half an hour after
        # the PR merged, which is the exact stale this path exists to remove.
        for ev in events:
            if ev.event == "pull_request" and ev.action == "closed" and ev.merged:
                self.write_receipt(ev)

        again = self._dispatch(repo, events, delivery)

        # One desk, about two GraphQL points, never a whole build. Done whether
        # or not there was a checkout to dispatch against: the news came from
        # GitHub, and a desk is a picture of GitHub. Skipped on a bare retry,
        # where there is no new news to draw.
        if events and self.refresh is not None:
            try:
                self.refresh(repo)
            except Exception as exc:  # noqa: BLE001
                log(f"{repo}: could not refresh the desk: {type(exc).__name__}: {exc}")
        return again

    def _dispatch(self, repo: str, events: list, delivery: str) -> bool:
        """Run the pipeline for one repo, or say why it could not. True = retry."""
        row = {"at": now_iso(), "trigger": "webhook", "delivery": delivery,
               "repo": repo, "path": "", "events": len(events), "rc": None,
               "seconds": 0.0, "log": []}

        busy = self.pipeline_busy()
        if busy is not None:
            # Spawning now would not queue behind it. dispatch.sh would log one
            # line, exit 0, and this repo's news would be gone.
            self.requeued += 1
            self.retry[repo] = delivery
            row["note"] = f"the pipeline was already running (pid {busy}); requeued"
            self.mailbox.record_run(row)
            log(f"{repo}: {row['note']}")
            return True

        path = self.local_path(repo)
        if path is None:
            # Not an error. A repo with a desk need not have a checkout here:
            # the office shows repos you can push to, and the pipeline only runs
            # where the code actually is.
            row["note"] = "no local checkout under the vault, so nothing to dispatch"
            log(f"{repo}: {row['note']}")
            self.retry.pop(repo, None)
            self.mailbox.record_run(row)
            return False

        rc, seconds, tail = self.runner(path)
        row.update(path=str(path), rc=rc, seconds=round(seconds, 1), log=tail)

        # The tell of a silent no-op: dispatch.sh's lock guard says one line and
        # exits 0. Believing that green exit would lose the event, so the lock is
        # read again and a run that cannot have done anything is retried.
        if rc == 0 and seconds < SILENT_EXIT_S:
            held = self.pipeline_busy()
            if held is not None:
                self.requeued += 1
                self.retry[repo] = delivery
                row["note"] = (f"exited 0 in {seconds:.1f}s while pid {held} held the "
                               f"lock, so it did nothing; requeued")
                self.mailbox.record_run(row)
                log(f"{repo}: {row['note']}")
                return True

        self.retry.pop(repo, None)
        self.mailbox.record_run(row)
        return False

    def pipeline_busy(self):
        """The pid holding dispatch.sh's ONE global lock, or None.

        Read only, and never written: this process must not be mistaken for the
        run that holds it. A pidfile naming a dead process is not busy, which is
        exactly how dispatch.sh itself reads it (`kill -0`, dispatch.sh:472).
        """
        path = self.lock_path
        if path is None:
            return None
        try:
            pid = int(path.read_text().strip())
        except (OSError, ValueError):
            return None
        if pid <= 0 or pid == os.getpid():
            return None
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        except PermissionError:
            # Alive, and owned by somebody else. Still busy.
            return pid
        except OSError:
            return None
        return pid

    def _run_dispatch(self, path):
        """`dispatch.sh --repo <path>`, and never without `--repo`.

        Without it the runner sweeps every repo under the vault, which is the
        hourly job's business and not a webhook's. A comment on one issue must
        never be able to start a full sweep.
        """
        script = self.dispatch
        if script is None or not script.exists():
            log(f"no dispatch script at {script}; nothing was run")
            return None, 0.0, [f"no dispatch script at {script}"]
        started = time.monotonic()
        try:
            p = subprocess.run(["bash", str(script), "--repo", str(path)],
                               capture_output=True, text=True, timeout=self.timeout_s)
            rc, out = p.returncode, (p.stdout or "") + (p.stderr or "")
        except subprocess.TimeoutExpired as exc:
            rc = 124
            out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            out += f"\ntimed out after {self.timeout_s}s"
        except (OSError, subprocess.SubprocessError) as exc:
            rc, out = 127, f"could not run {script}: {exc}"
        seconds = time.monotonic() - started
        tail = [l[:400] for l in out.splitlines() if l.strip()][-LOG_LINES:]
        return rc, seconds, tail

    # -- owner/name to a path ----------------------------------------------
    def local_path(self, nwo: str):
        m = self.repo_map()
        return m.get(str(nwo or "").lower())

    def repo_map(self) -> dict:
        now = time.monotonic()
        with self._map_lock:
            if self._map is not None and now - self._map_at < MAP_TTL_S:
                return self._map
            self._map = build_repo_map(self.root, self.config) if self.root else {}
            self._map_at = now
            return self._map

    # -- the receipt -------------------------------------------------------
    def write_receipt(self, ev) -> bool:
        """A merged PR, written where the office already reads landings.

        The office builds a desk's headline out of the receipts file. A merge
        that only exists on GitHub leaves the desk saying "in pr" until the next
        poll, so the merge writes its own line the moment it is delivered.

        The issue number is the one the PR body names, because that is the work
        that is finished. Falling back to the PR's own number keeps the receipt
        shaped right when a PR closes nothing.
        """
        path = self.receipts
        if path is None:
            return False
        issue = ev.closes if ev.closes else ev.number
        row = {
            "at": ev.at, "repo": ev.repo,
            "issue": str(issue) if issue else "",
            "outcome": "landed",
            "detail": f"PR #{ev.number} merged (webhook)",
            "trigger": "webhook",
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
        except OSError as exc:
            log(f"could not write a receipt for {ev.repo}: {exc}")
            return False
        return True
