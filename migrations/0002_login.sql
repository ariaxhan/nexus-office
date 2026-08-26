-- Rate limiting the password door.
--
-- A password endpoint with no throttle is a password endpoint that gets guessed.
-- One row per failed attempt, counted per client IP over a short window; the rows
-- are swept on every check so the table never grows.

CREATE TABLE IF NOT EXISTS login_attempt (
  ip TEXT NOT NULL,
  at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS login_attempt_ip ON login_attempt (ip, at);
