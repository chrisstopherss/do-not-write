"""New session, live session and history pages."""
from __future__ import annotations

import html
import json
import random
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QScrollArea,
    QSpinBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..analysis.diff import word_diff
from ..analysis.features import AnalysisInput, run_extractors
from ..engine.breaks import RhythmPlan, plan_rhythm
from ..engine.events import Event, apply_event
from ..engine.rhythm import (
    PRESET_TITLES,
    RhythmConfig,
    RhythmError,
)
from ..engine.schedule import (
    DEFAULT_GRACE_MS,
    MAX_DURATION_MS,
    PRESETS_MS,
    InfeasibleSchedule,
    ScheduleError,
    validate_duration,
)
from ..engine.session import COMPLETED, PAUSED, RUNNING
from ..engine.simulate import ALL_CATEGORIES, CHAR_LEVEL, CategoryConfig, MistakeConfig, SimConfig, simulate
from ..engine.text import sentences, word_count
from ..io import exporters as X
from ..io.textfiles import WARN_CHARS, InputError, read_text_file, validate_source_text
from ..live_input import (
    MIN_LIVE_DURATION_MS,
    FocusChangedError,
    LiveInputError,
    enumerate_windows,
    validate_live_duration,
)
from ..service import SessionController
from ..storage.db import Database, StorageError
from . import theme
from .charts import RhythmTimeline
from .rhythm_panel import RhythmPanel
from .widgets import button, fill, fmt_ms, label, table

CATEGORY_INFO = {
    "substitution": ("Substitution", "% of letter positions examined (one draw per letter)"),
    "missing_duplicated": ("Missing / duplicated character", "% of letter positions examined"),
    "misspelling": ("Misspelling (letter swap)", "% of words with 4+ letters"),
    "punctuation": ("Punctuation dropped", "% of . , ; : ! ? marks"),
    "grammar": ("Grammar-like swap (its/it's, their/there...)", "% of words found in the confusables table"),
    "repetition": ("Repeated word", "% of words"),
    "abandonment": ("Abandoned phrase and restart", "% of sentences"),
    "correction": ("Correction of introduced errors", "% of introduced errors that get fixed"),
}


def diff_html(a: str, b: str) -> str:
    c = theme.colors()
    out = []
    for op in word_diff(a, b):
        if op.tag == "equal":
            out.append(html.escape(op.a))
        else:
            if op.a:
                out.append(f'<span style="background:{c["del"]};text-decoration:line-through">{html.escape(op.a)}</span>')
            if op.b:
                out.append(f'<span style="background:{c["ins"]}">{html.escape(op.b)}</span>')
    return '<div style="white-space:pre-wrap">' + "".join(out) + "</div>"


