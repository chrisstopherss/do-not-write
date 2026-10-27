"""Glue between the pure session engine and the database. No Qt. Contents of documents are never logged."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from typing import Any

from . import ENGINE_VERSION
from .engine.events import Event, apply_event
from .engine.schedule import DEFAULT_GRACE_MS, DeadlineOutcome, add_checkpoints, apply_deadline, fit_to_duration, validate_duration
from .engine.session import COMPLETED, IDLE, PAUSED, RUNNING, Clock, Session, Snapshot
from .engine.simulate import SimConfig, simulate
from .storage.db import Database, StorageError

log = logging.getLogger("wpl.service")


def build_plan(text: str, seed: int, sim: SimConfig, duration_ms: int, grace_ms: int = DEFAULT_GRACE_MS,
               checkpoint_ms: int | None = None) -> tuple[list[Event], DeadlineOutcome]:
    validate_duration(duration_ms)
    if not text.strip():
        raise ValueError("input text is empty")
    res = simulate(text, seed, sim)
    events = fit_to_duration(res.events, duration_ms)
    events, outcome = apply_deadline(events, duration_ms, grace_ms)
    return add_checkpoints(events, checkpoint_ms), outcome


class SessionController:
    def __init__(self, db: Database, clock: Clock | None = None) -> None:
        self.db = db
        self.clock = clock
        self.sid = ""
        self.session: Session | None = None
        self.text = ""
        self.deadline: DeadlineOutcome | None = None
        self._source = ""

    @property
    def state(self) -> str:
        return self.session.state if self.session else IDLE

    def create(self, text: str, duration_ms: int, seed: int, sim: SimConfig, grace_ms: int = DEFAULT_GRACE_MS,
               checkpoint_ms: int | None = None, label: str = "") -> str:
        events, self.deadline = build_plan(text, seed, sim, duration_ms, grace_ms, checkpoint_ms)
        cfg = {"sim": sim.to_dict(), "grace_ms": grace_ms, "checkpoint_ms": checkpoint_ms}
        self.sid = self.db.create_session(source_text=text, seed=seed, duration_ms=duration_ms, config=cfg, label=label,
                                          status="idle", origin="simulated")
        self.session = Session(events, duration_ms, self.clock)
        self.text = ""
        self._source = text
        log.info("session created id=%s events=%d duration_ms=%d", self.sid, len(events), duration_ms)
        return self.sid

    def _save(self, **extra: Any) -> None:
        assert self.session
        self.db.update_session(self.sid, status=self.session.state, progress_cursor=self.session.cursor,
                               active_ms=int(self.session.active_ms()), **extra)

    def start(self) -> None:
        assert self.session
        self.session.start()
        self._save()
        log.info("session %s started", self.sid)

    def pause(self) -> None:
        assert self.session
        self.session.pause()
        self._save()
        log.info("session %s paused", self.sid)

    def resume(self) -> None:
        assert self.session
        self.session.resume()
        self._save()
        log.info("session %s resumed", self.sid)

    def cancel(self) -> None:
        assert self.session
        self.session.cancel()
        self._save(final_text=self.text)
        log.info("session %s cancelled", self.sid)

    def poll(self) -> list[Event]:
        assert self.session
        due = self.session.poll()
        if due:
            for e in due:
                self.text = apply_event(self.text, e)
            self.db.add_events(self.sid, due)
        if self.session.state == COMPLETED:
            self._save(final_text=self.text, deadline_json=json.dumps(asdict(self.deadline)) if self.deadline else None)
            log.info("session %s completed", self.sid)
        elif due:
            self._save()
        return due

    @classmethod
    def recover(cls, db: Database, sid: str, clock: Clock | None = None) -> SessionController:
        """Rebuild an interrupted session from its seed/config and stored progress. Comes back PAUSED."""
        row = db.get_session(sid)
        if row is None:
            raise StorageError("no such session")
        if row["engine_version"] != ENGINE_VERSION:
            raise StorageError(f"session was created with engine {row['engine_version']}; cannot replay deterministically with {ENGINE_VERSION}")
        cfg = json.loads(row["config_json"])
        c = cls(db, clock)
        events, c.deadline = build_plan(row["source_text"], row["seed"], SimConfig.from_dict(cfg["sim"]), row["duration_ms"],
                                        cfg.get("grace_ms", DEFAULT_GRACE_MS), cfg.get("checkpoint_ms"))
        stored = db.get_events(sid)
        if [(e.kind, e.t_ms, e.pos, e.removed, e.inserted) for e in stored] != \
           [(e.kind, e.t_ms, e.pos, e.removed, e.inserted) for e in events[:len(stored)]]:
            raise StorageError("stored events do not match the regenerated plan; refusing to resume")
        c.sid = sid
        c.session = Session.restore(events, row["duration_ms"], Snapshot(len(stored), float(row["active_ms"]), PAUSED), clock)
        text = ""
        for e in stored:
            text = apply_event(text, e)
        c.text = text
        c._source = row["source_text"]
        db.update_session(sid, status=PAUSED)
        return c


_ = RUNNING
