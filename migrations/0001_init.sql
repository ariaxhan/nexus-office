-- nexus-office: the cloud half of the office.
-- Two tables only. The snapshot is what the office LOOKS like; the decision
-- queue is what Aria did to it. The Worker never talks to GitHub: it holds
-- state and intent, and the local runner is the only thing with credentials.

CREATE TABLE IF NOT EXISTS snapshot (
  id   INTEGER PRIMARY KEY CHECK (id = 1),  -- exactly one row, always
  at   TEXT NOT NULL,
  json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decision (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  at         TEXT NOT NULL,
  kind       TEXT NOT NULL,   -- comment | unblock | close | reopen | label | nudge
  repo       TEXT NOT NULL,
  issue      TEXT,
  payload    TEXT,            -- JSON: {body, label, ...}
  status     TEXT NOT NULL DEFAULT 'pending',  -- pending | done | failed
  applied_at TEXT,
  result     TEXT
);

CREATE INDEX IF NOT EXISTS decision_pending ON decision (status, id);
