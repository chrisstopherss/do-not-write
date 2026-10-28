"""Glue between the pure session engine and the database. No Qt. Contents of documents are never logged."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from typing import Any

from . import ENGINE_VERSION, LEGACY_COMPATIBLE_ENGINES
from .engine.breaks import plan_rhythm
from .engine.events import Event, apply_event
from .engine.rhythm import RhythmConfig
from .engine.schedule import (
    DEFAULT_GRACE_MS,
    DeadlineOutcome,
    add_checkpoints,
    apply_deadline,
    fit_to_duration,
    validate_duration,
)
from .engine.session import COMPLETED, IDLE, PAUSED, RUNNING, Clock, Session, Snapshot
from .engine.simulate import SimConfig, simulate
from .storage.db import Database, StorageError

log = logging.getLogger("wpl.service")


def deadline_json(d: DeadlineOutcome | None) -> str | None:
    return json.dumps({**asdict(d), "completion": d.completion}) if d else None


def build_plan(text: str, seed: int, sim: SimConfig, duration_ms: int, grace_ms: int = DEFAULT_GRACE_MS,
               checkpoint_ms: int | None = None, rhythm: RhythmConfig | None = None) -> tuple[list[Event], DeadlineOutcome]:
    """Plan a session. With ``rhythm`` the break scheduler is used (may raise InfeasibleSchedule); without it the
    original planner runs unchanged. Both finish inside ``duration_ms`` subject to the deadline/grace rule."""
    validate_duration(duration_ms)
    if not text.strip():
        raise ValueError("input text is empty")
    if rhythm is not None:
        plan = plan_rhythm(text, seed, sim, rhythm, duration_ms, grace_ms, checkpoint_ms)
        return plan.events, plan.deadline
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
               checkpoint_ms: int | None = None, label: str = "", rhythm: RhythmConfig | None = None,
               input_mode: str = "internal") -> str:
        events, self.deadline = build_plan(text, seed, sim, duration_ms, grace_ms, checkpoint_ms, rhythm)
        cfg: dict[str, Any] = {"sim": sim.to_dict(), "grace_ms": grace_ms, "checkpoint_ms": checkpoint_ms}
        if rhythm is not None:
            cfg["rhythm"] = rhythm.to_dict()
        if input_mode not in ("internal", "focused_window"):
            raise ValueError(f"unknown input mode {input_mode!r}")
        if input_mode != "internal":
            cfg["input_mode"] = input_mode
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

    def _action(self, action: str) -> None:
        """Record a real user action (application telemetry) with the phase it happened in."""
        assert self.session
        self.db.add_user_action(self.sid, int(self.session.active_ms()), action, self.session.clock_phase())

    def pause(self, record: bool = True) -> None:
        """Pause the session clock. ``record=False`` is for pauses the UI makes itself (e.g. while a confirmation is open)."""
        assert self.session
        if record:
            self._action("pause")
        self.session.pause()
        self._save()
        log.info("session %s paused", self.sid)

    def resume(self, record: bool = True) -> None:
        assert self.session
        self.session.resume()
        if record:
            self._action("resume")
        self._save()
        log.info("session %s resumed", self.sid)

    def cancel(self) -> None:
        assert self.session
        self._action("cancel")  # recorded first so the phase (e.g. Break) is the one the user cancelled in
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
            self._save(final_text=self.text, deadline_json=deadline_json(self.deadline))
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
        cfg = json.loads(row["config_json"])
        if cfg.get("input_mode") == "focused_window":
            raise StorageError("Focused-window sessions cannot be resumed after restart. Start a new session and reselect the target window.")
        legacy_ok = row["engine_version"] in LEGACY_COMPATIBLE_ENGINES and "rhythm" not in cfg
        if row["engine_version"] != ENGINE_VERSION and not legacy_ok:
            raise StorageError(f"session was created with engine {row['engine_version']}; cannot replay deterministically with {ENGINE_VERSION}")
        c = cls(db, clock)
        rhythm = RhythmConfig.from_dict(cfg["rhythm"]) if "rhythm" in cfg else None
        events, c.deadline = build_plan(row["source_text"], row["seed"], SimConfig.from_dict(cfg["sim"]), row["duration_ms"],
                                        cfg.get("grace_ms", DEFAULT_GRACE_MS), cfg.get("checkpoint_ms"), rhythm)
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


def analyze_session(db: Database, sid: str) -> dict[str, dict[str, Any]]:
    """Run all extractors on a stored session, persist the results as a new analysis run, return them."""
    from .analysis.features import REGISTRY, AnalysisInput, run_extractors

    row = db.get_session(sid)
    if row is None:
        raise StorageError("no such session")
    events = db.get_events(sid)
    res = run_extractors(AnalysisInput("", row["final_text"] or "", events, row["duration_ms"], row["data_origin"], row["source_text"],
                                       db.get_user_actions(sid), "rhythm" in json.loads(row["config_json"])))
    # replay starts from an empty document; source_text is the reference input that was reproduced
    aid = db.new_analysis_run({k: v.version for k, v in REGISTRY.items()})
    db.save_features(aid, sid, res)
    return db.latest_features(sid)
