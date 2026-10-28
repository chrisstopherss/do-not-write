import sqlite3

import pytest

from conftest import SAMPLE
from wpl.engine.events import replay
from wpl.engine.session import FakeClock
from wpl.engine.simulate import MistakeConfig, SimConfig
from wpl.service import SessionController, build_plan
from wpl.storage.db import (
    LATEST_VERSION,
    Database,
    DatabaseCorrupt,
    StorageError,
    _migrations,
    quarantine_corrupt,
)


def test_migration_v1_to_latest_preserves_data(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript(_migrations()[0][1] + "\nPRAGMA user_version=1;")
    con.execute("INSERT INTO sessions(id,created_at,status,engine_version,app_version,source_text) VALUES('s1','t','completed','e','a','hello')")
    con.commit()
    con.close()
    d = Database(path)
    assert d.version() == LATEST_VERSION >= 2
    row = d.get_session("s1")
    assert row["source_text"] == "hello" and row["data_origin"] == "simulated" and row["dataset_id"] is None
    assert d.counts()["experiments"] == 0


def test_migrations_idempotent(tmp_path):
    Database(tmp_path / "a.db").close()
    d = Database(tmp_path / "a.db")
    assert d.version() == LATEST_VERSION


def test_corrupt_database_detected_and_quarantined(tmp_path):
    p = tmp_path / "bad.db"
    p.write_bytes(b"this is not a sqlite database" * 100)
    with pytest.raises(DatabaseCorrupt):
        Database(p)
    moved = quarantine_corrupt(p)
    assert moved.exists() and not p.exists()
    Database(p).close()


def test_unopenable_path_is_storage_error_not_corruption(tmp_path):
    with pytest.raises(StorageError) as ei:
        Database(tmp_path / "no" / "such" / "dir" / "x.db")
    assert not isinstance(ei.value, DatabaseCorrupt)


def test_source_text_is_immutable(db):
    sid = db.create_session(source_text="orig", seed=1, duration_ms=1000, config={})
    with pytest.raises(StorageError):
        db.update_session(sid, source_text="changed")
    assert db.get_session(sid)["source_text"] == "orig"


def test_sql_injection_attempt_is_inert(db):
    evil = "x'); DROP TABLE sessions; --"
    sid = db.create_session(source_text=evil, seed=1, duration_ms=1000, config={}, label=evil)
    assert db.get_session(sid)["label"] == evil and db.counts()["sessions"] == 1


def test_events_roundtrip_preserve_none_time(db):
    from wpl.engine.events import Event
    sid = db.create_session(source_text="", seed=None, duration_ms=None, config={})
    db.add_events(sid, [Event(0, "insert", "imported", None, 0, "", "é😀"), Event(1, "pause", "simulated", 5, duration_ms=9)])
    got = db.get_events(sid)
    assert got[0].t_ms is None and got[0].inserted == "é😀" and got[1].duration_ms == 9


def test_cascade_delete_and_counts(db):
    sid = db.create_session(source_text="a", seed=1, duration_ms=1000, config={})
    from wpl.engine.events import Event
    db.add_events(sid, [Event(0, "insert", "simulated", 0, 0, "", "a")])
    db.delete_session(sid)
    assert db.counts()["events"] == 0


def run_to_end(c, clock, step=5000):
    while c.state == "running":
        clock.advance(step)
        c.poll()


def test_full_session_completes_and_persists(db):
    clock = FakeClock()
    c = SessionController(db, clock)
    sim = SimConfig(MistakeConfig.default())
    sid = c.create(SAMPLE, 30 * 60_000, 99, sim, checkpoint_ms=300_000)
    c.start()
    run_to_end(c, clock)
    row = db.get_session(sid)
    assert row["status"] == "completed" and row["final_text"] == c.text
    stored = db.get_events(sid)
    assert replay(stored).text == row["final_text"] and row["source_text"] == SAMPLE
    assert any(e.kind == "checkpoint" for e in stored)
    assert all(e.source == "simulated" for e in stored)


def test_session_finishes_within_duration():
    ev, outcome = build_plan(SAMPLE, 5, SimConfig(MistakeConfig.default()), 3_600_000)
    assert max(e.t_ms for e in ev) <= 3_600_000 and not outcome.deadline_priority


@pytest.mark.parametrize("dur", [1000, 30 * 60_000, 60 * 60_000, 120 * 60_000, 24 * 3600 * 1000])
def test_all_duration_boundaries(dur):
    ev, _ = build_plan(SAMPLE, 5, SimConfig(MistakeConfig.default()), dur)
    assert max(e.t_ms for e in ev) <= dur


def test_recovery_after_unexpected_exit(tmp_path):
    path = tmp_path / "s.db"
    clock = FakeClock()
    d = Database(path)
    c = SessionController(d, clock)
    sid = c.create(SAMPLE, 3_600_000, 7, SimConfig(MistakeConfig.default()))
    c.start()
    clock.advance(1_200_000)
    c.poll()
    partial = c.text
    n_played = c.session.cursor
    assert 0 < n_played < len(c.session.events)
    d.close()  # simulated crash: no cancel/pause/complete
    d2 = Database(path)
    assert d2.mark_interrupted() == 1 and d2.get_session(sid)["status"] == "interrupted"
    clock2 = FakeClock()
    c2 = SessionController.recover(d2, sid, clock2)
    assert c2.text == partial and c2.state == "paused" and c2.session.cursor == n_played
    c2.resume()
    run_to_end(c2, clock2, 60_000)
    ref, _ = build_plan(SAMPLE, 7, SimConfig(MistakeConfig.default()), 3_600_000)
    assert c2.text == replay(ref).text
    assert d2.get_session(sid)["status"] == "completed"
    assert replay(d2.get_events(sid)).text == c2.text


def test_recovery_refuses_mismatched_events(db):
    clock = FakeClock()
    c = SessionController(db, clock)
    sid = c.create(SAMPLE, 3_600_000, 7, SimConfig(MistakeConfig.default()))
    c.start()
    clock.advance(1_000_000)
    c.poll()
    db.conn.execute("UPDATE events SET inserted='tampered' WHERE session_id=? AND seq=0", (sid,))
    db.conn.commit()
    with pytest.raises(StorageError):
        SessionController.recover(db, sid)


def test_cancel_keeps_partial_text(db):
    clock = FakeClock()
    c = SessionController(db, clock)
    sid = c.create(SAMPLE, 3_600_000, 7, SimConfig(MistakeConfig.default()))
    c.start()
    clock.advance(900_000)
    c.poll()
    c.cancel()
    r = db.get_session(sid)
    assert r["status"] == "cancelled" and r["final_text"] == c.text and r["source_text"] == SAMPLE


def test_empty_text_rejected(db):
    with pytest.raises(ValueError):
        SessionController(db).create("   ", 60_000, 1, SimConfig())


def test_one_character_text_session(db):
    clock = FakeClock()
    c = SessionController(db, clock)
    c.create("x", 60_000, 1, SimConfig())
    c.start()
    run_to_end(c, clock, 60_000)
    assert c.text == "x" and c.state == "completed"


def test_document_text_never_written_to_log(tmp_path, db):
    import logging

    from wpl.app import setup_logging
    log_path = setup_logging(tmp_path)
    secret = "Zanzibar-ultra-secret-phrase unlikely-to-appear-elsewhere."
    clock = FakeClock()
    c = SessionController(db, clock)
    c.create(secret, 60_000, 3, SimConfig(MistakeConfig.default()))
    c.start()
    run_to_end(c, clock, 60_000)
    c.session = None
    for h in logging.getLogger("wpl").handlers:
        h.flush()
    content = log_path.read_text(encoding="utf-8")
    assert "session created" in content and "completed" in content
    assert "Zanzibar" not in content and "unlikely" not in content


def test_selftest_result_file_and_flag_parsing(tmp_path):
    import pytest

    from wpl.app import _selftest_args, main
    assert _selftest_args(["x"]) == (False, None) and _selftest_args(["x", "--selftest"]) == (True, None)
    assert _selftest_args(["--selftest", "--selftest-out", "r.txt"]) == (True, "r.txt")
    for bad in (["--selftest", "--selftest-out"], ["--selftest", "--selftest-out", "--other"]):
        with pytest.raises(SystemExit):
            _selftest_args(bad)
    out = tmp_path / "r.txt"
    assert main(["--selftest", "--selftest-out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert text.count("PASS ") >= 6 and text.strip().endswith("all checks passed") and "FAIL" not in text
    # an unwritable result path must not change the verdict
    assert main(["--selftest", "--selftest-out", str(tmp_path / "no" / "dir" / "r.txt")]) == 0


def test_selftest_crash_keeps_earlier_lines(tmp_path, monkeypatch):
    import wpl.io.exporters as X
    from wpl.app import main
    monkeypatch.setattr(X, "events_csv", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = tmp_path / "r.txt"
    assert main(["--selftest", "--selftest-out", str(out)]) == 1
    text = out.read_text(encoding="utf-8")
    assert "PASS database created" in text and "FAIL selftest crashed: RuntimeError: boom" in text


def test_main_with_empty_argv_does_not_fall_back_to_sys_argv(monkeypatch):
    import sys

    from wpl.app import _selftest_args
    monkeypatch.setattr(sys, "argv", ["prog", "--selftest"])
    assert _selftest_args([]) == (False, None)
