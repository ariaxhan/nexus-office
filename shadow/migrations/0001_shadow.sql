-- nexus-shadow (#310): a copy of the Studio's fault + dispatch records, read by nobody operationally.
-- The Studio ledger is authoritative in every conflict; nothing here ever writes back.

-- One row per record revision, exactly as the Studio serialised it (record_json is byte-identical).
CREATE TABLE IF NOT EXISTS records (
  event_id TEXT PRIMARY KEY,            -- <namespace>:<record_id>:<revision>
  namespace TEXT NOT NULL CHECK (namespace IN ('office.dispatch', 'office.fault')),
  record_id TEXT NOT NULL,
  revision TEXT NOT NULL,               -- sha256(record_json + transitions)[:16]
  record_json TEXT NOT NULL CHECK (json_valid(record_json)),
  sha256 TEXT NOT NULL,                 -- sha256(record_json), checked on ingest
  transitions TEXT NOT NULL CHECK (json_valid(transitions)),
  origin TEXT NOT NULL,                 -- Studio host that wrote it
  observed_at REAL NOT NULL,
  received_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  judged_at TEXT
);
CREATE INDEX IF NOT EXISTS records_by_record ON records(namespace, record_id, observed_at);

-- One outcome pair per (revision, step): what the Studio did beside what the shadow's rules allow.
CREATE TABLE IF NOT EXISTS outcome_pairs (
  event_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  namespace TEXT NOT NULL,
  record_id TEXT NOT NULL,
  from_state TEXT,
  to_state TEXT,
  studio_outcome TEXT NOT NULL,
  shadow_outcome TEXT NOT NULL,          -- json array of the outcomes the shadow allows
  studio_verdict TEXT NOT NULL,
  shadow_verdict TEXT NOT NULL,
  match INTEGER NOT NULL CHECK (match IN (0, 1)),
  judged_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  PRIMARY KEY (namespace, record_id, seq)          -- seq: the Studio ledger event id of the step
);

-- What the consumer would have launched. Never fired: fired is 0 by construction.
CREATE TABLE IF NOT EXISTS simulated_invocations (
  record_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  queue TEXT NOT NULL,
  decision TEXT NOT NULL,
  fired INTEGER NOT NULL DEFAULT 0 CHECK (fired = 0),
  at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  PRIMARY KEY (record_id, seq)
);

-- The comparator's running summary, one row per run: the graduation evidence surface.
CREATE TABLE IF NOT EXISTS parity_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  records INTEGER NOT NULL,
  unjudged INTEGER NOT NULL,
  pairs INTEGER NOT NULL,
  matches INTEGER NOT NULL,
  mismatches INTEGER NOT NULL,
  window_start TEXT,
  summary TEXT NOT NULL CHECK (json_valid(summary))
);
