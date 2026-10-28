"""Duration limits, fitting event times to a duration, checkpoints, and the deadline/grace rule."""
from __future__ import annotations

from dataclasses import dataclass, replace

from .events import Event

MAX_DURATION_MS = 24 * 3600 * 1000
MIN_DURATION_MS = 1000
MAX_GRACE_MS = 10 * 60 * 1000
DEFAULT_GRACE_MS = 5 * 60 * 1000
PRESETS_MS = {"30 minutes": 30 * 60_000, "1 hour": 60 * 60_000, "2 hours": 120 * 60_000}


class ScheduleError(ValueError):
    pass


def validate_duration(duration_ms: int) -> int:
    if not isinstance(duration_ms, int) or isinstance(duration_ms, bool):
        raise ScheduleError("duration must be an integer number of milliseconds")
    if duration_ms < MIN_DURATION_MS:
        raise ScheduleError("duration must be at least 1 second")
    if duration_ms > MAX_DURATION_MS:
        raise ScheduleError("duration must not exceed 24 hours")
    return duration_ms


def renumber(events: list[Event]) -> list[Event]:
    return [replace(e, seq=i) for i, e in enumerate(events)]


def fit_to_duration(events: list[Event], duration_ms: int, fill: float = 0.97) -> list[Event]:
    """Linearly rescale t_ms/duration_ms so the last event lands at ``fill * duration_ms``.
    Monotonic order is preserved (rescaling by a positive constant cannot reorder, rounding is
    clamped to be non-decreasing)."""
    validate_duration(duration_ms)
    if not 0 < fill <= 1:
        raise ScheduleError("fill must be in (0, 1]")
    ts = [e.t_ms for e in events if e.t_ms is not None]
    if not ts or max(ts) == 0:
        return list(events)
    factor = duration_ms * fill / max(ts)
    out: list[Event] = []
    last = 0
    for e in events:
        t = e.t_ms
        if t is not None:
            t = max(last, int(round(t * factor)))
            last = t
        d = None if e.duration_ms is None else int(round(e.duration_ms * factor))
        out.append(replace(e, t_ms=t, duration_ms=d))
    return out


def add_checkpoints(events: list[Event], interval_ms: int | None, source: str = "simulated") -> list[Event]:
    """Insert a checkpoint event each time recorded time crosses a multiple of ``interval_ms``.
    A checkpoint marks 'document state after the preceding event'. Returns renumbered events."""
    if not interval_ms or interval_ms <= 0:
        return renumber(events)
    out: list[Event] = []
    next_cp = interval_ms
    n_cp = 0
    for e in events:
        while e.t_ms is not None and e.t_ms >= next_cp and out:
            n_cp += 1
            out.append(Event(seq=0, kind="checkpoint", source=source, t_ms=next_cp, meta={"index": n_cp}))
            next_cp += interval_ms
        out.append(e)
    return renumber(out)


MIN_ACTIVE_MS_PER_CHAR = 30  # fastest active writing pace the scheduler will plan (a feasibility floor, not a claim)


