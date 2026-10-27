"""Revision explorer model: versions of a document derived from recorded checkpoints, never invented."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..engine.events import Event, replay
from ..engine.text import word_count
from .diff import summarize_ops, word_diff


@dataclass
class Version:
    label: str
    kind: str                      # initial | checkpoint | final
    t_ms: int | None
    seq: int | None
    text: str
    chars: int
    words: int
    data_source: str               # simulated | captured | imported
    elapsed_since_prev_ms: int | None
    added_chars: int | None        # vs previous version
    removed_chars: int | None

    def to_dict(self, with_text: bool = False) -> dict[str, Any]:
        d = {k: getattr(self, k) for k in ("label", "kind", "t_ms", "seq", "chars", "words", "data_source",
                                           "elapsed_since_prev_ms", "added_chars", "removed_chars")}
        if with_text:
            d["text"] = self.text
        return d


def build_versions(initial_text: str, events: list[Event], snapshots: list[tuple[int | None, str, str]] | None = None,
                   snapshot_source: str = "imported", final_text: str | None = None) -> list[Version]:
    """Versions = initial text, each recorded checkpoint, final text.

    ``snapshots`` are (t_ms, label, text) for data that only has snapshots (imported). When events exist,
    checkpoint events define versions and text is reconstructed by replay (no stored duplicates)."""
    src = events[0].source if events else snapshot_source
    raw: list[tuple[str, str, int | None, int | None, str, str]] = []
    raw.append(("Initial", "initial", 0 if events else None, None, initial_text, src))
    for e in events:
        if e.kind == "checkpoint":
            idx = e.meta.get("index", "")
            raw.append((f"Checkpoint {idx}".strip(), "checkpoint", e.t_ms, e.seq, replay(events, initial_text, e.seq).text, e.source))
    for t, label, text in snapshots or []:
        raw.append((label or "Snapshot", "checkpoint", t, None, text, snapshot_source))
    if events or final_text is not None:
        ft = replay(events, initial_text).text if events else (final_text or "")
        last_t = max((e.t_ms for e in events if e.t_ms is not None), default=None)
        raw.append(("Final", "final", last_t, events[-1].seq if events else None, ft, src))
    out: list[Version] = []
    prev: Version | None = None
    for label, kind, t, seq, text, source in raw:
        elapsed = None if prev is None or prev.t_ms is None or t is None else t - prev.t_ms
        if prev is None:
            added = removed = None
        else:
            s = summarize_ops(word_diff(prev.text, text))
            added, removed = s["added_chars"], s["removed_chars"]
        v = Version(label, kind, t, seq, text, len(text), word_count(text), source, elapsed, added, removed)
        out.append(v)
        prev = v
    return out
