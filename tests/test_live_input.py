import pytest

from wpl.engine.events import Event
from wpl.engine.session import CANCELLED, COMPLETED, RUNNING, FakeClock, Session
from wpl.live_input import (
    FocusChangedError,
    InputAction,
    LiveInputError,
    WindowTarget,
    plan_event_input,
    validate_live_duration,
    validate_target_state,
)


def test_live_input_plan_preserves_unicode_punctuation_and_line_breaks():
    event = Event(0, "insert", "simulated", 0, 0, inserted="Café 😀,\n日本語!")
    actions, result = plan_event_input("", event)
    assert actions == [InputAction("text", "Café 😀,\n日本語!")]
    assert result == "Café 😀,\n日本語!"


def test_live_input_plan_revises_text_without_changing_event_replay():
    source = "A typo."
    correction = Event(1, "replace", "simulated", 100, 3, "ypo", "ype")
    actions, result = plan_event_input(source, correction)
    assert actions == [InputAction("left", 1), InputAction("backspace", 3), InputAction("text", "ype"),
                       InputAction("right", 1)]
    assert result == "A type."


def test_non_text_events_do_not_emit_input():
    event = Event(0, "pause", "simulated", 0, duration_ms=500)
    assert plan_event_input("existing", event) == ([], "existing")


def test_windows_backend_is_not_constructible_off_windows():
    import wpl.live_input as live_input

    if live_input.os.name == "nt":
        pytest.skip("Windows-only guard")
    with pytest.raises(live_input.LiveInputError):
        live_input.WindowsInputBackend(live_input.WindowTarget(1, 1, "target"))


def test_windows_input_structure_matches_native_abi_size():
    import ctypes

    from wpl.live_input import _input_type

    assert ctypes.sizeof(_input_type()) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)


def test_event_that_does_not_match_current_live_text_is_rejected():
    event = Event(0, "delete", "simulated", 0, 0, removed="wrong")
    with pytest.raises(ValueError):
        plan_event_input("actual", event)


def test_target_focus_change_fails_closed():
    target = WindowTarget(100, 200, "Editor")
    with pytest.raises(FocusChangedError):
        validate_target_state(target, True, 101)
    with pytest.raises(LiveInputError):
        validate_target_state(target, False, 100)
    validate_target_state(target, True, 100)


@pytest.mark.parametrize("duration", [30 * 60_000, 24 * 60 * 60_000])
def test_live_duration_bounds(duration):
    assert validate_live_duration(duration) == duration


@pytest.mark.parametrize("duration", [1_000, 29 * 60_000, 24 * 60 * 60_000 + 1])
def test_live_duration_rejects_out_of_range(duration):
    with pytest.raises((LiveInputError, ValueError)):
        validate_live_duration(duration)


def test_live_session_timing_pause_resume_and_cancel():
    clock = FakeClock()
    events = [Event(0, "insert", "simulated", 1000, 0, inserted="A"),
              Event(1, "insert", "simulated", 2500, 1, inserted="B")]
    session = Session(events, 30 * 60_000, clock)
    session.start()
    assert session.state == RUNNING
    clock.advance(1500)
    assert [event.seq for event in session.poll()] == [0]
    session.pause()
    clock.advance(90_000)
    assert session.active_ms() == 1500
    session.resume()
    clock.advance(1000)
    assert [event.seq for event in session.poll()] == [1]
    assert session.state == COMPLETED

    cancelled = Session(events, 24 * 60 * 60_000, clock)
    cancelled.start()
    cancelled.cancel()
    assert cancelled.state == CANCELLED and cancelled.poll() == []
