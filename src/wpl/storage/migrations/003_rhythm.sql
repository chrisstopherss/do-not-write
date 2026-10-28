-- Real application telemetry: actions the user took on a running session (pause, resume, cancel).
-- Kept separate from `events`, which hold simulated (or imported) writing events and are replayed to rebuild text.
-- Existing tables are untouched; sessions created before this migration simply have no rows here.
CREATE TABLE user_actions (
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  seq        INTEGER NOT NULL,
  active_ms  INTEGER NOT NULL,              -- simulated session clock at the moment of the action
  action     TEXT NOT NULL,                 -- pause|resume|cancel
  phase      TEXT NOT NULL,                 -- Writing|Break|Paused at that moment
  PRIMARY KEY (session_id, seq)
);
