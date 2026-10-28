"""Experimental conditions A-E: documented, seeded, synthetic event generators.

They are experimental treatments, NOT faithful models of any real writer or tool. The generators exist to
produce labeled synthetic data for evaluating analysis methods. They are deliberately not tuned to resemble
or to evade any detector.
"""
from __future__ import annotations

import random
import re
from dataclasses import asdict, dataclass, field
from importlib import resources
from typing import Any

from ..engine.builder import DocBuilder
from ..engine.events import Event
from ..engine.schedule import (
    DEFAULT_GRACE_MS,
    DeadlineOutcome,
    add_checkpoints,
    apply_deadline,
    fit_to_duration,
    validate_duration,
)
from ..engine.simulate import ALL_CATEGORIES, CategoryStats, MistakeConfig, SimConfig, type_text
from ..engine.text import WORD_RE, sentence_spans

CONDITION_NAMES = {
    "A": "Incremental composition",
    "B": "Whole-text reproduction (sentence by sentence)",
    "C": "Automated text insertion",
    "D": "Revision-heavy editing",
    "E": "Mixed workflow",
}
SYNTH_SENTENCES = ["The matter was noted for the record.", "A follow-up review was scheduled.",
                   "Several attendees asked for further detail.", "No objections were raised at that point.",
                   "The summary was circulated afterwards."]
BULK_THRESHOLD_DEFAULT = 200


def display_name(p: ConditionParams) -> str:
    return p.name or f"{p.code}: {CONDITION_NAMES[p.code]}"


def sample_text() -> str:
    return resources.files("wpl.lab").joinpath("sample_text.txt").read_text(encoding="utf-8").strip()


class ParamError(ValueError):
    pass


@dataclass
class ConditionParams:
    code: str = "A"
    name: str = ""                        # optional display name (e.g. for parameter sweeps)
    text: str = ""                        # synthetic or explicitly authorized text only
    duration_ms: int = 30 * 60_000
    checkpoint_interval_ms: int | None = 5 * 60_000
    revision_frequency: float = 4.0       # revision operations per 1000 characters (D, E)
    mistakes: dict[str, Any] = field(default_factory=lambda: MistakeConfig.default().to_dict())
    pause_rate: float = 0.15
    insert_chunks: int = 1                # C: number of pieces the text is inserted in
    bulk_threshold_chars: int = BULK_THRESHOLD_DEFAULT
    grace_ms: int = DEFAULT_GRACE_MS

    def validate(self) -> None:
        if self.code not in CONDITION_NAMES:
            raise ParamError(f"unknown condition {self.code!r}")
        if not self.text.strip():
            raise ParamError("input text is empty")
        validate_duration(self.duration_ms)
        if self.checkpoint_interval_ms is not None and self.checkpoint_interval_ms <= 0:
            raise ParamError("checkpoint interval must be positive or None")
        if not 0 <= self.revision_frequency <= 50:
            raise ParamError("revision_frequency must be within [0, 50] per 1000 chars")
        if not 1 <= self.insert_chunks <= 1000:
            raise ParamError("insert_chunks must be within [1, 1000]")
        MistakeConfig.from_dict(self.mistakes)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> ConditionParams:
        known = {k: v for k, v in d.items() if k in ConditionParams.__dataclass_fields__}
        return ConditionParams(**known)


@dataclass
class RunOutput:
    events: list[Event]
    final_text: str
    stats: dict[str, CategoryStats]
    ground_truth: dict[str, Any]
    deadline: DeadlineOutcome


def _revise(b: DocBuilder, rng: random.Random, n: int) -> None:
    for _ in range(n):
        b.advance(rng.uniform(2000, 20000))
        spans = sentence_spans(b.text)
        b.sentence = max(0, len(spans) - 1)
        kind = rng.choice(["replace", "replace", "delete", "add"])
        meta = {"category": "revision", "revision_kind": kind}
        words = list(WORD_RE.finditer(b.text))
        if kind == "replace" and len(words) > 3:
            m = words[rng.randrange(len(words))]
            pool = sorted({w.group().lower() for w in words if len(w.group()) >= 3 and w.group().lower() != m.group().lower()})
            if pool:
                new = rng.choice(pool)
                b.replace(m.start(), m.end() - m.start(), new.capitalize() if m.group()[0].isupper() else new, meta)
                continue
            kind = "add"
        if kind == "delete" and len(spans) > 2:
            a, z = spans[rng.randrange(len(spans))]
            end = z + 1 if z < len(b.text) and b.text[z] == " " else z
            b.delete(a, end - a, meta)
            continue
        a, z = spans[rng.randrange(len(spans))] if spans else (0, 0)
        b.insert(z, " " + rng.choice(SYNTH_SENTENCES), meta=meta)


def _split_points(text: str) -> tuple[int, int]:
    spans = sentence_spans(text)
    if len(spans) < 3:
        return (len(text) // 3, 2 * len(text) // 3)
    return spans[len(spans) // 3][0], spans[2 * len(spans) // 3][0]


def generate(params: ConditionParams, seed: int) -> RunOutput:
    params.validate()
    rng = random.Random(seed)
    text = params.text
    cfg = SimConfig(MistakeConfig.from_dict(params.mistakes), pause_rate=params.pause_rate)
    b = DocBuilder("")
    stats = {c: CategoryStats() for c in ALL_CATEGORIES}
    needs_fit = True
    code = params.code
    n_rev = round(params.revision_frequency * len(text) / 1000)

    def insert_by_sentence(seg: str, offset: int) -> None:
        spans = sentence_spans(seg)
        for i, (a, _z) in enumerate(spans):
            end = spans[i + 1][0] if i + 1 < len(spans) else len(seg)
            chunk = seg[a:end] if i else seg[:end]
            b.sentence = offset + i
            b.advance(rng.uniform(4000, 12000))
            b.insert(len(b.text), chunk, meta={"method": "sentence_reproduction"})

    if code == "A":
        type_text(b, text, rng, cfg, stats)
    elif code == "B":
        insert_by_sentence(text, 0)
    elif code == "C":
        needs_fit = False
        n = params.insert_chunks
        step = max(1, -(-len(text) // n))
        t0 = min(2000.0, params.duration_ms * 0.01)
        b.t = t0
        for i in range(0, len(text), step):
            b.sentence = 0
            b.insert(len(b.text), text[i:i + step], meta={"method": "engine_insert"})
            b.advance(50)
    elif code == "D":
        insert_by_sentence(text, 0)
        _revise(b, rng, n_rev)
    elif code == "E":
        p1, p2 = _split_points(text)
        type_text(b, text[:p1], rng, cfg, stats)
        n1 = len(sentence_spans(text[:p1]))
        b.advance(rng.uniform(3000, 9000))
        b.sentence = n1
        b.insert(len(b.text), text[p1:p2], meta={"method": "engine_insert"})
        insert_by_sentence(text[p2:], n1 + 1)
        _revise(b, rng, n_rev)
    events = fit_to_duration(b.events, params.duration_ms) if needs_fit else b.events
    events, outcome = apply_deadline(events, params.duration_ms, params.grace_ms)
    events = add_checkpoints(events, params.checkpoint_interval_ms)
    largest = max((len(e.inserted) for e in events if e.kind in ("insert", "paste", "import")), default=0)
    truth = {"workflow": code, "workflow_name": CONDITION_NAMES[code],
             "contains_bulk_insert": largest >= params.bulk_threshold_chars, "bulk_threshold_chars": params.bulk_threshold_chars}
    from ..engine.events import replay
    return RunOutput(events, replay(events).text, stats, truth, outcome)
_ = re
