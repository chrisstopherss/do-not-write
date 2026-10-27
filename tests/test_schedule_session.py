import pytest

from wpl.engine.events import Event
from wpl.engine.schedule import (MAX_GRACE_MS, ScheduleError, add_checkpoints, apply_deadline, fit_to_duration,
                                 validate_duration)
from wpl.engine.session import CANCELLED, COMPLETED, PAUSED, RUNNING, FakeClock, Session, SessionStateError


def ev(seq, t, sentence=0, kind="insert"):
    return Event(seq, kind, "simulated", t, seq if kind == "insert" else None, "", "x" if kind == "insert" else "",
                 None, {"sentence": sentence})


@pytest.mark.parametrize("bad", [0, -1, 999, 24 * 3600 * 1000 + 1, 1.5, True])
def test_duration_rejected(bad):
    with pytest.raises(ScheduleError):
        validate_duration(bad)


@pytest.mark.parametrize("ok", [1000, 30 * 60_000, 3_600_000, 7_200_000, 24 * 3600 * 1000])
def test_duration_accepted(ok):
    assert validate_duration(ok) == ok


def test_fit_scales_to_fill_and_stays_monotonic():
    out = fit_to_duration([ev(0, 0), ev(1, 100), ev(2, 400)], 1000, fill=0.5)
    assert [e.t_ms for e in out] == [0, 125, 500]


def test_fit_single_zero_time_event_unchanged():
    assert fit_to_duration([ev(0, 0)], 5000)[0].t_ms == 0


def test_deadline_nothing_late():
    evs = [ev(0, 100), ev(1, 900)]
    out, o = apply_deadline(evs, 1000)
    assert out == evs and not o.deadline_priority and o.grace_used_ms == 0


def test_deadline_finishes_sentence_within_grace():
    evs = [ev(0, 100, 0), ev(1, 200, 0), ev(2, 900, 1), ev(3, 1100, 1), ev(4, 1200, 2)]
    out, o = apply_deadline(evs, 1000, 200)
    assert [e.t_ms for e in out] == [100, 200, 900, 1100]
    assert (o.deadline_priority, o.grace_used_ms, o.last_sentence_kept, o.dropped_events) == (False, 100, 1, 1)


def test_deadline_grace_exceeded_stops_at_previous_sentence_boundary():
    evs = [ev(0, 100, 0), ev(1, 200, 0), ev(2, 900, 1), ev(3, 1100, 1), ev(4, 1200, 2)]
    out, o = apply_deadline(evs, 1000, 50)
    assert [e.t_ms for e in out] == [100, 200]
    assert o.deadline_priority and o.last_sentence_kept == 0 and o.dropped_events == 3


def test_deadline_first_sentence_overruns_cut_at_deadline():
    out, o = apply_deadline([ev(0, 500, 0), ev(1, 1500, 0)], 1000, 100)
    assert [e.t_ms for e in out] == [500] and o.deadline_priority


def test_grace_is_capped():
    d = 1_000_000
    out, o = apply_deadline([ev(0, 10, 0), ev(1, d + MAX_GRACE_MS + 60_000, 0)], d, 10**9)
    assert o.deadline_priority


def test_checkpoints_inserted_at_interval_and_renumbered():
    evs = [ev(0, 0), ev(1, 1200), ev(2, 2500)]
    out = add_checkpoints(evs, 1000)
    assert [e.kind for e in out] == ["insert", "checkpoint", "insert", "checkpoint", "insert"]
    assert [e.seq for e in out] == [0, 1, 2, 3, 4]
    assert [e.t_ms for e in out if e.kind == "checkpoint"] == [1000, 2000]


def make_session(clock):
    return Session([ev(0, 1000), ev(1, 2000), ev(2, 3000)], 5000, clock)


def test_session_pause_resume_uses_active_time():
    c = FakeClock()
    s = make_session(c)
    s.start()
    c.advance(1500)
    assert [e.seq for e in s.poll()] == [0]
    s.pause()
    c.advance(100_000)
    assert s.poll() == [] and s.active_ms() == 1500 and s.state == PAUSED
    s.resume()
    c.advance(600)
    assert [e.seq for e in s.poll()] == [1]
    assert s.next_due_in_ms() == 900
    c.advance(900)
    assert [e.seq for e in s.poll()] == [2] and s.state == COMPLETED


def test_session_late_poll_releases_all_due_without_drift():
    c = FakeClock()
    s = make_session(c)
    s.start()
    c.advance(10_000)
    assert len(s.poll()) == 3 and s.state == COMPLETED


def test_session_max_batch():
    c = FakeClock()
    s = Session([ev(i, 0) for i in range(10)], 1000, c, max_batch=4)
    s.start()
    assert len(s.poll()) == 4 and s.state == RUNNING


def test_session_illegal_transitions():
    c = FakeClock()
    s = make_session(c)
    with pytest.raises(SessionStateError):
        s.pause()
    s.start()
    with pytest.raises(SessionStateError):
        s.start()
    with pytest.raises(SessionStateError):
        s.resume()
    s.cancel()
    assert s.state == CANCELLED
    with pytest.raises(SessionStateError):
        s.cancel()
    assert s.poll() == []
