"""Experiment orchestration: conditions x repetitions -> stored sessions, events and features."""
from __future__ import annotations

import json
from dataclasses import asdict

from .. import ENGINE_VERSION
from ..analysis.features import REGISTRY, AnalysisInput, run_extractors
from ..storage.db import Database
from .conditions import ConditionParams, generate, sample_text

MAX_REPS = 200


def derive_seed(base_seed: int, cond_index: int, rep: int) -> int:
    """Stable, documented per-run seed derivation."""
    return (base_seed * 1_000_003 + cond_index * 10_007 + rep) & 0x7FFFFFFF


def run_experiment(db: Database, label: str, conditions: list[ConditionParams], reps: int, base_seed: int,
                   notes: str = "", expected: str = "", is_example: bool = False) -> str:
    if not label.strip():
        raise ValueError("experiment label is required")
    if not 1 <= reps <= MAX_REPS:
        raise ValueError(f"repetitions must be within [1, {MAX_REPS}]")
    if not conditions:
        raise ValueError("at least one condition is required")
    for c in conditions:
        c.validate()
    eid = db.create_experiment(label, notes, expected, is_example)
    aid = db.new_analysis_run({k: v.version for k, v in REGISTRY.items()})
    for ci, params in enumerate(conditions):
        cid = db.add_condition(eid, params.code, params.to_dict(), ground_truth="")
        for rep in range(reps):
            seed = derive_seed(base_seed, ci, rep)
            out = generate(params, seed)
            cfg = {"params": params.to_dict(), "ground_truth": out.ground_truth, "rep": rep,
                   "stats": {k: asdict(v) for k, v in out.stats.items()}, "engine_version": ENGINE_VERSION}
            sid = db.create_session(source_text=params.text, seed=seed, duration_ms=params.duration_ms, config=cfg,
                                    label=f"{label} / {params.code} / rep {rep + 1}", status="completed", origin="simulated")
            db.add_events(sid, out.events)
            db.update_session(sid, final_text=out.final_text, status="completed", progress_cursor=len(out.events),
                              deadline_json=json.dumps(asdict(out.deadline)))
            feats = run_extractors(AnalysisInput("", out.final_text, out.events, params.duration_ms, "simulated", params.text))
            db.save_features(aid, sid, feats)
            db.add_run(eid, cid, sid, rep, seed, "completed")
    db.conn.execute("UPDATE experiments SET status='completed' WHERE id=?", (eid,))
    db.conn.commit()
    return eid


def create_example_experiment(db: Database) -> str:
    """Synthetic example: bundled sample text, all five conditions, 5 reps each (explicitly labeled example)."""
    text = sample_text()
    conds = [ConditionParams(code=c, text=text, duration_ms=30 * 60_000) for c in "ABCDE"]
    return run_experiment(db, "Example: five workflows on synthetic text", conds, reps=5, base_seed=2024,
                          notes="Bundled example. Text and events are synthetic; nothing here was captured from a person.",
                          expected="Condition C should show one very large insertion; D and E should show more revision events.",
                          is_example=True)
