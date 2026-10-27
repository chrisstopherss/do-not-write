CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE sessions (
  id            TEXT PRIMARY KEY,
  created_at    TEXT NOT NULL,
  label         TEXT NOT NULL DEFAULT '',
  status        TEXT NOT NULL,              -- idle|running|paused|completed|cancelled|interrupted
  duration_ms   INTEGER,
  seed          INTEGER,
  config_json   TEXT NOT NULL DEFAULT '{}',
  engine_version TEXT NOT NULL,
  app_version   TEXT NOT NULL,
  source_text   TEXT NOT NULL DEFAULT '',   -- immutable original input
  final_text    TEXT,
  progress_cursor INTEGER NOT NULL DEFAULT 0,
  active_ms     INTEGER NOT NULL DEFAULT 0,
  deadline_json TEXT
);

CREATE TABLE events (
  session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  seq         INTEGER NOT NULL,
  t_ms        INTEGER,                      -- NULL = not recorded
  kind        TEXT NOT NULL,
  source      TEXT NOT NULL,                -- simulated|captured|imported
  pos         INTEGER,
  removed     TEXT NOT NULL DEFAULT '',
  inserted    TEXT NOT NULL DEFAULT '',
  duration_ms INTEGER,
  meta_json   TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (session_id, seq)
);

-- Snapshots only for data that has no event stream (e.g. imported revision snapshots).
-- Sessions with events reconstruct versions by replay and do not duplicate text here.
CREATE TABLE checkpoints (
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  idx        INTEGER NOT NULL,
  t_ms       INTEGER,
  label      TEXT NOT NULL DEFAULT '',
  text       TEXT NOT NULL,
  PRIMARY KEY (session_id, idx)
);

CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
