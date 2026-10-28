# Writing Process Lab

Offline desktop app (Python + PySide6) for generating and analyzing **synthetic** writing-process data:
text reproduced progressively over a chosen duration with seeded, reproducible mistake simulation, an event log, local
analytics, a research lab for comparing workflows, and exports. Everything stays on your machine.

> It is a research tool. All generated output and events are labeled SIMULATED or SYNTHETIC. No metric here establishes
> authorship or how a document was created, and there is no AI/human score. It does **not** type into other applications,
> connect to Google accounts, or try to defeat detectors or forge revision histories (see `docs/03-scope-decisions.md`).

## Status (honest)

| Area | Status |
|---|---|
| Session engine (schedule, deadline/grace rule, pause/resume/cancel, crash recovery) | Implemented, tested |
| Mistake engine (8 categories, defined denominators, seeds) | Implemented, tested |
| UI: New session, Active session, History/results, Settings | Implemented (screenshot-checked offscreen, 11 UI tests) |
| Writing Process Lab: overview, timeline, revisions, charts, experiments A-E, evaluation, final-text research, import, guide, data & privacy | Implemented, tested |
| SQLite storage with migrations, local deletion controls | Implemented, tested |
| Exports: TXT, Markdown, HTML, JSON, CSV (formula-injection safe) | Implemented, tested. **DOCX not implemented** |
| Dictionary spelling / grammar-pattern analysis modules | **Not implemented** |
| External typing, Google Docs integration | **Not built, by decision** |
| Windows exe, installer | Build files written (`packaging/`, CI workflow). **Never built or run on Windows.** Linux build of the same spec was made and self-tested |
| Code signing, app icon, license file | Not done |

140 automated tests pass on Linux (see `docs/testing-report.md`, including what was not run).

## Run from source
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.lock && pip install -e . --no-deps
python -m wpl                 # launch
python -m wpl --selftest      # non-interactive release check
pip install pytest ruff && pytest -q && ruff check src tests
```
Data: `%LOCALAPPDATA%\WritingProcessLab` (Windows) or `~/.local/share/WritingProcessLab`; override with `WPL_DATA_DIR`.

## Using it
1. **New session**: paste or import text, pick a duration (30 min / 1 h / 2 h / custom up to 24 h), set mistake categories,
   optionally preview transformations, start. Same seed + text + settings reproduces the same events.
2. **Writing Process Lab > Overview > Load example experiment** gives you 25 simulated runs across conditions A-E to explore.
3. Read the **Guide** tab before drawing conclusions from any chart.

## Documentation
`docs/01-environment-and-stack.md`, `02-ui-design-spec.md`, `03-scope-decisions.md`, `architecture.md`, `threat-model.md`,
`export-formats.md`, `windows-build.md`, `testing-report.md`; research guide and metric definitions in `src/wpl/lab/guide/`;
`THIRD_PARTY_NOTICES.md`; example configs in `examples/`; Phase 1 mockups in `mockups/`.
