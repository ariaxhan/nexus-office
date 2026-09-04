"""A v1 ledger shaped like the live file, for migration tests.

`build(path)` writes user_version 1 with the seven engine tables plus the four
legacy tables (objectives, observations, messages, gates), using the DDL the
live ledger reports from sqlite_master, and seeds one row in each so a
migration has something to copy, count, and drop. Nothing here imports
nexus.ledger: opening the file through Ledger would migrate it on the spot.
"""

import sqlite3

#: the live v1 DDL, verbatim from `SELECT sql FROM sqlite_master`.
V1_SCHEMA = """
CREATE TABLE objectives (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  statement TEXT NOT NULL,
  owner TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  autonomy_policy TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL
);
CREATE TABLE plans (
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
CREATE TABLE observations (
  id TEXT PRIMARY KEY,
  ts REAL NOT NULL,
  source TEXT NOT NULL,
  subject TEXT,
  payload TEXT NOT NULL DEFAULT '{}',
  handled_by_task TEXT REFERENCES tasks(id)
);
CREATE TABLE tasks (
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
CREATE INDEX tasks_state ON tasks(state);
CREATE INDEX tasks_dedupe ON tasks(dedupe_key, state);
CREATE TABLE flights (
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
CREATE INDEX flights_state ON flights(state);
CREATE INDEX flights_plan ON flights(plan_id, created_at);
CREATE INDEX flights_task ON flights(task_id);
CREATE TABLE artifacts (
  id TEXT PRIMARY KEY,
  flight_id TEXT NOT NULL REFERENCES flights(id),
  kind TEXT NOT NULL,
  ref TEXT NOT NULL,
  sha TEXT,
  created_at REAL NOT NULL
);
CREATE TABLE landings (
  id TEXT PRIMARY KEY,
  flight_id TEXT NOT NULL REFERENCES flights(id),
  target TEXT NOT NULL,
  expected_sha TEXT,
  verify_flight_id TEXT,
  state TEXT NOT NULL,
  applied_sha TEXT,
  created_at REAL NOT NULL
);
CREATE INDEX landings_state ON landings(state);
CREATE TABLE messages (
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
CREATE INDEX messages_task ON messages(task_id, delivered_at);
CREATE TABLE gates (
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
CREATE TABLE events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  kind TEXT NOT NULL,
  subject TEXT,
  payload TEXT NOT NULL DEFAULT '{}',
  source TEXT NOT NULL
);
CREATE INDEX events_kind ON events(kind, id);
CREATE INDEX events_subject ON events(subject, id);
CREATE TABLE leases (
  resource TEXT PRIMARY KEY,
  holder_flight TEXT NOT NULL REFERENCES flights(id),
  until REAL NOT NULL
);
CREATE TRIGGER events_no_update
BEFORE UPDATE ON events BEGIN
  SELECT RAISE(ABORT, 'events are append-only');
END;
CREATE TRIGGER events_no_delete
BEFORE DELETE ON events BEGIN
  SELECT RAISE(ABORT, 'events are append-only');
END;
CREATE TRIGGER flights_terminal_no_update
BEFORE UPDATE ON flights WHEN old.state IN ('landed','failed','cancelled') BEGIN
  SELECT RAISE(ABORT, 'terminal flight is history');
END;
CREATE TRIGGER flights_no_delete
BEFORE DELETE ON flights BEGIN
  SELECT RAISE(ABORT, 'flights are append-only');
END;
CREATE TRIGGER artifacts_no_update
BEFORE UPDATE ON artifacts BEGIN
  SELECT RAISE(ABORT, 'artifacts are append-only');
END;
CREATE TRIGGER artifacts_no_delete
BEFORE DELETE ON artifacts BEGIN
  SELECT RAISE(ABORT, 'artifacts are append-only');
END;
CREATE TRIGGER landings_applied_no_update
BEFORE UPDATE ON landings WHEN old.state = 'applied' BEGIN
  SELECT RAISE(ABORT, 'an applied landing is history');
END;
CREATE TRIGGER observations_no_delete
BEFORE DELETE ON observations BEGIN
  SELECT RAISE(ABORT, 'observations are append-only');
END;
CREATE TRIGGER tasks_decided_no_redecide
BEFORE UPDATE ON tasks
WHEN old.state IN ('rejected_duplicate','rejected_policy','done','abandoned') BEGIN
  SELECT RAISE(ABORT, 'a decided task is history');
END;
PRAGMA user_version=1;
"""

T0 = 1_757_000_000.0

#: one row per table, in insert order so every foreign key already exists.
SEED = """
INSERT INTO objectives VALUES
  ('obj_1', 'ship', 'keep main green', 'aria', 1, '{"gate": "risky"}', :t);
INSERT INTO plans VALUES
  ('plan_1', 'obj_1', 'nightly', 'script', '{"every": 60}', '{"cmd": "true"}',
   '[]', '{"timeout_s": 5}', '{}', '[]', 1, NULL, :t);
INSERT INTO tasks VALUES
  ('task_1', 'obj_1', 'plan_1', 'plan', 'run nightly', NULL, NULL, NULL, NULL,
   'running', 'nightly', NULL, NULL, :t);
INSERT INTO flights VALUES
  ('flt_1', 'task_1', 'plan_1', 'running', NULL, NULL, NULL, :t, :t, NULL, NULL, 1, NULL);
INSERT INTO observations VALUES
  ('obs_1', :t, 'github', 'ariaxhan/nexus-office#1', '{"event": "opened"}', 'task_1');
INSERT INTO messages VALUES
  ('msg_delivered', 'task_1', NULL, 'flight', 'flt_1', 'carry on', :t, 'flt_1', :t),
  ('msg_pending', 'task_1', 'flt_1', 'task', 'task_1', 'need a review', :t, NULL, NULL);
INSERT INTO gates VALUES
  ('gate_1', 'task_1', 'flt_1', 'push to main?', '["yes", "no"]', 'risky',
   NULL, NULL, NULL, NULL, :t);
"""


def build(path):
    """Write a seeded v1 ledger at `path` and return it."""
    conn = sqlite3.connect(path)
    conn.executescript(V1_SCHEMA)
    for statement in SEED.strip().split(";"):
        if statement.strip():
            conn.execute(statement, {"t": T0})
    conn.commit()
    conn.close()
    return path
