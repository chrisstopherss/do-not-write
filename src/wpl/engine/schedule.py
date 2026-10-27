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


@dataclass(frozen=True)
class DeadlineOutcome:
    deadline_priority: bool  # True when the deadline forced an earlier stop than sentence completion
    grace_used_ms: int
    last_sentence_kept: int | None
    dropped_events: int


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
        return renumber(kept), DeadlineOutcome(False, max(0, finish_t - duration_ms), s, len(events) - len(kept))
    if s > 0:
        kept = [e for e in events if int(e.meta.get("sentence", 0)) <= s - 1]
        last_s: int | None = s - 1
    else:
        kept = [e for e in events if (e.t_ms or 0) <= duration_ms]
        last_s = None
    return renumber(kept), DeadlineOutcome(True, 0, last_s, len(events) - len(kept))
