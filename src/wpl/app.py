"""Application entry point. Offline: no network modules are used anywhere in the application."""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

from .storage.db import Database, DatabaseCorrupt, StorageError, default_data_dir, quarantine_corrupt


def setup_logging(data_dir: Path) -> Path:
    logs = data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / "wpl.log"
    h = logging.handlers.RotatingFileHandler(path, maxBytes=512 * 1024, backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger("wpl")
    root.setLevel(logging.INFO)
    root.addHandler(h)
    return path


def selftest() -> int:
    """Release check: runs without user interaction against a throwaway data folder. Prints one line per check."""
    import tempfile

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from . import APP_VERSION, ENGINE_VERSION
    from .io import exporters as X
    from .lab.overview import experiment_rows
    from .lab.runner import create_example_experiment
    from .ui.main_window import MainWindow

    failures: list[str] = []

    def check(name: str, ok: bool) -> None:
        print(("PASS " if ok else "FAIL ") + name)
        if not ok:
            failures.append(name)

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        db = Database(d / "wpl.db")
        check("database created and migrated (incl. user_actions)", db.version() >= 3)
        from .engine.breaks import plan_rhythm
        from .engine.rhythm import preset
        from .engine.simulate import MistakeConfig, SimConfig
        from .lab.conditions import sample_text
        plan = plan_rhythm(sample_text(), 7, SimConfig(MistakeConfig.default()), preset("reflective"), 3_600_000)
        check("break scheduler plans a session inside its time budget", plan.summary.total_ms <= 3_600_000 and plan.summary.idle_ms > 0)
        eid = create_example_experiment(db)
        rows = experiment_rows(db, eid)
        check("example experiment (25 runs) from stored data", len(rows) == 25)
        sid = rows[0]["session_id"]
        csv = X.events_csv(sid, "simulated", db.get_events(sid))
        check("events CSV export", csv.startswith("session_id,data_origin") and len(csv.splitlines()) > 2)
        path = X.write_new(d / "x.json", X.dataset_json("selftest", [X.session_dict(db.get_session(sid), db.get_events(sid))]))
        check("JSON export written", path.exists() and path.stat().st_size > 100)
        app = QApplication.instance() or QApplication([])
        win = MainWindow(db, d, d / "wpl.log")
        win.show()
        app.processEvents()
        check("main window builds", win.nav.count() == 5)
        win.close()
        db.close()
    print(f"Writing Process Lab {APP_VERSION} (engine {ENGINE_VERSION}): {'FAILED' if failures else 'all checks passed'}")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    if "--selftest" in (argv or sys.argv):
        return selftest()
    from PySide6.QtWidgets import QApplication, QMessageBox

    from .ui.main_window import MainWindow

    app = QApplication(argv or sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("Writing Process Lab")
    data_dir = Path(os.environ.get("WPL_DATA_DIR") or default_data_dir())
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        log_path = setup_logging(data_dir)
    except OSError as exc:
        QMessageBox.critical(None, "Cannot create data folder", f"{data_dir}\n\n{exc.strerror or exc}\n\nSet WPL_DATA_DIR to a writable folder.")
        return 2
    db_path = data_dir / "wpl.db"
    try:
        db = Database(db_path)
    except DatabaseCorrupt as exc:
        r = QMessageBox.question(None, "Database problem", f"The database could not be read:\n{exc}\n\nMove it aside (it is not deleted) and start a new one?")
        if r != QMessageBox.StandardButton.Yes:
            return 3
        moved = quarantine_corrupt(db_path)
        logging.getLogger("wpl").error("corrupt database moved to %s", moved)
        db = Database(db_path)
    except StorageError as exc:
        QMessageBox.critical(None, "Cannot open database", str(exc))
        return 2
    win = MainWindow(db, data_dir, log_path)
    win.show()
    return app.exec()
