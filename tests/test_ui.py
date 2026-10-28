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
