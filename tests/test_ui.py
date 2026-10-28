"""UI tests run against the real widgets using Qt's offscreen platform."""
import json

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QFileDialog  # noqa: E402

from conftest import SAMPLE  # noqa: E402
from wpl.engine.session import FakeClock  # noqa: E402
from wpl.engine.simulate import CategoryConfig, MistakeConfig, SimConfig  # noqa: E402
from wpl.service import SessionController  # noqa: E402
from wpl.storage.db import Database  # noqa: E402
from wpl.ui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    a.setStyle("Fusion")
    return a


@pytest.fixture
def win(app, tmp_path):
    db = Database(tmp_path / "t.db")
    w = MainWindow(db, tmp_path, tmp_path / "wpl.log")
    w.show()
    yield w
    w.close()
    w.deleteLater()  # otherwise closed windows pile up and every setStyleSheet re-polishes all of them
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    db.close()


def test_all_pages_open_and_empty_states(win):
    for i in range(win.nav.count()):
        win.nav.setCurrentRow(i)
        QApplication.processEvents()
    assert win.lab.overview.empty.isVisibleTo(win.lab.overview)
    assert win.history.empty.isVisibleTo(win.history)
    assert not win.new_page.start_btn.isEnabled()  # empty text disables Start
    assert win.lab.overview.table.rowCount() == 0


def test_live_session_view_matches_controller_with_emoji_and_newlines(win):
    text = "Hello 😀 world. Café naïve 日本語!\n\nSecond 😀😀 paragraph here. The end."
    m = MistakeConfig()
    for k, r in {"substitution": 0.15, "missing_duplicated": 0.1, "repetition": 0.1, "abandonment": 0.5, "correction": 0.8}.items():
        m.categories[k] = CategoryConfig(True, r)
    clock = FakeClock()
    ctl = SessionController(win.db, clock)
    ctl.create(text, 600_000, 31, SimConfig(m))
    win.active.attach(ctl)
    ctl.start()
    for _ in range(40):
        clock.advance(20_000)
        win.active._on_due()
        win.active._due.stop()
        assert win.active.view.toPlainText() == ctl.text == win.active.view.shadow
        if ctl.state == "completed":
            break
    assert ctl.state == "completed"
    assert win.active.view.toPlainText() == ctl.text


def test_view_ignores_user_edits(win):
    v = win.active.view
    v.reset("abc")
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    QTest.keyClicks(v, "zzz")
    QTest.keyClick(v, Qt.Key.Key_Backspace)
    assert v.toPlainText() == "abc"


def test_start_flow_from_new_session_page(win):
    win.new_page.set_text(SAMPLE)
    win.new_page.chips["30 minutes"].click()
    assert win.new_page.duration_ms() == 30 * 60_000
    win.new_page._start()
    assert win.ctl is not None and win.nav.currentRow() == 1 and win.ctl.state == "running"
    win.active._pause()
    assert win.ctl.state == "paused" and win.db.get_session(win.ctl.sid)["status"] == "paused"
    win.active._due.stop()


def test_custom_duration_validation_message(win):
    win.new_page.set_text("Some text.")
    win.new_page.chips["Custom"].setChecked(True)
    win.new_page.hours.setValue(0)
    win.new_page.minutes.setValue(0)
    win.new_page._start()
    assert win.new_page.banner.isVisibleTo(win.new_page) and win.ctl is None


def test_example_experiment_dashboard_uses_stored_data(win):
    lab = win.lab
    lab.overview._example()
    t = lab.overview.table
    assert t.rowCount() == 25
    hdr = [t.horizontalHeaderItem(i).text() for i in range(t.columnCount())]
    assert t.item(0, hdr.index("Origin")).text() == "SIMULATED"
    c_rows = [r for r in range(t.rowCount()) if t.item(r, 0).text().startswith("C:")]
    assert {t.item(r, hdr.index("Insertions")).text() for r in c_rows} == {"1"}  # matches stored events
    assert lab.overview.card_vals["experiments"].text() == "1"
    assert lab.timeline.table.rowCount() > 0 and lab.revisions.vtable.rowCount() >= 3
    lab.evaluation._run()
    assert lab.evaluation.cm.cm is not None
    assert win.db.list_evaluations(lab.evaluation.eids[0])