class NewSessionPage(QWidget):
    start_requested = Signal(str, int, int, object, str, object, object)  # final object is an optional window target

    def __init__(self, db: Database) -> None:
        super().__init__()
        self.setObjectName("page")
        self.db = db
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setAccessibleName("New session form")
        inner = QWidget()
        inner.setObjectName("page")
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        root = QVBoxLayout(inner)
        root.setContentsMargins(28, 20, 28, 20)
        head = QHBoxLayout()
        head.addWidget(label("New session", "h1"))
        b = label("Synthetic output", "badge", wrap=False)
        head.addWidget(b)
        head.addStretch(1)
        root.addLayout(head)
        self.banner = label("", "banner_err")
        self.banner.hide()
        root.addWidget(self.banner)

        self.editor = QPlainTextEdit()
        self.editor.setObjectName("editor")
        self.editor.setAccessibleName("Source text")
        self.editor.setPlaceholderText("Paste or import some text to begin.\nNothing leaves this computer. Your original text is never modified.")
        self.editor.textChanged.connect(self._update_estimate)
        self.editor.setMinimumHeight(170)
        root.addWidget(self.editor, 1)
        row = QHBoxLayout()
        self.paste_btn = button("Paste from clipboard")
        self.import_btn = button("Import .txt / .md...")
        self.paste_btn.clicked.connect(self._paste)
        self.import_btn.clicked.connect(self._import)
        self.counts = label("", "muted", wrap=False)
        row.addWidget(self.paste_btn)
        row.addWidget(self.import_btn)
        row.addWidget(self.counts, 1)
        root.addLayout(row)

        dur = QHBoxLayout()
        dur.addWidget(QLabel("Duration"))
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.chips = {}
        for name in [*PRESETS_MS, "Custom"]:
            ch = button(name)
            ch.setProperty("chip", True)
            ch.setCheckable(True)
            ch.setAccessibleName(f"Duration {name}")
            self.group.addButton(ch)
            dur.addWidget(ch)
            self.chips[name] = ch
        self.chips["1 hour"].setChecked(True)
        self.hours = QSpinBox()
        self.hours.setRange(0, 24)
        self.hours.setSuffix(" h")
        self.hours.setAccessibleName("Custom hours")
        self.minutes = QSpinBox()
        self.minutes.setRange(0, 59)
        self.minutes.setSuffix(" min")
        self.minutes.setAccessibleName("Custom minutes")
        self.hours.setValue(1)
        dur.addWidget(self.hours)
        dur.addWidget(self.minutes)
        dur.addStretch(1)
        self.group.buttonClicked.connect(lambda _b: self._update_estimate())
        self.hours.valueChanged.connect(self._custom_changed)
        self.minutes.valueChanged.connect(self._custom_changed)
        root.addLayout(dur)

        live_box = QGroupBox("Live writing")
        live_row = QHBoxLayout(live_box)
        self.external_input = QCheckBox("Type into a focused window")
        self.external_input.setAccessibleName("Enable focused-window input")
        self.target_window = QComboBox()
        self.target_window.setAccessibleName("Target window")
        self.target_window.setMinimumWidth(280)
        self.refresh_targets_btn = button("Refresh windows")
        self.refresh_targets_btn.clicked.connect(self._refresh_targets)
        self.external_input.toggled.connect(self._live_toggled)
        live_row.addWidget(self.external_input)
        live_row.addWidget(QLabel("Target"))
        live_row.addWidget(self.target_window, 1)
        live_row.addWidget(self.refresh_targets_btn)
        root.addWidget(live_box)
        self.live_note = label("Internal preview remains available. Focused-window input is opt-in and stops if focus changes.", "muted")
        root.addWidget(self.live_note)
        self._refresh_targets()
        self._live_toggled(False)

        self.rhythm = RhythmPanel()
        self.rhythm.changed.connect(self._update_estimate)
        self.rhythm.preview_requested.connect(self._preview_schedule)
        root.addWidget(self.rhythm)

        box = QGroupBox("Mistake and revision settings (experimental transformations)")
        g = QGridLayout(box)
        self.cat_checks: dict[str, QCheckBox] = {}
        self.cat_rates: dict[str, QDoubleSpinBox] = {}
        defaults = MistakeConfig.default()
        for i, cat in enumerate(ALL_CATEGORIES):
            name, denom = CATEGORY_INFO[cat]
            cb = QCheckBox(name)
            cb.setChecked(defaults.categories[cat].enabled)
            sp = QDoubleSpinBox()
            sp.setDecimals(2)
            sp.setRange(0, 50 if cat in CHAR_LEVEL else 100)
            sp.setSuffix(" %")
            sp.setValue(defaults.categories[cat].rate * 100)
            sp.setAccessibleName(f"{name} rate")
            sp.setMaximumWidth(110)
            sp.setEnabled(cb.isChecked())
            cb.toggled.connect(sp.setEnabled)
            d = label(denom, "muted")
            g.addWidget(cb, i, 0)
            g.addWidget(sp, i, 1)
            g.addWidget(d, i, 2)
            self.cat_checks[cat], self.cat_rates[cat] = cb, sp
        root.addWidget(box)

        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("Random seed"))
        self.seed = QSpinBox()
        self.seed.setRange(0, 2**31 - 1)
        self.seed.setAccessibleName("Random seed")
        self.seed.setMaximumWidth(150)
        self.seed.setValue(random.randrange(1, 2**31 - 1))
        self.auto_seed = QCheckBox("Generate a new seed each time")
        self.auto_seed.setChecked(True)
        bottom.addWidget(self.seed)
        bottom.addWidget(self.auto_seed)
        bottom.addStretch(1)
        self.preview_btn = button("Preview transformations...")
        self.preview_btn.clicked.connect(self._preview)
        self.start_btn = button("Start session", "primary")
        self.start_btn.clicked.connect(self._start)
        bottom.addWidget(self.preview_btn)
        bottom.addWidget(self.start_btn)
        root.addLayout(bottom)
        self.estimate = label("", "muted")
        root.addWidget(self.estimate)
        self._update_estimate()

    # -- helpers
    def set_text(self, text: str) -> None:
        self.editor.setPlainText(text)

    def duration_ms(self) -> int:
        if self.chips["Custom"].isChecked():
            return (self.hours.value() * 60 + self.minutes.value()) * 60_000
        for name, ms in PRESETS_MS.items():
            if self.chips[name].isChecked():
                return ms
        return 3_600_000

    def _custom_changed(self) -> None:
        self.chips["Custom"].setChecked(True)
        self._update_estimate()

    def _live_toggled(self, enabled: bool) -> None:
        self.target_window.setEnabled(enabled)
        self.refresh_targets_btn.setEnabled(enabled)
        self.live_note.setText("Focused-window input requires 30 minutes to 24 hours. Focus moving here pauses; another window stops the session."
                       if enabled else "Internal preview remains available. Focused-window input is opt-in and stops if focus changes.")
        if hasattr(self, "start_btn"):
            self._update_estimate()

    def _refresh_targets(self) -> None:
        selected = self.target_window.currentData()
        selected_hwnd = getattr(selected, "hwnd", None)
        self.target_window.clear()
        try:
            targets = enumerate_windows()
        except (OSError, LiveInputError) as exc:
            self._show_error(f"Could not list windows: {exc}")
            targets = []
        for target in targets:
            self.target_window.addItem(f"{target.title}  (PID {target.process_id})", target)
        for index in range(self.target_window.count()):
            if getattr(self.target_window.itemData(index), "hwnd", None) == selected_hwnd:
                self.target_window.setCurrentIndex(index)
                break
        if not targets:
            self.target_window.addItem("No eligible windows found")

    def sim_config(self) -> SimConfig:
        m = MistakeConfig()
        for cat in ALL_CATEGORIES:
            m.categories[cat] = CategoryConfig(self.cat_checks[cat].isChecked(), self.cat_rates[cat].value() / 100)
        m.validate()
        return SimConfig(m)

    def load_config(self, cfg: SimConfig, seed: int, duration_ms: int, rhythm: RhythmConfig | None = None) -> None:
        if rhythm is not None:
            self.rhythm.set_config(rhythm)
        for cat, c in cfg.mistakes.categories.items():
            self.cat_checks[cat].setChecked(c.enabled)
            self.cat_rates[cat].setValue(c.rate * 100)
        self.auto_seed.setChecked(False)
        self.seed.setValue(seed)
        self.chips["Custom"].setChecked(True)
        self.hours.setValue(duration_ms // 3_600_000)
        self.minutes.setValue((duration_ms // 60_000) % 60)

    def _show_error(self, msg: str) -> None:
        self.banner.setText(msg)
        self.banner.setVisible(bool(msg))

    def _update_estimate(self) -> None:
        text = self.editor.toPlainText()
        words, sents = word_count(text), len(sentences(text))
        self.counts.setText(f"{len(text):,} characters   {words:,} words   {sents:,} sentences")
        ms = self.duration_ms()
        if not text.strip():
            self.estimate.setText("Add text to see the schedule estimate.")
        elif self.external_input.isChecked() and ms < MIN_LIVE_DURATION_MS:
            self.estimate.setText("Focused-window sessions require at least 30 minutes (maximum 24 hours).")
        elif ms < 1000:
            self.estimate.setText("Choose a duration of at least 1 minute (maximum 24 hours).")
        else:
            per = ms / max(1, sents)
            self.estimate.setText(f"Schedule: about one sentence every {fmt_ms(per)}; planned to finish within {fmt_ms(ms)}. "
                                  f"If the deadline falls mid-sentence, up to {fmt_ms(DEFAULT_GRACE_MS)} extra is allowed to finish it.")
        self.start_btn.setEnabled(bool(text.strip()) and (not self.external_input.isChecked() or ms >= MIN_LIVE_DURATION_MS))
        if len(text) > WARN_CHARS:
            self._show_error(f"Large input ({len(text):,} characters); the interface may feel slower.")
        else:
            self._show_error("")

    def _paste(self) -> None:
        t = QGuiApplication.clipboard().text()  # read only because the user pressed the button
        if not t.strip():
            self._show_error("The clipboard has no text.")
            return
        self.editor.setPlainText(t)

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import text file", "", "Text files (*.txt *.md *.markdown *.text)")
        if not path:
            return
        try:
            self.editor.setPlainText(read_text_file(path))
        except InputError as exc:
            self._show_error(str(exc))

    def _validated(self) -> tuple[str, int, SimConfig] | None:
        try:
            text = validate_source_text(self.editor.toPlainText())
            ms = validate_duration(self.duration_ms())
            if self.external_input.isChecked():
                validate_live_duration(ms)
            return text, ms, self.sim_config()
        except (InputError, ScheduleError, LiveInputError, ValueError) as exc:
            self._show_error(str(exc))
            return None

    def _preview(self) -> None:
        v = self._validated()
        if not v:
            return
        text, _ms, cfg = v
        res = simulate(text, self.seed.value(), cfg)
        dlg = QDialog(self)
        dlg.setWindowTitle("Preview of proposed transformations")
        dlg.resize(820, 600)
        lay = QVBoxLayout(dlg)
        lay.addWidget(label(f"Seed {self.seed.value()} - {len(res.events)} simulated events. Highlighted: differences between your "
                            "original text and the final simulated text (errors not corrected stay in the output).", "muted"))
        tb = QTextBrowser()
        tb.setObjectName("editor")
        tb.setHtml(diff_html(text, res.final_text))
        lay.addWidget(tb, 1)
        t = table(["Category", "Opportunities", "Selected", "Applied"])
        fill(t, [[CATEGORY_INFO[c][0], str(s.opportunities), str(s.selected), str(s.applied)] for c, s in res.stats.items()])
        t.setMaximumHeight(230)
        lay.addWidget(t)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.exec()

    def _plan(self, text: str, ms: int, cfg: SimConfig, rhythm: RhythmConfig) -> RhythmPlan:
        return plan_rhythm(text, self.seed.value(), cfg, rhythm, ms)

    def _offer_alternative(self, exc: InfeasibleSchedule) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Schedule does not fit the selected duration")
        box.setText(exc.explain())
        use = None
        if exc.needed_duration_ms <= MAX_DURATION_MS:
            use = box.addButton(f"Use {fmt_ms(exc.needed_duration_ms)} as the duration", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Edit rhythm settings", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if use is not None and box.clickedButton() is use:
            ms = exc.needed_duration_ms
            self.chips["Custom"].setChecked(True)
            self.hours.setValue(ms // 3_600_000)
            self.minutes.setValue((ms // 60_000) % 60)

    def _preview_schedule(self) -> None:
        v = self._validated()
        if not v:
            return
        text, ms, cfg = v
        try:
            rhythm = self.rhythm.config()
        except RhythmError as exc:
            self._show_error(str(exc))
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Preview of the proposed activity and break schedule")
        dlg.resize(860, 560)
        lay = QVBoxLayout(dlg)
        try:
            plan = self._plan(text, ms, cfg, rhythm)
        except InfeasibleSchedule as exc:
            lay.addWidget(label(exc.explain(), "banner_warn"))
            bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            bb.rejected.connect(dlg.reject)
            lay.addWidget(bb)
            dlg.exec()
            return
        s = plan.summary
        lay.addWidget(label(f"Mode: {PRESET_TITLES.get(rhythm.name, rhythm.name)}. Seed {self.seed.value()}. Times are on the simulated session clock. "
                            "Break lengths are experimental parameters, not evidence of human behavior.", "muted"))
        tl = RhythmTimeline()
        tl.set_schedule(s.segments, ms)
        lay.addWidget(tl)
        t = table(["Quantity", "Value"])
        writing = [g for g in s.segments if g.kind == "writing"]
        fill(t, [["Planned session length", fmt_ms(s.total_ms) + f" of {fmt_ms(ms)} selected"], ["Active writing time (simulated)", fmt_ms(s.active_ms)],
                 ["Idle time (short pauses + breaks, simulated)", fmt_ms(s.idle_ms)], ["Breaks", str(s.breaks)], ["Short pauses", str(s.short_pauses)],
                 ["Average break", fmt_ms(s.avg_break_ms) if s.avg_break_ms is not None else "no breaks"],
                 ["Longest break", fmt_ms(s.longest_break_ms) if s.longest_break_ms is not None else "no breaks"],
                 ["Writing intervals", str(len(writing))], ["Completion", plan.deadline.completion.replace("_", " ")]])
        lay.addWidget(t, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.exec()

    def _start(self) -> None:
        v = self._validated()
        if not v:
            return
        text, ms, cfg = v
        try:
            rhythm = self.rhythm.config()
        except RhythmError as exc:
            self._show_error(str(exc))
            return
        if self.auto_seed.isChecked():
            self.seed.setValue(random.randrange(1, 2**31 - 1))
        try:
            self._plan(text, ms, cfg, rhythm)  # fail early, with alternatives, if the break schedule cannot fit
        except InfeasibleSchedule as exc:
            self._offer_alternative(exc)
            return
        target = self.target_window.currentData() if self.external_input.isChecked() else None
        if self.external_input.isChecked() and target is None:
            self._show_error("Select a target window and refresh the list if it is missing.")
            return
        self.start_requested.emit(text, ms, self.seed.value(), cfg, "", rhythm, target)


class OutputView(QPlainTextEdit):
    """Read-only presentation of the evolving text. Edits arrive as events and are applied in place (no full
    rebuild per event). Qt offsets are UTF-16 units, so positions are mapped when the text has non-BMP characters."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("editor")
        self.setAccessibleName("Output so far (synthetic)")
        self.setCursorWidth(2)
        self.shadow = ""

    def keyPressEvent(self, e) -> None:
        if e.matches(QKeySequence.StandardKey.Copy) or e.key() in (
                Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Home, Qt.Key.Key_End, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            super().keyPressEvent(e)

    def insertFromMimeData(self, _s) -> None:
        return

    def reset(self, text: str = "") -> None:
        self.shadow = text
        self.setPlainText(text)
        self.moveCursor(QTextCursor.MoveOperation.End)

    def _q(self, pos: int) -> int:
        s = self.shadow[:pos]
        return pos if s.isascii() else len(s.encode("utf-16-le")) // 2

    def apply(self, e: Event) -> None:
        if not e.is_text_op:
            return
        assert e.pos is not None
        cur = QTextCursor(self.document())
        start = self._q(e.pos)
        cur.setPosition(start)
        if e.removed:
            cur.setPosition(self._q(e.pos + len(e.removed)), QTextCursor.MoveMode.KeepAnchor)
            cur.removeSelectedText()
        if e.inserted:
            cur.insertText(e.inserted)
        self.shadow = apply_event(self.shadow, e)
        self.setTextCursor(cur)
        self.ensureCursorVisible()


class ActiveSessionPage(QWidget):
    finished = Signal(str)  # session id

    def __init__(self, db: Database) -> None:
        super().__init__()
        self.setObjectName("page")
        self.db = db
        self.ctl: SessionController | None = None
        self.input_backend = None
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 20, 28, 20)
        head = QHBoxLayout()
        head.addWidget(label("Active session", "h1"))
        self.mode_badge = label("Synthetic output", "badge", wrap=False)
        head.addWidget(self.mode_badge)
        head.addStretch(1)
        self.state_lb = label("", "muted", wrap=False)
        head.addWidget(self.state_lb)
        root.addLayout(head)
        self.empty = label("No session is running. Start one from New session, or resume an interrupted one from History.", "muted")
        root.addWidget(self.empty)
        self.banner = label("", "banner_info")
        self.banner.hide()
        root.addWidget(self.banner)
        self.view = OutputView()
        root.addWidget(self.view, 3)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setAccessibleName("Session progress")
        root.addWidget(self.bar)
        times = QHBoxLayout()
        self.elapsed = label("Elapsed 00:00", wrap=False)
        self.waiting = label("", "muted", wrap=False)
        self.remaining = label("Remaining 00:00", wrap=False)
        times.addWidget(self.elapsed)
        times.addStretch(1)
        times.addWidget(self.waiting)
        times.addStretch(1)
        times.addWidget(self.remaining)
        root.addLayout(times)
        self.split_lb = label("", "muted", wrap=False)
        root.addWidget(self.split_lb)
        self.timeline = RhythmTimeline("Schedule: writing intervals and breaks (simulated clock)")
        self.timeline.setMinimumHeight(138)
        self.timeline.setMaximumHeight(160)
        root.addWidget(self.timeline)
        ctl = QHBoxLayout()
        self.pause_btn = button("Pause")
        self.resume_btn = button("Resume", "primary")
        self.cancel_btn = button("Stop session...", "danger")
        self.pause_btn.clicked.connect(self._pause)
        self.resume_btn.clicked.connect(self._resume)
        self.cancel_btn.clicked.connect(self._cancel)
        ctl.addWidget(self.pause_btn)
        ctl.addWidget(self.resume_btn)
        ctl.addWidget(self.cancel_btn)
        ctl.addStretch(1)
        self.info = label("", "muted", wrap=False)
        ctl.addWidget(self.info)
        root.addLayout(ctl)
        root.addWidget(label("Recent session events (last 500; generated by the engine, not a record of human activity)", "h2"))
        self.events = QPlainTextEdit()
        self.events.setReadOnly(True)
        self.events.setMaximumHeight(130)
        self.events.document().setMaximumBlockCount(500)
        self.events.setAccessibleName("Recent session events (simulated)")
        root.addWidget(self.events)
        self._due = QTimer(self)
        self._due.setSingleShot(True)
        self._due.timeout.connect(self._on_due)
        self._tick = QTimer(self)
        self._tick.setInterval(500)
        self._tick.timeout.connect(self._refresh)
        self._sync()

    def attach(self, ctl: SessionController, text: str = "", input_backend=None) -> None:
        self.ctl = ctl
        self.input_backend = input_backend
        if input_backend is not None:
            ctl.session.max_batch = 1
        self.mode_badge.setText("Synthetic output · focused window" if input_backend is not None else "Synthetic output")
        self.view.reset(ctl.text)
        self.events.clear()
        s = self.db.get_session(ctl.sid)
        self.info.setText(f"Seed {s['seed']}  |  engine {s['engine_version']}")
        self.banner.hide()
        from ..engine.breaks import summarize
        self.timeline.set_schedule(summarize(ctl.session.events).segments, ctl.session.duration_ms, self._markers())
        self._sync()

    def _markers(self) -> list[tuple[int, str]]:
        return [(a["active_ms"], a["action"]) for a in self.db.get_user_actions(self.ctl.sid)] if self.ctl else []

    def _sync(self) -> None:
        st = self.ctl.state if self.ctl else "idle"
        has = self.ctl is not None
        self.empty.setVisible(not has)
        self.view.setVisible(has)
        self.pause_btn.setVisible(st == RUNNING)
        self.resume_btn.setVisible(st == PAUSED)
        self.cancel_btn.setVisible(st in (RUNNING, PAUSED))
        self._refresh()

    def begin(self) -> None:
        assert self.ctl
        if self.ctl.state == "idle":
            self.ctl.start()
        self._tick.start()
        self._sync()
        self._on_due()

    def _refresh(self) -> None:
        if not self.ctl or not self.ctl.session:
            return
        s = self.ctl.session
        if s.state == RUNNING and self.input_backend is not None and not self.input_backend.has_target_focus():
            if self.input_backend.is_own_process_foreground():
                self._pause_for_focus()
            else:
                self._stop_for_focus_change()
            return
        phase, detail = s.phase_detail()
        self.state_lb.setText(phase if not detail else f"{phase} ({detail})")
        self.split_lb.setText(f"Active writing {fmt_ms(s.writing_ms())}   |   Idle (simulated pauses and breaks) {fmt_ms(s.idle_ms())}")
        self.timeline.set_now(s.active_ms())
        self.elapsed.setText(f"Elapsed {fmt_ms(s.active_ms())}")
        self.remaining.setText(f"Remaining {fmt_ms(s.remaining_ms())}")
        self.bar.setValue(int(s.progress() * 1000))
        nd = s.next_due_in_ms()
        self.waiting.setText("Paused, clock stopped" if s.state == PAUSED else
                             f"{'Break' if phase == 'Break' else 'Waiting'}: next writing event in ~{max(0, round(nd / 1000))} s"
                             if nd is not None and nd > 1500 else "")

    def _on_due(self) -> None:
        if not self.ctl or not self.ctl.session or self.ctl.state != RUNNING:
            return
        if self.input_backend is not None and not self.input_backend.has_target_focus():
            if self.input_backend.is_own_process_foreground():
                self._pause_for_focus()
            else:
                self._stop_for_focus_change()
            return
        if self.input_backend is not None:
            session = self.ctl.session
            if session.cursor < len(session.events) and session.next_due_in_ms() == 0:
                event = session.events[session.cursor]
                if event.is_text_op:
                    try:
                        self.input_backend.apply_event(event)
                    except FocusChangedError:
                        self._stop_for_focus_change()
                        return
                    except LiveInputError as exc:
                        self._fail(f"Focused-window input stopped: {exc}")
                        return
        try:
            due = self.ctl.poll()
        except StorageError as exc:
            self._fail(str(exc))
            return
        for e in due:
            self.view.apply(e)
            self._log(e)
        if self.ctl.state == COMPLETED:
            self._tick.stop()
            self._sync()
            d = self.ctl.deadline
            msg = "Session complete (completed normally)."
            if d and d.deadline_priority:
                msg = "Stopped by the deadline: output ended at the nearest sentence boundary before the grace limit, so part of the text was not written."
            elif d and d.completion == "deadline_stop":
                msg = f"Stopped by the deadline after finishing the current sentence (grace used: {fmt_ms(d.grace_used_ms)}); the rest of the text was not written."
            elif d and d.grace_used_ms:
                msg = f"Session complete. The deadline fell mid-sentence; finished within the grace limit ({fmt_ms(d.grace_used_ms)} used)."
            self.banner.setText(msg)
            self.banner.show()
            self.finished.emit(self.ctl.sid)
            return
        nd = self.ctl.session.next_due_in_ms()
        self._due.start(int(max(1, min(nd if nd is not None else 1000, 60_000))))
        self._refresh()

    def _log(self, e: Event) -> None:
        detail = e.meta.get("category") or ""
        if e.kind == "pause":
            detail = f"{fmt_ms(e.duration_ms)}"
        elif e.is_text_op:
            detail += f"  {e.removed[:20]!r} -> {e.inserted[:20]!r}"
        self.events.appendPlainText(f"{fmt_ms(e.t_ms)}  {e.kind:<10} {detail}  [SIMULATED]")

    def _fail(self, msg: str) -> None:
        self._due.stop()
        if self.ctl and self.ctl.state == RUNNING:
            self.ctl.pause()
        self.banner.setObjectName("banner_err")
        self.banner.setStyleSheet(self.banner.styleSheet())
        self.banner.setText(f"Could not save session progress: {msg}. The session was paused. Your text is still on screen.")
        self.banner.show()
        self._sync()

    def _pause_for_focus(self) -> None:
        if self.ctl and self.ctl.state == RUNNING:
            self._due.stop()
            self.ctl.pause()
            self.banner.setText("Paused because focus moved to Writing Process Lab. Resume to return to the selected target.")
            self.banner.show()
            self._sync()

    def _stop_for_focus_change(self) -> None:
        if not self.ctl or self.ctl.state not in (RUNNING, PAUSED):
            return
        self._due.stop()
        self._tick.stop()
        self.ctl.cancel()
        self.banner.setObjectName("banner_err")
        self.banner.setStyleSheet(self.banner.styleSheet())
        self.banner.setText("Session stopped because the selected target lost focus. No further input was sent; partial output was saved to History.")
        self.banner.show()
        self._sync()
        self.finished.emit(self.ctl.sid)

    def _pause(self) -> None:
        if self.ctl:
            self._due.stop()
            self.ctl.pause()
            self.timeline.markers = self._markers()
            self._sync()

    def _resume(self) -> None:
        if self.ctl:
            if self.input_backend is not None:
                try:
                    self.input_backend.activate_target()
                except LiveInputError as exc:
                    self.banner.setText(f"Cannot resume focused-window input: {exc}")
                    self.banner.show()
                    return
            self.ctl.resume()
            self.timeline.markers = self._markers()
            self._tick.start()
            self._sync()
            self._on_due()

    def _cancel(self) -> None:
        if not self.ctl:
            return
        was_running = self.ctl.state == RUNNING
        if was_running:  # hold the clock while the confirmation is open; this is UI mechanics, not a logged user pause
            self._due.stop()
            self.ctl.pause(record=False)
            self._sync()
        r = QMessageBox.question(self, "Stop session", "Stop this session? The text produced so far is kept in History, "
                     "but the session cannot be resumed.")
        if r == QMessageBox.StandardButton.Yes:
            self._due.stop()
            self._tick.stop()
            self.ctl.cancel()
            self.timeline.markers = self._markers()
            self.banner.setText("Session stopped. Partial output was saved to History.")
            self.banner.show()
            self._sync()
            self.finished.emit(self.ctl.sid)
        elif was_running:
            self.ctl.resume(record=False)
            self._tick.start()
            self._sync()
            self._on_due()


class HistoryPage(QWidget):
    rerun_requested = Signal(str)
    resume_requested = Signal(str)

    def __init__(self, db: Database) -> None:
        super().__init__()
        self.setObjectName("page")
        self.db = db
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 20, 28, 20)
        head = QHBoxLayout()
        head.addWidget(label("History and results", "h1"))
        head.addStretch(1)
        root.addLayout(head)
        self.empty = label("No saved sessions yet. Finished and cancelled sessions appear here.", "muted")
        root.addWidget(self.empty)
        self.table = table(["Created", "Label / id", "Origin", "Status", "Duration", "Seed"])
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.itemSelectionChanged.connect(self._selected)
        root.addWidget(self.table, 1)
        self.detail = QTextBrowser()
        self.detail.setObjectName("editor")
        self.detail.setAccessibleName("Original versus final text")
        root.addWidget(self.detail, 2)
        self.btns = {}
        rows = [QHBoxLayout(), QHBoxLayout()]
        specs = (("txt", "Export TXT", "", 0), ("md", "Export Markdown", "", 0), ("json", "Export JSON", "", 0), ("csv", "Export events CSV", "", 0),
                 ("rerun", "Re-run with same seed", "", 1), ("resume", "Resume interrupted", "", 1), ("compare", "Compare 2 selected", "", 1),
                 ("delete", "Delete...", "danger", 1))
        for key, text, name, r in specs:
            b = button(text, name)
            b.setEnabled(False)
            b.setMinimumWidth(b.fontMetrics().horizontalAdvance(text) + 40)
            rows[r].addWidget(b)
            self.btns[key] = b
        for r in rows:
            r.addStretch(1)
            root.addLayout(r)
        self.btns["txt"].clicked.connect(lambda: self._export("txt"))
        self.btns["md"].clicked.connect(lambda: self._export("md"))
        self.btns["json"].clicked.connect(lambda: self._export("json"))
        self.btns["csv"].clicked.connect(lambda: self._export("csv"))
        self.btns["rerun"].clicked.connect(lambda: self.rerun_requested.emit(self._sid()))
        self.btns["resume"].clicked.connect(lambda: self.resume_requested.emit(self._sid()))
        self.btns["compare"].clicked.connect(self._compare)
        self.btns["delete"].clicked.connect(self._delete)
        self.msg = label("", "muted")
        root.addWidget(self.msg)
        self.refresh()

    def refresh(self) -> None:
        rows = self.db.list_sessions()
        self.empty.setVisible(not rows)
        self.table.setVisible(bool(rows))
        self._ids = [r["id"] for r in rows]
        fill(self.table, [[r["created_at"], r["label"] or r["id"][:8], r["data_origin"].upper(), r["status"],
                           fmt_ms(r["duration_ms"]) if r["duration_ms"] else "n/a", str(r["seed"]) if r["seed"] is not None else "n/a"] for r in rows])
        self.detail.clear()

    def _sids(self) -> list[str]:
        return [self._ids[i.row()] for i in self.table.selectionModel().selectedRows()] if self.table.selectionModel() else []

    def _sid(self) -> str:
        s = self._sids()
        return s[0] if s else ""

    def _selected(self) -> None:
        sids = self._sids()
        one = len(sids) == 1
        row = self.db.get_session(sids[0]) if one else None
        for k in ("txt", "md", "json", "csv", "delete"):
            self.btns[k].setEnabled(bool(sids) if k == "delete" else one)
        self.btns["rerun"].setEnabled(bool(row and row["seed"] is not None and "sim" in json.loads(row["config_json"])))
        can_resume = bool(row and row["status"] == "interrupted" and
                  json.loads(row["config_json"]).get("input_mode") != "focused_window")
        self.btns["resume"].setEnabled(can_resume)
        self.btns["compare"].setEnabled(len(sids) == 2)
        if row:
            c = theme.colors()
            comp = ""
            if row["deadline_json"]:
                dl = json.loads(row["deadline_json"])
                comp = f" Completion: <b>{dl.get('completion', 'normal').replace('_', ' ')}</b>."
            self.detail.setHtml(f'<p style="color:{c["muted"]}">Data origin: <b>{row["data_origin"].upper()}</b>.{comp} Original vs final text '
                                f'(green = added, red = removed).</p>' + diff_html(row["source_text"], row["final_text"] or ""))

    def _row(self):
        return self.db.get_session(self._sid())

    def _export(self, kind: str) -> None:
        row = self._row()
        if not row:
            return
        events = self.db.get_events(row["id"])
        if kind == "txt":
            content, ext = row["final_text"] or "", "txt"
        elif kind == "json":
            content, ext = X.dataset_json(row["label"] or row["id"], [X.session_dict(row, events, self.db.latest_features(row["id"]), self.db.get_user_actions(row["id"]))]), "json"
        elif kind == "csv":
            content, ext = X.events_csv(row["id"], row["data_origin"], events), "csv"
        else:
            res = run_extractors(AnalysisInput("", row["final_text"] or "", events, row["duration_ms"], row["data_origin"], row["source_text"]))
            from ..analysis.revisions import build_versions
            vs = [v.to_dict() for v in build_versions("", events, final_text=row["final_text"])]
            content, ext = X.report_markdown(row["label"] or row["id"], row["data_origin"], res, vs), "md"
            content += "\n## Final text\n\n" + (row["final_text"] or "") + "\n"
        path, _ = QFileDialog.getSaveFileName(self, "Export", f"session.{ext}", f"*.{ext}")
        if not path:
            return
        try:
            used = X.write_new(path, content)
            self.msg.setText(f"Saved {used}" + ("  (file existed, saved under a new name; nothing was overwritten)" if Path(path) != used else ""))
        except OSError as exc:
            self.msg.setText(f"Could not save: {exc.strerror or exc}")

    def _delete(self) -> None:
        sids = self._sids()
        if QMessageBox.question(self, "Delete sessions", f"Permanently delete {len(sids)} session(s) and their events? This cannot be undone.") \
                == QMessageBox.StandardButton.Yes:
            for s in sids:
                self.db.delete_session(s)
            self.refresh()

    def _compare(self) -> None:
        a, b = self._sids()
        fa, fb = self._features(a), self._features(b)
        t = table(["Metric", "Session 1", "Session 2"])
        keys = sorted(set(fa) | set(fb))
        fill(t, [[k, fa.get(k, "Not recorded"), fb.get(k, "Not recorded")] for k in keys])
        dlg = QDialog(self)
        dlg.setWindowTitle("Compare sessions (scalar metrics)")
        dlg.resize(700, 500)
        lay = QVBoxLayout(dlg)
        lay.addWidget(label("Descriptive comparison only. Differences do not indicate how either text was produced.", "muted"))
        lay.addWidget(t)
        dlg.exec()

    def _features(self, sid: str) -> dict[str, str]:
        from ..service import analyze_session
        f = self.db.latest_features(sid) or analyze_session(self.db, sid)
        out = {}
        for k, v in f.items():
            if v["status"] != "ok":
                out[k] = "Not recorded" if v["status"] == "missing" else "Insufficient data"
            elif isinstance(v["value"], (int, float)) and not isinstance(v["value"], bool):
                out[k] = f"{v['value']:.4g}"
        return out
_ = QComboBox, MAX_DURATION_MS