class InfeasibleSchedule(ValueError):
    """The break schedule plus the required writing time do not fit in the selected duration."""

    def __init__(self, duration_ms: int, idle_ms: int, required_active_ms: int, fill: float) -> None:
        self.duration_ms, self.idle_ms, self.required_active_ms = duration_ms, idle_ms, required_active_ms
        budget = int(duration_ms * fill)
        self.needed_duration_ms = -(-int((idle_ms + required_active_ms) / fill) // 60_000) * 60_000
        self.max_idle_ms = max(0, budget - required_active_ms)
        super().__init__(self.explain())

    def explain(self) -> str:
        def m(ms: int) -> str:
            return f"{ms / 60_000:.1f} min"
        text = (f"The selected duration ({m(self.duration_ms)}) cannot hold this schedule: the planned breaks and pauses add up to "
                f"{m(self.idle_ms)} and the text needs at least {m(self.required_active_ms)} of active writing.")
        if self.needed_duration_ms <= MAX_DURATION_MS:
            text += f" Option 1: raise the duration to at least {m(self.needed_duration_ms)}."
        else:
            text += " Option 1 is unavailable: the required duration would exceed 24 hours."
        text += f" Option 2: reduce total break and pause time to at most {m(self.max_idle_ms)} (fewer or shorter breaks)."
        return text


def _idle_split(events: list[Event]) -> tuple[list[tuple[int, int]], int]:
    """Per event: (active_raw_time, idle_before) and total idle. Idle = sum of pause durations."""
    out: list[tuple[int, int]] = []
    acc = 0
    open_dur = 0
    total = 0
    for e in events:
        t = e.t_ms or 0
        if e.kind == "pause" and e.duration_ms is not None:
            out.append((t - acc, acc))
            open_dur = e.duration_ms
            total += e.duration_ms
        elif e.kind == "resume":
            acc += open_dur
            open_dur = 0
            out.append((t - acc, acc))
        else:
            out.append((t - acc, acc))
    return out, total


def fit_active(events: list[Event], duration_ms: int, text_chars: int, fill: float = 0.97) -> list[Event]:
    """Fit a plan with absolute-length pauses into ``duration_ms``: only ACTIVE time is rescaled; pause and break
    durations are kept exactly. Raises InfeasibleSchedule if the active time left is below the feasibility floor."""
    validate_duration(duration_ms)
    split, idle = _idle_split(events)
    required = text_chars * MIN_ACTIVE_MS_PER_CHAR
    budget = int(duration_ms * fill) - idle
    raw_active = max((a for a, _ in split), default=0)
    if budget < required:
        raise InfeasibleSchedule(duration_ms, idle, required, fill)
    if raw_active <= 0:
        return list(events)
    factor = budget / raw_active
    out: list[Event] = []
    last = 0
    for e, (active, idle_before) in zip(events, split, strict=True):
        t = max(last, int(round(active * factor)) + idle_before)
        last = t
        out.append(replace(e, t_ms=t))
    return out


def _drop_dangling_pause(events: list[Event]) -> list[Event]:
    for i in range(len(events) - 1, -1, -1):
        if events[i].kind == "resume":
            return events
        if events[i].kind == "pause" and events[i].duration_ms is not None:
            return events[:i] + events[i + 1:]
    return events


@dataclass(frozen=True)
class DeadlineOutcome:
    deadline_priority: bool  # True when the deadline forced an earlier stop than sentence completion
    grace_used_ms: int
    last_sentence_kept: int | None
    dropped_events: int

    @property
    def completion(self) -> str:
        """normal: whole plan played inside the duration. grace: finished within the grace period.
        deadline_stop: later events were dropped (text incomplete) because of the deadline."""
        if self.dropped_events or self.deadline_priority:
            return "deadline_stop"
        return "grace" if self.grace_used_ms else "normal"


def apply_deadline(events: list[Event], duration_ms: int, grace_ms: int = DEFAULT_GRACE_MS
                   ) -> tuple[list[Event], DeadlineOutcome]:
    """Enforce the deadline rule.

    * Nothing past ``duration_ms``: events unchanged.
    * Otherwise find the sentence S (meta['sentence']) of the first event past the deadline. If every event
      of sentences <= S completes within ``duration_ms + grace_ms``, keep them (grace used = overshoot).
    * If finishing S would exceed the grace cap, stop at the end of sentence S-1 and record
      ``deadline_priority=True``. For S == 0 stop at the last event within the deadline.
    The grace is capped at MAX_GRACE_MS regardless of the argument.
    """
    validate_duration(duration_ms)
    grace = min(max(0, grace_ms), MAX_GRACE_MS)
    first_late = next((i for i, e in enumerate(events) if e.t_ms is not None and e.t_ms > duration_ms), None)
    if first_late is None:
        return list(events), DeadlineOutcome(False, 0, None, 0)
    s = int(events[first_late].meta.get("sentence", 0))
    upto_s = [e for e in events if int(e.meta.get("sentence", 0)) <= s]
    finish_t = max((e.t_ms or 0) for e in upto_s)
    if finish_t <= duration_ms + grace:
        kept = [e for e in events if int(e.meta.get("sentence", 0)) <= s]
        kept = _drop_dangling_pause(kept)
        return renumber(kept), DeadlineOutcome(False, max(0, finish_t - duration_ms), s, len(events) - len(kept))
    if s > 0:
        kept = [e for e in events if int(e.meta.get("sentence", 0)) <= s - 1]
        last_s: int | None = s - 1
    else:
        kept = [e for e in events if (e.t_ms or 0) <= duration_ms]
        last_s = None
    kept = _drop_dangling_pause(kept)
    return renumber(kept), DeadlineOutcome(True, 0, last_s, len(events) - len(kept))
