"""Event model and replay.

Every event carries a ``source`` so simulated, captured and imported data can never be confused:
  simulated  - produced by this application's simulation engine (synthetic)
  captured   - recorded by the application from real input in its own editor
  imported   - loaded from a user-authorized external dataset
``t_ms`` is milliseconds from session start on the active clock, or None when not recorded.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SOURCES = ("simulated", "captured", "imported")
TEXT_KINDS = ("insert", "delete", "replace")
OTHER_KINDS = ("cursor_move", "pause", "resume", "checkpoint", "interruption", "paste", "import", "external")
KINDS = TEXT_KINDS + OTHER_KINDS
# paste / import carry text and are applied like an insert (explicitly recorded as such by the producer).
TEXT_APPLYING = ("insert", "delete", "replace", "paste", "import")


class EventError(ValueError):
    pass


@dataclass(frozen=True)
class Event:
    seq: int
    kind: str
    source: str
    t_ms: int | None = None
    pos: int | None = None
    removed: str = ""
    inserted: str = ""
    duration_ms: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if self.kind not in KINDS:
            raise EventError(f"unknown kind {self.kind!r}")
        if self.source not in SOURCES:
            raise EventError(f"unknown source {self.source!r}")
        if self.t_ms is not None and (not isinstance(self.t_ms, int) or self.t_ms < 0):
            raise EventError("t_ms must be a non-negative int or None")
        if self.duration_ms is not None and (not isinstance(self.duration_ms, int) or self.duration_ms < 0):
            raise EventError("duration_ms must be a non-negative int or None")
        if self.kind in TEXT_APPLYING:
            if self.pos is None or self.pos < 0:
                raise EventError("text event needs pos >= 0")
            if self.kind in ("insert", "paste", "import") and (self.removed or not self.inserted):
                raise EventError(f"{self.kind} needs inserted text and no removed text")
            if self.kind == "delete" and (self.inserted or not self.removed):
                raise EventError("delete needs removed text and no inserted text")
            if self.kind == "replace" and not (self.removed and self.inserted):
                raise EventError("replace needs both removed and inserted text")

    @property
    def is_text_op(self) -> bool:
        return self.kind in TEXT_APPLYING


def check_order(events: list[Event]) -> list[str]:
    """Return problems: seq must be strictly increasing; recorded t_ms must be non-decreasing."""
    problems: list[str] = []
    last_t: int | None = None
    for i, e in enumerate(events):
        if i and e.seq <= events[i - 1].seq:
            problems.append(f"seq {e.seq} not greater than previous {events[i - 1].seq}")
        if e.t_ms is not None:
            if last_t is not None and e.t_ms < last_t:
                problems.append(f"t_ms goes backwards at seq {e.seq} ({e.t_ms} < {last_t})")
            last_t = e.t_ms
    return problems


@dataclass
class ReplayResult:
    text: str
    origin: list[int]  # per character: seq of the event that created it, or -1 for initial text
    problems: list[str]
    lengths: list[tuple[int | None, int, int]]  # (t_ms, seq, text length) after each text-applying event


def replay(events: list[Event], initial_text: str = "", upto_seq: int | None = None) -> ReplayResult:
    """Apply text events in order. An event whose ``removed`` text does not match the document is
    skipped and reported as a problem rather than guessed at."""
    doc = list(initial_text)
    origin = [-1] * len(doc)
    problems: list[str] = []
    lengths: list[tuple[int | None, int, int]] = []
    for e in events:
        if upto_seq is not None and e.seq > upto_seq:
            break
        if not e.is_text_op:
            continue
        try:
            e.validate()
        except EventError as exc:
            problems.append(f"seq {e.seq}: {exc}")
            continue
        assert e.pos is not None
        end = e.pos + len(e.removed)
        if e.pos > len(doc) or "".join(doc[e.pos:end]) != e.removed:
            problems.append(f"seq {e.seq}: removed text does not match document at pos {e.pos}")
            continue
        new_chars = list(e.inserted)
        doc[e.pos:end] = new_chars
        origin[e.pos:end] = [e.seq] * len(new_chars)
        lengths.append((e.t_ms, e.seq, len(doc)))
    return ReplayResult("".join(doc), origin, problems, lengths)


def apply_event(text: str, e: Event) -> str:
    """Apply one text event to ``text``; non-text events return it unchanged. Raises EventError on mismatch."""
    if not e.is_text_op:
        return text
    e.validate()
    assert e.pos is not None
    end = e.pos + len(e.removed)
    if e.pos > len(text) or text[e.pos:end] != e.removed:
        raise EventError(f"seq {e.seq}: removed text does not match document at pos {e.pos}")
    return text[:e.pos] + e.inserted + text[end:]
