"""Session playback state machine. No Qt, no I/O. Uses an injectable monotonic clock.

Time is measured on the *active* clock: time spent paused does not count. Events are released by absolute
active time (never by counting timer ticks), so late callbacks cannot cause cumulative drift; a late poll
simply releases everything that is due (bounded by ``max_batch`` per call to avoid UI stalls).
Scheduling uses ``next_due_in_ms`` so the UI sets one single-shot timer instead of polling at a high rate.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from .events import Event


class Clock(Protocol):
    def now_ms(self) -> float: ...


class MonotonicClock:
    def now_ms(self) -> float:
        return time.monotonic_ns() / 1_000_000


class FakeClock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def now_ms(self) -> float:
        return self.t

    def advance(self, ms: float) -> None:
        self.t += ms


IDLE, RUNNING, PAUSED, COMPLETED, CANCELLED = "idle", "running", "paused", "completed", "cancelled"


class SessionStateError(RuntimeError):
    pass


@dataclass
class Snapshot:
    cursor: int
    active_ms: float
    state: str


class Session:
    def __init__(self, events: list[Event], duration_ms: int, clock: Clock | None = None, max_batch: int = 200) -> None:
        self.events = events
        self.duration_ms = duration_ms
        self.clock = clock or MonotonicClock()
        self.max_batch = max_batch
        self.state = IDLE
        self.cursor = 0
        self._active_before = 0.0
        self._run_started: float | None = None

    # -- clock
    def active_ms(self) -> float:
        if self.state == RUNNING and self._run_started is not None:
            return self._active_before + (self.clock.now_ms() - self._run_started)
        return self._active_before

    def remaining_ms(self) -> float:
        return max(0.0, self.duration_ms - self.active_ms())

    def progress(self) -> float:
        return 0.0 if not self.events else self.cursor / len(self.events)

    # -- transitions
    def start(self) -> None:
        if self.state != IDLE:
            raise SessionStateError(f"cannot start from {self.state}")
        self.state = RUNNING
        self._run_started = self.clock.now_ms()

    def pause(self) -> None:
        if self.state != RUNNING:
            raise SessionStateError(f"cannot pause from {self.state}")
        self._active_before = self.active_ms()
        self._run_started = None
        self.state = PAUSED

    def resume(self) -> None:
        if self.state != PAUSED:
            raise SessionStateError(f"cannot resume from {self.state}")
        self._run_started = self.clock.now_ms()
        self.state = RUNNING

    def cancel(self) -> None:
        if self.state in (COMPLETED, CANCELLED):
            raise SessionStateError(f"cannot cancel from {self.state}")
        self._active_before = self.active_ms()
        self._run_started = None
        self.state = CANCELLED

    # -- playback
    def poll(self) -> list[Event]:
        """Release due events. Marks the session completed once the last event has been released."""
        if self.state != RUNNING:
            return []
        now = self.active_ms()
        out: list[Event] = []
        while self.cursor < len(self.events) and len(out) < self.max_batch:
            t = self.events[self.cursor].t_ms
            if t is not None and t > now:
                break
            out.append(self.events[self.cursor])
            self.cursor += 1
        if self.cursor >= len(self.events):
            self._active_before = self.active_ms()
            self._run_started = None
            self.state = COMPLETED
        return out

    def next_due_in_ms(self) -> float | None:
        """Milliseconds until the next event is due (0 if due now); None when nothing is scheduled."""
        if self.state != RUNNING or self.cursor >= len(self.events):
            return None
        t = self.events[self.cursor].t_ms or 0
        return max(0.0, t - self.active_ms())

    # -- recovery
    def snapshot(self) -> Snapshot:
        return Snapshot(self.cursor, self.active_ms(), self.state)

    @classmethod
    def restore(cls, events: list[Event], duration_ms: int, snap: Snapshot, clock: Clock | None = None) -> Session:
        s = cls(events, duration_ms, clock)
        s.cursor = min(snap.cursor, len(events))
        s._active_before = snap.active_ms
        s.state = PAUSED if snap.state in (RUNNING, PAUSED) else snap.state  # always resume explicitly
        return s
