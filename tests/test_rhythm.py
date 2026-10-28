import json
import math
from dataclasses import replace

import pytest

from conftest import SAMPLE
from wpl.analysis.features import AnalysisInput, run_extractors
from wpl.engine.breaks import plan_rhythm, summarize
from wpl.engine.events import Event, replay
from wpl.engine.rhythm import MAX_BREAK_MS, PRESETS, RhythmConfig, RhythmError, preset
from wpl.engine.schedule import MAX_GRACE_MS, InfeasibleSchedule, apply_deadline, fit_active
from wpl.engine.session import FakeClock, Session
from wpl.engine.simulate import MistakeConfig, SimConfig
from wpl.lab.conditions import ConditionParams, sample_text
from wpl.lab.runner import run_experiment
from wpl.service import SessionController
from wpl.storage.db import Database

SIM = SimConfig(MistakeConfig.default())
HOUR = 3_600_000


def E(seq, kind, t, pos=None, rem="", ins="", dur=None, sentence=0, **meta):
    return Event(seq, kind, "simulated", t, pos, rem, ins, dur, {"sentence": sentence, **meta})


# ---------------------------------------------------------------- validation limits
@pytest.mark.parametrize("field,value", [
    ("pause_min_ms", 99), ("pause_min_ms", 60_001), ("pause_max_ms", 400), ("pause_max_ms", 60_001),
    ("short_pause_prob", -0.01), ("short_pause_prob", 0.51), ("long_break_prob", -0.1), ("long_break_prob", 1.01),
    ("break_min_ms", 999), ("break_min_ms", MAX_BREAK_MS + 1), ("break_max_ms", MAX_BREAK_MS + 1), ("break_max_ms", 5_000),
    ("revision_per_1000", -1), ("revision_per_1000", 50.1), ("activity", "chaotic"), ("pace_ms_per_char", 29), ("pace_ms_per_char", 2001),
])
def test_invalid_rhythm_parameters_rejected(field, value):
    with pytest.raises(RhythmError):
        replace(preset("custom"), **{field: value}).validate()


@pytest.mark.parametrize("field,value", [
    ("pause_min_ms", 100), ("pause_max_ms", 60_000), ("short_pause_prob", 0.5), ("long_break_prob", 1.0),
    ("break_min_ms", 1_000), ("break_max_ms", 300_000), ("revision_per_1000", 50), ("pace_ms_per_char", 30), ("pace_ms_per_char", 2000),
])
def test_boundary_values_accepted(field, value):
    replace(preset("custom"), **{field: value}).validate()


def test_presets_valid_and_round_trip():
    for name, cfg in PRESETS.items():
        assert cfg.name == name and RhythmConfig.from_dict(cfg.to_dict()) == cfg
    assert PRESETS["reflective"].break_max_ms == MAX_BREAK_MS == 300_000 and PRESETS["extended"].break_max_ms == 300_000


def test_unknown_keys_ignored_but_bad_values_rejected():
    assert RhythmConfig.from_dict({**preset("regular").to_dict(), "future_field": 1}).name == "regular"
    with pytest.raises(RhythmError):
        RhythmConfig.from_dict({**preset("regular").to_dict(), "break_max_ms": 10**9})


# ---------------------------------------------------------------- reproducibility
def test_same_seed_same_schedule_different_seed_differs():
    r = preset("reflective")
    a, b = plan_rhythm(SAMPLE * 2, 11, SIM, r, 2 * HOUR), plan_rhythm(SAMPLE * 2, 11, SIM, r, 2 * HOUR)
    c = plan_rhythm(SAMPLE * 2, 12, SIM, r, 2 * HOUR)
    assert a.events == b.events and a.summary == b.summary
    assert [(s.start_ms, s.kind) for s in a.summary.segments] != [(s.start_ms, s.kind) for s in c.summary.segments]


def test_plan_replays_to_final_text_for_all_presets_and_seeds():
    for name in ("regular", "reflective", "extended"):
        for seed in range(8):
            p = plan_rhythm(sample_text(), seed, SIM, preset(name), 2 * HOUR)
            rp = replay(p.events)
            assert rp.problems == [] and rp.text == p.final_text


# ---------------------------------------------------------------- zero breaks / pauses
def test_zero_break_session():
    r = replace(preset("custom"), long_break_prob=0.0)
    for seed in range(5):
        p = plan_rhythm(sample_text(), seed, SIM, r, HOUR)
        assert p.summary.breaks == 0 and p.summary.longest_break_ms is None and p.summary.avg_break_ms is None
        assert not [e for e in p.events if e.kind == "pause" and e.meta.get("pause_class") == "break"]


