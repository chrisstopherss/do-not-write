# Testing report

Environment: Linux container, Python 3.13.17, PySide6-Essentials 6.12.0, pytest 9.2.0, ruff 0.17.3, Qt `offscreen` platform.
**Nothing was run on Windows.** Date of the last full run: 2026-10-28.

## Executed and passing

| Check | Command | Result |
|---|---|---|
| Automated tests | `pytest -q` | **217 passed**, 0 failed, 0 skipped (~25 s) |
| Lint | `ruff check src tests` (rules E,F,W,I,B) | all checks passed |
| Dependency audit | `pip-audit -r requirements.lock --require-hashes` | no known vulnerabilities (uses the network from the dev machine; the app does not) |
| Selftest from source | `python -m wpl --selftest` | 5/5 checks pass |
| Frozen Linux build | `pyinstaller packaging/wpl.spec`, then `WritingProcessLab --selftest` | 5/5 checks pass; this found and fixed a real packaging bug (relative import in the entry script) |
| App startup | `python -m wpl` under offscreen Qt for 6 s | started, created `wpl.db` and `logs/wpl.log`, no traceback |

### Test inventory (by file)
| File | Tests | Covers |
|---|---|---|
| test_text_events.py | 8 | Unicode counting, sentence spans, event validation, replay of insert/delete/replace with provenance, mismatch handling, ordering |
| test_schedule_session.py | 23 | duration bounds (1 s ... 24 h, invalid values), fit-to-duration, checkpoints, deadline with grace, grace cap, pause/resume on active time, late-poll burst limit, illegal transitions, cancel |
| test_simulate.py | 17 | zero-rate identity, same-seed reproducibility, config round trip, replay-equals-final invariant over 160 seeded runs incl. emoji/CJK, measured rate vs denominator (substitution at 20%: within 2 points over >8000 draws), full correction, single-modification-per-word, grammar swaps, abandonment, rate limits, no global RNG use |
| test_analysis.py | 18 | hand-verified process metrics (counts, intervals, active/idle, revisions per minute, growth, provenance share), missing vs zero, typing-speed refusal on coarse data, text metrics, diff, versions, confusion-matrix metrics, Wilson interval, empty/single-class data, deterministic split, lexical/readability/similarity values |
| test_storage_service.py | 21 | v1 -> latest migration with data preserved, idempotent migration, corrupt DB + quarantine, unopenable path vs corruption, source-text immutability, SQL-injection string stored inertly, None timestamps round trip, cascade delete, full session persisted, all duration boundaries, **crash recovery (kill mid-session, restart, resume to identical text)**, tampered-event refusal, cancel keeps partial text, empty and one-character text, **document text absent from log file** |
| test_io_lab.py | 42 | CSV formula injection, CSV quoting, unique filenames, report/JSON origin labels, import accept/reject (12 malformed variants, authorization, snapshots-only, no code execution, atomic rollback), conditions A-E determinism and signatures, parameter validation, event/session source labels, example experiment, reproducibility across databases, evaluation train/test split and small-sample warnings, **no network/eval/pickle in source**, **runs with sockets disabled**, example config files |
| test_ui.py | 11 | all pages construct, empty states, live view equals controller text with emoji and newlines (UTF-16 offset mapping), view rejects user edits, start/pause flow, invalid duration message, dashboard values come from stored events, untimed imported events show "Not recorded", import rejection leaves nothing, final-text tab has no probability score, theme switch, delete-research-data keeps normal sessions |

## Not run / not verified
* **Any Windows execution**: portable exe, installer, uninstaller, Start Menu shortcut, clean-VM startup, offline run on Windows.
* The GitHub Actions workflow has never run.
* Screen-reader testing, keyboard-only walkthrough, high-DPI and high-contrast modes. Accessible names are set on controls and
  charts expose text summaries, but this has not been tested with assistive technology.
* Disk-full behavior (the active page handles a storage error by pausing, but only unopenable-path errors are tested).
* UI path for "Resume interrupted" (the controller path it calls is tested).
* The deadline banner text in the UI (the underlying rule is tested).
* Performance with the maximum allowed input (1M characters).
* Offline operation was tested by disabling sockets in-process, not by disconnecting the machine.

## Known defects and limitations
* The example evaluation shows poor recall for the bulk-insert rule on condition E (the 200-character threshold treats E's
  middle insertion as bulk but its share of the final text is below the learned threshold). This is real, expected behavior
  of the demo rule and is why the tab carries warnings; it is not a bug.
* Spelling dictionary and grammar-pattern analysis modules, DOCX export, app icon, settings for default duration / default
  mistake configuration / export preferences / retention, and a "load experiment config from file" button are **not implemented**.
* Python 3.13 was used for testing; the project declares >=3.12 and CI pins 3.12.

## v0.3 (writing rhythm) additions
217 tests pass (66 in `test_rhythm.py`, 22 UI tests), ruff clean, `pip-audit` clean, frozen Linux build selftest passes (6 checks). Legacy planner output is pinned by golden hashes and unchanged. Not run: Windows build/installer (CI does this on tag push; see the release page), screen readers.
