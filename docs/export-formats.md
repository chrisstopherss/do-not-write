# Export and import formats

All exports state their data origin. Files are never overwritten: a name clash produces `name (1).ext`.

## TXT
Final text of a session, UTF-8.

## Markdown / HTML report
Heading, a blockquote stating `Data origin: SIMULATED|SYNTHETIC|IMPORTED|CAPTURED`, a revision table, then a metric
table (extractor, version, metric, status, value). Missing values print as `Not recorded`. HTML is a single file with a
restrictive Content-Security-Policy; all text is HTML-escaped.

## CSV (events): one row per event
`session_id, data_origin, seq, t_ms, kind, source, pos, removed_chars, inserted_chars, removed, inserted, duration_ms, category`
`t_ms` blank = not recorded. CRLF line endings, RFC 4180 quoting, formula-injection guard on text cells.

## CSV (metrics)
`session_id, data_origin, extractor, extractor_version, metric, status, value, note`
`value` holds JSON for structured metrics. `status` is `ok`, `missing` or `insufficient_data`.

## JSON dataset `wpl.dataset/1`
```
{ "schema": "wpl.dataset/1", "name", "app_version", "engine_version", "provenance_notice",
  "sessions": [ { "id", "label", "created_at", "data_origin", "status", "duration_ms", "seed", "engine_version",
                  "app_version", "config", "source_text", "final_text",
                  "events": [ {"seq","t_ms","kind","source","pos","removed","inserted","duration_ms","meta"} ],
                  "user_actions": [ {"seq","active_ms","action","phase","source":"captured"} ],   // real app telemetry, when recorded
                  "features": { "<extractor>.<metric>": {"value","status","note","version"} } } ] }
```
`config.rhythm` holds the writing-rhythm parameters (if used). Pause events carry `duration_ms` and `meta.pause_class`
(`short` or `break`). Keys are sorted; output is deterministic for identical data.

## Import `wpl.revision_history/1`
Documented in the in-app guide ("Document history vs final text") and `src/wpl/lab/guide/document_history_research.md`;
an example is saved by the Import tab and in `examples/revision_history_example.json`.

## Experiment configs `wpl.experiment_config/1`
`examples/experiment_*.json`: title, description, expected outcome, repetitions, base seed, and a list of
condition parameter objects (`ConditionParams.to_dict()`). These are documentation/examples: the UI templates build the same
parameters; there is no "load config file" button yet.

## Not implemented
DOCX export.
