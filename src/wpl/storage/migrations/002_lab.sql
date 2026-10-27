ALTER TABLE sessions ADD COLUMN data_origin TEXT NOT NULL DEFAULT 'simulated';  -- synthetic|simulated|imported|captured
ALTER TABLE sessions ADD COLUMN dataset_id TEXT;

CREATE TABLE experiments (
  id         TEXT PRIMARY KEY,
  label      TEXT NOT NULL,
  created_at TEXT NOT NULL,
  notes      TEXT NOT NULL DEFAULT '',
  expected   TEXT NOT NULL DEFAULT '',
  is_example INTEGER NOT NULL DEFAULT 0,
  status     TEXT NOT NULL DEFAULT 'created'
);

CREATE TABLE conditions (
  id            TEXT PRIMARY KEY,
  experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
  code          TEXT NOT NULL,              -- A..E
  params_json   TEXT NOT NULL,
  ground_truth  TEXT NOT NULL DEFAULT ''    -- label known from how the data was generated
);

CREATE TABLE runs (
  id           TEXT PRIMARY KEY,
  experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
  condition_id TEXT NOT NULL REFERENCES conditions(id) ON DELETE CASCADE,
  session_id   TEXT REFERENCES sessions(id) ON DELETE SET NULL,
  rep          INTEGER NOT NULL,
  seed         INTEGER NOT NULL,
  status       TEXT NOT NULL
);

CREATE TABLE analysis_runs (
  id           TEXT PRIMARY KEY,
  created_at   TEXT NOT NULL,
  engine_version TEXT NOT NULL,
  extractors_json TEXT NOT NULL
);

CREATE TABLE features (
  analysis_run_id TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
  session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  extractor_id    TEXT NOT NULL,
  extractor_version TEXT NOT NULL,
  name            TEXT NOT NULL,
  value_json      TEXT,                      -- NULL when status != ok
  status          TEXT NOT NULL,             -- ok|missing|insufficient_data
  note            TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (analysis_run_id, session_id, extractor_id, name)
);

CREATE TABLE evaluation_results (
  id            TEXT PRIMARY KEY,
  experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
  created_at    TEXT NOT NULL,
  method_id     TEXT NOT NULL,
  method_version TEXT NOT NULL,
  split_json    TEXT NOT NULL,
  tp INTEGER NOT NULL, fp INTEGER NOT NULL, tn INTEGER NOT NULL, fn INTEGER NOT NULL,
  metrics_json  TEXT NOT NULL
);

CREATE TABLE datasets (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  origin      TEXT NOT NULL,                 -- synthetic|imported
  created_at  TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  source_description TEXT NOT NULL DEFAULT '',
  sha256      TEXT NOT NULL
);

CREATE INDEX idx_events_session ON events(session_id, seq);
CREATE INDEX idx_runs_experiment ON runs(experiment_id);
