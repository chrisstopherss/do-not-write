from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import APP_VERSION, ENGINE_VERSION
from ..service import SessionController, analyze_session
from ..storage.db import Database, StorageError
from . import theme
from .charts import ChartBase
from .lab_pages import LabPage
from .session_pages import ActiveSessionPage, HistoryPage, NewSessionPage
from .widgets import button, card, label

log = logging.getLogger("wpl.ui")


class SettingsPage(QWidget):
    theme_changed = Signal(str)

    def __init__(self, db: Database, data_dir: Path, log_path: Path) -> None:
        super().__init__()
        self.setObjectName("page")
        self.db, self.log_path = db, log_path
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 20, 28, 20)
        root.addWidget(label("Settings", "h1"))
        f, lay = card("Appearance")
        self.theme = QComboBox()
        self.theme.addItems(["Light", "Dark"])
        self.theme.setCurrentText(db.get_setting("theme", "Light"))
        self.theme.setAccessibleName("Theme")
        self.theme.currentTextChanged.connect(self._theme)
        lay.addWidget(self.theme)
        root.addWidget(f)
        f, lay = card("Local data")
        lay.addWidget(label(f"Database: {data_dir / 'wpl.db'}\nLogs: {log_path}\nEverything is stored on this computer only. No network access is used, "
                            "and nothing is uploaded. Use the Writing Process Lab > Data and privacy tab to inspect and delete saved data.", "muted"))
        root.addWidget(f)
        f, lay = card("Diagnostic log", "Records errors and session state changes. Document text, clipboard contents and file contents are never logged. "
                      "The log is a plain file and is not tamper-proof. It rotates at 512 KB (3 backups).")
        b1, b2 = button("View log"), button("Clear logs...", "danger")
        b1.clicked.connect(self._view)
        b2.clicked.connect(self._clear)
        row = QHBoxLayout()
        row.addWidget(b1)
        row.addWidget(b2)
        row.addStretch(1)
        lay.addLayout(row)
        root.addWidget(f)
        f, lay = card("About")
        lay.addWidget(label(f"Writing Process Lab {APP_VERSION}, simulation engine {ENGINE_VERSION}. External typing into other applications is not "
                            "implemented. There is no Google account integration and no AI-detector integration.", "muted"))
        root.addWidget(f)
        root.addStretch(1)

    def _theme(self, t: str) -> None:
        self.db.set_setting("theme", t)
        self.theme_changed.emit(t)

    def _view(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Diagnostic log")
        dlg.resize(760, 480)
        lay = QVBoxLayout(dlg)
        tb = QPlainTextEdit()
        tb.setReadOnly(True)
        tb.setAccessibleName("Log contents")
        try:
            tb.setPlainText("\n".join(self.log_path.read_text(encoding="utf-8").splitlines()[-300:]) or "(empty)")
        except OSError:
            tb.setPlainText("(no log file yet)")
        lay.addWidget(tb)
        dlg.exec()

    def _clear(self) -> None:
        if QMessageBox.question(self, "Clear logs", "Delete all diagnostic log files?") == QMessageBox.StandardButton.Yes:
            for p in self.log_path.parent.glob(self.log_path.name + "*"):
                try:
                    p.unlink()
                except OSError:
                    pass


class MainWindow(QMainWindow):
    NAV = ["New session", "Active session", "History", "Writing Process Lab", "Settings"]

    def __init__(self, db: Database, data_dir: Path, log_path: Path) -> None:
        super().__init__()
        self.db = db
        self.setWindowTitle("Writing Process Lab")
        self.setMinimumSize(1000, 680)
        self.resize(1200, 800)
        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        title = label("Writing Process Lab", "h2")
        title.setContentsMargins(16, 14, 8, 8)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(210)
        self.nav.setAccessibleName("Primary navigation")
        self.nav.addItems(self.NAV)
        foot = label("Offline | local data only", "muted")
        foot.setContentsMargins(16, 6, 8, 10)
        sidew = QWidget()
        sidew.setObjectName("nav")
        sidew.setFixedWidth(210)
        sv = QVBoxLayout(sidew)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.addWidget(title)
        sv.addWidget(self.nav, 1)
        sv.addWidget(foot)
        sidew.setStyleSheet("QWidget#nav{background:transparent}")
        lay.addWidget(sidew)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.new_page = NewSessionPage(db)
        self.active = ActiveSessionPage(db)
        self.history = HistoryPage(db)
        self.lab = LabPage(db, data_dir)
        self.settings = SettingsPage(db, data_dir, log_path)
        for w in (self.new_page, self.active, self.history, self.lab, self.settings):
            self.stack.addWidget(w)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)
        for i in range(len(self.NAV)):
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self, activated=lambda i=i: self.nav.setCurrentRow(i))
        self.new_page.start_requested.connect(self._start)
        self.active.finished.connect(self._finished)
        self.history.rerun_requested.connect(self._rerun)
        self.history.resume_requested.connect(self._resume)
        self.lab.data_changed.connect(self.history.refresh)
        self.settings.theme_changed.connect(self.apply_theme)
        self.ctl: SessionController | None = None
        self.apply_theme(db.get_setting("theme", "Light"))
        n = db.mark_interrupted()
        if n:
            log.warning("%d session(s) were interrupted by an unclean exit", n)
            self.history.refresh()
            self.statusBar().showMessage(f"{n} session(s) were interrupted last time. Open History to resume them.", 15000)

    def apply_theme(self, name: str) -> None:
        theme.set_theme(name == "Dark")
        app = QApplication.instance()
        if app:
            app.setStyleSheet(theme.stylesheet())
        for c in self.findChildren(ChartBase):
            c.update()

    def _start(self, text: str, ms: int, seed: int, cfg, label_: str) -> None:
        try:
            ctl = SessionController(self.db)
            ctl.create(text, ms, seed, cfg, label=label_)
        except (ValueError, StorageError) as exc:
            QMessageBox.warning(self, "Cannot start session", str(exc))
            log.error("session start failed: %s", type(exc).__name__)
            return
        self.ctl = ctl
        self.active.attach(ctl)
        self.nav.setCurrentRow(1)
        self.active.begin()

    def _finished(self, sid: str) -> None:
        try:
            analyze_session(self.db, sid)
        except StorageError as exc:
            log.error("analysis failed: %s", exc)
        self.history.refresh()
        self.lab.refresh_all()

    def _rerun(self, sid: str) -> None:
        import json

        from ..engine.simulate import SimConfig
        row = self.db.get_session(sid)
        if not row:
            return
        cfg = json.loads(row["config_json"])
        self.new_page.set_text(row["source_text"])
        self.new_page.load_config(SimConfig.from_dict(cfg["sim"]), row["seed"], row["duration_ms"])
        self.nav.setCurrentRow(0)

    def _resume(self, sid: str) -> None:
        try:
            ctl = SessionController.recover(self.db, sid)
        except (StorageError, ValueError) as exc:
            QMessageBox.warning(self, "Cannot resume", str(exc))
            return
        self.ctl = ctl
        self.active.attach(ctl)
        self.nav.setCurrentRow(1)
