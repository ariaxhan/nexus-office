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
GitHub retries on any non-2xx and gives the request 10 seconds. The door
commits the handoff obligation before answering 200; the existing serial
drainer performs the slow work. Delivery ids survive restart in SQLite.

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
  webhook-runs.jsonl   every Tower handoff attempt, with its outcome

ONE DRAINER, AND WHAT SETTLES AN OBLIGATION
-------------------------------------------
Nexus Tower owns GitHub issue work, and it polls. A webhook's job is to make
the next discovery pass happen now instead of in five minutes. So the drainer
hands each accepted delivery to Tower as one `work.discovery_requested` event
in the Nexus ledger (subject: lowercase owner/name), idempotent per delivery
id, and Tower's tick discovers that repo on its next pass.

An obligation settles ONLY when that event is durably in the ledger (inserted,
or already there), or when the repo has no Tower owner (recorded as such: the
desk refresh is all there is to do). A missing ledger or registry, a sqlite
error, anything unknown: the obligation stays owed and the repo is retried
with exponential backoff from OFFICE_TRIGGER_REQUEUE_S to RETRY_CAP_S.
`reconcile_obligation` settles an old one when Tower's own records prove it
was covered anyway.

Configuration:

  OFFICE_WEBHOOK_SECRET      the shared secret GitHub signs with (required)
  OFFICE_TRIGGER_DEBOUNCE_S  collect a repo's events for this long (default 20)
  OFFICE_TRIGGER_REQUEUE_S   first retry delay after a failed handoff (60)
  OFFICE_WORK_LEDGER         the Nexus ledger (falls back as run_board.py does)
  OFFICE_WORK_REGISTRY       Tower's work registry (or NEXUS_WORK_REGISTRY)
  OFFICE_STATE               where the three files above live
