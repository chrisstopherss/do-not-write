"""Data shaping for the Writing Process Lab dashboard. Everything here reads stored data; nothing is invented."""
from __future__ import annotations

import json
import statistics
from typing import Any

from ..analysis.features import REGISTRY
from ..engine.events import Event
from ..service import analyze_session
from ..storage.db import Database
from .conditions import ConditionParams, display_name

NR = None  # sentinel meaning "not recorded"


def feature_value(feats: dict[str, dict[str, Any]], key: str) -> Any:
    f = feats.get(key)
    return f["value"] if f and f["status"] == "ok" else NR


def ensure_features(db: Database, sid: str) -> dict[str, dict[str, Any]]:
    """Stored features if present and from current extractor versions, otherwise computed from stored events and saved."""
    f = db.latest_features(sid)
    cur = {f"{k}": v.version for k, v in REGISTRY.items()}
    if f and all(v["version"] == cur.get(k.split(".")[0], v["version"]) for k, v in f.items()):
        return f
    return analyze_session(db, sid)


def session_row(db: Database, sid: str) -> dict[str, Any]:
    s = db.get_session(sid)
    assert s is not None
    f = ensure_features(db, sid)
    g = lambda k: feature_value(f, k)
    events = db.get_events(sid)
    timed = sum(1 for e in events if e.t_ms is not None)
    problems = g("process_events.replay_problems")
    if not events:
        completeness = "No events recorded"
    elif timed == len(events):
        completeness = "All events timestamped"
    elif timed == 0:
        completeness = "No timestamps"
    else:
        completeness = f"{timed}/{len(events)} timestamped"
    if not events:
        validation = "No event data"
    elif problems == 0:
        validation = "OK"
    elif problems is None:
        validation = "Not checked"
    else:
        validation = f"{problems} issue(s)"
    cfg = json.loads(s["config_json"])
    return {"session_id": sid, "origin": s["data_origin"], "status": s["status"], "seed": s["seed"], "duration_ms": s["duration_ms"],
            "label": s["label"], "words_in": g("text_basic.initial_words") if s["data_origin"] == "imported" else None,
            "words_source": _words(s["source_text"]), "words_final": g("text_basic.words"),
            "insertions": g("process_events.insertions"), "deletions": g("process_events.deletions"),
            "replacements": g("process_events.replacements"), "checkpoints": g("process_events.checkpoint_count"),
            "revisions": g("process_events.revision_events"), "pauses": g("process_events.pause_count"),
            "events": len(events), "validation": validation, "completeness": completeness,
            "condition": cfg.get("params", {}).get("code", ""), "rep": cfg.get("rep")}


def _words(t: str) -> int:
    from ..engine.text import word_count
    return word_count(t)


def experiment_rows(db: Database, eid: str) -> list[dict[str, Any]]:
    out = []
    for r in db.experiment_runs(eid):
        if r["session_id"]:
            row = session_row(db, r["session_id"])
            cond = next((c for c in db.experiment_conditions(eid) if c["id"] == r["condition_id"]), None)
            if cond:
                row["condition"] = display_name(ConditionParams.from_dict(json.loads(cond["params_json"])))
            row["rep"] = r["rep"] + 1
            out.append(row)
    return out


def summary_counts(db: Database) -> dict[str, int]:
    c = db.counts()
    done = db.conn.execute("SELECT COUNT(*) FROM sessions WHERE status='completed'").fetchone()[0]
    return {"experiments": c["experiments"], "completed_sessions": done, "events": c["events"], "datasets": c["datasets"]}


# ------------------------------------------------------------------ chart data
def growth_points(events: list[Event], initial: str = "") -> list[tuple[float, float]]:
    from ..engine.events import replay
    r = replay(events, initial)
    return [(float(t), float(n)) for t, _s, n in r.lengths if t is not None]


def kind_counts(events: list[Event]) -> tuple[list[str], list[float | None]]:
    ins = sum(1 for e in events if e.kind in ("insert", "paste", "import"))
    dele = sum(1 for e in events if e.kind == "delete")
    rep = sum(1 for e in events if e.kind == "replace")
    return ["Insertions", "Deletions", "Replacements"], [float(ins), float(dele), float(rep)]


REV_EDGES = [1, 2, 4, 8, 16, 32, 64, 128]


def revision_size_hist(events: list[Event]) -> tuple[list[str], list[float | None]]:
    sizes = [len(e.removed) for e in events if e.kind in ("delete", "replace")]
    labels, vals = [], []
    for lo, hi in zip(REV_EDGES, REV_EDGES[1:] + [None], strict=True):
        labels.append(f"{lo}+" if hi is None else str(lo) if hi - 1 == lo else f"{lo}-{hi - 1}")
        vals.append(float(sum(1 for s in sizes if s >= lo and (hi is None or s < hi))))
    return labels, vals if sizes else [None] * len(labels)


INTERVAL_EDGES = [0, 100, 250, 500, 1000, 2000, 5000, 15000, 60000]


def interval_hist(events: list[Event]) -> tuple[list[str], list[float | None]]:
    ts = [e.t_ms for e in events if e.is_text_op and e.t_ms is not None]
    gaps = [b - a for a, b in zip(ts, ts[1:], strict=False)]
    labels = [f"{lo}+" if hi is None else str(lo) for lo, hi in zip(INTERVAL_EDGES, INTERVAL_EDGES[1:] + [None], strict=True)]  # bin lower bounds
    if not gaps:
        return labels, [None] * len(labels)
    return labels, [float(sum(1 for g in gaps if g >= lo and (hi is None or g < hi))) for lo, hi in zip(INTERVAL_EDGES, INTERVAL_EDGES[1:] + [None], strict=True)]


METRIC_CHOICES = {"Total events": "process_events.total_events", "Insertions": "process_events.insertions",
                  "Deletions": "process_events.deletions", "Replacements": "process_events.replacements",
                  "Revision events": "process_events.revision_events", "Inserted characters": "process_events.inserted_chars",
                  "Removed characters": "process_events.removed_chars", "Checkpoints": "process_events.checkpoint_count",
                  "Pauses": "process_events.pause_count", "Largest single-event share": "process_events.largest_single_event_share",
                  "Final words": "text_basic.words"}


def condition_means(db: Database, eid: str, metric_key: str) -> tuple[list[str], list[float | None], dict[str, int]]:
    """Mean of a stored feature per condition. Runs where the feature is not recorded are excluded (and counted)."""
    by: dict[str, list[float]] = {}
    missing: dict[str, int] = {}
    order: list[str] = []
    for row in experiment_rows(db, eid):
        c = row["condition"]
        if c not in order:
            order.append(c)
        f = ensure_features(db, row["session_id"])
        v = feature_value(f, metric_key)
        if isinstance(v, (int, float)):
            by.setdefault(c, []).append(float(v))
        else:
            missing[c] = missing.get(c, 0) + 1
    return order, [statistics.fmean(by[c]) if c in by else None for c in order], missing
