"""SQLite persistence. Parameterized queries only; versioned migrations tracked in PRAGMA user_version."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from .. import APP_VERSION, ENGINE_VERSION
from ..engine.events import Event


class StorageError(RuntimeError):
    pass


class DatabaseCorrupt(StorageError):
    pass


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id() -> str:
    return uuid.uuid4().hex


def _migrations() -> list[tuple[int, str]]:
    out = []
    for f in resources.files("wpl.storage").joinpath("migrations").iterdir():
        if f.name.endswith(".sql"):
            out.append((int(f.name.split("_", 1)[0]), f.read_text(encoding="utf-8")))
    return sorted(out)


LATEST_VERSION = max(v for v, _ in _migrations())


def default_data_dir() -> Path:
    import os

    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "WritingProcessLab"


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        try:
            self.conn = sqlite3.connect(self.path)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys=ON")
            ok = self.conn.execute("PRAGMA quick_check").fetchone()[0]
            if ok != "ok":
                raise DatabaseCorrupt(f"integrity check failed: {ok}")
        except sqlite3.OperationalError as exc:  # cannot open, read-only location, disk full, locked
            raise StorageError(f"cannot open database: {exc}") from exc
        except sqlite3.DatabaseError as exc:  # not a database / malformed
            raise DatabaseCorrupt(str(exc)) from exc
        self.migrate()

    # -- migrations
    def version(self) -> int:
        return int(self.conn.execute("PRAGMA user_version").fetchone()[0])

    def migrate(self, target: int | None = None) -> None:
        target = target or LATEST_VERSION
        for v, sql in _migrations():
            if self.version() < v <= target:
                try:
                    self.conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version={v};\nCOMMIT;")
                except sqlite3.Error as exc:
                    self.conn.rollback()
                    raise StorageError(f"migration {v} failed: {exc}") from exc
        self.conn.execute("INSERT OR REPLACE INTO schema_meta(key,value) VALUES('app_version',?)", (APP_VERSION,))
        self.conn.execute("INSERT OR REPLACE INTO schema_meta(key,value) VALUES('engine_version',?)", (ENGINE_VERSION,))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # -- sessions
    def create_session(self, *, source_text: str, seed: int | None, duration_ms: int | None, config: dict[str, Any],
                       label: str = "", status: str = "idle", origin: str = "simulated",
                       dataset_id: str | None = None, session_id: str | None = None) -> str:
        sid = session_id or new_id()
        self.conn.execute(
            "INSERT INTO sessions(id,created_at,label,status,duration_ms,seed,config_json,engine_version,app_version,"
            "source_text,data_origin,dataset_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, now_iso(), label, status, duration_ms, seed, json.dumps(config, sort_keys=True), ENGINE_VERSION,
             APP_VERSION, source_text, origin, dataset_id))
        self.conn.commit()
        return sid

    def update_session(self, sid: str, **fields: Any) -> None:
        allowed = {"status", "final_text", "progress_cursor", "active_ms", "deadline_json", "label"}
        bad = set(fields) - allowed
        if bad:
            raise StorageError(f"cannot update fields {sorted(bad)}")  # source_text is immutable by design
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE sessions SET {cols} WHERE id=?", (*fields.values(), sid))
        self.conn.commit()

    def get_session(self, sid: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()

    def list_sessions(self, origin: str | None = None) -> list[sqlite3.Row]:
        if origin:
            return self.conn.execute("SELECT * FROM sessions WHERE data_origin=? ORDER BY created_at DESC, rowid DESC", (origin,)).fetchall()
        return self.conn.execute("SELECT * FROM sessions ORDER BY created_at DESC, rowid DESC").fetchall()

    def delete_session(self, sid: str) -> None:
        self.conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
        self.conn.commit()

    def mark_interrupted(self) -> int:
        """Called at startup: any session left 'running' by an unclean exit is marked interrupted."""
        cur = self.conn.execute("UPDATE sessions SET status='interrupted' WHERE status IN ('running','paused')")
        self.conn.commit()
        return cur.rowcount

    # -- events
    def add_events(self, sid: str, events: list[Event]) -> None:
        self.conn.executemany(
            "INSERT INTO events(session_id,seq,t_ms,kind,source,pos,removed,inserted,duration_ms,meta_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            [(sid, e.seq, e.t_ms, e.kind, e.source, e.pos, e.removed, e.inserted, e.duration_ms,
              json.dumps(e.meta, sort_keys=True)) for e in events])
        self.conn.commit()

    def get_events(self, sid: str) -> list[Event]:
        rows = self.conn.execute("SELECT * FROM events WHERE session_id=? ORDER BY seq", (sid,)).fetchall()
        return [Event(r["seq"], r["kind"], r["source"], r["t_ms"], r["pos"], r["removed"], r["inserted"],
                      r["duration_ms"], json.loads(r["meta_json"])) for r in rows]

    def add_checkpoint_snapshot(self, sid: str, idx: int, t_ms: int | None, label: str, text: str) -> None:
        self.conn.execute("INSERT INTO checkpoints(session_id,idx,t_ms,label,text) VALUES(?,?,?,?,?)", (sid, idx, t_ms, label, text))
        self.conn.commit()

    def get_checkpoint_snapshots(self, sid: str) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM checkpoints WHERE session_id=? ORDER BY idx", (sid,)).fetchall()

    # -- user actions (real application telemetry)
    def add_user_action(self, sid: str, active_ms: int, action: str, phase: str) -> None:
        n = self.conn.execute("SELECT COUNT(*) FROM user_actions WHERE session_id=?", (sid,)).fetchone()[0]
        self.conn.execute("INSERT INTO user_actions(session_id,seq,active_ms,action,phase) VALUES(?,?,?,?,?)", (sid, n, active_ms, action, phase))
        self.conn.commit()

    def get_user_actions(self, sid: str) -> list[dict]:
        rows = self.conn.execute("SELECT seq,active_ms,action,phase FROM user_actions WHERE session_id=? ORDER BY seq", (sid,)).fetchall()
        return [dict(r) for r in rows]

    # -- experiments
    def create_experiment(self, label: str, notes: str = "", expected: str = "", is_example: bool = False) -> str:
        eid = new_id()
        self.conn.execute("INSERT INTO experiments(id,label,created_at,notes,expected,is_example) VALUES(?,?,?,?,?,?)",
                          (eid, label, now_iso(), notes, expected, int(is_example)))
        self.conn.commit()
        return eid

    def add_condition(self, eid: str, code: str, params: dict[str, Any], ground_truth: str = "") -> str:
        cid = new_id()
        self.conn.execute("INSERT INTO conditions(id,experiment_id,code,params_json,ground_truth) VALUES(?,?,?,?,?)",
                          (cid, eid, code, json.dumps(params, sort_keys=True), ground_truth))
        self.conn.commit()
        return cid

    def add_run(self, eid: str, cid: str, sid: str | None, rep: int, seed: int, status: str) -> str:
        rid = new_id()
        self.conn.execute("INSERT INTO runs(id,experiment_id,condition_id,session_id,rep,seed,status) VALUES(?,?,?,?,?,?,?)",
                          (rid, eid, cid, sid, rep, seed, status))
        self.conn.commit()
        return rid

    def list_experiments(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM experiments ORDER BY created_at DESC, rowid DESC").fetchall()

    def experiment_conditions(self, eid: str) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM conditions WHERE experiment_id=? ORDER BY code", (eid,)).fetchall()

    def experiment_runs(self, eid: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT r.*, c.code AS code, c.ground_truth AS ground_truth FROM runs r JOIN conditions c ON c.id=r.condition_id "
            "WHERE r.experiment_id=? ORDER BY c.code, r.rep", (eid,)).fetchall()

    def delete_experiment(self, eid: str) -> None:
        """Deletes the experiment and the sessions created for its runs."""
        sids = [r["session_id"] for r in self.conn.execute("SELECT session_id FROM runs WHERE experiment_id=?", (eid,)) if r["session_id"]]
        for s in sids:
            self.conn.execute("DELETE FROM sessions WHERE id=?", (s,))
        self.conn.execute("DELETE FROM experiments WHERE id=?", (eid,))
        self.conn.commit()

    # -- features / evaluation / datasets
    def new_analysis_run(self, extractor_versions: dict[str, str]) -> str:
        aid = new_id()
        self.conn.execute("INSERT INTO analysis_runs(id,created_at,engine_version,extractors_json) VALUES(?,?,?,?)",
                          (aid, now_iso(), ENGINE_VERSION, json.dumps(extractor_versions, sort_keys=True)))
        self.conn.commit()
        return aid

    def save_features(self, aid: str, sid: str, results: dict[str, dict[str, Any]]) -> None:
        rows = []
        for ex_id, res in results.items():
            for f in res["features"]:
                rows.append((aid, sid, ex_id, res["version"], f["name"],
                             json.dumps(f["value"]) if f["status"] == "ok" else None, f["status"], f.get("note", "")))
        self.conn.executemany("INSERT OR REPLACE INTO features(analysis_run_id,session_id,extractor_id,extractor_version,"
                              "name,value_json,status,note) VALUES(?,?,?,?,?,?,?,?)", rows)
        self.conn.commit()

    def latest_features(self, sid: str) -> dict[str, dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT f.* FROM features f JOIN analysis_runs a ON a.id=f.analysis_run_id WHERE f.session_id=? "
            "AND a.rowid=(SELECT MAX(a2.rowid) FROM analysis_runs a2 JOIN features f2 ON f2.analysis_run_id=a2.id WHERE f2.session_id=?)",
            (sid, sid)).fetchall()
        return {f"{r['extractor_id']}.{r['name']}": {"value": json.loads(r["value_json"]) if r["value_json"] is not None else None,
                                                    "status": r["status"], "note": r["note"], "version": r["extractor_version"]} for r in rows}

    def save_evaluation(self, eid: str, method_id: str, method_version: str, split: dict[str, Any],
                        tp: int, fp: int, tn: int, fn: int, metrics: dict[str, Any]) -> str:
        rid = new_id()
        self.conn.execute("INSERT INTO evaluation_results VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                          (rid, eid, now_iso(), method_id, method_version, json.dumps(split), tp, fp, tn, fn, json.dumps(metrics)))
        self.conn.commit()
        return rid

    def list_evaluations(self, eid: str) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM evaluation_results WHERE experiment_id=? ORDER BY created_at DESC, rowid DESC", (eid,)).fetchall()

    def add_dataset(self, name: str, origin: str, schema_version: str, source_description: str, sha256: str) -> str:
        did = new_id()
        self.conn.execute("INSERT INTO datasets VALUES(?,?,?,?,?,?,?)", (did, name, origin, now_iso(), schema_version, source_description, sha256))
        self.conn.commit()
        return did

    def delete_dataset(self, did: str) -> None:
        self.conn.execute("DELETE FROM sessions WHERE dataset_id=?", (did,))
        self.conn.execute("DELETE FROM datasets WHERE id=?", (did,))
        self.conn.commit()

    def list_datasets(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM datasets ORDER BY created_at DESC, rowid DESC").fetchall()

    # -- settings
    def get_setting(self, key: str, default: str = "") -> str:
        r = self.conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES(?,?)", (key, value))
        self.conn.commit()

    # -- retention / deletion
    def counts(self) -> dict[str, int]:
        return {t: self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("sessions", "events", "experiments", "runs", "datasets", "features", "evaluation_results", "user_actions")}

    def delete_all_research_data(self) -> None:
        """Removes experiments, their run sessions, imported sessions, datasets, features and evaluations.
        Sessions created in the normal Session screen are kept."""
        self.conn.execute("DELETE FROM sessions WHERE id IN (SELECT session_id FROM runs WHERE session_id IS NOT NULL) "
                          "OR data_origin='imported'")
        for t in ("evaluation_results", "features", "analysis_runs", "runs", "conditions", "experiments", "datasets"):
            self.conn.execute(f"DELETE FROM {t}")
        self.conn.commit()

    def delete_everything(self) -> None:
        self.delete_all_research_data()
        self.conn.execute("DELETE FROM sessions")
        self.conn.commit()
        self.conn.execute("VACUUM")


def quarantine_corrupt(path: str | Path) -> Path:
    """Move an unreadable database aside (never deletes) so a fresh one can be created."""
    p = Path(path)
    dest = p.with_name(f"{p.name}.corrupt-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}")
    p.rename(dest)
    return dest
