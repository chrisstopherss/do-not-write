"""Writing rhythm profiles and the break scheduler's draw logic.

These are EXPERIMENTAL TIMING PARAMETERS for studying how pauses and breaks change measurable process statistics.
They are not models of how any person writes, and no preset is intended to resemble human authorship or to avoid
detection. Nothing here is tuned against any third-party tool.

Definitions
  gap          the moment before a word-sized writing unit is entered
  short pause  drawn at each gap with probability ``short_pause_prob``; duration uniform in [pause_min, pause_max]
  break        drawn at each sentence boundary (not before the first sentence) with probability ``long_break_prob``;
               duration uniform in [break_min, break_max]; never longer than MAX_BREAK_MS (5 minutes)
  A sentence boundary draws the break first; if a break happens no short pause is added at that gap.
All draws use the session's seeded ``random.Random`` in document order, so a saved seed reproduces the schedule.
Pause and break durations are absolute milliseconds: fitting a plan to a session duration rescales only active
writing time, never the pauses (see schedule.fit_active).
"""
from __future__ import annotations

import random
from collections import Counter
from dataclasses import asdict, dataclass, replace
from typing import Any

from .builder import DocBuilder
from .revise import revise

MAX_BREAK_MS = 300_000
MIN_PAUSE_MS = 100
MAX_SHORT_PAUSE_MS = 60_000
MIN_BREAK_MS = 1_000
ACTIVITIES = ("uniform", "front_loaded", "back_loaded", "bursty")
ACTIVITY_LABELS = {"uniform": "Uniform", "front_loaded": "Front-loaded (faster early)",
                   "back_loaded": "Back-loaded (faster late)", "bursty": "Bursty (alternating fast and slow groups)"}
MIN_PACE_MS = 30
MAX_PACE_MS = 2000


class RhythmError(ValueError):
    pass