"""

from __future__ import annotations

import dataclasses
from contextlib import closing, contextmanager
import hashlib
import hmac
import json
import os
import pathlib
import re
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone

import private_state as private

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
# First retry after a failed handoff; doubles per consecutive failure up to the
# cap, so an owed obligation is retried forever but never in a hot loop.
REQUEUE_S = float(os.environ.get("OFFICE_TRIGGER_REQUEUE_S", "") or 60)
RETRY_CAP_S = 30 * 60
REQUESTED = "work.discovery_requested"
LEDGER_TIMEOUT_S = 2.0   # Tower holds busy_timeout 30s; a webhook waits briefly and retries later

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
        self.anchor = self.dir.parent
        private.ensure_dir(self.dir, anchor=self.anchor)
        private.migrate_tree(self.dir, anchor=self.anchor)
        self.seen_path = self.dir / SEEN_FILE
        self.events_path = self.dir / EVENTS_FILE
        self.runs_path = self.dir / RUNS_FILE
        self.obligations_path = self.dir / "webhook-obligations.sqlite"
        self.lock = threading.Lock()
        self._rows = None     # loaded on first use, oldest first
        self._out = None      # delivery id -> outcome
        self._inflight = set()
        with self._obligations() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS obligations (
                delivery TEXT PRIMARY KEY, repo TEXT NOT NULL, event TEXT NOT NULL,
                accepted_at TEXT NOT NULL, settled_at TEXT)""")

    @contextmanager
    def _obligations(self):
        with closing(sqlite3.connect(self.obligations_path, timeout=10)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db

    def accept(self, ev):
        """Commit the handoff obligation before acknowledging its delivery."""
        if not NWO_RE.match(ev.repo or ""):
            raise ValueError("invalid webhook repo")
        with self._obligations() as db:
            db.execute("INSERT OR IGNORE INTO obligations VALUES (?,?,?,?,NULL)",
                       (ev.delivery, ev.repo, json.dumps(ev.as_dict()), now_iso()))

    def pending_obligations(self):
        with self._obligations() as db:
            rows = db.execute("SELECT event FROM obligations WHERE settled_at IS NULL ORDER BY accepted_at,delivery").fetchall()
        return [Event(**json.loads(row[0])) for row in rows]

    def settle_obligations(self, ids):
        if ids:
            with self._obligations() as db:
                db.executemany("UPDATE obligations SET settled_at=? WHERE delivery=? AND settled_at IS NULL",
                               [(now_iso(), delivery) for delivery in ids])
                db.execute("""DELETE FROM obligations WHERE settled_at IS NOT NULL AND delivery NOT IN
                    (SELECT delivery FROM obligations WHERE settled_at IS NOT NULL
                     ORDER BY settled_at DESC,delivery DESC LIMIT ?)""", (SEEN_MAX,))

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
            with self._obligations() as db:
                accepted = db.execute("SELECT 1 FROM obligations WHERE delivery=?", (delivery,)).fetchone()
            if accepted or self._out.get(delivery) == self.OK or delivery in self._inflight:
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
            private.atomic_write_text(
                self.seen_path, json.dumps({"ids": self._rows}), anchor=self.anchor)
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
                private.append_text(path, json.dumps(row) + "\n", anchor=self.anchor)
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
        private.atomic_write_text(
            path, "\n".join(lines[-EVENTS_KEEP:]) + "\n", anchor=self.anchor)

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


# ── the Tower handoff ────────────────────────────────────────────────────────

class HandoffError(RuntimeError):
    """The downstream did not durably record the work. The obligation stays owed."""


def _nexus_work():
    """Tower's own registry parser and ownership predicate, imported, never restated."""
    root = str(pathlib.Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.append(root)
    from nexus import work
    return work


def ledger_path(explicit=None) -> pathlib.Path:
    """OFFICE_WORK_LEDGER, else the ledger run_board.py reads."""
    if explicit:
        return pathlib.Path(explicit).expanduser()
    env = _env_path("OFFICE_WORK_LEDGER")
    if env is not None:
        return env
    import run_board
    return run_board.LEDGER


def tower_owned(repo: str, registry=None) -> bool:
    """`enabled && tower_row`, exactly as nexus/work.py decides it. Raises when unreadable."""
    path = registry or os.environ.get("OFFICE_WORK_REGISTRY") or os.environ.get("NEXUS_WORK_REGISTRY")
    if not path:
        raise HandoffError("no work registry (OFFICE_WORK_REGISTRY)")
    work = _nexus_work()
    try:
        rows = work.registry(path)
    except (OSError, ValueError, KeyError, TypeError, work.WorkError) as exc:
        raise HandoffError(f"work registry unreadable: {exc}") from exc
    return any(r["repo"] == repo.lower() and r["enabled"] and work.tower_row(r) for r in rows)


def handoff(repo: str, events: list, ledger=None, registry=None) -> str:
    """One `work.discovery_requested` per delivery into the Nexus ledger, in one transaction.

    Returns the run note on success; raises on anything else, so nothing can settle on it."""
    if not tower_owned(repo, registry):
        return f"no Tower owner for {repo}; desk refreshed"
    path = ledger_path(ledger)
    if not path.is_file():
        raise HandoffError(f"no Nexus ledger at {path}")
    subject, added = repo.lower(), 0
    try:
        with closing(sqlite3.connect(path, timeout=LEDGER_TIMEOUT_S, isolation_level=None)) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                for ev in events:
                    if db.execute("SELECT 1 FROM events WHERE kind=? AND json_extract(payload,'$.delivery')=?",
                                  (REQUESTED, ev.delivery)).fetchone():
                        continue
                    payload = {"delivery": ev.delivery, "event": ev.event, "action": ev.action,
                               "number": ev.number, "at": ev.at, "requested_at": now_iso()}
                    db.execute("INSERT INTO events (ts, kind, subject, payload, source) VALUES (?,?,?,?,?)",
                               (time.time(), REQUESTED, subject, json.dumps(payload), "office-webhook"))
                    added += 1
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
    except sqlite3.Error as exc:
        raise HandoffError(f"Nexus ledger write failed: {exc}") from exc
    return f"handed to Tower: {added} requested, {len(events) - added} already recorded"


def _epoch(iso: str) -> float:
    return datetime.strptime(iso, ISO).replace(tzinfo=timezone.utc).timestamp()


def reconcile_obligation(ev, ledger=None):
    """A reason when Tower's own records prove this delivery needs nothing more, else None.

    Proof: a `work.issue` capture of repo#number, or a `work.serviced` pass over the
    repo, recorded after the delivery arrived. Polling covered it."""
    repo, since = ev.repo.lower(), _epoch(ev.at)
    uri = ledger_path(ledger).as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=LEDGER_TIMEOUT_S)) as db:
        if ev.number and db.execute(
                "SELECT 1 FROM events e JOIN tasks t ON t.id=e.subject WHERE e.kind='work.issue'"
                " AND lower(t.dedupe_key)=? AND e.ts>? LIMIT 1", (f"github:{repo}#{ev.number}", since)).fetchone():
            return f"Tower captured {repo}#{ev.number} after the delivery"
        if db.execute("SELECT 1 FROM events WHERE kind='work.serviced' AND subject=? AND ts>? LIMIT 1",
                      (repo, since)).fetchone():
            return f"Tower serviced {repo} after the delivery"
    return None


# ── acting on it ─────────────────────────────────────────────────────────────