def test_no_pauses_at_all_means_all_time_is_active():
    r = replace(preset("custom"), long_break_prob=0.0, short_pause_prob=0.0)
    p = plan_rhythm(sample_text(), 1, SIM, r, HOUR)
    s = p.summary
    assert not [e for e in p.events if e.kind in ("pause", "resume")]
    assert s.idle_ms == 0 and s.active_ms == s.total_ms and [x.kind for x in s.segments] == ["writing"]


# ---------------------------------------------------------------- break length cap
def test_breaks_never_exceed_five_minutes():
    r = replace(preset("custom"), long_break_prob=1.0, break_min_ms=290_000, break_max_ms=MAX_BREAK_MS)
    for seed in range(6):
        p = plan_rhythm(sample_text(), seed, SIM, r, 3 * HOUR)
        brk = [e.duration_ms for e in p.events if e.kind == "pause" and e.meta.get("pause_class") == "break"]
        assert brk and all(290_000 <= d <= 300_000 for d in brk)
        assert p.summary.longest_break_ms <= 300_000


def test_exactly_five_minute_breaks():
    r = replace(preset("custom"), long_break_prob=1.0, break_min_ms=300_000, break_max_ms=300_000)
    p = plan_rhythm(sample_text(), 3, SIM, r, 3 * HOUR)
    assert {e.duration_ms for e in p.events if e.kind == "pause" and e.meta.get("pause_class") == "break"} == {300_000}
    assert p.summary.avg_break_ms == 300_000 == p.summary.longest_break_ms


def test_break_durations_are_not_rescaled_by_fitting():
    r = preset("reflective")
    for dur in (HOUR, 2 * HOUR):
        p = plan_rhythm(sample_text(), 5, SIM, r, dur)
        raw = plan_rhythm(sample_text(), 5, SIM, r, dur * 2)  # same draws, different budget
        assert sorted(e.duration_ms for e in p.events if e.kind == "pause") == sorted(e.duration_ms for e in raw.events if e.kind == "pause")


# ---------------------------------------------------------------- profile behavior
def totals(name, seeds=range(12)):
    sums = {"short": 0, "breaks": 0, "idle": 0}
    for s in seeds:
        sm = plan_rhythm(sample_text(), s, SIM, preset(name), 3 * HOUR).summary
        sums["short"] += sm.short_pauses
        sums["breaks"] += sm.breaks
        sums["idle"] += sm.idle_ms
    return sums


def test_profiles_differ_in_the_intended_direction():
    reg, ref, ext = totals("regular"), totals("reflective"), totals("extended")
    assert ref["short"] > reg["short"]          # reflective: more frequent pauses
    assert ref["breaks"] > reg["breaks"]        # and more breaks
    assert ext["breaks"] > reg["breaks"] and ext["idle"] > reg["idle"] * 3   # extended: much more idle time


def test_time_budget_includes_breaks():
    p = plan_rhythm(sample_text(), 4, SIM, preset("reflective"), HOUR)
    s = p.summary
    assert s.total_ms <= HOUR and s.total_ms == int(HOUR * 0.97) or abs(s.total_ms - int(HOUR * 0.97)) <= 1
    assert s.active_ms + s.idle_ms == s.total_ms and s.idle_ms == sum(e.duration_ms for e in p.events if e.kind == "pause")
    assert p.deadline.completion == "normal"


# ---------------------------------------------------------------- active / idle accounting (hand verified)
HAND = [
    E(0, "insert", 0, 0, ins="aaaa"),
    E(1, "pause", 1000, dur=5000, pause_class="break"),
    E(2, "resume", 6000, pause_class="break"),
    E(3, "insert", 7000, 4, ins="bb"),
    E(4, "pause", 8000, dur=500, pause_class="short"),
    E(5, "resume", 8500, pause_class="short"),
    E(6, "insert", 10000, 6, ins="cccccc"),
]


