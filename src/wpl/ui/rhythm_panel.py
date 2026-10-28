"""Writing Rhythm section of the session configuration: mode selector, description, editable custom settings."""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

from ..engine.rhythm import (
    ACTIVITIES,
    ACTIVITY_LABELS,
    MAX_BREAK_MS,
    MAX_PACE_MS,
    MAX_SHORT_PAUSE_MS,
    MIN_BREAK_MS,
    MIN_PACE_MS,
    MIN_PAUSE_MS,
    PRESET_DESCRIPTIONS,
    PRESET_TITLES,
    RhythmConfig,
    RhythmError,
    preset,
)
from .widgets import button, label

MODES = ["regular", "reflective", "extended", "custom"]


def _dspin(lo: float, hi: float, step: float, suffix: str, name: str, decimals: int = 1) -> QDoubleSpinBox:
    sp = QDoubleSpinBox()
    sp.setRange(lo, hi)
    sp.setSingleStep(step)
    sp.setDecimals(decimals)
    sp.setSuffix(suffix)
    sp.setAccessibleName(name)
    sp.setMinimumWidth(150)
    sp.setMaximumWidth(190)
    return sp


class RhythmPanel(QGroupBox):
    changed = Signal()
    preview_requested = Signal()

    def __init__(self) -> None:
        super().__init__("Writing rhythm (experimental timing parameters, not a model of any person)")
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Mode"))
        self.mode = QComboBox()
        self.mode.setAccessibleName("Writing rhythm mode")
        for m in MODES:
            self.mode.addItem(PRESET_TITLES[m], m)
        top.addWidget(self.mode)
        top.addStretch(1)
        self.preview_btn = button("Preview schedule...")
        self.reset_btn = button("Reset to defaults")
        top.addWidget(self.preview_btn)
        top.addWidget(self.reset_btn)
        lay.addLayout(top)
        self.desc = label("", "muted")
        lay.addWidget(self.desc)

        g = QGridLayout()
        self.pause_min = _dspin(MIN_PAUSE_MS / 1000, MAX_SHORT_PAUSE_MS / 1000, 0.1, " s", "Minimum pause")
        self.pause_max = _dspin(MIN_PAUSE_MS / 1000, MAX_SHORT_PAUSE_MS / 1000, 0.1, " s", "Maximum pause")
        self.short_prob = _dspin(0, 50, 1, " %", "Probability of a short pause")
        self.break_prob = _dspin(0, 100, 1, " %", "Probability of a longer break")
        self.break_min = _dspin(MIN_BREAK_MS / 1000, MAX_BREAK_MS / 1000, 5, " s", "Minimum break")
        self.break_max = _dspin(MIN_BREAK_MS / 1000, MAX_BREAK_MS / 1000, 5, " s", "Maximum break (up to 5 minutes)")
        self.revisions = _dspin(0, 50, 0.5, " per 1000 chars", "Revision frequency")
        self.activity = QComboBox()
        self.activity.setAccessibleName("Session activity distribution")
        for a in ACTIVITIES:
            self.activity.addItem(ACTIVITY_LABELS[a], a)
        self.fit = QCheckBox("Fit writing speed to the session duration (recommended)")
        self.fit.setChecked(True)
        self.pace = QSpinBox()
        self.pace.setRange(MIN_PACE_MS, MAX_PACE_MS)
        self.pace.setValue(180)
        self.pace.setSuffix(" ms per character")
        self.pace.setAccessibleName("Fixed writing pace")
        self.pace.setEnabled(False)
        self.pace.setMaximumWidth(210)
        rows = [("Pause duration, min / max", self.pause_min, self.pause_max, "Short pauses between writing units"),
                ("Probability of a short pause", self.short_prob, None, "per writing unit (word)"),
                ("Probability of a longer break", self.break_prob, None, "per sentence boundary"),
                ("Break duration, min / max", self.break_min, self.break_max, f"up to {MAX_BREAK_MS // 60_000} minutes"),
                ("Revision frequency", self.revisions, None, "revision operations at sentence boundaries"),
                ("Session activity distribution", self.activity, None, "how writing speed varies across the text")]
        for i, (name, a, b, note) in enumerate(rows):
            g.addWidget(QLabel(name), i, 0)
            g.addWidget(a, i, 1)
            if b is not None:
                g.addWidget(b, i, 2)
            g.addWidget(label(note, "muted"), i, 3)
        g.addWidget(self.fit, len(rows), 0, 1, 2)
        g.addWidget(self.pace, len(rows), 2, 1, 2)
        lay.addLayout(g)
        self.legend = label("Legend: segments in schedules and timelines are SIMULATED by the engine. Break lengths are experimental "
                            "parameters; they do not show how a person writes. The random seed below also fixes this schedule.", "muted")
        lay.addWidget(self.legend)
        self.error = label("", "banner_err")
        self.error.hide()
        lay.addWidget(self.error)

        self._custom = preset("custom")
        self._editors = [self.pause_min, self.pause_max, self.short_prob, self.break_prob, self.break_min, self.break_max,
                         self.revisions, self.activity, self.fit, self.pace]
        self.mode.currentIndexChanged.connect(self._mode_changed)
        for w in self._editors:
            (w.valueChanged if hasattr(w, "valueChanged") else w.currentIndexChanged if hasattr(w, "currentIndexChanged") else w.toggled).connect(self._edited)
        self.fit.toggled.connect(lambda on: self.pace.setEnabled(not on and self.mode_key() == "custom"))
        self.preview_btn.clicked.connect(self.preview_requested)
        self.reset_btn.clicked.connect(self.reset)
        self._mode_changed()

    # -- state
    def mode_key(self) -> str:
        return str(self.mode.currentData())

    def _load(self, cfg: RhythmConfig) -> None:
        for w in self._editors:
            w.blockSignals(True)
        self.pause_min.setValue(cfg.pause_min_ms / 1000)
        self.pause_max.setValue(cfg.pause_max_ms / 1000)
        self.short_prob.setValue(cfg.short_pause_prob * 100)
        self.break_prob.setValue(cfg.long_break_prob * 100)
        self.break_min.setValue(cfg.break_min_ms / 1000)
        self.break_max.setValue(cfg.break_max_ms / 1000)
        self.revisions.setValue(cfg.revision_per_1000)
        self.activity.setCurrentIndex(ACTIVITIES.index(cfg.activity))
        self.fit.setChecked(cfg.pace_ms_per_char is None)
        self.pace.setValue(cfg.pace_ms_per_char or 180)
        for w in self._editors:
            w.blockSignals(False)

    def _from_widgets(self) -> RhythmConfig:
        return RhythmConfig("custom", round(self.pause_min.value() * 1000), round(self.pause_max.value() * 1000),
                            self.short_prob.value() / 100, self.break_prob.value() / 100, round(self.break_min.value() * 1000),
                            round(self.break_max.value() * 1000), self.revisions.value(), str(self.activity.currentData()),
                            None if self.fit.isChecked() else self.pace.value())

    def _mode_changed(self) -> None:
        key = self.mode_key()
        custom = key == "custom"
        self.desc.setText(PRESET_DESCRIPTIONS[key])
        self._load(self._custom if custom else replace(preset(key), pace_ms_per_char=None))
        for w in self._editors:
            w.setEnabled(custom)
        self.pace.setEnabled(custom and not self.fit.isChecked())
        self._edited(emit=False)
        self.changed.emit()

    def _edited(self, *_a, emit: bool = True) -> None:
        if self.mode_key() == "custom":
            self._custom = self._from_widgets()
        try:
            self.config()
            self.error.hide()
        except RhythmError as exc:
            self.error.setText(str(exc))
            self.error.show()
        if emit:
            self.changed.emit()

    def is_valid(self) -> bool:
        try:
            self.config()
            return True
        except RhythmError:
            return False

    def config(self) -> RhythmConfig:
        """The RhythmConfig for the current selection. Raises RhythmError if custom values are out of range."""
        key = self.mode_key()
        if key != "custom":
            return preset(key)
        return self._from_widgets().validate()

    def set_config(self, cfg: RhythmConfig) -> None:
        key = cfg.name if cfg.name in MODES else "custom"
        if key == "custom" or preset(key).to_dict() != {**cfg.to_dict(), "name": key}:
            self._custom = replace(cfg, name="custom")
            key = "custom"
        self.mode.setCurrentIndex(MODES.index(key))
        self._mode_changed()

    def reset(self) -> None:
        """Reset to defaults: Regular mode, and the Custom parameters back to their starting values."""
        self._custom = preset("custom")
        self.mode.setCurrentIndex(MODES.index("regular"))
        self._mode_changed()
