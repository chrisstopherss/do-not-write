"""Versioned, transparent feature extractors. Every metric is defined in docs/metrics.md.

Missing is not zero: a metric that cannot be computed from the supplied data is returned with
status 'missing' (data not recorded) or 'insufficient_data' (recorded but too coarse/small), value=None.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from ..engine.events import Event, check_order, replay
from ..engine.text import paragraphs, sentences, word_count, words

IDLE_THRESHOLD_MS = 2000
KEYSTROKE_MAX_CHARS = 3
KEYSTROKE_MIN_EVENTS = 30


@dataclass
class FeatureValue:
    name: str
    value: Any
    status: str = "ok"  # ok | missing | insufficient_data
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "value": self.value, "status": self.status, "note": self.note}


def ok(name: str, value: Any, note: str = "") -> FeatureValue:
    return FeatureValue(name, value, "ok", note)


def missing(name: str, note: str) -> FeatureValue:
    return FeatureValue(name, None, "missing", note)


def insufficient(name: str, note: str) -> FeatureValue:
    return FeatureValue(name, None, "insufficient_data", note)


@dataclass
class AnalysisInput:
    initial_text: str
    final_text: str
    events: list[Event] = field(default_factory=list)
    duration_ms: int | None = None
    origin: str = "simulated"  # synthetic | simulated | imported | captured (data origin of the session)
    source_text: str = ""      # the original input that was reproduced, if any (reference for text_diff)


def summarize(vals: list[float]) -> dict[str, Any]:
    """n, min, max, mean, median, stdev(sample; None when n<2)."""
    return {"n": len(vals), "min": min(vals), "max": max(vals), "mean": statistics.fmean(vals),
            "median": statistics.median(vals), "stdev": statistics.stdev(vals) if len(vals) >= 2 else None}


def _hist(vals: list[int], edges: list[int]) -> dict[str, int]:
    out: dict[str, int] = {}
    for lo, hi in zip(edges, edges[1:] + [None], strict=True):
        label = f"{lo}+" if hi is None else str(lo) if hi - 1 == lo else f"{lo}-{hi - 1}"
        out[label] = sum(1 for v in vals if v >= lo and (hi is None or v < hi))
    return out


# ---------------------------------------------------------------- text extractors
def text_basic(a: AnalysisInput) -> list[FeatureValue]:
    t = a.final_text
    return [ok("chars", len(t)), ok("words", word_count(t)), ok("sentences", len(sentences(t))),
            ok("paragraphs", len(paragraphs(t))), ok("initial_chars", len(a.initial_text)),
            ok("initial_words", word_count(a.initial_text))]


def text_distributions(a: AnalysisInput) -> list[FeatureValue]:
    t = a.final_text
    out: list[FeatureValue] = []
    sl = [word_count(s) for s in sentences(t)]
    pl = [word_count(p) for p in paragraphs(t)]
    out.append(ok("sentence_length_words", summarize(sl)) if sl else missing("sentence_length_words", "no sentences"))
    out.append(ok("paragraph_length_words", summarize(pl)) if pl else missing("paragraph_length_words", "no paragraphs"))
    marks = Counter(c for c in t if c in ".,;:!?-—–'\"‘’“”()[]…")
    out.append(ok("punctuation_counts", dict(sorted(marks.items()))))
    return out


def repeated_phrases(a: AnalysisInput) -> list[FeatureValue]:
    ws = [w.lower() for w in words(a.final_text)]
    out: list[FeatureValue] = []
    immediate = sum(1 for x, y in zip(ws, ws[1:], strict=False) if x == y)
    out.append(ok("immediate_word_repeats", immediate, "adjacent identical words, e.g. 'the the'"))
    for n in (2, 3):
        grams = Counter(tuple(ws[i:i + n]) for i in range(len(ws) - n + 1))
        rep = [(" ".join(g), c) for g, c in grams.most_common() if c >= 2]
        out.append(ok(f"repeated_{n}grams", {"distinct_repeated": len(rep), "top": rep[:10]}))
    return out


def text_diff_features(a: AnalysisInput) -> list[FeatureValue]:
    from .diff import word_diff

    ref = a.source_text or a.initial_text
    if not ref:
        return [missing("text_diff", "no source or initial text to compare against")]
    ops = word_diff(ref, a.final_text)
    c = Counter(o.tag for o in ops if o.tag != "equal")
    ratio = SequenceMatcher(None, ref, a.final_text, autojunk=False).ratio()
    return [ok("diff_word_inserts", c.get("insert", 0)), ok("diff_word_deletes", c.get("delete", 0)),
            ok("diff_word_replaces", c.get("replace", 0)), ok("char_similarity_ratio", round(ratio, 6),
            "difflib SequenceMatcher ratio between source (or initial) text and final text; 1.0 = identical")]


# ---------------------------------------------------------------- process extractors
def process_events(a: AnalysisInput) -> list[FeatureValue]:
    evs = a.events
    if not evs:
        return [missing("process_events", "no events recorded for this session")]
    out: list[FeatureValue] = []
    problems = check_order(evs)
    rp = replay(evs, a.initial_text)
    kinds = Counter(e.kind for e in evs)
    out.append(ok("total_events", len(evs)))
    out.append(ok("event_counts_by_kind", dict(sorted(kinds.items()))))
    ops = [e for e in evs if e.is_text_op]
    ins = [e for e in ops if e.kind in ("insert", "paste", "import")]
    dels = [e for e in ops if e.kind == "delete"]
    reps = [e for e in ops if e.kind == "replace"]
    out.append(ok("insertions", len(ins)))
    out.append(ok("deletions", len(dels)))
    out.append(ok("replacements", len(reps)))
    out.append(ok("inserted_chars", sum(len(e.inserted) for e in ops)))
    out.append(ok("removed_chars", sum(len(e.removed) for e in ops)))
    sizes = [max(len(e.inserted), len(e.removed)) for e in ops]
    if sizes:
        out.append(ok("event_size_chars", {**summarize(sizes), "histogram": _hist(sizes, [1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024])}))
    else:
        out.append(missing("event_size_chars", "no text events"))
    out.append(ok("pause_count", kinds.get("pause", 0)))
    out.append(ok("interruption_count", kinds.get("interruption", 0)))
    out.append(ok("checkpoint_count", kinds.get("checkpoint", 0)))
    revisions = dels + reps
    out.append(ok("revision_events", len(revisions), "delete + replace events"))
    out.append(ok("changed_existing_chars", sum(len(e.removed) for e in revisions),
                  "characters removed by delete/replace events"))
    out.append(ok("mean_removed_chars_per_revision", statistics.fmean(len(e.removed) for e in revisions) if revisions else None,
                  "None when there were no revision events") if revisions
               else missing("mean_removed_chars_per_revision", "no revision events"))
    out.append(ok("replay_problems", len(rp.problems) + len(problems), "; ".join((rp.problems + problems)[:5])))
    # origin / provenance of the surviving final text
    n = len(rp.origin)
    if n:
        from_events = sum(1 for o in rp.origin if o >= 0)
        out.append(ok("final_share_from_recorded_events", from_events / n,
                      "fraction of final-text characters created by a recorded insert/replace event (rest is initial text)"))
        share = Counter(o for o in rp.origin if o >= 0)
        out.append(ok("largest_single_event_share", max(share.values()) / n if share else 0.0,
                      "largest fraction of final text created by one single event"))
    else:
        out.append(missing("final_share_from_recorded_events", "final text is empty"))
    # time-based
    ts_ops = [e for e in ops if e.t_ms is not None]
    if len(ts_ops) < 2:
        for nm in ("event_intervals_ms", "active_ms", "idle_ms", "revisions_per_minute", "text_growth"):
            out.append(missing(nm, "fewer than 2 timestamped text events"))
    else:
        gaps = [b.t_ms - a_.t_ms for a_, b in zip(ts_ops, ts_ops[1:], strict=False)]  # type: ignore[operator]
        out.append(ok("event_intervals_ms", {**summarize(gaps), "histogram": _hist(gaps, [0, 100, 250, 500, 1000, 2000, 5000, 15000, 60000])}))
        out.append(ok("active_ms", sum(g for g in gaps if g <= IDLE_THRESHOLD_MS), f"sum of gaps <= {IDLE_THRESHOLD_MS} ms"))
        out.append(ok("idle_ms", sum(g for g in gaps if g > IDLE_THRESHOLD_MS), f"sum of gaps > {IDLE_THRESHOLD_MS} ms"))
        span = ts_ops[-1].t_ms - ts_ops[0].t_ms  # type: ignore[operator]
        out.append(ok("revisions_per_minute", len(revisions) / (span / 60000), "revision events / minutes between first and last text event") if span > 0
                   else insufficient("revisions_per_minute", "zero time span"))
        out.append(ok("text_growth", [(t, ln) for t, _s, ln in rp.lengths if t is not None]))
    # typing speed only from keystroke-grain data
    keys = [e for e in ins if e.t_ms is not None and len(e.inserted) <= KEYSTROKE_MAX_CHARS]
    if len(ins) >= KEYSTROKE_MIN_EVENTS and len(keys) == len(ins) and len(ts_ops) >= 2:
        span_min = (ins[-1].t_ms - ins[0].t_ms) / 60000  # type: ignore[operator]
        out.append(ok("chars_per_minute_inserted", sum(len(e.inserted) for e in ins) / span_min, "keystroke-grain data only") if span_min > 0
                   else insufficient("chars_per_minute_inserted", "zero time span"))
    else:
        out.append(insufficient("chars_per_minute_inserted",
                                f"needs >= {KEYSTROKE_MIN_EVENTS} timestamped insert events of <= {KEYSTROKE_MAX_CHARS} chars; "
                                "event data is too coarse to support a typing-speed figure"))
    return out


@dataclass(frozen=True)
class Extractor:
    id: str
    version: str
    description: str
    kind: str  # text | process
    fn: Callable[[AnalysisInput], list[FeatureValue]]


REGISTRY: dict[str, Extractor] = {}


def register(e: Extractor) -> None:
    REGISTRY[e.id] = e


for _e in (
    Extractor("text_basic", "1.0.0", "Character, word, sentence, paragraph counts", "text", text_basic),
    Extractor("text_distributions", "1.0.0", "Sentence/paragraph length and punctuation distributions", "text", text_distributions),
    Extractor("repeated_phrases", "1.0.0", "Immediate word repeats and repeated 2/3-grams", "text", repeated_phrases),
    Extractor("text_diff", "1.0.0", "Initial/source vs final text differences", "text", text_diff_features),
    Extractor("process_events", "1.0.0", "Event, revision, interval and provenance metrics", "process", process_events),
):
    register(_e)


def run_extractors(a: AnalysisInput, ids: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """Run extractors; an extractor that raises is reported as an error entry, never silently dropped."""
    out: dict[str, dict[str, Any]] = {}
    for eid in ids or list(REGISTRY):
        ex = REGISTRY[eid]
        try:
            feats = [f.to_dict() for f in ex.fn(a)]
            err = None
        except Exception as exc:  # noqa: BLE001 - report, don't crash the dashboard
            feats, err = [], f"{type(exc).__name__}: {exc}"
        out[eid] = {"version": ex.version, "kind": ex.kind, "features": feats, "error": err}
    return out


_ = re  # re kept for extractor authors
