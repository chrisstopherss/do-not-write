# Writing Process Lab

Offline Windows desktop app for generating and analyzing **synthetic** writing-process data: text reproduced progressively over a chosen duration, with configurable, seeded, reproducible mistake and revision simulation, a full event log, local analytics, and exports.

## Status: Phase 1 (research and mockups) only

Nothing is built yet beyond design. Be skeptical of anything below that doesn't say "done".

| Item | Status |
|---|---|
| Environment inspection | Done, `docs/01-environment-and-stack.md` |
| Stack decision (Python + PySide6) | Done, documented with comparison |
| Three UI mockups (rendered HTML + screenshots) | Done, `mockups/` |
| Design spec, states, accessibility | Done, `docs/02-ui-design-spec.md` |
| Scope decision on external typing | Done, `docs/03-scope-decisions.md` (feature not being built) |
| Engine, UI, storage, exports, tests | Not started |
| Windows .exe / installer | Not started. Cannot be built or verified in the Linux dev environment; needs a Windows CI runner |

Nothing has been tested because there is nothing to test yet.

## Viewing the mockups
Open `mockups/index.html` in a browser, or see `mockups/screenshots/`.