def test_summary_hand_verified():
    s = summarize(HAND)
    assert (s.total_ms, s.idle_ms, s.active_ms) == (10_000, 5_500, 4_500)
    assert (s.breaks, s.short_pauses, s.break_total_ms, s.avg_break_ms, s.longest_break_ms) == (1, 1, 5000, 5000, 5000)
    assert [(g.start_ms, g.end_ms, g.kind) for g in s.segments] == [
        (0, 1000, "writing"), (1000, 6000, "break"), (6000, 8000, "writing"), (8000, 8500, "short_pause"), (8500, 10000, "writing")]


def test_rhythm_extractor_hand_verified():
    evs = [HAND[0], E(1, "delete", 500, 0, rem="a")] + [replace(e, seq=e.seq + 1) for e in HAND[1:]]
    evs.insert(5, E(9, "replace", 7500, 4, rem="b", ins="x"))
    evs = [replace(e, seq=i) for i, e in enumerate(evs)]
    res = run_extractors(AnalysisInput("", "", evs, pauses_recorded=True), ["rhythm"])["rhythm"]["features"]
    f = {x["name"]: x for x in res}
    assert f["rhythm_data_sources"]["value"] == ["simulated"]
    assert (f["sim_total_ms"]["value"], f["sim_idle_ms"]["value"], f["sim_active_ms"]["value"]) == (10_000, 5_500, 4_500)
    assert (f["break_count"]["value"], f["short_pause_count"]["value"], f["avg_break_ms"]["value"], f["longest_break_ms"]["value"]) == (1, 1, 5000, 5000)
    pd = f["pause_duration_distribution"]["value"]
    assert pd["n"] == 2 and pd["min"] == 500 and pd["max"] == 5000 and pd["histogram"]["5000-9999"] == 1 and pd["histogram"]["0-999"] == 1
    ai = f["active_interval_distribution"]["value"]
    assert ai["n"] == 3 and ai["max"] == 2000 and math.isclose(ai["mean"], (1000 + 2000 + 1500) / 3)
    ra = f["revisions_around_breaks"]["value"]
    assert ra["breaks"] == 1 and ra["revisions_before"] == 1 and ra["revisions_after"] == 1
    assert math.isclose(ra["per_active_minute_before"], 60.0) and math.isclose(ra["per_active_minute_after"], 30.0)
    per = f["text_added_per_active_interval"]["value"]["per_interval"]
    assert per == [4, 3, 6]   # chars inserted in [0,1000): 'aaaa'; [6000,8000): 'bb'+'x'; [8500,10000]: 'cccccc'


def test_rhythm_metrics_missing_not_zero_when_pauses_not_recorded():
    evs = [E(0, "insert", 0, 0, ins="ab"), E(1, "insert", 500, 2, ins="cd")]
    f = {x["name"]: x for x in run_extractors(AnalysisInput("", "abcd", evs), ["rhythm"])["rhythm"]["features"]}
    assert f["break_count"]["status"] == "missing" and f["sim_idle_ms"]["value"] is None
    f = {x["name"]: x for x in run_extractors(AnalysisInput("", "abcd", evs, pauses_recorded=True), ["rhythm"])["rhythm"]["features"]}
    assert f["break_count"]["value"] == 0 and f["sim_idle_ms"]["value"] == 0
    assert f["avg_break_ms"]["status"] == "missing" and f["revisions_around_breaks"]["status"] == "missing"
    f = {x["name"]: x for x in run_extractors(AnalysisInput("", "", [E(0, "insert", None, 0, ins="a")]), ["rhythm"])["rhythm"]["features"]}
    assert f["sim_total_ms"]["status"] == "missing"


def test_extractor_trailing_pause_without_resume_is_excluded_and_cancel_truncates_it():
    evs = [E(0, "insert", 0, 0, ins="ab"), E(1, "pause", 1000, dur=60_000, pause_class="break")]
    f = {x["name"]: x for x in run_extractors(AnalysisInput("", "ab", evs, pauses_recorded=True), ["rhythm"])["rhythm"]["features"]}
    assert f["incomplete_breaks"]["value"] == 1 and f["break_count"]["value"] == 0   # unknown length: excluded, not guessed
    cancel = [{"action": "cancel", "active_ms": 21_000, "phase": "Break"}]
    f = {x["name"]: x for x in run_extractors(AnalysisInput("", "ab", evs, user_actions=cancel, pauses_recorded=True), ["rhythm"])["rhythm"]["features"]}
    assert f["incomplete_breaks"]["value"] == 1 and f["break_count"]["value"] == 1
    assert f["longest_break_ms"]["value"] == 20_000 and f["sim_idle_ms"]["value"] == 20_000 and f["sim_total_ms"]["value"] == 21_000


