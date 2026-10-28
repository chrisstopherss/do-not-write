# PyInstaller spec. Build with:  pyinstaller packaging/wpl.spec --noconfirm
# Produces dist/WritingProcessLab/ (one-folder, recommended). Set WPL_ONEFILE=1 for a single-file portable exe.
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH).parent
datas = [
    (str(root / "src/wpl/storage/migrations"), "wpl/storage/migrations"),
    (str(root / "src/wpl/lab/guide"), "wpl/lab/guide"),
    (str(root / "src/wpl/lab/sample_text.txt"), "wpl/lab"),
]
onefile = os.environ.get("WPL_ONEFILE") == "1"
# Only the Qt modules the app uses are pulled in; network-capable Qt modules are explicitly excluded.
excludes = ["PySide6.QtNetwork", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml", "PySide6.QtQuick",
            "PySide6.QtSql", "PySide6.QtMultimedia", "tkinter", "http", "xmlrpc", "ftplib", "smtplib"]
# urllib is deliberately NOT excluded: Python's own pathlib imports urllib.parse on Windows, and excluding it crashes the exe at
# startup (found by the first Windows CI run). It ships in the bundle; no application code uses it (enforced by a test).

a = Analysis([str(root / "packaging/launcher.py")], pathex=[str(root / "src")], datas=datas, hiddenimports=[], excludes=excludes, noarchive=False)
pyz = PYZ(a.pure)
if onefile:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="WritingProcessLab-portable", console=False, upx=False)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="WritingProcessLab", console=False, upx=False)
    coll = COLLECT(exe, a.binaries, a.datas, name="WritingProcessLab", upx=False)
