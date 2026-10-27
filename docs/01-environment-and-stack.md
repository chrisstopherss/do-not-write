# Phase 1: environment findings and stack decision

## Environment (inspected, not assumed)

| Item | Finding |
|---|---|
| Repo | Empty, no commits at start |
| Dev OS | Linux container (not Windows) |
| Python | 3.13 available; PySide6 6.12 installs and imports (`PySide6-Essentials`) |
| .NET SDK / WPF / WinUI | Not installed. WPF and WinUI only run on Windows, so UI could not be executed or tested here |
| Node / Cargo | Present. Playwright + Chromium present (used to render mockups) |
| Wine, Inno Setup, NSIS | Not installed |
| Windows VM | None |

**Consequence:** nothing built in this environment can produce or verify a Windows `.exe` or installer. Any release artifact has to be built on a Windows runner. Claims about "tested on clean Windows" can only be made after that happens.

## Stacks compared

| | C# / .NET + WPF | Python + PySide6 | Tauri + React/TS |
|---|---|---|---|
| Runs and tests UI in this environment | No | Yes (Qt `offscreen` platform) | Partly (web part only; Rust shell needs WebView2 stack) |
| Engine unit tests here | Yes if engine is pure .NET, but needs SDK install | Yes, trivially | Yes (TS or Rust) |
| Windows packaging | Excellent (publish single-file, MSIX/Inno) | PyInstaller or Nuitka + Inno Setup; larger binary (~80-150 MB), occasional AV false positives on one-file mode | Good, small binaries, installers built in |
| Accessibility | Best (UIA built in) | Good (Qt exposes UIA on Windows) | Depends on web a11y discipline |
| Offline / no network surface | Easy | Easy | WebView is a larger attack surface; needs CSP lockdown |
| Data/analysis tooling | OK | Best (stdlib `sqlite3`, `csv`, `random`, easy stats) | OK |
| Team complexity | Single language | Single language | Three layers (TS, Rust, WebView) |

## Decision: Python 3.12+ with PySide6 (Qt 6)

Reasons, in order of weight:

1. It is the only option where I can run the real UI and tests in this environment, so "tested" can mean tested.
2. The engine is the hard part (deterministic scheduling and simulation). Pure-Python, UI-independent, 100% unit-testable.
3. Stdlib covers SQLite, CSV, JSON, hashing. Fewer dependencies to audit.
4. Qt gives proper native widgets, high-DPI, theming, and UI Automation accessibility on Windows.

Costs I am accepting: bigger binaries than .NET/Tauri, and PyInstaller one-file builds trip some antivirus heuristics (the installer + one-folder build is the recommended path; portable one-file is secondary). Unsigned binaries will show SmartScreen warnings.

### Dependencies (kept minimal)
- `PySide6-Essentials` (LGPLv3; dynamic linking, ship license notices)
- `python-docx` (MIT) for DOCX export, optional
- Dev only: `pytest`, `ruff`, `mypy`, `pip-audit`, `pyinstaller`
- No network libraries. A test will assert the app imports no `socket`/`urllib`/`http` usage in app code paths.

Pinned via `requirements.lock` with hashes (`pip-compile --generate-hashes`).

### Build/release plan
- `.github/workflows/windows-release.yml` on `windows-latest`: install pinned deps, run tests (offscreen), build PyInstaller one-folder + one-file, build Inno Setup installer, compute SHA-256, upload artifacts.
- Nothing is called "verified" until that workflow has actually run and a human has launched the result on a clean Windows machine.

## Architecture (module boundaries)

```
src/wpl/
  engine/        pure Python, no Qt, no I/O
    text.py        sentence/paragraph segmentation (Unicode aware)
    schedule.py    duration -> event times; deadline + bounded grace rule
    simulate.py    seeded event generation per category, defined denominators
    session.py     state machine: idle/ready/running/paused/completed/cancelled/failed
    clock.py       monotonic Clock interface (injectable for tests)
  analysis/      plugin-style modules: id, version, run(input)->Result
  storage/       sqlite3, parameterized queries, migrations/
  io/            import (size/encoding checks), export (txt/md/json/csv/docx)
  ui/            PySide6 only; talks to engine through signals
  app.py         wiring, logging, settings
tests/
```

Rules: `engine/` never imports Qt; `ui/` never contains scheduling or simulation logic; every random draw goes through one seeded `random.Random` (Mersenne Twister, documented) owned by the simulator, with draws ordered by document position so output does not depend on execution order.
