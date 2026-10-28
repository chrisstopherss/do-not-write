# Architecture

Python 3.12+, PySide6 (Qt 6), SQLite (stdlib). No network libraries are imported anywhere (enforced by a test).

```
src/wpl/
  engine/      pure Python, no Qt, no I/O
    text.py      Unicode-aware words / sentences / paragraphs
    events.py    Event model (kind, source, t_ms|None), validation, ordering check, replay with per-character provenance
    builder.py   DocBuilder used by generators
    simulate.py  seeded mistake engine (8 categories, defined denominators, one modification per word)
    schedule.py  duration limits, fit-to-duration, checkpoints, deadline + bounded grace rule
    session.py   playback state machine on an injectable monotonic clock (pause/resume/cancel/restore);
                 phases Writing / Break / Paused / Completed, simulated idle-time accounting
    rhythm.py    RhythmConfig (validation limits), presets Regular / Reflective / Extended Breaks / Custom, draw logic
    breaks.py    break scheduler: plan_rhythm() and schedule summaries (active vs idle, segments)
    revise.py    seeded revision operations shared by condition D and rhythm profiles
  analysis/    features.py (versioned extractors, missing != zero), rhythm.py (break/idle analytics), diff.py, revisions.py,
               finaltext.py (descriptive statistics only), evaluation.py (confusion matrix, Wilson CI, split)
  lab/         conditions.py (A-E generators), runner.py (experiments), methods.py (evaluated rule),
               templates.py, overview.py (dashboard data shaping), guide/*.md (research docs)
  storage/     db.py + migrations/001_core.sql, 002_lab.sql (PRAGMA user_version)
  io/          exporters.py (CSV with formula-injection guard, JSON, MD, HTML, unique filenames),
               importer.py (wpl.revision_history/1), textfiles.py (validated input files)
  service.py   SessionController (engine <-> database), plan rebuild for crash recovery, analyze_session
  ui/          main_window, session_pages, lab_pages, charts (QPainter), theme, widgets
  app.py       entry point, logging, --selftest [--selftest-out FILE]
```

## Key design decisions

* **Events are the ground truth.** Versions, growth curves, diffs and process metrics are all derived from stored
  events (or imported snapshots). Nothing is stored twice: sessions keep `source_text` once; checkpoints are markers.
* **Provenance on every record.** `events.source` (simulated/captured/imported) and `sessions.data_origin`
  (simulated/synthetic/imported/captured) are NOT NULL columns. Exports and the UI print them.
* **Determinism.** One `random.Random(seed)` (Mersenne Twister) per run; draws in document order; the engine version is
  stored with each session. Crash recovery regenerates the plan from seed + config and refuses to resume if the stored
  events do not match the regenerated plan.
* **Active-time clock.** Elapsed time excludes pauses. Events are released by absolute active time, so late timer
  callbacks cannot accumulate drift; the UI arms one single-shot timer for the next event instead of polling.
* **Deadline rule.** If planned events run past the duration, the sentence in progress is finished provided it ends within
  a grace period (default 5 min, hard cap 10 min). Otherwise output stops at the previous sentence boundary and the
  result is flagged `deadline_priority`.
* **Missing data.** Extractors return `missing` or `insufficient_data` (value null) rather than 0.
* **Break scheduler.** `plan_rhythm` generates the writing stream with absolute-length pauses and breaks from the
  session seed, then `fit_active` rescales only active writing time so the whole plan (writing + breaks) fits the selected
  duration. If breaks plus a minimum writing time (30 ms/char, a feasibility floor) exceed the duration it raises
  `InfeasibleSchedule`, whose message offers a longer duration or less total break time. With a fixed pace the plan is not
  fitted and `apply_deadline` decides the outcome, recorded as `normal`, `grace` or `deadline_stop` in `sessions.deadline_json`.
  Breaks are capped at 5 minutes and cannot extend a session: nothing is ever scheduled past duration + grace.
* **Real vs simulated.** Pauses and breaks are simulated events. The user's own Pause/Resume/Cancel are real application
  telemetry stored in `user_actions` (migration 003), never in `events`, so replay and crash recovery are unaffected.
  Pauses the UI makes itself (holding the clock while a confirmation is open) are not logged.
* **Migrations.** Numbered SQL files applied in a transaction; `002` adds columns with defaults, and `003` adds the `user_actions` table, so older databases upgrade
  without data loss (tested). Sessions made before the rhythm feature keep recovering: the legacy planner is unchanged and its
  output is pinned by golden hashes in the tests.

## Mistake categories and denominators
See the module docstring in `engine/simulate.py`; the UI shows the denominator next to each rate. Percentages are
per-opportunity probabilities, not guarantees about the share of erroneous words in the output.

## Extending
* New analysis: write a function `AnalysisInput -> list[FeatureValue]`, register an `Extractor(id, version, ...)` in
  `analysis/features.py`. Bump its version when the definition changes. Add a hand-verified test.
* New condition: add a branch in `lab/conditions.generate` that drives a `DocBuilder`; add it to `CONDITION_NAMES`.
* New migration: add `NNN_name.sql`; never edit an applied one.
