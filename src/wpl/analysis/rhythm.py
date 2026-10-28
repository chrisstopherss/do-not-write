"""Rhythm extractor: active vs idle time, breaks, pause and interval distributions, revisions around breaks.

Works on the simulated clock carried by event timestamps and pause durations. Pauses/breaks that were not recorded
produce 'missing', never zero. Everything is descriptive of the event data; none of it says how a text was written.
"""
from __future__ import annotations

from ..engine.breaks import Segment, summarize
from ..engine.events import Event
from .features import AnalysisInput, Extractor, FeatureValue, missing, ok, register
from .features import summarize as summ

PAUSE_EDGES = [0, 1_000, 2_000, 5_000, 10_000, 30_000, 60_000, 120_000, 300_000]
INTERVAL_EDGES = [0, 5_000, 15_000, 30_000, 60_000, 120_000, 300_000, 600_000]


def _hist(vals: list[int], edges: list[int]) -> dict[str, int]:
    out: dict[str, int] = {}
    for lo, hi in zip(edges, edges[1:] + [None], strict=True):
        label = f"{lo}+" if hi is None else f"{lo}-{hi - 1}"
        out[label] = sum(1 for v in vals if v >= lo and (hi is None or v < hi))
    return out


def rhythm_metrics(a: AnalysisInput) -> list[FeatureValue]:
    evs = a.events
    sources = sorted({e.source for e in evs})
    out = [ok("rhythm_data_sources", sources, "who produced the events: simulated (engine), captured (app), imported (external)")]
    names = ("sim_total_ms", "sim_active_ms", "sim_idle_ms", "break_count", "short_pause_count", "break_total_ms", "avg_break_ms",
             "longest_break_ms", "incomplete_breaks", "pause_duration_distribution", "active_interval_distribution",
             "text_added_per_active_interval", "revisions_around_breaks")

    def all_missing(why: str) -> list[FeatureValue]:
        return out + [missing(n, why) for n in names]

    if not evs:
        return all_missing("no events recorded")
    if not any(e.t_ms is not None for e in evs):
        return all_missing("events have no timestamps")
    pauses = [e for e in evs if e.kind == "pause"]
    if not pauses and not a.pauses_recorded:
        return all_missing("pauses and breaks were not recorded for this session, so zero cannot be assumed")
    cancel = next((u for u in a.user_actions if u.get("action") == "cancel"), None)
    end_ms = int(cancel["active_ms"]) if cancel else None
    # a trailing pause with no resume and no cancel: its real length is unknown, so it is excluded and counted
    trailing = None
    last_pause = pauses[-1] if pauses else None
    if last_pause is not None and not cancel:
        later_resume = any(e.kind == "resume" and e.seq > last_pause.seq for e in evs)
        if not later_resume:
            trailing = last_pause
    usable: list[Event] = [p for p in pauses if p is not trailing and p.duration_ms is not None and p.t_ms is not None]
    if trailing is not None:
        evs_for_summary = [e for e in evs if e is not trailing]
    else:
        evs_for_summary = list(evs)
    summ_events = [e for e in evs_for_summary if not (e.kind == "pause" and e not in usable)]
    s = summarize(summ_events, end_ms=end_ms)
    incomplete = (1 if trailing is not None else 0) + sum(1 for p in usable if p.t_ms + (p.duration_ms or 0) > s.total_ms)
    out += [ok("sim_total_ms", s.total_ms, "simulated session clock from start to the last event (or to the cancel)"),
            ok("sim_active_ms", s.active_ms, "total minus short pauses and breaks"),
            ok("sim_idle_ms", s.idle_ms, "sum of short pauses and breaks"),
            ok("break_count", s.breaks, "pauses classified 'break'"),
            ok("short_pause_count", s.short_pauses, "other recorded pauses"),
            ok("break_total_ms", s.break_total_ms),
            ok("incomplete_breaks", incomplete, "pauses whose end was not reached (cancelled during, or end unknown)")]
    out.append(ok("avg_break_ms", s.avg_break_ms) if s.avg_break_ms is not None else missing("avg_break_ms", "no breaks in this session"))
    out.append(ok("longest_break_ms", s.longest_break_ms) if s.longest_break_ms is not None else missing("longest_break_ms", "no breaks in this session"))
    durs = [seg.length_ms for seg in s.segments if seg.kind != "writing"]
    out.append(ok("pause_duration_distribution", {**summ(durs), "histogram": _hist(durs, PAUSE_EDGES)}) if durs
               else missing("pause_duration_distribution", "no pauses or breaks"))
    writing = [seg for seg in s.segments if seg.kind == "writing" and seg.length_ms > 0]
    wl = [seg.length_ms for seg in writing]
    out.append(ok("active_interval_distribution", {**summ(wl), "histogram": _hist(wl, INTERVAL_EDGES)}) if wl
               else missing("active_interval_distribution", "no active intervals"))
    ops = [e for e in evs if e.is_text_op and e.t_ms is not None]

    def in_seg(e: Event, seg: Segment, last: bool) -> bool:
        return seg.start_ms <= (e.t_ms or 0) < seg.end_ms or (last and (e.t_ms or 0) == seg.end_ms)

    per = []
    for i, seg in enumerate(writing):
        last = i == len(writing) - 1
        per.append(sum(len(e.inserted) for e in ops if in_seg(e, seg, last)))
    out.append(ok("text_added_per_active_interval", {**summ(per), "per_interval": per}, "inserted characters inside each active interval") if per
               else missing("text_added_per_active_interval", "no active intervals"))
    breaks = [seg for seg in s.segments if seg.kind == "break"]
    if not breaks:
        out.append(missing("revisions_around_breaks", "no breaks in this session"))
    else:
        before_n = after_n = 0
        before_ms = after_ms = 0
        pairs = 0
        for br in breaks:
            prev = next((w for w in writing if w.end_ms == br.start_ms), None)
            nxt = next((w for w in writing if w.start_ms == br.end_ms), None)
            if prev is not None:
                before_n += sum(1 for e in ops if e.kind in ("delete", "replace") and prev.start_ms <= (e.t_ms or 0) < prev.end_ms)
                before_ms += prev.length_ms
            if nxt is not None:
                after_n += sum(1 for e in ops if e.kind in ("delete", "replace") and nxt.start_ms <= (e.t_ms or 0) <= nxt.end_ms)
                after_ms += nxt.length_ms
            pairs += 1
        per_min = lambda n, ms: (n / (ms / 60_000)) if ms else None  # noqa: E731
        out.append(ok("revisions_around_breaks", {"breaks": pairs, "revisions_before": before_n, "revisions_after": after_n,
                                                  "active_ms_before": before_ms, "active_ms_after": after_ms,
                                                  "per_active_minute_before": per_min(before_n, before_ms),
                                                  "per_active_minute_after": per_min(after_n, after_ms)},
                      "delete/replace events in the active interval just before vs just after each break; intervals differ in length, so compare rates, and treat small counts with caution"))
    return out


register(Extractor("rhythm", "1.0.0", "Active/idle time, breaks, pause and interval distributions, revisions around breaks", "process", rhythm_metrics))