class Trigger:
    """Collect a repo's events, then hand them to Tower. One at a time.

    **Debounce.** A push, a PR opened and a comment inside two seconds are three
    deliveries carrying one piece of news: look at this repo.

    **One drainer, serial.** One writer into the Nexus ledger from this process,
    and one place that decides whether an obligation is settled. A failed handoff
    keeps its events owed and puts the repo back with backoff.
    """

    def __init__(self, mailbox, debounce_s=None, refresh=None, receipts=None,
                 runner=None, requeue_s=None, ledger=None, registry=None):
        self.mailbox = mailbox
        self.debounce_s = DEBOUNCE_S if debounce_s is None else float(debounce_s)
        self.requeue_s = REQUEUE_S if requeue_s is None else float(requeue_s)
        self.refresh = refresh
        self.receipts = receipts if receipts is not None else _env_path("OFFICE_RECEIPTS")
        # runner(repo, events) -> note; raises when the work is not durably recorded.
        self.runner = runner or (lambda repo, events: handoff(repo, events, ledger, registry))

        self.cv = threading.Condition()
        self.pending = {}     # repo -> [Event] waiting on a debounce window
        self.due = {}         # repo -> monotonic deadline
        self.owed = {}        # repo -> {delivery: Event} a failed handoff still owes
        self.failures = {}    # repo -> consecutive failed handoffs
        self.stopped = False
        self.acts = 0         # handoff attempts
        self.requeued = 0     # attempts that failed and were put back

        self._restore_pending()

        self.thread = threading.Thread(target=self._drain, daemon=True,
                                       name="webhook-drainer")
        self.thread.start()

        global RUNNING_TRIGGER
        RUNNING_TRIGGER = self

    # -- the queue ---------------------------------------------------------
    def _restore_pending(self):
        # The same drainer replays committed work after a service stop.
        for ev in self.mailbox.pending_obligations():
            self.pending.setdefault(ev.repo, []).append(ev)
            self.due.setdefault(ev.repo, time.monotonic() + self.debounce_s)

    def notice(self, ev, accepted=False) -> None:
        """One event in. Its obligation is durable before the handoff."""
        if ev is None or not NWO_RE.match(ev.repo or ""):
            return
        if not accepted:
            self.mailbox.accept(ev)
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
            return sorted(set(self.pending) | set(self.owed))

    def stop(self) -> None:
        """Put the drainer down. For a test, and for a clean shutdown."""
        with self.cv:
            self.stopped = True
            self.pending.clear()
            self.due.clear()
            self.owed.clear()
            self.cv.notify_all()

    cancel = stop

    def _next(self):
        """The repo whose window has closed, or None. Blocks until there is one."""
        with self.cv:
            while True:
                if self.stopped:
                    return None, [], []
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
                return repo, self.pending.pop(repo, []), list(self.owed.pop(repo, {}).values())

    def _drain(self) -> None:
        """The single serial drainer. Settles only what the downstream recorded."""
        while True:
            repo, events, owed = self._next()
            if repo is None:
                return
            try:
                again = self.act(repo, events, owed)
            except Exception as exc:  # noqa: BLE001 - one bad act, not a dead office
                log(f"{repo}: the trigger failed: {type(exc).__name__}: {exc}")
                again = True
            ids = {ev.delivery for ev in owed + events}
            if not again:
                try:
                    self.mailbox.settle_obligations(ids)
                except (OSError, sqlite3.Error) as exc:
                    log(f"{repo}: could not settle durable webhook obligations: {exc}")
                    again = True
            if again:
                self._owe(repo, owed + events)

    def _owe(self, repo, events):
        with self.cv:
            if self.stopped:
                return
            self.owed.setdefault(repo, {}).update((ev.delivery, ev) for ev in events)
            self.due.setdefault(repo, time.monotonic() + self.backoff(repo))
            self.cv.notify_all()

    def backoff(self, repo) -> float:
        n = max(1, self.failures.get(repo, 1))
        return min(RETRY_CAP_S, self.requeue_s * 2 ** (n - 1))

    # -- the act -----------------------------------------------------------
    def act(self, repo: str, events: list, owed=()) -> bool:
        """One debounce window's worth of work. True means still owed.

        Receipts and the desk refresh are about the NEW deliveries, so they
        happen once; only the handoff is retried, for new and owed alike.
        """
        self.acts += 1
        # The receipt goes FIRST: it is a fact about the delivery, not the handoff.
        for ev in events:
            if ev.event == "pull_request" and ev.action == "closed" and ev.merged:
                self.write_receipt(ev)

        again = self._handoff(repo, list(owed) + list(events))

        # One desk, about two GraphQL points. Skipped on a bare retry.
        if events and self.refresh is not None:
            try:
                self.refresh(repo)
            except Exception as exc:  # noqa: BLE001
                log(f"{repo}: could not refresh the desk: {type(exc).__name__}: {exc}")
        return again

    def _handoff(self, repo: str, events: list) -> bool:
        """Hand the deliveries to Tower, or record why not. True = still owed."""
        row = {"at": now_iso(), "trigger": "webhook", "delivery": events[-1].delivery if events else "",
               "repo": repo, "events": len(events), "rc": 0}
        try:
            row["note"] = self.runner(repo, events)
            self.failures.pop(repo, None)
        except Exception as exc:  # noqa: BLE001 - unknown outcome is owed, never settled
            self.requeued += 1
            self.failures[repo] = self.failures.get(repo, 0) + 1
            row.update(rc=1, attempt=self.failures[repo], retry_in_s=self.backoff(repo),
                       note=f"handoff failed, still owed: {type(exc).__name__}: {exc}")
            log(f"{repo}: {row['note']}")
        self.mailbox.record_run(row)
        return row["rc"] != 0


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