def test_timeline_not_recorded_for_untimed_imported_events(win, tmp_path, monkeypatch):
    doc = {"schema": "wpl.revision_history/1", "origin": "synthetic", "name": "untimed",
           "events": [{"kind": "insert", "pos": 0, "inserted": "ab"}, {"kind": "insert", "pos": 2, "inserted": "cd"}]}
    p = tmp_path / "u.json"
    p.write_text(json.dumps(doc))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(p), ""))
    win.lab.importer._import()
    assert "Imported 2 events" in win.lab.importer.msg.text()
    t = win.lab.timeline
    assert t.table.item(0, 1).text() == "Not recorded" and t.table.item(0, 7).text() == "Not recorded"
    assert t.table.item(0, 9).text().startswith("IMPORTED")
    from wpl.lab.overview import session_row
    sid = next(r["id"] for r in win.db.list_sessions() if r["data_origin"] == "synthetic")
    row = session_row(win.db, sid)
    assert row["completeness"] == "No timestamps" and row["validation"] == "OK" and row["events"] == 2
    assert row["insertions"] == 2 and row["pauses"] == 0  # counts are real; timing metrics stay not-recorded


def test_import_rejection_is_reported_and_nothing_saved(win, tmp_path, monkeypatch):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"schema": "nope"}))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(p), ""))
    win.lab.importer._import()
    assert "rejected" in win.lab.importer.msg.text() and win.db.counts()["datasets"] == 0


def test_final_text_tab_has_no_probability_score(win):
    f = win.lab.finaltext
    f.a.setPlainText("The cat sat on the mat. The cat sat again. A dog barked loudly at night.")
    f._run()
    out = f.out.toPlainText().lower()
    assert "type-token ratio" in out and "flesch" in out
    assert "probability" not in out and "ai-generated" not in out.replace("not", "")


def test_theme_switch(win):
    win.settings.theme.setCurrentText("Dark")
    assert win.db.get_setting("theme") == "Dark"
    win.settings.theme.setCurrentText("Light")