# ---------------------------------------------------------------- deadlines
def test_fixed_pace_session_stops_by_deadline_and_never_runs_unbounded():
    r = replace(preset("extended"), pace_ms_per_char=400)
    p = plan_rhythm(sample_text(), 2, SIM, r, 5 * 60_000, grace_ms=60_000)
    assert p.deadline.completion == "deadline_stop"
    assert max(e.t_ms for e in p.events) <= 5 * 60_000 + 60_000
    assert len(p.final_text) < len(sample_text())
    # even a huge requested grace is capped
    q = plan_rhythm(sample_text(), 2, SIM, r, 5 * 60_000, grace_ms=10**9)
    assert max(e.t_ms for e in q.events) <= 5 * 60_000 + MAX_GRACE_MS


def test_zero_grace_stops_at_previous_sentence_boundary():
    r = replace(preset("regular"), pace_ms_per_char=400)
    p = plan_rhythm(sample_text(), 2, SIM, r, 4 * 60_000, grace_ms=0)
    assert p.deadline.deadline_priority and max(e.t_ms for e in p.events) <= 4 * 60_000
    assert p.final_text.rstrip().endswith((".", "!", "?"))  # stopped at a sentence boundary


def test_fit_mode_completes_normally():
    p = plan_rhythm(sample_text(), 2, SIM, preset("regular"), HOUR)
    assert p.deadline.completion == "normal" and len(p.final_text) > 0.9 * len(sample_text())


def test_break_spanning_deadline_leaves_no_dangling_pause():
    evs = [E(0, "insert", 100, 0, ins="a", sentence=0), E(1, "pause", 900, dur=500, sentence=0, pause_class="break"),
           E(2, "resume", 1400, sentence=0), E(3, "insert", 1500, 1, ins="b", sentence=0)]
    out, o = apply_deadline(evs, 1000, 100)
    assert [e.kind for e in out] == ["insert"] and o.deadline_priority
    evs = [E(0, "insert", 100, 0, ins="a", sentence=0), E(1, "pause", 900, dur=500, sentence=1, pause_class="break"),
           E(2, "resume", 1400, sentence=1), E(3, "insert", 1500, 1, ins="b", sentence=1)]
    out, o = apply_deadline(evs, 1000, 100)
    assert [e.kind for e in out] == ["insert"] and o.deadline_priority and o.completion == "deadline_stop"


def test_completion_labels():
    from wpl.engine.schedule import DeadlineOutcome as D
    assert D(False, 0, None, 0).completion == "normal"
    assert D(False, 5, 1, 0).completion == "grace"
    assert D(False, 5, 1, 3).completion == "deadline_stop" and D(True, 0, 0, 3).completion == "deadline_stop"


# ---------------------------------------------------------------- infeasible schedules
def forced_breaks(n_min=300_000):
    return replace(preset("custom"), long_break_prob=1.0, break_min_ms=n_min, break_max_ms=MAX_BREAK_MS)


def test_infeasible_schedule_explained_with_valid_alternatives():
    r = forced_breaks()
    with pytest.raises(InfeasibleSchedule) as ei:
        plan_rhythm(sample_text(), 1, SIM, r, 30 * 60_000)
    e = ei.value
    msg = str(e)
    assert "cannot hold this schedule" in msg and "Option 1" in msg and "Option 2" in msg
    assert e.needed_duration_ms > 30 * 60_000 and e.needed_duration_ms % 60_000 == 0
    ok = plan_rhythm(sample_text(), 1, SIM, r, e.needed_duration_ms)          # option 1 really works
    assert ok.summary.total_ms <= e.needed_duration_ms
    assert e.max_idle_ms < e.idle_ms
    fewer = plan_rhythm(sample_text(), 1, SIM, replace(r, long_break_prob=0.0), 30 * 60_000)  # option 2 direction works
    assert fewer.summary.breaks == 0


def test_infeasible_message_when_even_24h_is_not_enough():
    msg = str(InfeasibleSchedule(24 * HOUR, 30 * HOUR, 1000, 0.97))
    assert "unavailable" in msg


def test_fit_active_rejects_when_text_cannot_be_written():
    evs = [E(0, "insert", 0, 0, ins="a" * 100), E(1, "pause", 1000, dur=900_000, pause_class="break"), E(2, "resume", 901_000)]
    with pytest.raises(InfeasibleSchedule):
        fit_active(evs, 900_000, 100)
    assert fit_active(evs, 1_000_000, 100)


