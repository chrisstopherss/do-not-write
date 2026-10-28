"""Writing Process Lab: overview, timeline, revisions, charts, experiments, evaluation, final-text research,
import, guide, and data/privacy controls. All values shown are read from stored data."""
from __future__ import annotations

import html
import json
import random
from importlib import resources
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..analysis.diff import unified, word_diff
from ..analysis.finaltext import describe, similarity
from ..analysis.revisions import Version, build_versions
from ..engine.events import Event
from ..engine.schedule import validate_duration
from ..engine.text import word_count
from ..io import exporters as X
from ..io.importer import SCHEMA, ImportRejected, import_file
from ..lab import methods
from ..lab.conditions import CONDITION_NAMES, ParamError, sample_text
from ..lab.overview import (
    METRIC_CHOICES,
    condition_means,
    ensure_features,
    experiment_rows,
    growth_points,
    interval_hist,
    kind_counts,
    revision_size_hist,
    rhythm_comparison,
    session_row,
    session_schedule,
    summary_counts,
)
from ..lab.runner import create_example_experiment, run_experiment
from ..lab.templates import TEMPLATES
from ..storage.db import Database
from . import theme
from .charts import BarChart, ConfusionWidget, LineChart, RhythmTimeline, TimelineChart
from .session_pages import diff_html
from .widgets import button, card, fill, fmt_ms, hbox, label, table

ORIGIN_LABEL = {"simulated": "SIMULATED", "synthetic": "SYNTHETIC", "imported": "IMPORTED", "captured": "CAPTURED"}
SOURCE_LABEL = {"simulated": "SIMULATED (engine)", "captured": "REAL (captured by app)", "imported": "IMPORTED (external)"}


def nr(v) -> str:
    return "Not recorded" if v is None else (f"{v:.4g}" if isinstance(v, float) else str(v))


def populate_sessions(db: Database, cb: QComboBox, with_events: bool = False) -> list[str]:
    """Refill a persistent combo box with stored sessions; returns the matching session ids."""
    cb.blockSignals(True)
    cur = cb.currentIndex()
    cb.clear()
    ids: list[str] = []
    for r in db.list_sessions():
        if with_events and not db.conn.execute("SELECT 1 FROM events WHERE session_id=? LIMIT 1", (r["id"],)).fetchone() \
                and not db.get_checkpoint_snapshots(r["id"]):
            continue
        cb.addItem(f"[{ORIGIN_LABEL.get(r['data_origin'], r['data_origin'])}] {r['label'] or r['id'][:8]}")
        ids.append(r["id"])
    if ids:
        cb.setCurrentIndex(min(max(cur, 0), len(ids) - 1))
    cb.blockSignals(False)
    return ids


class Tab(QWidget):
    def __init__(self, db: Database) -> None:
        super().__init__()
        self.db = db
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 12, 0, 0)

    def refresh(self) -> None: ...


class OverviewTab(Tab):
    changed = Signal()

    def __init__(self, db: Database) -> None:
        super().__init__(db)
        self.cards = QHBoxLayout()
        self.lay.addLayout(self.cards)
        self.card_vals: dict[str, QLabel] = {}
        for key, title in (("experiments", "Saved experiments"), ("sessions", "Completed sessions"), ("events", "Stored events"), ("datasets", "Imported datasets")):
            f, lay = card()
            v = label("0", "big", wrap=False)
            self.card_vals[key] = v
            lay.addWidget(v)
            lay.addWidget(label(title, "muted"))
            self.cards.addWidget(f)
        self.empty = label("No experiments yet. Load the synthetic example to explore the dashboard, or design one in the Experiments tab. "
                           "The example uses bundled synthetic text and simulated events; nothing is captured from a person.", "banner_info")
        self.lay.addWidget(self.empty)
        row = QHBoxLayout()
        self.combo = QComboBox()
        self.combo.setAccessibleName("Experiment")
        self.combo.currentIndexChanged.connect(self._show)
        self.example_btn = button("Load example experiment", "primary")
        self.example_btn.clicked.connect(self._example)
        self.export_json = button("Export dataset JSON")
        self.export_csv = button("Export metrics CSV")
        self.delete_btn = button("Delete experiment...", "danger")
        self.export_json.clicked.connect(lambda: self._export("json"))
        self.export_csv.clicked.connect(lambda: self._export("csv"))
        self.delete_btn.clicked.connect(self._delete)
        row.addWidget(label("Experiment:", wrap=False))
        row.addWidget(self.combo, 1)
        for b in (self.example_btn, self.export_json, self.export_csv, self.delete_btn):
            row.addWidget(b)
        self.lay.addLayout(row)
        self.detail = label("", "muted")
        self.lay.addWidget(self.detail)
        self.table = table(["Condition", "Rep", "Origin", "Seed", "Words: source", "Words: final", "Duration", "Insertions", "Deletions",
                            "Replacements", "Checkpoints", "Revisions", "Pauses", "Breaks", "Idle (sim.)", "Validation", "Completeness"])
        self.lay.addWidget(self.table, 1)
        self.msg = label("", "muted")
        self.lay.addWidget(self.msg)
        self.eids: list[str | None] = []
        self.refresh()

    def _cards(self) -> None:
        c = summary_counts(self.db)
        for key, val in (("experiments", c["experiments"]), ("sessions", c["completed_sessions"]), ("events", c["events"]), ("datasets", c["datasets"])):
            self.card_vals[key].setText(f"{val:,}")

    def refresh(self) -> None:
        self._cards()
        cur = self.combo.currentIndex()
        self.combo.blockSignals(True)
        self.combo.clear()
        self.eids = []
        for e in self.db.list_experiments():
            self.combo.addItem(("[EXAMPLE] " if e["is_example"] else "") + e["label"])
            self.eids.append(e["id"])
        self.combo.addItem("All saved sessions")
        self.eids.append(None)
        self.combo.setCurrentIndex(min(max(cur, 0), self.combo.count() - 1) if cur >= 0 else 0)
        self.combo.blockSignals(False)
        has = bool(self.db.list_experiments())
        self.empty.setVisible(not has)
        self._show()

    def _show(self) -> None:
        i = self.combo.currentIndex()
        eid = self.eids[i] if 0 <= i < len(self.eids) else None
        if eid:
            rows = experiment_rows(self.db, eid)
            e = next(x for x in self.db.list_experiments() if x["id"] == eid)
            conds = self.db.experiment_conditions(eid)
            ps = [json.loads(c["params_json"]) for c in conds]
            self.detail.setText(f"Type: controlled synthetic experiment, {len(conds)} condition(s), {len(rows)} run(s). "
                                f"Duration {fmt_ms(ps[0]['duration_ms']) if ps else 'n/a'}; checkpoint interval "
                                f"{fmt_ms(ps[0]['checkpoint_interval_ms']) if ps and ps[0]['checkpoint_interval_ms'] else 'none'}. "
                                f"Data origin: SIMULATED. Expected outcome (researcher's note): {e['expected'] or 'none recorded'}. Notes: {e['notes'] or 'none'}.")
        else:
            rows = [session_row(self.db, r["id"]) for r in self.db.list_sessions()]
            self.detail.setText("All saved sessions (application sessions, experiment runs, imported datasets). Each row shows its own data origin.")
        for b in (self.export_json, self.export_csv):
            b.setEnabled(bool(rows))
        self.delete_btn.setEnabled(bool(eid))
        fill(self.table, [[r["condition"] or "-", nr(r["rep"]), ORIGIN_LABEL.get(r["origin"], r["origin"]), nr(r["seed"]),
                           str(r["words_source"]), nr(r["words_final"]), fmt_ms(r["duration_ms"]) if r["duration_ms"] else "Not recorded",
                           nr(r["insertions"]), nr(r["deletions"]), nr(r["replacements"]), nr(r["checkpoints"]), nr(r["revisions"]),
                           nr(r["pauses"]), nr(r["breaks"]), fmt_ms(r["idle_ms"]), r["validation"], r["completeness"]] for r in rows])
        self._rows = rows

    def _example(self) -> None:
        create_example_experiment(self.db)
        self.refresh()
        self.changed.emit()

    def _export(self, kind: str) -> None:
        sids = [r["session_id"] for r in self._rows]
        sessions = [(self.db.get_session(s), self.db.get_events(s)) for s in sids]
        if kind == "json":
            content = X.dataset_json(self.combo.currentText(), [X.session_dict(row, ev, ensure_features(self.db, row["id"]), self.db.get_user_actions(row["id"])) for row, ev in sessions])
            ext = "json"
        else:
            content = X.metrics_csv([(row["id"], row["data_origin"], _as_results(ensure_features(self.db, row["id"]))) for row, _ in sessions])
            ext = "csv"
        path, _ = QFileDialog.getSaveFileName(self, "Export", f"experiment.{ext}", f"*.{ext}")
        if path:
            try:
                self.msg.setText(f"Saved {X.write_new(path, content)}")
            except OSError as exc:
                self.msg.setText(f"Could not save: {exc.strerror or exc}")

    def _delete(self) -> None:
        eid = self.eids[self.combo.currentIndex()]
        if eid and QMessageBox.question(self, "Delete experiment", "Delete this experiment, its runs and their generated sessions? This cannot be undone.") \
                == QMessageBox.StandardButton.Yes:
            self.db.delete_experiment(eid)
            self.refresh()
            self.changed.emit()


