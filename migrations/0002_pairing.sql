-- Signing in on a phone.
--
-- The view token is 48 hex characters, which is the right length for a secret and
-- the wrong length for a thumb. The local runner mints a six-character code that
-- can be exchanged for the real token exactly once, inside ten minutes. The token
-- itself is never stored here: the row is a permission slip, not a copy of the key.

CREATE TABLE IF NOT EXISTS pairing (
  code TEXT PRIMARY KEY,
  at   TEXT NOT NULL,
  used INTEGER NOT NULL DEFAULT 0
);