def test_delete_research_data_keeps_normal_sessions(win, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    ctl = SessionController(win.db, FakeClock())
    sid = ctl.create("One sentence here.", 60_000, 1, SimConfig())
    win.lab.overview._example()
    win.lab.privacy._del_research()
    c = win.db.counts()
    assert c["experiments"] == 0 and c["runs"] == 0 and win.db.get_session(sid) is not None and c["sessions"] == 1


# ---------------------------------------------------------------- writing rhythm UI
def _pick(win, title):
    panel = win.new_page.rhythm
    panel.mode.setCurrentIndex(panel.mode.findText(title))
    return panel


def test_rhythm_modes_descriptions_and_editability(win):
    from wpl.engine.rhythm import PRESET_DESCRIPTIONS, preset
    panel = win.new_page.rhythm
    assert [panel.mode.itemText(i) for i in range(4)] == ["Regular", "Reflective", "Extended Breaks", "Custom"]
    for title, key in (("Regular", "regular"), ("Reflective", "reflective"), ("Extended Breaks", "extended")):
        _pick(win, title)
        assert panel.desc.text() == PRESET_DESCRIPTIONS[key] and panel.config() == preset(key)
        assert not panel.break_max.isEnabled()                       # presets are shown, not editable
    assert panel.break_max.value() == 300.0                           # extended: breaks up to five minutes
    _pick(win, "Custom")
    assert panel.break_max.isEnabled() and panel.maximumWidth() > 0


def test_custom_rhythm_validation_and_limits(win):
    panel = _pick(win, "Custom")
    assert panel.break_max.maximum() == 300.0 and panel.short_prob.maximum() == 50 and panel.pause_max.maximum() == 60.0
    panel.break_min.setValue(120)
    panel.break_max.setValue(30)                                      # max below min
    assert panel.error.isVisibleTo(panel) and not panel.is_valid()
    win.new_page.set_text("A short text. With two sentences.")
    win.new_page._start()
    assert win.ctl is None and win.new_page.banner.isVisibleTo(win.new_page)
    panel.break_max.setValue(200)
    assert panel.is_valid() and not panel.error.isVisibleTo(panel)
    cfg = panel.config()
    assert (cfg.break_min_ms, cfg.break_max_ms, cfg.name) == (120_000, 200_000, "custom")


def test_reset_to_defaults(win):
    from wpl.engine.rhythm import preset
    panel = _pick(win, "Custom")
    panel.break_prob.setValue(90)
    panel.revisions.setValue(20)
    panel.reset()
    assert panel.mode_key() == "regular" and panel.config() == preset("regular")
    _pick(win, "Custom")
    assert panel.break_prob.value() == preset("custom").long_break_prob * 100   # custom values were reset too


def test_infeasible_schedule_offers_a_valid_alternative_and_start_then_works(win, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    page = win.new_page
    from wpl.lab.conditions import sample_text
    page.set_text(sample_text())     # ~11 sentences: ten forced 5-minute breaks cannot fit in 30 minutes
    page.chips["30 minutes"].click()
    panel = _pick(win, "Custom")
    panel.break_prob.setValue(100)
    panel.break_min.setValue(300)
    panel.break_max.setValue(300)
    seen = {}
    monkeypatch.setattr(QMessageBox, "exec", lambda self: seen.update(text=self.text()) or 0)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: next(b for b in self.buttons() if b.text().startswith("Use")))
    page._start()
    assert "cannot hold this schedule" in seen["text"] and "Option 1" in seen["text"] and "Option 2" in seen["text"]
    assert win.ctl is None                                           # nothing started on the conflicting settings
    assert page.chips["Custom"].isChecked() and page.duration_ms() > 30 * 60_000
    assert page.duration_ms() % 60_000 == 0
    page._start()                                                    # the suggested duration really fits
    assert win.ctl is not None and win.ctl.state == "running"
    win.active._due.stop()


def test_preview_schedule_shows_timeline_and_numbers(win, monkeypatch):
    from PySide6.QtWidgets import QDialog, QTableWidget

    from wpl.ui.charts import RhythmTimeline
    win.new_page.set_text(SAMPLE)
    _pick(win, "Reflective")
    grabbed = {}

    def fake_exec(self):
        tl = self.findChild(RhythmTimeline)
        grabbed["segs"] = tl.segs if tl else None
        grabbed["rows"] = [self.findChild(QTableWidget).item(r, 0).text() for r in range(self.findChild(QTableWidget).rowCount())]
        return 0
    monkeypatch.setattr(QDialog, "exec", fake_exec)
    win.new_page._preview_schedule()
    assert grabbed["segs"] and {k for _, _, k in grabbed["segs"]} <= {"writing", "short_pause", "break"}
    assert "Idle time (short pauses + breaks, simulated)" in grabbed["rows"] and "Longest break" in grabbed["rows"]


def test_active_page_shows_phase_idle_split_and_cancel_in_break(win, monkeypatch):
    from dataclasses import replace

    from PySide6.QtWidgets import QMessageBox

    from wpl.engine.rhythm import preset
    from wpl.lab.conditions import sample_text
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    clock = FakeClock()
    ctl = SessionController(win.db, clock)
    r = replace(preset("custom"), long_break_prob=1.0, break_min_ms=120_000, break_max_ms=120_000)
    sid = ctl.create(sample_text(), 3 * 3_600_000, 5, SimConfig(MistakeConfig.default()), rhythm=r)
    brk = next(e for e in ctl.session.events if e.kind == "pause" and e.meta.get("pause_class") == "break")
    win.ctl = ctl
    win.active.attach(ctl)
    assert win.active.timeline.segs                                   # schedule drawn before it starts
    ctl.start()
    clock.advance(brk.t_ms - 1000)
    win.active._on_due()
    win.active._due.stop()
    assert win.active.state_lb.text().startswith("Writing")
    clock.advance(31_000)
    win.active._on_due()
    win.active._due.stop()
    assert win.active.state_lb.text().startswith("Break (") and "Idle" in win.active.split_lb.text()
    assert "Break: next writing event" in win.active.waiting.text()
    win.active._pause()
    assert win.active.state_lb.text().startswith("Paused")
    win.active._resume()
    win.active._due.stop()
    win.active._cancel()                                              # cancel while the break is running
    assert ctl.state == "cancelled" and win.active.banner.text().startswith("Session cancelled")
    acts = win.db.get_user_actions(sid)
    assert [a["action"] for a in acts] == ["pause", "resume", "cancel"] and acts[-1]["phase"] == "Break"
    assert len(win.active.timeline.markers) == 3
    win.active._tick.stop()


def test_completed_state_and_deadline_banner(win):
    from dataclasses import replace

    from wpl.engine.rhythm import preset
    from wpl.lab.conditions import sample_text
    clock = FakeClock()
    ctl = SessionController(win.db, clock)
    ctl.create(sample_text(), 5 * 60_000, 2, SimConfig(MistakeConfig.default()), rhythm=replace(preset("regular"), pace_ms_per_char=400), grace_ms=60_000)
    win.active.attach(ctl)
    ctl.start()
    for _ in range(100):
        clock.advance(10_000)
        win.active._on_due()
        win.active._due.stop()
        if ctl.state == "completed":
            break
    assert win.active.state_lb.text().startswith("Completed")
    assert "Stopped by the deadline" in win.active.banner.text()
    win.active._tick.stop()


def test_rhythm_tab_compares_profiles_from_stored_data(win):
    from wpl.lab.conditions import sample_text
    from wpl.lab.runner import run_experiment
    from wpl.lab.templates import TEMPLATES
    t = next(x for x in TEMPLATES if x.id == "rhythm")
    run_experiment(win.db, "rhythm", t.build(sample_text(), 60 * 60_000), 3, 5)
    win.lab.refresh_all()
    tab = win.lab.rhythm
    assert tab.cmp.rowCount() == 3 and tab.cmp.item(0, 1).text() == "3"
    names = [tab.cmp.item(r, 0).text() for r in range(3)]
    assert names == ["A + Regular rhythm", "A + Reflective rhythm", "A + Extended Breaks rhythm"]
    assert "not statistically supported" in tab.cmp_note.text() and "SIMULATED" in tab.cmp_note.text()
    assert tab.timeline.segs and "Data source: simulated" in tab.sum.text()
    hdr = [win.lab.overview.table.horizontalHeaderItem(i).text() for i in range(win.lab.overview.table.columnCount())]
    assert "Breaks" in hdr and "Idle (sim.)" in hdr


def test_rhythm_tab_not_recorded_for_sessions_without_pause_data(win):
    from wpl.service import analyze_session
    clock = FakeClock()
    ctl = SessionController(win.db, clock)
    sid = ctl.create("Just one sentence here.", 60_000, 1, SimConfig())      # legacy path, no rhythm
    ctl.start()
    clock.advance(60_000)
    ctl.poll()
    analyze_session(win.db, sid)
    win.lab.refresh_all()
    tab = win.lab.rhythm
    assert "Idle (sim.): Not recorded" in tab.sum.text() or "Breaks: Not recorded" in tab.sum.text()


def test_rerun_restores_rhythm_settings(win):
    from dataclasses import replace

    from wpl.engine.rhythm import preset
    r = replace(preset("custom"), break_max_ms=200_000, break_min_ms=50_000, revision_per_1000=7.5, activity="bursty")
    ctl = SessionController(win.db, FakeClock())
    sid = ctl.create(SAMPLE, 3_600_000, 77, SimConfig(MistakeConfig.default()), rhythm=r)
    win._rerun(sid)
    panel = win.new_page.rhythm
    assert panel.mode_key() == "custom" and panel.config() == r and win.new_page.seed.value() == 77


def test_privacy_tab_separates_simulated_from_real_telemetry(win):
    txt = win.lab.privacy.findChildren(__import__("PySide6.QtWidgets", fromlist=["QTextBrowser"]).QTextBrowser)[0].toPlainText()
    assert "REAL application telemetry" in txt and "user_actions" in txt and "SIMULATED" in txt
