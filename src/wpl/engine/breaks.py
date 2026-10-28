"""Break scheduler: plans active writing, pauses and breaks within one overall time budget.

Pipeline: seeded generation (rhythm hooks) -> fit active time to the budget (breaks keep their absolute length)
-> deadline rule (sentence completion within the existing grace cap) -> checkpoints.
The result is reproducible from (text, seed, configs, durations, ENGINE_VERSION). Break lengths are experimental
parameters and say nothing about how a person writes.
"""
from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from typing import Any

from .. import ENGINE_VERSION
from .builder import DocBuilder
from .events import Event, replay
from .rhythm import RhythmConfig
from .schedule import (
    DEFAULT_GRACE_MS,
    DeadlineOutcome,
    add_checkpoints,
    apply_deadline,
    fit_active,
    validate_duration,
)
from .simulate import ALL_CATEGORIES, CategoryStats, SimConfig, type_text


@dataclass
class Segment:
    start_ms: int
    end_ms: int
    kind: str  # writing | short_pause | break

    @property
    def length_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass
class ScheduleSummary:
    """Numbers derived from the event list (simulated clock). 'idle' = short pauses + breaks."""
    total_ms: int
    active_ms: int
    idle_ms: int
    breaks: int
    short_pauses: int
    break_total_ms: int
    avg_break_ms: float | None
    longest_break_ms: int | None
    segments: list[Segment] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("segments")
        return d


def summarize(events: list[Event], end_ms: int | None = None) -> ScheduleSummary:
    """Active/idle accounting from pause events. ``end_ms`` defaults to the last event time."""
    pauses = [e for e in events if e.kind == "pause" and e.duration_ms is not None and e.t_ms is not None]
    last = max((e.t_ms for e in events if e.t_ms is not None), default=0)
    total = max(last, end_ms or 0)
    segs: list[Segment] = []
    cursor = 0
    for p in pauses:
        assert p.t_ms is not None and p.duration_ms is not None
        if p.t_ms > cursor:
            segs.append(Segment(cursor, p.t_ms, "writing"))
        end = min(p.t_ms + p.duration_ms, total)
        segs.append(Segment(p.t_ms, end, "break" if p.meta.get("pause_class") == "break" else "short_pause"))
        cursor = max(cursor, end)
    if total > cursor:
        segs.append(Segment(cursor, total, "writing"))
    brk = [s for s in segs if s.kind == "break"]
    idle = sum(s.length_ms for s in segs if s.kind != "writing")
    return ScheduleSummary(total, total - idle, idle, len(brk), sum(1 for s in segs if s.kind == "short_pause"),
                           sum(s.length_ms for s in brk), (sum(s.length_ms for s in brk) / len(brk)) if brk else None,
                           max((s.length_ms for s in brk), default=None), segs)


@dataclass
class RhythmPlan:
    events: list[Event]
    final_text: str
    stats: dict[str, CategoryStats]
    deadline: DeadlineOutcome
    rhythm: RhythmConfig
    seed: int
    engine_version: str = ENGINE_VERSION

    @property
    def summary(self) -> ScheduleSummary:
        return summarize(self.events)


def plan_rhythm(text: str, seed: int, sim: SimConfig, rhythm: RhythmConfig, duration_ms: int,
                grace_ms: int = DEFAULT_GRACE_MS, checkpoint_ms: int | None = None) -> RhythmPlan:
    """Plan a whole session. Raises InfeasibleSchedule (with alternatives) if breaks + required writing exceed the duration."""
    validate_duration(duration_ms)
    rhythm.validate()
    if not text.strip():
        raise ValueError("input text is empty")
    rng = random.Random(seed)
    b = DocBuilder("")
    stats = {c: CategoryStats() for c in ALL_CATEGORIES}
    type_text(b, text, rng, sim, stats, rhythm=rhythm)
    events = b.events
    if rhythm.pace_ms_per_char is None:
        events = fit_active(events, duration_ms, len(text))
    events, outcome = apply_deadline(events, duration_ms, grace_ms)
    events = add_checkpoints(events, checkpoint_ms)
    return RhythmPlan(events, replay(events).text, stats, outcome, rhythm, seed)