def test_validation_error_for_empty_text():
    with pytest.raises(ValueError):
        plan_rhythm("  ", 1, SIM, preset("regular"), HOUR)


# ---------------------------------------------------------------- live session: phases, idle time, pause/resume, cancel
def live_events():
    return [E(0, "insert", 0, 0, ins="aaaa"), E(1, "pause", 1000, dur=5000, pause_class="break"), E(2, "resume", 6000),
            E(3, "insert", 7000, 4, ins="bb"), E(4, "pause", 8000, dur=500, pause_class="short"), E(5, "resume", 8500),
            E(6, "insert", 10000, 6, ins="cc")]


def test_session_phases_and_idle_time():
    c = FakeClock()
    s = Session(live_events(), 12_000, c)
    assert s.phase() == "Ready"
    s.start()
    assert s.phase() == "Writing" and s.idle_ms() == 0
    c.advance(1500)
    assert [e.seq for e in s.poll()] == [0, 1]
    assert s.phase_detail()[0] == "Break" and "left" in s.phase_detail()[1]
    assert s.idle_ms() == 500 and s.writing_ms() == 1000
    c.advance(2000)
    assert s.poll() == [] and s.phase() == "Break"            # nothing due during the break: no busy work
    assert s.next_due_in_ms() == 2500                         # single wake-up at the break's end
    c.advance(2500)
    assert [e.seq for e in s.poll()] == [2] and s.phase() == "Writing"
    c.advance(4500)                                           # now = 10000 total: inside short pause end?
    s.poll()
    assert s.idle_ms() == 5500 and math.isclose(s.writing_ms(), s.active_ms() - 5500)
    c.advance(5000)
    s.poll()
    assert s.phase() == "Completed"


def test_short_pause_is_writing_phase_with_detail():
    c = FakeClock()
    s = Session(live_events(), 12_000, c)
    s.start()
    c.advance(8200)
    assert s.phase_detail() == ("Writing", "short simulated pause")


def test_manual_pause_during_break_stops_clock_and_resumes_remaining_break():
    c = FakeClock()
    s = Session(live_events(), 12_000, c)
    s.start()
    c.advance(3000)
    s.poll()
    assert s.phase() == "Break"
    s.pause()
    assert s.phase() == "Paused"
    c.advance(10 * 60_000)                                    # a long real-world interruption
    assert s.poll() == [] and s.active_ms() == 3000 and s.idle_ms() == 2000
    s.resume()
    assert s.phase() == "Break" and s.next_due_in_ms() == 3000   # break still has 3 s of simulated time left
    c.advance(3000)
    assert [e.seq for e in s.poll()] == [2]


