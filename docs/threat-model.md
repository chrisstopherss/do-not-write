# Threat model and security notes

Scope: a local, single-user desktop app that stores text and event data in a local SQLite file. It makes no network
connections. This is not a claim that the app is perfectly secure.

| Threat | Mitigation | Residual risk |
|---|---|---|
| Malicious imported file (huge, malformed, hostile content) | `.json` only, 20 MB / 200k events / 5k snapshots limits, strict type validation, UTF-8 only, replay consistency check, all-or-nothing import, content treated as data (never evaluated, no pickle/eval) | Very large text fields inside the limits can still use memory |
| Malicious text file input | Extension allow-list, 2 MB / 1M char limit, UTF-8/UTF-16 decoding with explicit error, NUL check | Large inputs make the UI slow |
| SQL injection | Parameterized queries only; the few dynamic SQL fragments use whitelisted column/table names (tested with an injection string) | None known |
| Spreadsheet formula injection in CSV | Cells starting with `= + - @ TAB CR LF` are prefixed with `'` (tested) | Spreadsheet apps may still show the apostrophe |
| Accidental exposure of document text | Logs contain ids, counts and state names only (tested); no telemetry; no network code; data in the user's local app-data folder | Local file is not encrypted; anyone with access to the user account can read it. No disk encryption is provided |
| Overwriting user files | Exports use exclusive-create with unique names; source text immutable in the DB (tested) | None known |
| Dependency compromise | Hash-pinned `requirements.lock`, `pip-audit` (no known vulnerabilities at the time of the run, see testing report), minimal dependency set, network-capable Qt modules excluded from the bundle | Supply-chain attacks on PyPI/Qt/PyInstaller remain possible; audits only know public advisories |
| Corrupted database | `quick_check` at open; corrupt files are moved aside (never deleted) and a new DB is created after user consent; unopenable paths are reported separately from corruption | Partial corruption that passes `quick_check` is not detected |
| Interrupted session / crash | Progress persisted as events play; startup marks sessions `interrupted`; resume rebuilds the plan and verifies it matches stored events | Resume refuses if the engine version changed |
| Unauthorized external input | Feature not implemented: the app never sends input to other applications | - |
| Misuse to misrepresent authorship | See `docs/03-scope-decisions.md`. Outputs are labeled SIMULATED/SYNTHETIC everywhere and the app has no external typing, no Google integration, no detector evasion | Exported text is plain text and can be passed off by someone determined; labels in files can be edited |
| Unsigned binaries | Not signed; Windows SmartScreen will warn. Signing requires an Authenticode certificate (typically a few hundred USD/year for OV/EV) | Users cannot verify publisher identity; use the published SHA-256 sums |

Assumptions: the user's OS account is trusted; the machine is not already compromised; the Python/Qt runtime is genuine.
Logs are plain files and are not tamper-proof.