@dataclass
class RhythmConfig:
    name: str = "regular"
    pause_min_ms: int = 500
    pause_max_ms: int = 3_000
    short_pause_prob: float = 0.10     # per gap, [0, 0.5]
    long_break_prob: float = 0.05      # per sentence boundary, [0, 1]
    break_min_ms: int = 10_000
    break_max_ms: int = 60_000
    revision_per_1000: float = 3.0     # revision operations per 1000 characters of text, [0, 50]
    activity: str = "uniform"
    pace_ms_per_char: int | None = None  # None: active time is fitted to the session duration

    def validate(self) -> RhythmConfig:
        def bad(msg: str) -> RhythmError:
            return RhythmError(msg)

        if not MIN_PAUSE_MS <= self.pause_min_ms <= MAX_SHORT_PAUSE_MS:
            raise bad(f"Minimum pause must be between {MIN_PAUSE_MS} ms and {MAX_SHORT_PAUSE_MS // 1000} s.")
        if not self.pause_min_ms <= self.pause_max_ms <= MAX_SHORT_PAUSE_MS:
            raise bad(f"Maximum pause must be at least the minimum and at most {MAX_SHORT_PAUSE_MS // 1000} s.")
        if not 0.0 <= self.short_pause_prob <= 0.5:
            raise bad("Short-pause probability must be between 0% and 50% per writing unit.")
        if not 0.0 <= self.long_break_prob <= 1.0:
            raise bad("Break probability must be between 0% and 100% per sentence boundary.")
        if not MIN_BREAK_MS <= self.break_min_ms <= MAX_BREAK_MS:
            raise bad(f"Minimum break must be between {MIN_BREAK_MS // 1000} s and {MAX_BREAK_MS // 60_000} minutes.")
        if not self.break_min_ms <= self.break_max_ms <= MAX_BREAK_MS:
            raise bad(f"Maximum break must be at least the minimum and at most {MAX_BREAK_MS // 60_000} minutes.")
        if not 0.0 <= self.revision_per_1000 <= 50.0:
            raise bad("Revision frequency must be between 0 and 50 per 1000 characters.")
        if self.activity not in ACTIVITIES:
            raise bad(f"Activity distribution must be one of {', '.join(ACTIVITIES)}.")
        if self.pace_ms_per_char is not None and not MIN_PACE_MS <= self.pace_ms_per_char <= MAX_PACE_MS:
            raise bad(f"Fixed pace must be between {MIN_PACE_MS} and {MAX_PACE_MS} ms per character.")
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> RhythmConfig:
        known = {k: v for k, v in d.items() if k in RhythmConfig.__dataclass_fields__}
        return RhythmConfig(**known).validate()

    def weight(self, sidx: int, n: int) -> float:
        """Relative writing speed for sentence ``sidx`` of ``n`` (>1 = faster). Shapes how active time is spread."""
        if self.activity == "uniform" or n <= 1:
            return 1.0
        x = sidx / (n - 1)
        if self.activity == "front_loaded":
            return 1.6 - 1.2 * x
        if self.activity == "back_loaded":
            return 0.4 + 1.2 * x
        return 2.0 if (sidx // 3) % 2 == 0 else 0.5  # bursty


PRESETS: dict[str, RhythmConfig] = {
    "regular": RhythmConfig("regular", 500, 3_000, 0.10, 0.05, 10_000, 60_000, 3.0, "uniform"),
    "reflective": RhythmConfig("reflective", 1_000, 8_000, 0.25, 0.20, 20_000, 300_000, 6.0, "uniform"),
    "extended": RhythmConfig("extended", 1_000, 5_000, 0.10, 0.40, 60_000, 300_000, 2.0, "bursty"),
    "custom": RhythmConfig("custom", 500, 3_000, 0.10, 0.05, 10_000, 60_000, 3.0, "uniform"),
}
PRESET_TITLES = {"regular": "Regular", "reflective": "Reflective", "extended": "Extended Breaks", "custom": "Custom"}
PRESET_DESCRIPTIONS = {
    "regular": "Short pauses between writing events, occasional brief breaks (10-60 s), moderate revisions. A baseline for comparing synthetic sessions.",
    "reflective": "More frequent pauses, occasional longer thinking breaks (20 s to 5 min), more corrections and revisions. For studying how pauses affect duration and event statistics.",
    "extended": "Less frequent writing in bursts, with frequent longer breaks (1 to 5 min) and long idle gaps between groups of writing events. For studying the effect of extended inactivity on session statistics.",
    "custom": "Set every parameter yourself. Limits: pauses up to 60 s, breaks up to 5 minutes.",
}


def preset(name: str) -> RhythmConfig:
    return replace(PRESETS[name])


class RhythmHooks:
    """Per-run state used by ``type_text``: which sentence boundaries get a revision, and the pause/break draws."""

    def __init__(self, cfg: RhythmConfig, n_sentences: int, text_len: int, rng: random.Random) -> None:
        self.cfg = cfg
        self.n = n_sentences
        n_rev = round(cfg.revision_per_1000 * text_len / 1000)
        picks = [rng.randrange(1, n_sentences) for _ in range(n_rev)] if n_sentences > 1 else []
        self.rev_at: Counter[int] = Counter(picks)

    def boundary(self, b: DocBuilder, rng: random.Random, sidx: int, offset: int) -> bool:
        """Called at each sentence boundary after the first. Applies revisions, then maybe a break.
        Returns True if a break was taken."""
        for _ in range(self.rev_at.get(sidx, 0)):
            revise(b, rng, 1)
        b.sentence = offset + sidx
        if rng.random() < self.cfg.long_break_prob:
            dur = rng.randint(self.cfg.break_min_ms, self.cfg.break_max_ms)
            b.pause(dur, {"pause_class": "break", "rhythm": self.cfg.name})
            return True
        return False

    def gap(self, b: DocBuilder, rng: random.Random, apply: bool = True) -> None:
        """Draw a short pause. The draw always happens (fixed RNG order); ``apply`` is False when a break was just taken."""
        if rng.random() < self.cfg.short_pause_prob:
            dur = rng.randint(self.cfg.pause_min_ms, self.cfg.pause_max_ms)
            if apply:
                b.pause(dur, {"pause_class": "short", "rhythm": self.cfg.name})