def test_cancel_during_break_records_real_user_action(db):
    r = forced_breaks(60_000)
    clock = FakeClock()
    ctl = SessionController(db, clock)
    sid = ctl.create(sample_text(), 3 * HOUR, 9, SIM, rhythm=r)
    first_break = next(e for e in ctl.session.events if e.kind == "pause" and e.meta.get("pause_class") == "break")
    ctl.start()
    clock.advance(first_break.t_ms + first_break.duration_ms // 2)
    ctl.poll()
    assert ctl.session.phase() == "Break"
    ctl.cancel()
    row = db.get_session(sid)
    assert row["status"] == "cancelled" and row["final_text"] == ctl.text
    acts = db.get_user_actions(sid)
    assert acts[-1]["action"] == "cancel" and acts[-1]["phase"] == "Break"
    from wpl.service import analyze_session
    f = analyze_session(db, sid)
    assert f["rhythm.incomplete_breaks"]["value"] == 1
    assert f["rhythm.sim_idle_ms"]["status"] == "ok"
    assert "captured" not in {e.source for e in db.get_events(sid)}      # user actions are not mixed into simulated events


def test_user_pause_and_resume_logged_as_real_telemetry(db):
    clock = FakeClock()
    ctl = SessionController(db, clock)
    sid = ctl.create(sample_text(), HOUR, 3, SIM, rhythm=preset("regular"))
    ctl.start()
    clock.advance(60_000)
    ctl.poll()
    ctl.pause()
    clock.advance(500_000)
    ctl.resume()
    acts = db.get_user_actions(sid)
    assert [a["action"] for a in acts] == ["pause", "resume"] and acts[0]["active_ms"] == 60_000 == acts[1]["active_ms"]
    assert acts[0]["phase"] in ("Writing", "Break")


# ---------------------------------------------------------------- saving and restoring
def drain(ctl, clock, step=30_000):
    while ctl.state == "running":
        clock.advance(step)
        ctl.poll()


def test_save_and_restore_session_with_rhythm_mid_break(tmp_path):
    path = tmp_path / "r.db"
    r = preset("reflective")
    clock = FakeClock()
    d = Database(path)
    ctl = SessionController(d, clock)
    sid = ctl.create(sample_text(), 2 * HOUR, 21, SIM, rhythm=r)
    ref_events = list(ctl.session.events)
    brk = next(e for e in ref_events if e.kind == "pause" and e.meta.get("pause_class") == "break")
    ctl.start()
    clock.advance(brk.t_ms + 1000)
    ctl.poll()
    assert ctl.session.phase() == "Break"
    played = ctl.session.cursor
    d.close()                                                  # crash during a break
    d2 = Database(path)
    assert d2.mark_interrupted() == 1
    assert json.loads(d2.get_session(sid)["config_json"])["rhythm"] == r.to_dict()
    clock2 = FakeClock()
    ctl2 = SessionController.recover(d2, sid, clock2)
    assert ctl2.state == "paused" and ctl2.session.cursor == played and ctl2.session.events == ref_events
    ctl2.resume()
    assert ctl2.session.phase() == "Break"                     # restored inside the break
    drain(ctl2, clock2)
    assert ctl2.state == "completed" and d2.get_session(sid)["status"] == "completed"
    assert replay(d2.get_events(sid)).text == replay(ref_events).text
    dl = json.loads(d2.get_session(sid)["deadline_json"])
    assert dl["completion"] == "normal"


def test_legacy_session_without_rhythm_still_recovers(db):
    clock = FakeClock()
    ctl = SessionController(db, clock)
    sid = ctl.create(SAMPLE, HOUR, 7, SIM)
    ctl.start()
    clock.advance(1_200_000)
    ctl.poll()
    assert "rhythm" not in json.loads(db.get_session(sid)["config_json"])
    db.conn.execute("UPDATE sessions SET engine_version='sim-1.0.0' WHERE id=?", (sid,))   # created by the previous release
    db.conn.commit()
    assert SessionController.recover(db, sid, FakeClock()).session.cursor == ctl.session.cursor


def test_rhythm_session_from_older_engine_is_refused(db):
    ctl = SessionController(db, FakeClock())
    sid = ctl.create(sample_text(), HOUR, 7, SIM, rhythm=preset("regular"))
    db.conn.execute("UPDATE sessions SET engine_version='sim-1.0.0' WHERE id=?", (sid,))
    db.conn.commit()
    from wpl.storage.db import StorageError
    with pytest.raises(StorageError):
        SessionController.recover(db, sid)


def test_deadline_completion_saved_with_session(db):
    clock = FakeClock()
    ctl = SessionController(db, clock)
    sid = ctl.create(sample_text(), 5 * 60_000, 2, SIM, rhythm=replace(preset("regular"), pace_ms_per_char=400), grace_ms=60_000)
    ctl.start()
    drain(ctl, clock, 10_000)
    assert json.loads(db.get_session(sid)["deadline_json"])["completion"] == "deadline_stop"


def test_migration_3_preserves_existing_data(tmp_path):
    import sqlite3

    from wpl.storage.db import _migrations
    path = tmp_path / "v2.db"
    con = sqlite3.connect(path)
    for v, sql in _migrations()[:2]:
        con.executescript(sql + f"\nPRAGMA user_version={v};")
    con.execute("INSERT INTO sessions(id,created_at,status,engine_version,app_version,source_text) VALUES('s1','t','completed','sim-1.0.0','0.2.0','hello')")
    con.commit()
    con.close()
    d = Database(path)
    assert d.version() == 3 and d.get_session("s1")["source_text"] == "hello" and d.get_user_actions("s1") == []
    d.add_user_action("s1", 5, "pause", "Writing")
    d.delete_session("s1")
    assert d.counts()["user_actions"] == 0


# ---------------------------------------------------------------- experiments comparing profiles
def test_rhythm_profile_experiment_compares_conditions(db):
    from wpl.lab.overview import condition_means, ensure_features, experiment_rows
    from wpl.lab.templates import TEMPLATES
    t = next(x for x in TEMPLATES if x.id == "rhythm")
    conds = t.build(sample_text(), 30 * 60_000)
    assert all(c.duration_ms >= HOUR for c in conds)
    eid = run_experiment(db, "rhythm", conds, 6, 99)
    rows = experiment_rows(db, eid)
    assert len(rows) == 18 and {r["condition"] for r in rows} == {"A + Regular rhythm", "A + Reflective rhythm", "A + Extended Breaks rhythm"}
    cats, idle, _ = condition_means(db, eid, "rhythm.sim_idle_ms")
    d = dict(zip(cats, idle, strict=True))
    assert d["A + Regular rhythm"] < d["A + Reflective rhythm"] and d["A + Regular rhythm"] < d["A + Extended Breaks rhythm"]
    f = ensure_features(db, rows[0]["session_id"])
    assert f["rhythm.rhythm_data_sources"]["value"] == ["simulated"]
    assert all(r["origin"] == "simulated" for r in rows)


def test_infeasible_experiment_is_atomic(db):
    p = ConditionParams(code="A", text=sample_text(), duration_ms=10 * 60_000, rhythm=forced_breaks().to_dict())
    with pytest.raises(InfeasibleSchedule):
        run_experiment(db, "x", [p], 2, 1)
    assert db.counts()["experiments"] == 0 and db.counts()["sessions"] == 0


def test_conditions_without_rhythm_are_byte_identical_to_before():
    import hashlib

    from wpl.lab.conditions import generate

    def h(ev):
        return hashlib.sha256(json.dumps([(e.kind, e.t_ms, e.pos, e.removed, e.inserted, e.duration_ms, e.meta) for e in ev], sort_keys=True).encode()).hexdigest()[:16]
    gold = {"A": "60f95edb1d146046", "B": "595341b2bdd527a1", "C": "de481876b5c59323", "D": "a75af4a9da28a670", "E": "4db6aeeb0495d643"}
    for c, g in gold.items():
        assert h(generate(ConditionParams(code=c, text=sample_text()), 123).events) == g, c


def test_legacy_plan_is_byte_identical_to_before():
    import hashlib

    from wpl.service import build_plan
    ev, _ = build_plan(SAMPLE, 5, SIM, 3_600_000, checkpoint_ms=300000)
    got = hashlib.sha256(json.dumps([(e.kind, e.t_ms, e.pos, e.removed, e.inserted, e.duration_ms, e.meta) for e in ev], sort_keys=True).encode()).hexdigest()[:16]
    assert got == "babb1e79eebcd6e2"


def test_user_pause_inside_break_is_labelled_break_and_ui_pauses_are_not_logged(db):
    r = forced_breaks(60_000)
    clock = FakeClock()
    ctl = SessionController(db, clock)
    sid = ctl.create(sample_text(), 3 * HOUR, 9, SIM, rhythm=r)
    brk = next(e for e in ctl.session.events if e.kind == "pause" and e.meta.get("pause_class") == "break")
    ctl.start()
    clock.advance(brk.t_ms + 5_000)
    ctl.poll()
    ctl.pause(record=False)                      # e.g. held while a confirmation dialog is open
    ctl.resume(record=False)
    assert db.get_user_actions(sid) == []
    ctl.pause()
    assert ctl.session.phase() == "Paused" and ctl.session.clock_phase() == "Break"
    assert db.get_user_actions(sid)[0]["phase"] == "Break"
    ctl.resume()
    assert [a["action"] for a in db.get_user_actions(sid)] == ["pause", "resume"]


def test_json_export_carries_user_actions_labelled_as_captured(db):
    from wpl.io import exporters as X
    clock = FakeClock()
    ctl = SessionController(db, clock)
    sid = ctl.create(sample_text(), HOUR, 3, SIM, rhythm=preset("regular"))
    ctl.start()
    clock.advance(120_000)
    ctl.poll()
    ctl.pause()
    ctl.resume()
    row = db.get_session(sid)
    doc = json.loads(X.dataset_json("n", [X.session_dict(row, db.get_events(sid), None, db.get_user_actions(sid))]))
    s = doc["sessions"][0]
    assert [a["action"] for a in s["user_actions"]] == ["pause", "resume"] and {a["source"] for a in s["user_actions"]} == {"captured"}
    assert {e["source"] for e in s["events"]} == {"simulated"} and s["config"]["rhythm"]["name"] == "regular"