def _as_results(flat: dict[str, dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for key, v in flat.items():
        ex, name = key.split(".", 1)
        out.setdefault(ex, {"version": v["version"], "features": []})["features"].append(
            {"name": name, "value": v["value"], "status": v["status"], "note": v["note"]})
    return out


class TimelineTab(Tab):
    def __init__(self, db: Database) -> None:
        super().__init__(db)
        self.row = QHBoxLayout()
        self.lay.addLayout(self.row)
        self.chart = TimelineChart()
        self.lay.addWidget(self.chart)
        self.info = label("", "muted")
        self.lay.addWidget(self.info)
        self.table = table(["#", "Time", "Kind", "Range", "Chars", "Previous text", "Resulting text", "Interval", "Source", "Real or simulated"])
        self.lay.addWidget(self.table, 1)
        self.combo = QComboBox()
        self.combo.setAccessibleName("Session")
        self.kind = QComboBox()
        self.kind.setAccessibleName("Event kind filter")
        self.kind.addItems(["All kinds", "insert", "delete", "replace", "pause", "resume", "checkpoint"])
        self.combo.currentIndexChanged.connect(self._show)
        self.kind.currentIndexChanged.connect(self._show)
        self.row.addWidget(label("Session:", wrap=False))
        self.row.addWidget(self.combo, 1)
        self.row.addWidget(label("Filter:", wrap=False))
        self.row.addWidget(self.kind)
        self.ids: list[str] = []
        self.refresh()

    def refresh(self) -> None:
        self.ids = populate_sessions(self.db, self.combo, with_events=True)
        self._show()

    def _show(self) -> None:
        i = self.combo.currentIndex()
        if not self.ids or i < 0:
            self.chart.set_events([])
            self.table.setRowCount(0)
            self.info.setText("No sessions with recorded events yet. Run a session or load the example experiment.")
            return
        evs = self.db.get_events(self.ids[i])
        self.chart.set_events([(e.t_ms, e.kind) for e in evs])
        flt = self.kind.currentText()
        rows, last_t = [], None
        for e in evs:
            interval = None if e.t_ms is None or last_t is None else e.t_ms - last_t
            if e.t_ms is not None:
                last_t = e.t_ms
            if flt != "All kinds" and e.kind != flt:
                continue
            rng = f"{e.pos}-{e.pos + len(e.removed)}" if e.is_text_op and e.pos is not None else "n/a"
            chars = str(max(len(e.removed), len(e.inserted))) if e.is_text_op else "n/a"
            rows.append([str(e.seq), fmt_ms(e.t_ms), e.kind, rng, chars, repr(e.removed[:40]) if e.removed else "", repr(e.inserted[:40]) if e.inserted else "",
                         "Not recorded" if interval is None else f"{interval} ms", e.source, SOURCE_LABEL.get(e.source, e.source)])
        shown = rows[:3000]
        fill(self.table, shown)
        untimed = sum(1 for e in evs if e.t_ms is None)
        self.info.setText(f"{len(evs)} stored events ({len(rows)} match filter{', first 3000 shown' if len(rows) > 3000 else ''}); "
                          f"{untimed} without a timestamp (shown as Not recorded). Cursor movement is not captured by this version.")


class RevisionsTab(Tab):
    def __init__(self, db: Database) -> None:
        super().__init__(db)
        self.row = QHBoxLayout()
        self.lay.addLayout(self.row)
        self.note = label("The original source text is stored once and never modified. Versions below are rebuilt from the stored event log; "
                          "nothing is inferred.", "muted")
        self.lay.addWidget(self.note)
        self.vtable = table(["Version", "Time", "Chars", "Words", "Added", "Removed", "Since previous", "Source"])
        self.vtable.setMaximumHeight(190)
        self.lay.addWidget(self.vtable)
        sel = QHBoxLayout()
        self.a, self.b, self.mode = QComboBox(), QComboBox(), QComboBox()
        self.a.setAccessibleName("Compare from version")
        self.b.setAccessibleName("Compare to version")
        self.mode.addItems(["Side by side", "Unified diff"])
        for w in (self.a, self.b, self.mode):
            w.currentIndexChanged.connect(self._diff)
        sel.addWidget(label("From", wrap=False))
        sel.addWidget(self.a, 1)
        sel.addWidget(label("To", wrap=False))
        sel.addWidget(self.b, 1)
        sel.addWidget(self.mode)
        self.export_btn = button("Export revision summary (MD / HTML)")
        self.export_btn.clicked.connect(self._export)
        sel.addWidget(self.export_btn)
        self.lay.addLayout(sel)
        d = QHBoxLayout()
        self.left, self.right = QTextBrowser(), QTextBrowser()
        for w, n in ((self.left, "From version"), (self.right, "To version")):
            w.setObjectName("editor")
            w.setAccessibleName(n)
            d.addWidget(w)
        self.lay.addLayout(d, 1)
        self.versions: list[Version] = []
        self.ids: list[str] = []
        self.combo = QComboBox()
        self.combo.setAccessibleName("Session")
        self.combo.currentIndexChanged.connect(self._load)
        self.row.addWidget(label("Session:", wrap=False))
        self.row.addWidget(self.combo, 1)
        self.refresh()

    def refresh(self) -> None:
        self.ids = populate_sessions(self.db, self.combo, with_events=True)
        self._load()

    def _load(self) -> None:
        i = self.combo.currentIndex()
        for w in (self.a, self.b):
            w.blockSignals(True)
            w.clear()
        self.versions = []
        if not self.ids or i < 0:
            self.vtable.setRowCount(0)
            self.left.setPlainText("No sessions with stored versions yet.")
            self.right.clear()
            for w in (self.a, self.b):
                w.blockSignals(False)
            return
        sid = self.ids[i]
        s = self.db.get_session(sid)
        evs = self.db.get_events(sid)
        snaps = [(r["t_ms"], r["label"], r["text"]) for r in self.db.get_checkpoint_snapshots(sid)]
        vs = build_versions("", evs, snaps, snapshot_source=s["data_origin"] if s["data_origin"] != "synthetic" else "imported", final_text=s["final_text"])
        if s["source_text"] and evs:
            vs.insert(0, Version("Original input (reference)", "source", None, None, s["source_text"], len(s["source_text"]),
                                 word_count(s["source_text"]), "reference", None, None, None))
        self.versions = vs
        fill(self.vtable, [[v.label, fmt_ms(v.t_ms), str(v.chars), str(v.words), nr(v.added_chars), nr(v.removed_chars),
                            fmt_ms(v.elapsed_since_prev_ms), SOURCE_LABEL.get(v.data_source, v.data_source)] for v in vs])
        for v in vs:
            self.a.addItem(v.label)
            self.b.addItem(v.label)
        self.a.setCurrentIndex(0)
        self.b.setCurrentIndex(len(vs) - 1)
        for w in (self.a, self.b):
            w.blockSignals(False)
        self._diff()

    def _diff(self) -> None:
        if not self.versions or self.a.currentIndex() < 0 or self.b.currentIndex() < 0:
            return
        va, vb = self.versions[self.a.currentIndex()], self.versions[self.b.currentIndex()]
        if self.mode.currentText() == "Unified diff":
            self.left.setPlainText(unified(va.text, vb.text, va.label, vb.label) or "(identical)")
            self.right.hide()
            return
        self.right.show()
        c = theme.colors()
        ops = word_diff(va.text, vb.text)
        la = "".join(html.escape(o.a) if o.tag == "equal" else f'<span style="background:{c["del"]}">{html.escape(o.a)}</span>' for o in ops)
        rb = "".join(html.escape(o.b) if o.tag == "equal" else f'<span style="background:{c["ins"]}">{html.escape(o.b)}</span>' for o in ops)
        self.left.setHtml(f'<div style="white-space:pre-wrap">{la}</div>')
        self.right.setHtml(f'<div style="white-space:pre-wrap">{rb}</div>')

    def _export(self) -> None:
        if not self.versions:
            return
        sid = self.ids[self.combo.currentIndex()]
        s = self.db.get_session(sid)
        feats = _as_results(ensure_features(self.db, sid))
        path, _ = QFileDialog.getSaveFileName(self, "Export revision summary", "revisions.md", "Markdown (*.md);;HTML (*.html)")
        if path:
            build = X.report_html if path.lower().endswith(".html") else X.report_markdown
            content = build(s["label"] or sid, s["data_origin"], feats, [v.to_dict() for v in self.versions])
            try:
                X.write_new(path, content)
            except OSError as exc:
                QMessageBox.warning(self, "Export failed", str(exc))


class ChartsTab(Tab):
    def __init__(self, db: Database) -> None:
        super().__init__(db)
        self.row = QHBoxLayout()
        self.lay.addLayout(self.row)
        g = QGridLayout()
        self.growth = LineChart("Text length over time (x: seconds)", "", "characters", "Not recorded: this session has no timestamped text events.")
        self.kinds = BarChart("Insertions vs deletions vs replacements", "events")
        self.revsize = BarChart("Revision-size distribution (characters removed)", "revisions", "No delete or replace events in this session.")
        self.intervals = BarChart("Event intervals: ms between text events (bin lower bound)", "intervals", "Not recorded: fewer than 2 timestamped text events.")
        g.addWidget(self.growth, 0, 0)
        g.addWidget(self.kinds, 0, 1)
        g.addWidget(self.revsize, 1, 0)
        g.addWidget(self.intervals, 1, 1)
        self.lay.addLayout(g)
        cmp_row = QHBoxLayout()
        self.exp, self.metric_box = QComboBox(), QComboBox()
        self.metric_box.addItems(list(METRIC_CHOICES))
        self.metric_box.currentIndexChanged.connect(self._compare)
        self.exp.currentIndexChanged.connect(self._compare)
        cmp_row.addWidget(label("Condition comparison:", wrap=False))
        cmp_row.addWidget(self.exp, 1)
        cmp_row.addWidget(self.metric_box)
        self.lay.addLayout(cmp_row)
        self.cmp = BarChart("Mean metric per condition (runs without the metric are excluded)", "", "Select an experiment.")
        self.lay.addWidget(self.cmp)
        self.cmp_note = label("", "muted")
        self.lay.addWidget(self.cmp_note)
        self.combo = QComboBox()
        self.combo.setAccessibleName("Session")
        self.combo.currentIndexChanged.connect(self._session)
        self.row.addWidget(label("Session:", wrap=False))
        self.row.addWidget(self.combo, 1)
        self.exp.setAccessibleName("Experiment")
        self.metric_box.setAccessibleName("Metric")
        self.ids: list[str] = []
        self.eids: list[str] = []
        self.refresh()

    def refresh(self) -> None:
        self.ids = populate_sessions(self.db, self.combo, with_events=True)
        self.exp.blockSignals(True)
        self.exp.clear()
        self.eids = []
        for e in self.db.list_experiments():
            self.exp.addItem(e["label"])
            self.eids.append(e["id"])
        self.exp.blockSignals(False)
        self._session()
        self._compare()

    def _session(self) -> None:
        i = self.combo.currentIndex()
        evs = self.db.get_events(self.ids[i]) if self.ids and i >= 0 else []
        self.growth.set_points(growth_points(evs))
        c, v = kind_counts(evs)
        self.kinds.set_data(c, {"events": v} if evs else {})
        c, v = revision_size_hist(evs)
        self.revsize.set_data(c, {"revisions": v})
        c, v = interval_hist(evs)
        self.intervals.set_data(c, {"intervals": v})

    def _compare(self) -> None:
        i = self.exp.currentIndex()
        if i < 0 or not self.eids:
            self.cmp.set_data([], {})
            return
        cats, vals, missing = condition_means(self.db, self.eids[i], METRIC_CHOICES[self.metric_box.currentText()])
        short = [c.split(":")[0] if ":" in c else c for c in cats]
        self.cmp.title = f"Mean {self.metric_box.currentText().lower()} per condition"
        self.cmp.set_data(short, {self.metric_box.currentText(): vals})
        self.cmp_note.setText("Conditions: " + "; ".join(cats) + (". Runs excluded for missing metric: " + str(missing) if missing else "."))


class RhythmTab(Tab):
    """Writing rhythm analytics: per-session distributions and profile comparison. Simulated clock; descriptive only."""

    def __init__(self, db: Database) -> None:
        super().__init__(db)
        scroll = QScrollArea()  # this tab has more content than fits a window, so it scrolls
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setAccessibleName("Rhythm analytics")
        inner = QWidget()
        inner.setObjectName("page")
        scroll.setWidget(inner)
        self.lay.addWidget(scroll)
        self.lay = QVBoxLayout(inner)
        self.lay.setContentsMargins(0, 0, 8, 0)
        self.lay.addWidget(label("Rhythm statistics describe the pauses and breaks in the stored event data. In this version all pauses and "
                                 "breaks are SIMULATED by the engine; the only real application telemetry is your own pause, resume and cancel "
                                 "actions (shown as markers). Break lengths are experimental parameters and say nothing about how a text was "
                                 "written. Sessions without recorded pauses show Not recorded, not zero.", "banner_info"))
        row = QHBoxLayout()
        self.exp = QComboBox()
        self.exp.setAccessibleName("Experiment")
        self.exp.currentIndexChanged.connect(self._compare)
        row.addWidget(label("Compare rhythm profiles in:", wrap=False))
        row.addWidget(self.exp, 1)
        self.lay.addLayout(row)
        self.cmp = table(["Condition / profile", "Runs", "Active (mean)", "Idle (mean)", "Breaks (mean)", "Avg break (mean)", "Longest break (max)",
                          "Text per active interval", "Revisions/min before breaks", "Revisions/min after breaks", "Runs without data"])
        self.cmp.setMinimumHeight(170)
        self.cmp.setMaximumHeight(200)
        self.lay.addWidget(self.cmp)
        self.cmp_note = label("", "muted")
        self.lay.addWidget(self.cmp_note)
        row2 = QHBoxLayout()
        self.combo = QComboBox()
        self.combo.setAccessibleName("Session")
        self.combo.currentIndexChanged.connect(self._session)
        row2.addWidget(label("Session:", wrap=False))
        row2.addWidget(self.combo, 1)
        self.lay.addLayout(row2)
        self.timeline = RhythmTimeline()
        self.lay.addWidget(self.timeline)
        g = QGridLayout()
        self.pauses = BarChart("Pause and break durations (bin lower bound)", "count", "Not recorded: no pauses or breaks in this session.")
        self.intervals = BarChart("Writing-interval lengths (bin lower bound)", "count", "Not recorded for this session.")
        self.added = BarChart("Text added per active interval (characters)", "chars", "Not recorded for this session.")
        self.around = BarChart("Revisions per active minute, before vs after breaks", "per minute", "Needs at least one break.")
        g.addWidget(self.pauses, 0, 0)
        g.addWidget(self.intervals, 0, 1)
        g.addWidget(self.added, 1, 0)
        g.addWidget(self.around, 1, 1)
        self.lay.addLayout(g)
        self.sum = label("", "muted")
        self.lay.addWidget(self.sum)
        self.ids: list[str] = []
        self.eids: list[str] = []
        self.refresh()

    def refresh(self) -> None:
        self.ids = populate_sessions(self.db, self.combo, with_events=True)
        self.exp.blockSignals(True)
        self.exp.clear()
        self.eids = []
        for e in self.db.list_experiments():
            self.exp.addItem(e["label"])
            self.eids.append(e["id"])
        self.exp.blockSignals(False)
        self._compare()
        self._session()

    def _compare(self) -> None:
        i = self.exp.currentIndex()
        if i < 0 or not self.eids:
            self.cmp.setRowCount(0)
            self.cmp_note.setText("No experiments yet. Run the template 'Rhythm profiles' in the Experiments tab.")
            return
        rows = rhythm_comparison(self.db, self.eids[i])
        f = lambda v: "Not recorded" if v is None else fmt_ms(v)  # noqa: E731
        n = lambda v: "Not recorded" if v is None else f"{v:.2f}"  # noqa: E731
        fill(self.cmp, [[r["condition"], str(r["runs"]), f(r["active_ms"]), f(r["idle_ms"]), n(r["breaks"]), f(r["avg_break_ms"]), f(r["longest_break_ms"]),
                         n(r["text_per_interval"]), n(r["rev_per_min_before"]), n(r["rev_per_min_after"]), str(r["runs_without_rhythm_data"])] for r in rows])
        small = min((r["runs"] for r in rows), default=0)
        self.cmp_note.setText("Means over runs, SIMULATED data. " + (f"Only {small} run(s) per condition: differences are illustrative, not statistically supported. " if small < 30 else "")
                              + "Runs without recorded pauses are excluded from rhythm means and counted in the last column.")

    def _session(self) -> None:
        i = self.combo.currentIndex()
        if not self.ids or i < 0:
            self.timeline.set_schedule([])
            for c in (self.pauses, self.intervals, self.added, self.around):
                c.set_data([], {})
            self.sum.setText("No sessions with stored events yet.")
            return
        sid = self.ids[i]
        segs, total, markers = session_schedule(self.db, sid)
        s = self.db.get_session(sid)
        paused_known = "rhythm" in json.loads(s["config_json"]) or any(k == "pause" for k in (e.kind for e in self.db.get_events(sid)))
        self.timeline.set_schedule(segs if paused_known else [], total, markers)
        f = ensure_features(self.db, sid)
        g = lambda k: f.get(k, {}).get("value") if f.get(k, {}).get("status") == "ok" else None  # noqa: E731
        pd, ai, tp, ra = g("rhythm.pause_duration_distribution"), g("rhythm.active_interval_distribution"), g("rhythm.text_added_per_active_interval"), g("rhythm.revisions_around_breaks")
        def bins(h: dict) -> list[str]:  # "5000-9999" -> "5s", "300000+" -> "300s+": lower bound in seconds keeps labels short
            return [f"{int(k.rstrip('+').split('-')[0]) / 1000:g}s" + ("+" if k.endswith("+") else "") for k in h]
        self.pauses.set_data(bins(pd["histogram"]) if pd else [], {"pauses": [float(v) for v in pd["histogram"].values()]} if pd else {})
        self.intervals.set_data(bins(ai["histogram"]) if ai else [], {"intervals": [float(v) for v in ai["histogram"].values()]} if ai else {})
        self.added.set_data([str(k + 1) for k in range(len(tp["per_interval"]))] if tp else [], {"chars": [float(v) for v in tp["per_interval"]]} if tp else {})
        self.around.set_data(["before breaks", "after breaks"] if ra else [], {"revisions/min": [ra["per_active_minute_before"], ra["per_active_minute_after"]]} if ra else {})
        parts = [f"Data source: {', '.join(g('rhythm.rhythm_data_sources') or ['unknown'])}."]
        for label_, k in (("Active (sim.)", "rhythm.sim_active_ms"), ("Idle (sim.)", "rhythm.sim_idle_ms")):
            v = g(k)
            parts.append(f"{label_}: {fmt_ms(v) if v is not None else 'Not recorded'}.")
        for label_, k in (("Breaks", "rhythm.break_count"), ("Short pauses", "rhythm.short_pause_count")):
            v = g(k)
            parts.append(f"{label_}: {v if v is not None else 'Not recorded'}.")
        for label_, k in (("Average break", "rhythm.avg_break_ms"), ("Longest break", "rhythm.longest_break_ms")):
            v = g(k)
            parts.append(f"{label_}: {fmt_ms(v) if v is not None else 'Not recorded'}.")
        self.sum.setText(" ".join(parts))


class ExperimentsTab(Tab):
    changed = Signal()

    def __init__(self, db: Database) -> None:
        super().__init__(db)
        self.lay.addWidget(label("Design a controlled experiment. Use synthetic text or text you are explicitly authorized to use. "
                                 "Conditions are experimental treatments, not models of real writers or tools.", "muted"))
        form = QFormLayout()
        self.tpl = QComboBox()
        for t in TEMPLATES:
            self.tpl.addItem(t.title)
        self.tpl.currentIndexChanged.connect(self._tpl)
        self.label = QLineEdit("My experiment")
        self.notes = QLineEdit()
        self.expected = QLineEdit()
        self.hours, self.minutes = QSpinBox(), QSpinBox()
        self.hours.setRange(0, 24)
        self.minutes.setRange(0, 59)
        self.minutes.setValue(30)
        self.reps = QSpinBox()
        self.reps.setRange(1, 200)
        self.reps.setValue(5)
        self.seed = QSpinBox()
        self.seed.setRange(0, 2**31 - 1)
        self.seed.setValue(random.randrange(1, 100000))
        self.cp = QSpinBox()
        self.cp.setRange(0, 1440)
        self.cp.setValue(5)
        self.cp.setSuffix(" min (0 = none)")
        self.rev = QDoubleSpinBox()
        self.rev.setRange(0, 50)
        self.rev.setValue(4)
        self.rev.setSuffix(" per 1000 chars")
        self.text = QPlainTextEdit(sample_text())
        self.text.setMaximumHeight(110)
        self.text.setAccessibleName("Experiment input text")
        self.conds = {c: QCheckBox(f"{c}: {n}") for c, n in CONDITION_NAMES.items()}
        for cb in self.conds.values():
            cb.setChecked(True)
        form.addRow("Template", self.tpl)
        form.addRow("Experiment label", self.label)
        form.addRow("Notes", self.notes)
        form.addRow("Expected outcome", self.expected)
        dur = hbox(self.hours, self.minutes, stretch_last=True)
        form.addRow("Duration (h / min)", dur)
        form.addRow("Checkpoint interval", self.cp)
        form.addRow("Revision frequency (D, E)", self.rev)
        form.addRow("Repetitions", self.reps)
        form.addRow("Base random seed", self.seed)
        self.lay.addLayout(form)
        self.lay.addWidget(label("Input text (bundled synthetic sample by default):", "muted"))
        self.lay.addWidget(self.text)
        self.tplnote = label("", "muted")
        self.lay.addWidget(self.tplnote)
        grid = QHBoxLayout()
        for cb in self.conds.values():
            grid.addWidget(cb)
        self.lay.addLayout(grid)
        self.banner = label("", "banner_err")
        self.banner.hide()
        self.lay.addWidget(self.banner)
        self.run_btn = button("Run experiment", "primary")
        self.run_btn.clicked.connect(self._run)
        self.lay.addWidget(self.run_btn)
        self.lay.addStretch(1)
        self._tpl()

    def _tpl(self) -> None:
        t = TEMPLATES[self.tpl.currentIndex()]
        self.tplnote.setText(f"{t.description} Suggested expectation: {t.expected}")
        self.expected.setText(t.expected)
        custom = t.id == "five"
        for cb in self.conds.values():
            cb.setEnabled(custom)

    def _run(self) -> None:
        try:
            ms = validate_duration((self.hours.value() * 60 + self.minutes.value()) * 60_000)
            t = TEMPLATES[self.tpl.currentIndex()]
            conds = t.build(self.text.toPlainText(), ms)
            if t.id == "five":
                conds = [c for c in conds if self.conds[c.code].isChecked()]
            for c in conds:
                c.checkpoint_interval_ms = self.cp.value() * 60_000 or None
                c.revision_frequency = self.rev.value()
            run_experiment(self.db, self.label.text(), conds, self.reps.value(), self.seed.value(), self.notes.text(), self.expected.text())
        except (ValueError, ParamError, KeyError) as exc:
            self.banner.setText(str(exc))
            self.banner.show()
            return
        self.banner.hide()
        self.changed.emit()
        QMessageBox.information(self, "Experiment complete", "Saved. See the Overview tab.")


class EvaluationTab(Tab):
    def __init__(self, db: Database) -> None:
        super().__init__(db)
        self.lay.addWidget(label("This tab evaluates a descriptive rule against labels that are known because the data was generated here. "
                                 "It is NOT an authorship or AI detector, and synthetic-on-synthetic results say nothing about real documents.", "banner_warn"))
        self.lay.addWidget(label(f"Method {methods.METHOD_ID} v{methods.METHOD_VERSION}: {methods.METHOD_DESCRIPTION}", "muted"))
        row = QHBoxLayout()
        self.exp = QComboBox()
        self.exp.setAccessibleName("Experiment")
        self.frac = QDoubleSpinBox()
        self.frac.setRange(0.1, 0.9)
        self.frac.setSingleStep(0.05)
        self.frac.setValue(0.4)
        self.seed = QSpinBox()
        self.seed.setRange(0, 2**31 - 1)
        self.seed.setValue(1)
        self.run_btn = button("Run evaluation", "primary")
        self.run_btn.clicked.connect(self._run)
        for w in (label("Experiment", wrap=False), self.exp, label("Test fraction", wrap=False), self.frac, label("Split seed", wrap=False), self.seed, self.run_btn):
            row.addWidget(w)
        self.lay.addLayout(row)
        self.cm = ConfusionWidget()
        self.lay.addWidget(self.cm)
        self.out = QTextBrowser()
        self.out.setAccessibleName("Evaluation metrics")
        self.lay.addWidget(self.out, 1)
        self.eids: list[str] = []
        self.refresh()

    def refresh(self) -> None:
        self.exp.clear()
        self.eids = []
        for e in self.db.list_experiments():
            self.exp.addItem(e["label"])
            self.eids.append(e["id"])
        self.run_btn.setEnabled(bool(self.eids))
        if not self.eids:
            self.out.setPlainText("No experiments to evaluate. Load the example experiment from the Overview tab.")

    def _run(self) -> None:
        eid = self.eids[self.exp.currentIndex()]
        samples = []
        for r in self.db.experiment_runs(eid):
            s = self.db.get_session(r["session_id"])
            truth = json.loads(s["config_json"]).get("ground_truth", {}).get("contains_bulk_insert")
            if truth is None:
                continue
            f = ensure_features(self.db, s["id"]).get("process_events.largest_single_event_share")
            samples.append(methods.Sample(s["id"], f["value"] if f and f["status"] == "ok" else None, bool(truth)))
        res = methods.evaluate(samples, self.frac.value(), self.seed.value())
        if "error" in res:
            self.cm.set_matrix(None)
            self.out.setPlainText(res["error"])
            return
        c = res["confusion"]
        self.db.save_evaluation(eid, res["method_id"], res["method_version"], res["split"], c["tp"], c["fp"], c["tn"], c["fn"], res["metrics"])
        self.cm.set_matrix(c)
        m = res["metrics"]
        sp = res["split"]
        rows = "".join(f"<tr><td>{k}</td><td>{nr(m[k])}</td></tr>" for k in ("precision", "recall", "specificity", "false_positive_rate", "false_negative_rate", "accuracy"))
        ci = "".join(f"<li>{k}: " + ("n/a" if v is None else f"{v[0]:.3f} to {v[1]:.3f}") + "</li>" for k, v in m["ci95"].items())
        warn = "".join(f"<li>{html.escape(w)}</li>" for w in m["warnings"])
        self.out.setHtml(f"<p>Threshold {res['threshold']:.3f} was chosen on the <b>train</b> split only ({sp['train_n']} samples) and applied to the held-out "
                         f"<b>test</b> split ({sp['test_n']} samples, split seed {sp['seed']}). Skipped for missing feature: {sp['skipped_missing_feature']}.</p>"
                         f"<table border=1 cellpadding=4><tr><th>Metric</th><th>Value (None = undefined)</th></tr>{rows}</table>"
                         f"<p>95% Wilson intervals:</p><ul>{ci}</ul><p><b>Warnings</b></p><ul>{warn}</ul>")


class FinalTextTab(Tab):
    def __init__(self, db: Database) -> None:
        super().__init__(db)
        self.lay.addWidget(label("Final-text analysis describes a text. It cannot show how, or by whom or what, the text was produced. "
                                 "There is no AI or human score here, and none of these statistics is a detector.", "banner_warn"))
        row = QHBoxLayout()
        self.a, self.b = QPlainTextEdit(), QPlainTextEdit()
        self.a.setPlaceholderText("Text A (paste, or load a saved session's final text)")
        self.b.setPlaceholderText("Text B (optional, for similarity)")
        self.a.setAccessibleName("Text A")
        self.b.setAccessibleName("Text B")
        row.addWidget(self.a)
        row.addWidget(self.b)
        self.lay.addLayout(row, 1)
        btns = QHBoxLayout()
        self.go = button("Analyze", "primary")
        self.go.clicked.connect(self._run)
        self.load_btn = button("Load latest saved session into A")
        self.load_btn.clicked.connect(self._load)
        btns.addWidget(self.go)
        btns.addWidget(self.load_btn)
        btns.addStretch(1)
        self.lay.addLayout(btns)
        self.out = QTextBrowser()
        self.out.setAccessibleName("Final-text statistics")
        self.lay.addWidget(self.out, 2)
        self.out.setHtml("<p>Statistics appear here after you press Analyze. Definitions are in the Guide tab (metrics).</p>")

    def _load(self) -> None:
        rows = self.db.list_sessions()
        if rows and rows[0]["final_text"]:
            self.a.setPlainText(rows[0]["final_text"])

    def _run(self) -> None:
        def block(name: str, text: str) -> str:
            if not text.strip():
                return f"<h3>{name}</h3><p>No text.</p>"
            d = describe(text)
            ld, sv, rd = d["lexical_diversity"], d["sentence_length_variation"], d["readability"]
            rows = [("Tokens / types", f"{ld['tokens']} / {ld['types']}"), ("Type-token ratio", nr(ld["ttr"])),
                    ("MATTR (window 50)", nr(ld["mattr50"]) if ld["mattr50"] is not None else "Insufficient data (needs 50+ words)"),
                    ("Guiraud's R", nr(ld["guiraud_r"])), ("Sentences", str(sv["sentences"])), ("Mean sentence length (words)", nr(sv["mean"])),
                    ("Sentence length stdev", nr(sv["stdev"])), ("Coefficient of variation", nr(sv["cv"])),
                    ("Flesch reading ease", nr(rd["flesch_reading_ease"])), ("Flesch-Kincaid grade", nr(rd["flesch_kincaid_grade"]))]
            t = "".join(f"<tr><td>{html.escape(k)}</td><td>{html.escape(v)}</td></tr>" for k, v in rows)
            ph = "".join(f"<li>{html.escape(p)} ({c})</li>" for p, c in d["common_bigrams"][:8]) or "<li>none repeated</li>"
            return f"<h3>{name}</h3><table border=1 cellpadding=4>{t}</table><p>Repeated two-word phrases:</p><ul>{ph}</ul><p><i>{html.escape(rd['note'] or '')}</i></p>"
        ta, tb = self.a.toPlainText(), self.b.toPlainText()
        out = block("Text A", ta)
        if tb.strip():
            out += block("Text B", tb)
            s = similarity(ta, tb)
            out += (f"<h3>Similarity A vs B</h3><p>Jaccard (word sets): {nr(s['jaccard_words'])}; cosine (word counts): {nr(s['cosine_counts'])}; "
                    f"character match ratio: {nr(s['char_ratio'])}. Similarity of two texts is not evidence about either text's origin.</p>")
        self.out.setHtml(out)


class ImportTab(Tab):
    changed = Signal()

    def __init__(self, db: Database) -> None:
        super().__init__(db)
        self.lay.addWidget(label(f"Import a user-authorized revision-history export or a synthetic dataset (schema {SCHEMA}). Files are parsed as data only, "
                                 "validated, and imported all-or-nothing. Imported data requires an authorization statement. Do not import other people's "
                                 "writing data without their authorization. This version has no Google account integration.", "banner_info"))
        row = QHBoxLayout()
        self.imp = button("Import .json file...", "primary")
        self.imp.clicked.connect(self._import)
        self.sample = button("Save example file...")
        self.sample.clicked.connect(self._sample)
        self.delete = button("Delete selected dataset...", "danger")
        self.delete.clicked.connect(self._delete)
        for b in (self.imp, self.sample, self.delete):
            row.addWidget(b)
        row.addStretch(1)
        self.lay.addLayout(row)
        self.msg = label("", "muted")
        self.lay.addWidget(self.msg)
        self.table = table(["Name", "Origin", "Imported at", "Schema", "SHA-256 (first 12)", "Source description"])
        self.lay.addWidget(self.table, 1)
        self.ids: list[str] = []
        self.refresh()

    def refresh(self) -> None:
        ds = self.db.list_datasets()
        self.ids = [d["id"] for d in ds]
        fill(self.table, [[d["name"], d["origin"].upper(), d["created_at"], d["schema_version"], d["sha256"][:12], d["source_description"]] for d in ds])
        self.delete.setEnabled(bool(ds))

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import dataset", "", "JSON (*.json)")
        if not path:
            return
        try:
            r = import_file(self.db, path)
            self.msg.setText(f"Imported {r.events} events and {r.snapshots} snapshots. " + " ".join(r.warnings))
            self.refresh()
            self.changed.emit()
        except ImportRejected as exc:
            self.msg.setText("Import rejected, nothing was saved:\n- " + "\n- ".join(exc.errors[:8]))
        except OSError as exc:
            self.msg.setText(f"Could not read file: {exc.strerror or exc}")

    def _sample(self) -> None:
        text = resources.files("wpl.lab").joinpath("guide/example_revision_history.json").read_text(encoding="utf-8")
        path, _ = QFileDialog.getSaveFileName(self, "Save example", "example_revision_history.json", "*.json")
        if path:
            try:
                self.msg.setText(f"Saved {X.write_new(path, text)}")
            except OSError as exc:
                self.msg.setText(f"Could not save: {exc.strerror or exc}")

    def _delete(self) -> None:
        r = self.table.currentRow()
        if r >= 0 and QMessageBox.question(self, "Delete dataset", "Delete this dataset and its sessions? This cannot be undone.") == QMessageBox.StandardButton.Yes:
            self.db.delete_dataset(self.ids[r])
            self.refresh()
            self.changed.emit()


GUIDES = {"Research guide: workflow comparison": "research_guide.md", "Document history vs final text (Google Docs, extensions)": "document_history_research.md",
          "Metric definitions": "metrics.md"}


class GuideTab(Tab):
    def __init__(self, db: Database) -> None:
        super().__init__(db)
        self.combo = QComboBox()
        self.combo.addItems(list(GUIDES))
        self.combo.currentIndexChanged.connect(self._load)
        self.lay.addWidget(self.combo)
        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.view.setAccessibleName("Guide")
        self.lay.addWidget(self.view, 1)
        self._load()

    def _load(self) -> None:
        name = GUIDES[self.combo.currentText()]
        self.view.setMarkdown(resources.files("wpl.lab").joinpath("guide", name).read_text(encoding="utf-8"))


EVENT_TABLE = [
    ["insert / delete / replace", "SIMULATED (engine) or IMPORTED", "When a session or experiment runs, or a dataset is imported", "Affected text fragments, position, time", "Until you delete them"],
    ["checkpoint", "SIMULATED or IMPORTED", "At the configured checkpoint interval", "A marker only; versions are rebuilt from events (no duplicate text)", "Until you delete the session"],
    ["pause / resume (simulated)", "SIMULATED", "Generated by the engine between sentences", "Time and duration", "Until you delete the session"],
    ["pause / resume with a duration (rhythm)", "SIMULATED", "Planned by the break scheduler: short pauses and breaks (up to 5 minutes), with start and end", "Time, duration, class (short or break)", "Until you delete the session"],
    ["User pause / resume / cancel", "REAL application telemetry (CAPTURED)", "When you press Pause, Resume or Cancel in a session", "Session-clock time and the phase you were in (Writing, Break, Paused). No text, no clipboard, no keystrokes. Stored in the separate user_actions table", "Until you delete the session"],
    ["Crash / unclean exit", "Session status", "Detected at next start", "Status field and progress counter only", "Until you delete the session"],
    ["paste / import (captured)", "Not collected by this version", "-", "The app never reads the clipboard except when you press Paste in New session; that text becomes the session's source text and is not logged as an event", "-"],
    ["cursor_move", "Not collected by this version", "-", "-", "-"],
    ["focused-window input", "SIMULATED (engine) + real session controls", "Only after explicit opt-in, target selection and confirmation", "Synthetic event text is sent to the selected foreground window; focus changes pause or stop. Target handles and received-field contents are not stored", "Until you delete the session"],
    ["Captured keystrokes", "Not collected by this version", "-", "Nothing records keyboard input in any application", "-"],
]


class PrivacyTab(Tab):
    changed = Signal()

    def __init__(self, db: Database, data_dir: Path) -> None:
        super().__init__(db)
        self.data_dir = data_dir
        self.lay.addWidget(label("What is stored, where it comes from, and how long it stays. Everything is local. Nothing is uploaded, "
                                 "no telemetry is collected, and document text is never written to the log file.", "muted"))
        t = QTextBrowser()
        t.setAccessibleName("Event categories, sources, scope and retention")
        rows = "".join("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in r) + "</tr>" for r in EVENT_TABLE)
        t.setHtml("<table border=1 cellpadding=6 cellspacing=0 width=100%><tr><th>Event category</th><th>Data source</th><th>When recorded</th>"
                  f"<th>Scope</th><th>Retention</th></tr>{rows}</table>")
        self.lay.addWidget(t, 1)
        self.counts = label("", "muted")
        self.lay.addWidget(self.counts)
        row = QHBoxLayout()
        self.del_research = button("Delete research data...", "danger")
        self.del_all = button("Delete ALL saved data...", "danger")
        self.del_research.clicked.connect(self._del_research)
        self.del_all.clicked.connect(self._del_all)
        row.addWidget(self.del_research)
        row.addWidget(self.del_all)
        row.addStretch(1)
        self.lay.addLayout(row)
        self.lay.addWidget(label(f"Database location: {data_dir / 'wpl.db'}", "muted"))
        self.refresh()

    def refresh(self) -> None:
        c = self.db.counts()
        self.counts.setText("Currently stored: " + ", ".join(f"{k} {v:,}" for k, v in c.items()))

    def _del_research(self) -> None:
        if QMessageBox.question(self, "Delete research data", "Delete all experiments, their generated sessions, imported datasets, features and evaluations? "
                                "Sessions from the New session screen are kept. This cannot be undone.") == QMessageBox.StandardButton.Yes:
            self.db.delete_all_research_data()
            self.refresh()
            self.changed.emit()

    def _del_all(self) -> None:
        if QMessageBox.question(self, "Delete everything", "Delete ALL sessions, events and research data? This cannot be undone.") == QMessageBox.StandardButton.Yes:
            self.db.delete_everything()
            self.refresh()
            self.changed.emit()


class LabPage(QWidget):
    data_changed = Signal()

    def __init__(self, db: Database, data_dir: Path) -> None:
        super().__init__()
        self.setObjectName("page")
        self.db = db
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 20, 28, 20)
        head = QHBoxLayout()
        head.addWidget(label("Writing Process Lab", "h1"))
        head.addWidget(label("Research tool: observations, not verdicts", "badge", wrap=False))
        head.addStretch(1)
        root.addLayout(head)
        root.addWidget(label("Compare writing workflows, inspect process data, and evaluate analysis methods offline. "
                             "No score here establishes authorship or how a document was created.", "muted"))
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.overview = OverviewTab(db)
        self.timeline = TimelineTab(db)
        self.revisions = RevisionsTab(db)
        self.charts = ChartsTab(db)
        self.rhythm = RhythmTab(db)
        self.experiments = ExperimentsTab(db)
        self.evaluation = EvaluationTab(db)
        self.finaltext = FinalTextTab(db)
        self.importer = ImportTab(db)
        self.guide = GuideTab(db)
        self.privacy = PrivacyTab(db, data_dir)
        for w, n in ((self.overview, "Overview"), (self.timeline, "Timeline"), (self.revisions, "Revisions"), (self.charts, "Charts"), (self.rhythm, "Rhythm"),
                     (self.experiments, "Experiments"), (self.evaluation, "Evaluation"), (self.finaltext, "Final text"),
                     (self.importer, "Import"), (self.guide, "Guide"), (self.privacy, "Data && privacy")):
            self.tabs.addTab(w, n)
        for w in (self.overview, self.experiments, self.importer, self.privacy):
            w.changed.connect(self.refresh_all)

    def refresh_all(self) -> None:
        for w in (self.overview, self.timeline, self.revisions, self.charts, self.rhythm, self.evaluation, self.importer, self.privacy):
            w.refresh()
        self.data_changed.emit()
_ = Event, Qt, theme, diff_html
