# Building and releasing on Windows

**Status: nothing in this file has been executed on Windows.** The Linux build of the same PyInstaller spec was built and
its `--selftest` passed (see testing report), which validates data-file bundling and imports, not Windows behavior.

## Prerequisites
Windows 10/11 x64, Python 3.12, Git, [Inno Setup 6](https://jrsoftware.org/isinfo.php).

## Manual build
```bash
python -m venv .venv && .venv\Scripts\activate
pip install --require-hashes -r requirements.lock
pip install -e . --no-deps
pip install pytest ruff pip-audit pyinstaller
ruff check src tests
pytest -q
pyinstaller packaging/wpl.spec --noconfirm
dist\WritingProcessLab\WritingProcessLab.exe --selftest
```
Portable single file: set `WPL_ONEFILE=1` and run PyInstaller again (output `dist\WritingProcessLab-portable.exe`).
Installer: `iscc packaging\installer.iss /DAppVersion=0.2.0` (output in `release\`).

CI equivalent: `.github/workflows/windows-release.yml` (manual or on `v*` tags). It has never been run.

## Data location and portability
Data and logs live in `%LOCALAPPDATA%\WritingProcessLab` (override with the `WPL_DATA_DIR` environment variable, e.g. to keep
everything next to a portable exe). No administrator rights are needed; the installer installs per user by default.
The uninstaller does not delete user data.

## Release checklist (to do on a clean Windows machine/VM)
- [ ] Installer runs, Start Menu shortcut works, optional desktop shortcut works
- [ ] App starts offline; database created; `--selftest` passes
- [ ] Create a session, export TXT/CSV/JSON, restart, history is still there
- [ ] Kill the process during a session, restart, resume from History
- [ ] Uninstall leaves user data; deleting the data folder resets the app
- [ ] Record SHA-256 of each artifact (CI step does this)
- [ ] Signing: not done. Without an Authenticode certificate SmartScreen shows "unknown publisher".
      Do not describe artifacts as signed until `Get-AuthenticodeSignature` reports `Valid`.

## Known limitations
No app icon yet, no code signing, one-file build may trigger antivirus heuristics, installer script has no license page until a license is chosen.
