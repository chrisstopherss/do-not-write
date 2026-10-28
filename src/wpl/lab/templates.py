"""Experiment templates: starting points for documented comparisons. They configure treatments; they do not
claim to represent any real writer or tool."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from .conditions import ConditionParams


@dataclass(frozen=True)
class Template:
    id: str
    title: str
    description: str
    expected: str
    build: Callable[[str, int], list[ConditionParams]]


def _five(text: str, ms: int) -> list[ConditionParams]:
    return [ConditionParams(code=c, text=text, duration_ms=ms) for c in "ABCDE"]


def _ins_vs_inc(text: str, ms: int) -> list[ConditionParams]:
    return [ConditionParams(code="A", text=text, duration_ms=ms), ConditionParams(code="C", text=text, duration_ms=ms)]


def _rev_sweep(text: str, ms: int) -> list[ConditionParams]:
    return [ConditionParams(code="D", name=f"D rev={f:g}/1000", text=text, duration_ms=ms, revision_frequency=f) for f in (1.0, 4.0, 10.0)]


def _cp_sweep(text: str, ms: int) -> list[ConditionParams]:
    return [ConditionParams(code="A", name=f"A checkpoint={m}min", text=text, duration_ms=ms, checkpoint_interval_ms=m * 60_000) for m in (1, 5, 15)]


TEMPLATES = [
    Template("five", "Five workflows (A-E) on one text", "Runs every condition with identical text and duration.",
             "C: one very large insertion. D/E: more revision events than A/B.", _five),
    Template("ins_vs_inc", "Insertion vs incremental entry", "Contrasts A (incremental) with C (single automated insertion).",
             "Event counts differ by orders of magnitude; final text length is similar.", _ins_vs_inc),
    Template("rev_sweep", "Revision frequency sweep (D)", "Condition D at three revision frequencies.",
             "Revision event counts rise with frequency.", _rev_sweep),
    Template("cp_sweep", "Checkpoint frequency sweep (A)", "Condition A at three checkpoint intervals.",
             "Checkpoint count falls as the interval grows; text events are unchanged.", _cp_sweep),
]


_ = replace
