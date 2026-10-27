"""DocBuilder: tracks a document while generators emit events. Times are raw units (ms-like) and are
rescaled to the requested duration afterwards by ``fit_to_duration``."""
from __future__ import annotations

from typing import Any

from .events import Event


class DocBuilder:
    def __init__(self, initial: str = "", source: str = "simulated") -> None:
        self.initial = initial
        self.text = initial
        self.source = source
        self.events: list[Event] = []
        self.t = 0.0
        self.sentence = 0  # sentence index currently being produced, stamped into meta

    def _emit(self, kind: str, pos: int | None, removed: str, inserted: str, meta: dict[str, Any] | None,
              duration: float | None = None) -> Event:
        m = dict(meta or {})
        m.setdefault("sentence", self.sentence)
        ev = Event(seq=len(self.events), kind=kind, source=self.source, t_ms=int(round(self.t)), pos=pos,
                   removed=removed, inserted=inserted,
                   duration_ms=None if duration is None else int(round(duration)), meta=m)
        self.events.append(ev)
        return ev

    def advance(self, dt: float) -> None:
        self.t += max(0.0, dt)

    def insert(self, pos: int, s: str, kind: str = "insert", meta: dict[str, Any] | None = None) -> None:
        self._emit(kind, pos, "", s, meta)
        self.text = self.text[:pos] + s + self.text[pos:]

    def delete(self, pos: int, n: int, meta: dict[str, Any] | None = None) -> None:
        removed = self.text[pos:pos + n]
        self._emit("delete", pos, removed, "", meta)
        self.text = self.text[:pos] + self.text[pos + n:]

    def replace(self, pos: int, n: int, s: str, meta: dict[str, Any] | None = None) -> None:
        removed = self.text[pos:pos + n]
        self._emit("replace", pos, removed, s, meta)
        self.text = self.text[:pos] + s + self.text[pos + n:]

    def pause(self, ms: float, meta: dict[str, Any] | None = None) -> None:
        self._emit("pause", None, "", "", meta, duration=ms)
        self.t += ms
        self._emit("resume", None, "", "", meta)
