"""The ledger: everything nexus knows, in one sqlite file.

One rule shapes the whole module: a caller cannot leave this file in a state with
two readings. Every mutating call is a single transaction that also appends the
`events` row explaining it, state transitions are checked against a fixed table,
and history is made immutable by triggers rather than by good manners.

Schema v1 is `docs/foundation.md`: objectives, plans, observations, tasks,
flights, artifacts, landings, messages, gates, events, leases. Leases are on
RESOURCES (a repo+branch, a mailbox, a deploy slot, a paid budget), never on
filesystem paths: isolated workspaces already solve execution collisions.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sqlite3
import time
import uuid

SCHEMA_VERSION = 1

TERMINAL = ("landed", "failed", "cancelled")

#: the only state moves that exist. Anything else is a bug, not a state.
TRANSITIONS = {
    "queued": {"running", "resolving", "failed", "cancelled"},
    "running": {"produced", "resolving", "failed", "cancelled"},
    "produced": {"verifying", "verified", "resolving", "failed", "cancelled"},
    "verifying": {"verified", "resolving", "failed", "cancelled"},
    "verified": {"landing", "resolving", "failed", "cancelled"},
    "landing": {"landed", "resolving", "failed", "cancelled"},
    "resolving": {"queued", "running", "failed", "cancelled"},
    "landed": set(),
    "failed": set(),
    "cancelled": set(),
}

STATES = tuple(TRANSITIONS)

TASK_STATES = (
    "candidate", "ranked", "accepted", "rejected_duplicate", "rejected_policy",
    "running", "done", "abandoned",
)

#: a task that still owns its dedupe key. A second candidate with the same key
#: is a duplicate, not a second opinion.
TASK_LIVE = ("candidate", "ranked", "accepted", "running")

SCHEMA = """
CREATE TABLE IF NOT EXISTS objectives (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  statement TEXT NOT NULL,
  owner TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS plans (
  id TEXT PRIMARY KEY,
  objective_id TEXT REFERENCES objectives(id),
  name TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL,
  schedule TEXT NOT NULL DEFAULT '{}',
  inputs TEXT NOT NULL DEFAULT '{}',
  outputs TEXT NOT NULL DEFAULT '[]',
  budget TEXT NOT NULL DEFAULT '{}',
  resolution_policy TEXT NOT NULL DEFAULT '{}',
  resources TEXT NOT NULL DEFAULT '[]',
  enabled INTEGER NOT NULL DEFAULT 1,
  quarantined_at REAL,
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS observations (
  id TEXT PRIMARY KEY,
  ts REAL NOT NULL,
  source TEXT NOT NULL,
  subject TEXT,
  payload TEXT NOT NULL DEFAULT '{}',
  handled_by_task TEXT REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  objective_id TEXT REFERENCES objectives(id),
  plan_id TEXT REFERENCES plans(id),
  origin TEXT NOT NULL,
  title TEXT NOT NULL,
  reason TEXT,
  impact TEXT,
  risk TEXT,
  cost_estimate REAL,
  state TEXT NOT NULL,
  dedupe_key TEXT,
  decided_by TEXT,
  decided_at REAL,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS tasks_state ON tasks(state);
CREATE INDEX IF NOT EXISTS tasks_dedupe ON tasks(dedupe_key, state);

CREATE TABLE IF NOT EXISTS flights (
  id TEXT PRIMARY KEY,
  task_id TEXT REFERENCES tasks(id),
  plan_id TEXT NOT NULL REFERENCES plans(id),
  state TEXT NOT NULL,
  lease_until REAL,
  workspace TEXT,
  pid INTEGER,
  created_at REAL NOT NULL,
  started_at REAL,
  ended_at REAL,
  result TEXT,
  attempt INTEGER NOT NULL DEFAULT 1,
  resolution_step TEXT
);
CREATE INDEX IF NOT EXISTS flights_state ON flights(state);
CREATE INDEX IF NOT EXISTS flights_plan ON flights(plan_id, created_at);
CREATE INDEX IF NOT EXISTS flights_task ON flights(task_id);

CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY,
  flight_id TEXT NOT NULL REFERENCES flights(id),
  kind TEXT NOT NULL,
  ref TEXT NOT NULL,
  sha TEXT,
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS landings (
  id TEXT PRIMARY KEY,
  flight_id TEXT NOT NULL REFERENCES flights(id),
  target TEXT NOT NULL,
  expected_sha TEXT,
  verify_flight_id TEXT,
  state TEXT NOT NULL,
  applied_sha TEXT,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS landings_state ON landings(state);

CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  task_id TEXT REFERENCES tasks(id),
  from_flight TEXT,
  to_kind TEXT NOT NULL,
  to_ref TEXT,
  body TEXT NOT NULL,
  created_at REAL NOT NULL,
  delivered_to_flight TEXT,
  delivered_at REAL
);
CREATE INDEX IF NOT EXISTS messages_task ON messages(task_id, delivered_at);

CREATE TABLE IF NOT EXISTS gates (
  id TEXT PRIMARY KEY,
  task_id TEXT REFERENCES tasks(id),
  flight_id TEXT REFERENCES flights(id),
  question TEXT NOT NULL,
  options TEXT NOT NULL DEFAULT '[]',
  policy_reason TEXT,
  answer TEXT,
  answered_by TEXT,
  answered_at REAL,
  timeout_at REAL,
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  kind TEXT NOT NULL,
  subject TEXT,
  payload TEXT NOT NULL DEFAULT '{}',
  source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_kind ON events(kind, id);
CREATE INDEX IF NOT EXISTS events_subject ON events(subject, id);

CREATE TABLE IF NOT EXISTS leases (
  resource TEXT PRIMARY KEY,
  holder_flight TEXT NOT NULL REFERENCES flights(id),
  until REAL NOT NULL
);

-- history is history: append-only, enforced here and not by callers.
CREATE TRIGGER IF NOT EXISTS events_no_update
BEFORE UPDATE ON events BEGIN
  SELECT RAISE(ABORT, 'events are append-only');
END;
CREATE TRIGGER IF NOT EXISTS events_no_delete
BEFORE DELETE ON events BEGIN
  SELECT RAISE(ABORT, 'events are append-only');
END;
CREATE TRIGGER IF NOT EXISTS flights_terminal_no_update
BEFORE UPDATE ON flights WHEN old.state IN ('landed','failed','cancelled') BEGIN
  SELECT RAISE(ABORT, 'terminal flight is history');
END;
CREATE TRIGGER IF NOT EXISTS flights_no_delete
BEFORE DELETE ON flights BEGIN
  SELECT RAISE(ABORT, 'flights are append-only');
END;
CREATE TRIGGER IF NOT EXISTS artifacts_no_update
BEFORE UPDATE ON artifacts BEGIN
  SELECT RAISE(ABORT, 'artifacts are append-only');
END;
CREATE TRIGGER IF NOT EXISTS artifacts_no_delete
BEFORE DELETE ON artifacts BEGIN
  SELECT RAISE(ABORT, 'artifacts are append-only');
END;
CREATE TRIGGER IF NOT EXISTS landings_applied_no_update
BEFORE UPDATE ON landings WHEN old.state = 'applied' BEGIN
  SELECT RAISE(ABORT, 'an applied landing is history');
END;
CREATE TRIGGER IF NOT EXISTS observations_no_delete
BEFORE DELETE ON observations BEGIN
  SELECT RAISE(ABORT, 'observations are append-only');
END;
CREATE TRIGGER IF NOT EXISTS tasks_decided_no_redecide
BEFORE UPDATE ON tasks
WHEN old.state IN ('rejected_duplicate','rejected_policy','done','abandoned') BEGIN
  SELECT RAISE(ABORT, 'a decided task is history');
END;
"""


class LedgerError(Exception):
    """A refusal from the ledger. Never a corrupt row."""


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def default_path() -> str:
    env = os.environ.get("NEXUS_LEDGER")
    if env:
        return env
    return os.path.expanduser("~/Library/Application Support/nexus/ledger.sqlite")


def _j(value) -> str:
    return json.dumps(value, sort_keys=True)


def loads(value, default=None):
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default


class Ledger:
    def __init__(self, path: str | None = None):
        self.path = path or default_path()
        os.makedirs(os.path.dirname(os.path.abspath(self.path)) or ".", exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.migrate()

    # ---- lifecycle -------------------------------------------------------

    def close(self):
        with contextlib.suppress(Exception):
            self.conn.close()

    def user_version(self) -> int:
        return int(self.conn.execute("PRAGMA user_version").fetchone()[0])

    def migrate(self):
        """Bring the file to SCHEMA_VERSION, backing it up before it moves."""
        version = self.user_version()
        if version == SCHEMA_VERSION:
            return
        if version > SCHEMA_VERSION:
            raise LedgerError(
                f"ledger is version {version}, this nexus knows {SCHEMA_VERSION}"
            )
        # A file with bytes in it is somebody's history: copy it before moving it.
        # A brand new file is zero bytes and has nothing to lose.
        if os.path.exists(self.path) and os.path.getsize(self.path) > 0:
            self.conn.execute("PRAGMA wal_checkpoint(FULL)")
            shutil.copyfile(self.path, f"{self.path}.bak-{version}")
        # executescript commits on its own, so the schema cannot ride inside tx().
        # It is idempotent (every statement is IF NOT EXISTS), so a crash halfway
        # through leaves the next open to finish the job.
        self.conn.executescript(SCHEMA)
        self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        self.event("ledger.migrated", None, {"from": version, "to": SCHEMA_VERSION}, "tower")

    @contextlib.contextmanager
    def tx(self):
        """One writer, one transaction, no partial anything."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            with contextlib.suppress(Exception):
                self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    def _abort(self):
        """Give up on the current transaction and hand the caller a False."""
        self.conn.execute("ROLLBACK")
        self.conn.execute("BEGIN IMMEDIATE")

    # ---- events ----------------------------------------------------------

    def _event(self, kind, subject, payload, source, ts=None):
        self.conn.execute(
            "INSERT INTO events (ts, kind, subject, payload, source) VALUES (?,?,?,?,?)",
            (ts if ts is not None else time.time(), kind, subject, _j(payload or {}), source),
        )

    def event(self, kind, subject=None, payload=None, source="tower", ts=None):
        with self.tx():
            self._event(kind, subject, payload, source, ts)

    def events(self, kind=None, subject=None, after_id=0):
        sql = "SELECT * FROM events WHERE id > ?"
        args = [after_id]
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        if subject:
            sql += " AND subject = ?"
            args.append(subject)
        return self.conn.execute(sql + " ORDER BY id", args).fetchall()

    def last_event(self, kinds):
        marks = ",".join("?" * len(kinds))
        return self.conn.execute(
            f"SELECT * FROM events WHERE kind IN ({marks}) ORDER BY id DESC LIMIT 1", list(kinds)
        ).fetchone()

    # ---- objectives ------------------------------------------------------

    def add_objective(self, name, statement, owner=None, now=None):
        now = now if now is not None else time.time()
        oid = new_id("obj")
        with self.tx():
            self.conn.execute(
                "INSERT INTO objectives (id,name,statement,owner,active,created_at)"
                " VALUES (?,?,?,?,1,?)", (oid, name, statement, owner, now))
            self._event("objective.added", oid, {"name": name}, "tower", now)
        return oid

    def objectives(self, active_only=True):
        sql = "SELECT * FROM objectives"
        if active_only:
            sql += " WHERE active=1"
        return self.conn.execute(sql + " ORDER BY created_at").fetchall()

    # ---- plans -----------------------------------------------------------

    def add_plan(self, name, kind="script", schedule=None, objective_id=None,
                 inputs=None, outputs=None, budget=None, resolution_policy=None,
                 resources=None, now=None):
        now = now if now is not None else time.time()
        pid = new_id("plan")
        with self.tx():
            self.conn.execute(
                "INSERT INTO plans (id,objective_id,name,kind,schedule,inputs,outputs,budget,"
                "resolution_policy,resources,enabled,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,1,?)",
                (pid, objective_id, name, kind, _j(schedule or {}), _j(inputs or {}),
                 _j(outputs or []), _j(budget or {}), _j(resolution_policy or {}),
                 _j(resources or []), now),
            )
            self._event("plan.added", pid, {"name": name, "kind": kind}, "tower", now)
        return pid

    def plan(self, plan_id):
        return self.conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()

    def plan_by_name(self, name):
        return self.conn.execute("SELECT * FROM plans WHERE name=?", (name,)).fetchone()

    def plans(self, runnable_only=False):
        sql = "SELECT * FROM plans"
        if runnable_only:
            sql += " WHERE enabled=1 AND quarantined_at IS NULL"
        return self.conn.execute(sql + " ORDER BY created_at").fetchall()

    def set_plan_enabled(self, plan_id, enabled, now=None):
        now = now if now is not None else time.time()
        with self.tx():
            self.conn.execute("UPDATE plans SET enabled=? WHERE id=?",
                              (1 if enabled else 0, plan_id))
            self._event("plan.enabled" if enabled else "plan.disabled", plan_id, {}, "tower", now)

    def quarantine_plan(self, plan_id, reason, now=None):
        now = now if now is not None else time.time()
        with self.tx():
            self.conn.execute(
                "UPDATE plans SET quarantined_at=? WHERE id=? AND quarantined_at IS NULL",
                (now, plan_id))
            self._event("plan.quarantined", plan_id, {"reason": reason}, "tower", now)

    def unquarantine_plan(self, plan_id, now=None):
        now = now if now is not None else time.time()
        with self.tx():
            self.conn.execute("UPDATE plans SET quarantined_at=NULL WHERE id=?", (plan_id,))
            self._event("plan.unquarantined", plan_id, {}, "tower", now)

    # ---- observations ----------------------------------------------------

    def observe(self, source, subject=None, payload=None, now=None):
        now = now if now is not None else time.time()
        oid = new_id("obs")
        with self.tx():
            self.conn.execute(
                "INSERT INTO observations (id,ts,source,subject,payload) VALUES (?,?,?,?,?)",
                (oid, now, source, subject, _j(payload or {})))
            self._event("observation.recorded", oid, {"source": source, "subject": subject},
                        source if source in ("webhook", "click", "flight") else "tower", now)
        return oid

    def observations(self, unhandled_only=False):
        sql = "SELECT * FROM observations"
        if unhandled_only:
            sql += " WHERE handled_by_task IS NULL"
        return self.conn.execute(sql + " ORDER BY ts").fetchall()

    # ---- tasks -----------------------------------------------------------

    def add_task(self, title, origin, plan_id=None, objective_id=None, reason=None,
                 impact=None, risk=None, cost_estimate=None, dedupe_key=None,
                 state="candidate", observation_id=None, now=None):
        now = now if now is not None else time.time()
        tid = new_id("task")
        if state not in TASK_STATES:
            raise LedgerError(f"unknown task state: {state}")
        with self.tx():
            self.conn.execute(
                "INSERT INTO tasks (id,objective_id,plan_id,origin,title,reason,impact,risk,"
                "cost_estimate,state,dedupe_key,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, objective_id, plan_id, origin, title, reason, impact, risk,
                 cost_estimate, state, dedupe_key, now))
            if observation_id:
                self.conn.execute("UPDATE observations SET handled_by_task=? WHERE id=?",
                                  (tid, observation_id))
            self._event("task.state", tid,
                        {"to": state, "origin": origin, "plan_id": plan_id,
                         "dedupe_key": dedupe_key}, "tower", now)
        return tid

    def task(self, task_id):
        return self.conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()

    def tasks(self, states=None, limit=None):
        sql = "SELECT * FROM tasks"
        args = []
        if states:
            sql += " WHERE state IN (%s)" % ",".join("?" * len(states))
            args += list(states)
        sql += " ORDER BY created_at"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return self.conn.execute(sql, args).fetchall()

    def set_task_state(self, task_id, to_state, decided_by=None, expect=None,
                       reason=None, now=None):
        now = now if now is not None else time.time()
        if to_state not in TASK_STATES:
            raise LedgerError(f"unknown task state: {to_state}")
        with self.tx():
            row = self.conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise LedgerError(f"no such task: {task_id}")
            if expect is not None and row["state"] != expect:
                self._abort()
                return False
            self.conn.execute(
                "UPDATE tasks SET state=?, decided_by=COALESCE(?,decided_by), decided_at=?"
                " WHERE id=?", (to_state, decided_by, now, task_id))
            self._event("task.state", task_id,
                        {"from": row["state"], "to": to_state, "decided_by": decided_by,
                         "reason": reason}, "tower", now)
        return True

    def live_task_with_key(self, dedupe_key, exclude=None):
        if not dedupe_key:
            return None
        marks = ",".join("?" * len(TASK_LIVE))
        sql = f"SELECT * FROM tasks WHERE dedupe_key=? AND state IN ({marks})"
        args = [dedupe_key, *TASK_LIVE]
        if exclude:
            sql += " AND id != ?"
            args.append(exclude)
        return self.conn.execute(sql + " ORDER BY created_at LIMIT 1", args).fetchone()

    # ---- flights ---------------------------------------------------------

    def create_flight(self, plan_id, task_id=None, attempt=1, now=None, source="tower",
                      trigger=None, unique_for_task=False):
        """Create a queued flight. Returns None when `unique_for_task` and the task
        already has a flight in the air, which is what stops two ticks from
        launching the same work twice."""
        now = now if now is not None else time.time()
        fid = new_id("flt")
        payload = {"plan_id": plan_id, "task_id": task_id, "attempt": attempt, "to": "queued"}
        if trigger is not None:
            payload["trigger_event_id"] = trigger
        with self.tx():
            if self.conn.execute("SELECT 1 FROM plans WHERE id=?", (plan_id,)).fetchone() is None:
                raise LedgerError(f"no such plan: {plan_id}")
            if unique_for_task and task_id:
                marks = ",".join("?" * len(TERMINAL))
                live = self.conn.execute(
                    f"SELECT 1 FROM flights WHERE task_id=? AND state NOT IN ({marks})",
                    (task_id, *TERMINAL)).fetchone()
                if live is not None:
                    self._abort()
                    return None
            self.conn.execute(
                "INSERT INTO flights (id,task_id,plan_id,state,created_at,attempt)"
                " VALUES (?,?,?,'queued',?,?)", (fid, task_id, plan_id, now, attempt))
            self._event("flight.state", fid, payload, source, now)
        return fid

    def flight(self, flight_id):
        return self.conn.execute("SELECT * FROM flights WHERE id=?", (flight_id,)).fetchone()

    def flights(self, states=None, plan_id=None, task_id=None, limit=None):
        sql = "SELECT * FROM flights"
        args, where = [], []
        if states:
            where.append("state IN (%s)" % ",".join("?" * len(states)))
            args += list(states)
        if plan_id:
            where.append("plan_id = ?")
            args.append(plan_id)
        if task_id:
            where.append("task_id = ?")
            args.append(task_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC, id DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return self.conn.execute(sql, args).fetchall()

    def set_state(self, flight_id, to_state, expect=None, now=None, source="tower", **fields):
        """Move a flight. Returns False when someone else moved it first.

        `expect` makes the move a compare-and-set, which is the whole reason two
        ticks running at once cannot both launch the same flight.
        """
        now = now if now is not None else time.time()
        if to_state not in TRANSITIONS:
            raise LedgerError(f"unknown state: {to_state}")
        allowed = {"lease_until", "workspace", "pid", "started_at", "ended_at", "result",
                   "resolution_step"}
        bad = set(fields) - allowed
        if bad:
            raise LedgerError(f"not flight columns: {sorted(bad)}")
        with self.tx():
            row = self.conn.execute("SELECT * FROM flights WHERE id=?", (flight_id,)).fetchone()
            if row is None:
                raise LedgerError(f"no such flight: {flight_id}")
            frm = row["state"]
            if expect is not None and frm != expect:
                self._abort()
                return False
            if to_state not in TRANSITIONS[frm]:
                raise LedgerError(f"illegal transition {frm} -> {to_state} ({flight_id})")
            sets, args = ["state=?"], [to_state]
            for key, value in fields.items():
                sets.append(f"{key}=?")
                args.append(_j(value) if key == "result" else value)
            if to_state in TERMINAL:
                if "ended_at" not in fields:
                    sets.append("ended_at=?")
                    args.append(now)
                sets.append("lease_until=NULL")
                self.conn.execute("DELETE FROM leases WHERE holder_flight=?", (flight_id,))
            args.append(flight_id)
            self.conn.execute(f"UPDATE flights SET {','.join(sets)} WHERE id=?", args)
            payload = {"from": frm, "to": to_state}
            if "result" in fields:
                payload["result"] = fields["result"]
            self._event("flight.state", flight_id, payload, source, now)
        return True

    def set_pid(self, flight_id, pid, now=None):
        """Recorded after the fork. A running flight with no pid yet is reconciled
        as vanished once its grace has passed, never left ambiguous."""
        now = now if now is not None else time.time()
        with self.tx():
            row = self.conn.execute("SELECT state FROM flights WHERE id=?",
                                    (flight_id,)).fetchone()
            if row is None or row["state"] != "running":
                self._abort()
                return False
            self.conn.execute("UPDATE flights SET pid=? WHERE id=?", (pid, flight_id))
            self._event("flight.pid", flight_id, {"pid": pid}, "tower", now)
        return True

    def fail(self, flight_id, code, detail="", expect=None, now=None, cost=None):
        """Every failure carries a code. Free text is never the result."""
        return self.set_state(
            flight_id, "failed", expect=expect, now=now,
            result={"ok": False, "artifacts": [], "error": {"code": code, "detail": detail},
                    "cost": cost or {}})

    # ---- artifacts -------------------------------------------------------

    def add_artifact(self, flight_id, kind, ref, sha=None, now=None):
        now = now if now is not None else time.time()
        aid = new_id("art")
        with self.tx():
            self.conn.execute(
                "INSERT INTO artifacts (id,flight_id,kind,ref,sha,created_at)"
                " VALUES (?,?,?,?,?,?)", (aid, flight_id, kind, ref, sha, now))
            self._event("artifact.added", flight_id, {"kind": kind, "ref": ref, "sha": sha},
                        "tower", now)
        return aid

    def artifacts(self, flight_id=None):
        if flight_id:
            return self.conn.execute(
                "SELECT * FROM artifacts WHERE flight_id=? ORDER BY created_at",
                (flight_id,)).fetchall()
        return self.conn.execute("SELECT * FROM artifacts ORDER BY created_at").fetchall()

    # ---- landings: a recoverable state machine, not a transaction --------

    def create_landing(self, flight_id, target, expected_sha=None, verify_flight_id=None,
                       state="pending", now=None):
        now = now if now is not None else time.time()
        lid = new_id("lnd")
        with self.tx():
            self.conn.execute(
                "INSERT INTO landings (id,flight_id,target,expected_sha,verify_flight_id,state,"
                "created_at) VALUES (?,?,?,?,?,?,?)",
                (lid, flight_id, target, expected_sha, verify_flight_id, state, now))
            self._event(f"landing.{state}", flight_id, {"landing": lid, "target": target},
                        "tower", now)
        return lid

    def landing(self, landing_id):
        return self.conn.execute("SELECT * FROM landings WHERE id=?", (landing_id,)).fetchone()

    def landings(self, states=None):
        sql = "SELECT * FROM landings"
        args = []
        if states:
            sql += " WHERE state IN (%s)" % ",".join("?" * len(states))
            args += list(states)
        return self.conn.execute(sql + " ORDER BY created_at", args).fetchall()

    def start_applying(self, landing_id, expected_sha, now=None):
        """Written BEFORE the push, so a crash mid-push is a row to reconcile."""
        now = now if now is not None else time.time()
        with self.tx():
            row = self.conn.execute("SELECT * FROM landings WHERE id=?",
                                    (landing_id,)).fetchone()
            if row is None:
                raise LedgerError(f"no such landing: {landing_id}")
            if row["state"] not in ("pending", "verified", "applying"):
                self._abort()
                return False
            self.conn.execute("UPDATE landings SET state='applying', expected_sha=? WHERE id=?",
                              (expected_sha, landing_id))
            flight = self.conn.execute("SELECT * FROM flights WHERE id=?",
                                       (row["flight_id"],)).fetchone()
            if flight is not None and flight["state"] == "verified":
                self.conn.execute("UPDATE flights SET state='landing' WHERE id=?",
                                  (flight["id"],))
                self._event("flight.state", flight["id"], {"from": "verified", "to": "landing"},
                            "tower", now)
            self._event("landing.applying", row["flight_id"],
                        {"landing": landing_id, "expected_sha": expected_sha}, "tower", now)
        return True

    def apply_landing(self, landing_id, applied_sha, now=None):
        """Idempotent: the landing row and the flight's `landed` state move together.

        Calling it twice on the same landing is a no-op returning False, which is
        what makes retrying a landing safe.
        """
        now = now if now is not None else time.time()
        with self.tx():
            row = self.conn.execute("SELECT * FROM landings WHERE id=?",
                                    (landing_id,)).fetchone()
            if row is None:
                raise LedgerError(f"no such landing: {landing_id}")
            if row["state"] == "applied":
                self._abort()
                return False
            flight = self.conn.execute("SELECT * FROM flights WHERE id=?",
                                       (row["flight_id"],)).fetchone()
            if flight["state"] == "verified":
                # verified -> landing -> landed, written as two events in one
                # transaction so the history never skips a rung.
                self.conn.execute("UPDATE flights SET state='landing' WHERE id=?",
                                  (flight["id"],))
                self._event("flight.state", flight["id"], {"from": "verified", "to": "landing"},
                            "tower", now)
                flight = self.conn.execute("SELECT * FROM flights WHERE id=?",
                                           (flight["id"],)).fetchone()
            if "landed" not in TRANSITIONS[flight["state"]]:
                raise LedgerError(
                    f"illegal transition {flight['state']} -> landed ({flight['id']})")
            self.conn.execute(
                "UPDATE landings SET state='applied', applied_sha=? WHERE id=?",
                (applied_sha, landing_id))
            self.conn.execute(
                "UPDATE flights SET state='landed', ended_at=?, lease_until=NULL WHERE id=?",
                (now, flight["id"]))
            self.conn.execute("DELETE FROM leases WHERE holder_flight=?", (flight["id"],))
            self._event("flight.state", flight["id"],
                        {"from": flight["state"], "to": "landed", "landing": landing_id,
                         "applied_sha": applied_sha}, "tower", now)
        return True

    def refuse_landing(self, landing_id, reason, now=None):
        now = now if now is not None else time.time()
        with self.tx():
            row = self.conn.execute("SELECT * FROM landings WHERE id=?",
                                    (landing_id,)).fetchone()
            if row is None or row["state"] == "applied":
                self._abort()
                return False
            self.conn.execute("UPDATE landings SET state='refused' WHERE id=?", (landing_id,))
            self._event("landing.refused", row["flight_id"],
                        {"landing": landing_id, "reason": reason}, "tower", now)
        return True

    # ---- resource leases -------------------------------------------------

    def acquire_leases(self, flight_id, resources, until, now=None):
        """All the resources or none. Overlap is refused, never queued behind."""
        now = now if now is not None else time.time()
        with self.tx():
            for resource in resources:
                held = self.conn.execute(
                    "SELECT * FROM leases WHERE resource=? AND until > ?",
                    (resource, now)).fetchone()
                if held is not None and held["holder_flight"] != flight_id:
                    self._abort()
                    return False
            for resource in resources:
                self.conn.execute(
                    "INSERT INTO leases (resource, holder_flight, until) VALUES (?,?,?)"
                    " ON CONFLICT(resource) DO UPDATE SET holder_flight=excluded.holder_flight,"
                    " until=excluded.until", (resource, flight_id, until))
            self.conn.execute("UPDATE flights SET lease_until=? WHERE id=?", (until, flight_id))
            if resources:
                self._event("lease.acquired", flight_id,
                            {"resources": list(resources), "until": until}, "tower", now)
        return True

    def release_leases(self, flight_id, now=None):
        now = now if now is not None else time.time()
        with self.tx():
            held = self.conn.execute("SELECT resource FROM leases WHERE holder_flight=?",
                                     (flight_id,)).fetchall()
            if not held:
                return []
            self.conn.execute("DELETE FROM leases WHERE holder_flight=?", (flight_id,))
            resources = [r["resource"] for r in held]
            self._event("lease.released", flight_id, {"resources": resources}, "tower", now)
            return resources

    def expire_leases(self, now):
        """Expiry is a fact about the clock, not about a process being polite."""
        with self.tx():
            dead = self.conn.execute("SELECT * FROM leases WHERE until <= ?", (now,)).fetchall()
            if not dead:
                return []
            self.conn.execute("DELETE FROM leases WHERE until <= ?", (now,))
            for row in dead:
                self._event("lease.expired", row["holder_flight"],
                            {"resource": row["resource"]}, "tower", now)
            return [dict(r) for r in dead]

    def leases(self):
        return self.conn.execute("SELECT * FROM leases ORDER BY resource").fetchall()

    # ---- radio: messages belong to the task, not the flight --------------

    def send_message(self, body, task_id=None, from_flight=None, to_kind="task",
                     to_ref=None, now=None):
        now = now if now is not None else time.time()
        mid = new_id("msg")
        if to_kind not in ("task", "flight", "operator"):
            raise LedgerError(f"unknown message target: {to_kind}")
        with self.tx():
            self.conn.execute(
                "INSERT INTO messages (id,task_id,from_flight,to_kind,to_ref,body,created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (mid, task_id, from_flight, to_kind, to_ref, body, now))
            self._event("message.sent", task_id or to_ref or "operator",
                        {"id": mid, "to_kind": to_kind}, "flight" if from_flight else "click", now)
        return mid

    def undelivered(self, task_id=None):
        sql = "SELECT * FROM messages WHERE delivered_at IS NULL"
        args = []
        if task_id:
            sql += " AND task_id = ?"
            args.append(task_id)
        return self.conn.execute(sql + " ORDER BY created_at", args).fetchall()

    def deliver_messages(self, task_id, flight_id, now=None):
        """A task's mail goes to whoever holds the task now, not to who sent for it."""
        now = now if now is not None else time.time()
        with self.tx():
            rows = self.conn.execute(
                "SELECT * FROM messages WHERE task_id=? AND delivered_at IS NULL"
                " AND to_kind != 'operator' ORDER BY created_at", (task_id,)).fetchall()
            if not rows:
                return []
            self.conn.execute(
                "UPDATE messages SET delivered_to_flight=?, delivered_at=? WHERE task_id=?"
                " AND delivered_at IS NULL AND to_kind != 'operator'",
                (flight_id, now, task_id))
            self._event("message.delivered", task_id,
                        {"flight": flight_id, "count": len(rows)}, "tower", now)
            return [dict(r) for r in rows]

    # ---- gates: the last rung of the ladder, never the first -------------

    def open_gate(self, question, options, task_id=None, flight_id=None, policy_reason=None,
                  timeout_at=None, now=None):
        now = now if now is not None else time.time()
        gid = new_id("gate")
        with self.tx():
            self.conn.execute(
                "INSERT INTO gates (id,task_id,flight_id,question,options,policy_reason,"
                "timeout_at,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (gid, task_id, flight_id, question, _j(options), policy_reason, timeout_at, now))
            self._event("gate.opened", gid,
                        {"task": task_id, "flight": flight_id, "question": question,
                         "policy_reason": policy_reason}, "tower", now)
        return gid

    def gate(self, gate_id):
        return self.conn.execute("SELECT * FROM gates WHERE id=?", (gate_id,)).fetchone()

    def open_gates(self):
        return self.conn.execute(
            "SELECT * FROM gates WHERE answer IS NULL ORDER BY created_at").fetchall()

    def answer_gate(self, gate_id, answer, answered_by, now=None):
        """Answering by id, and only once: a stale answer is refused, not applied."""
        now = now if now is not None else time.time()
        with self.tx():
            row = self.conn.execute("SELECT * FROM gates WHERE id=?", (gate_id,)).fetchone()
            if row is None:
                raise LedgerError(f"no such gate: {gate_id}")
            if row["answer"] is not None:
                self._abort()
                return False
            if row["timeout_at"] is not None and row["timeout_at"] <= now:
                self._event("gate.stale_answer_refused", gate_id, {"answer": answer}, "click", now)
                return False
            self.conn.execute(
                "UPDATE gates SET answer=?, answered_by=?, answered_at=? WHERE id=?",
                (answer, answered_by, now, gate_id))
            self._event("gate.answered", gate_id, {"answer": answer, "by": answered_by},
                        "click", now)
        return True

    # ---- integrity -------------------------------------------------------

    def integrity_check(self, now=None):
        """Return a list of problems. Empty means the file has exactly one reading."""
        now = now if now is not None else time.time()
        problems = []

        for row in self.conn.execute("PRAGMA integrity_check").fetchall():
            if row[0] != "ok":
                problems.append(f"sqlite: {row[0]}")

        for row in self.conn.execute("SELECT id, state FROM flights").fetchall():
            if row["state"] not in TRANSITIONS:
                problems.append(f"flight {row['id']}: unknown state {row['state']!r}")

        for row in self.conn.execute("SELECT id, state FROM tasks").fetchall():
            if row["state"] not in TASK_STATES:
                problems.append(f"task {row['id']}: unknown state {row['state']!r}")

        for row in self.conn.execute("SELECT id FROM flights WHERE state='landed'").fetchall():
            applied = self.conn.execute(
                "SELECT 1 FROM landings WHERE flight_id=? AND state='applied'",
                (row["id"],)).fetchone()
            if applied is None:
                problems.append(f"flight {row['id']}: landed with no applied landing")

        for row in self.conn.execute("SELECT id, state FROM flights").fetchall():
            seen = self.conn.execute(
                "SELECT 1 FROM events WHERE kind='flight.state' AND subject=?"
                " AND json_extract(payload,'$.to') = ?", (row["id"], row["state"])).fetchone()
            if seen is None:
                problems.append(f"flight {row['id']}: state {row['state']} has no event")

        for row in self.conn.execute("SELECT id, state FROM tasks").fetchall():
            seen = self.conn.execute(
                "SELECT 1 FROM events WHERE kind='task.state' AND subject=?"
                " AND json_extract(payload,'$.to') = ?", (row["id"], row["state"])).fetchone()
            if seen is None:
                problems.append(f"task {row['id']}: state {row['state']} has no event")

        for row in self.conn.execute(
            "SELECT l.resource, l.holder_flight, f.state FROM leases l"
            " LEFT JOIN flights f ON f.id = l.holder_flight").fetchall():
            if row["state"] not in ("queued", "running", "verifying", "verified", "landing",
                                    "resolving"):
                problems.append(
                    f"lease {row['resource']}: holder {row['holder_flight']} is {row['state']}")

        return problems
