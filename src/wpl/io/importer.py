"""Offline import of user-authorized revision-history exports and synthetic datasets (schema wpl.revision_history/1).

Imported content is data only: parsed with json only (never executed or deserialized into objects), size- and shape-limited, validated, and
imported atomically (all or nothing). Events are stored with source='imported'. Nothing is inferred: a snapshot-only
file yields checkpoints but no events.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..engine.events import Event, EventError, check_order, replay
from ..storage.db import Database
from .exporters import sha256_text

SCHEMA = "wpl.revision_history/1"
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_EVENTS = 200_000
MAX_SNAPSHOTS = 5_000
MAX_TEXT_CHARS = 5_000_000


class ImportRejected(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors[:5]))
        self.errors = errors


@dataclass
class ImportResult:
    session_id: str
    dataset_id: str
    events: int
    snapshots: int
    warnings: list[str] = field(default_factory=list)


def _int_or_none(v: Any, name: str, errs: list[str], i: int) -> int | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        errs.append(f"events[{i}].{name}: must be a non-negative integer or null")
        return None
    return v


def parse(raw: str) -> tuple[dict[str, Any], list[Event], list[tuple[int | None, str, str]], list[str]]:
    errs: list[str] = []
    warnings: list[str] = []
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ImportRejected([f"not valid JSON: {exc.msg} (line {exc.lineno})"]) from exc
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise ImportRejected([f"schema must be {SCHEMA!r}"])
    origin = doc.get("origin")
    if origin not in ("imported", "synthetic"):
        errs.append("origin must be 'imported' or 'synthetic'")
    if origin == "imported":
        auth = doc.get("authorization")
        if not (isinstance(auth, dict) and auth.get("attested") is True and isinstance(auth.get("statement"), str) and auth["statement"].strip()):
            errs.append("imported data requires authorization: {attested: true, statement: '<who authorized and how>'}")
    for k in ("name", "source_description"):
        if k in doc and not isinstance(doc[k], str):
            errs.append(f"{k} must be a string")
    initial = doc.get("initial_text", "")
    if not isinstance(initial, str) or len(initial) > MAX_TEXT_CHARS:
        errs.append("initial_text must be a string within the size limit")
        initial = ""
    evs_raw = doc.get("events", [])
    snaps_raw = doc.get("snapshots", [])
    if not isinstance(evs_raw, list) or len(evs_raw) > MAX_EVENTS:
        errs.append(f"events must be a list of at most {MAX_EVENTS}")
        evs_raw = []
    if not isinstance(snaps_raw, list) or len(snaps_raw) > MAX_SNAPSHOTS:
        errs.append(f"snapshots must be a list of at most {MAX_SNAPSHOTS}")
        snaps_raw = []
    if not evs_raw and not snaps_raw:
        errs.append("file contains neither events nor snapshots")
    events: list[Event] = []
    for i, r in enumerate(evs_raw):
        if not isinstance(r, dict):
            errs.append(f"events[{i}]: must be an object")
            continue
        for k in ("removed", "inserted"):
            if k in r and (not isinstance(r[k], str) or len(r[k]) > MAX_TEXT_CHARS):
                errs.append(f"events[{i}].{k}: must be a string within the size limit")
        meta = r.get("meta", {})
        if not isinstance(meta, dict):
            errs.append(f"events[{i}].meta: must be an object")
            meta = {}
        pos = r.get("pos")
        if pos is not None and (isinstance(pos, bool) or not isinstance(pos, int)):
            errs.append(f"events[{i}].pos: must be an integer or null")
            pos = None
        ev = Event(seq=i, kind=str(r.get("kind")), source="imported", t_ms=_int_or_none(r.get("t_ms"), "t_ms", errs, i),
                   pos=pos, removed=r.get("removed", "") if isinstance(r.get("removed", ""), str) else "",
                   inserted=r.get("inserted", "") if isinstance(r.get("inserted", ""), str) else "",
                   duration_ms=_int_or_none(r.get("duration_ms"), "duration_ms", errs, i), meta=meta)
        try:
            ev.validate()
        except EventError as exc:
            errs.append(f"events[{i}]: {exc}")
        events.append(ev)
    snaps: list[tuple[int | None, str, str]] = []
    for i, s in enumerate(snaps_raw):
        if not (isinstance(s, dict) and isinstance(s.get("text"), str) and len(s["text"]) <= MAX_TEXT_CHARS):
            errs.append(f"snapshots[{i}]: needs a string 'text' within the size limit")
            continue
        t = s.get("t_ms")
        if t is not None and (isinstance(t, bool) or not isinstance(t, int) or t < 0):
            errs.append(f"snapshots[{i}].t_ms: must be a non-negative integer or null")
            t = None
        snaps.append((t, str(s.get("label", ""))[:200], s["text"]))
    if not errs and events:
        problems = check_order(events) + replay(events, initial).problems
        if problems:
            errs.extend(f"inconsistent event data: {p}" for p in problems[:10])
        if any(e.t_ms is None for e in events):
            warnings.append("some events have no timestamp; time-based metrics will show 'Not recorded'")
    if errs:
        raise ImportRejected(errs)
    return doc, events, snaps, warnings


def import_file(db: Database, path: str | Path) -> ImportResult:
    p = Path(path)
    if p.suffix.lower() != ".json":
        raise ImportRejected(["only .json files are supported"])
    size = p.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ImportRejected([f"file is {size} bytes; limit is {MAX_FILE_BYTES}"])
    try:
        raw = p.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ImportRejected(["file is not valid UTF-8"]) from exc
    return import_text(db, raw)


def import_text(db: Database, raw: str) -> ImportResult:
    doc, events, snaps, warnings = parse(raw)
    origin = doc["origin"]
    name = doc.get("name") or "Imported dataset"
    did = sid = None
    try:
        did = db.add_dataset(name, origin, SCHEMA, doc.get("source_description", ""), sha256_text(raw))
        initial = doc.get("initial_text", "")
        final = replay(events, initial).text if events else (snaps[-1][2] if snaps else "")
        sid = db.create_session(source_text=initial, seed=None, duration_ms=None, config={"import": {"schema": SCHEMA}},
                                label=name, status="completed", origin=origin, dataset_id=did)
        if events:
            db.add_events(sid, events)
        for i, (t, label, text) in enumerate(snaps):
            db.add_checkpoint_snapshot(sid, i, t, label, text)
        db.update_session(sid, final_text=final)
    except Exception:
        # each db helper commits, so undo what was written to keep the import all-or-nothing
        if sid:
            db.delete_session(sid)
        if did:
            db.conn.execute("DELETE FROM datasets WHERE id=?", (did,))
            db.conn.commit()
        raise
    return ImportResult(sid, did, len(events), len(snaps), warnings)
