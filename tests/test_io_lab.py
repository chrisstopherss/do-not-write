import json
import socket
from pathlib import Path

import pytest

from wpl.analysis.features import REGISTRY, AnalysisInput, run_extractors
from wpl.engine.events import Event
from wpl.io import exporters as X
from wpl.io.importer import ImportRejected, import_file, import_text
from wpl.lab import methods
from wpl.lab.conditions import CONDITION_NAMES, ConditionParams, ParamError, generate, sample_text
from wpl.lab.runner import create_example_experiment, derive_seed, run_experiment


@pytest.mark.parametrize("v", ["=1+1", "+SUM(A1)", "-2+3", "@cmd", "\tx", "\rx"])
def test_csv_formula_injection_neutralized(v):
    out = X.to_csv(["c"], [[v]])
    assert out.splitlines()[1].lstrip('"').startswith("'")


def test_csv_numbers_and_none_unchanged_and_quoting():
    out = X.to_csv(["a", "b", "c"], [[-5, None, 'he said "hi", ok\nnext']])
    import csv, io
    row = list(csv.reader(io.StringIO(out)))[1]
    assert row == ["-5", "", 'he said "hi", ok\nnext']


def test_events_csv_blank_for_unrecorded_time_and_labels_origin():
    out = X.events_csv("s", "simulated", [Event(0, "insert", "simulated", None, 0, "", "=evil")])
    lines = out.splitlines()
    assert "data_origin" in lines[0] and ",simulated," in lines[1] and "'=evil" in lines[1]


def test_unique_path_never_overwrites(tmp_path):
    a = X.write_new(tmp_path / "r.txt", "one")
    b = X.write_new(tmp_path / "r.txt", "two")
    assert a.read_text() == "one" and b.name == "r (1).txt" and b.read_text() == "two"


def test_reports_state_origin_and_missing(db):
    res = run_extractors(AnalysisInput("", "ab", [Event(0, "insert", "simulated", None, 0, "", "ab")]))
    md = X.report_markdown("T", "simulated", res, [])
    assert "Data origin: SIMULATED" in md and "Not recorded" in md and "missing" in md
    assert "<script" not in X.report_html("<script>x</script>", "simulated", res, [])


def test_dataset_json_schema(db):
    eid = run_experiment(db, "t", [ConditionParams(code="C", text=sample_text())], 1, 1)
    row = db.list_sessions()[0]
    doc = json.loads(X.dataset_json("n", [X.session_dict(row, db.get_events(row["id"]))]))
    assert doc["schema"] == "wpl.dataset/1" and doc["sessions"][0]["data_origin"] == "simulated"
    assert doc["sessions"][0]["events"][0]["source"] == "simulated" and eid


GOOD = {"schema": "wpl.revision_history/1", "origin": "synthetic", "name": "demo", "initial_text": "",
        "events": [{"t_ms": 0, "kind": "insert", "pos": 0, "inserted": "Hello"},
                   {"t_ms": 500, "kind": "insert", "pos": 5, "inserted": " there"},
                   {"t_ms": 900, "kind": "replace", "pos": 6, "removed": "there", "inserted": "world"}]}


def test_import_good_dataset_labels_imported_source(db):
    r = import_text(db, json.dumps(GOOD))
    s = db.get_session(r["session_id"] if isinstance(r, dict) else r.session_id)
    assert s["final_text"] == "Hello world" and s["data_origin"] == "synthetic"
    assert {e.source for e in db.get_events(s["id"])} == {"imported"}
    assert db.list_datasets()[0]["sha256"]


def test_import_requires_authorization_for_imported(db):
    doc = {**GOOD, "origin": "imported"}
    with pytest.raises(ImportRejected) as e:
        import_text(db, json.dumps(doc))
    assert any("authorization" in m for m in e.value.errors)
    doc["authorization"] = {"attested": True, "statement": "I own this document and its history."}
    assert import_text(db, json.dumps(doc)).events == 3


@pytest.mark.parametrize("mut", [
    lambda d: d.update(schema="other/1"),
    lambda d: d.update(events=[{"kind": "insert", "pos": 0}]),
    lambda d: d.update(events=[{"kind": "explode", "pos": 0, "inserted": "x"}]),
    lambda d: d["events"][2].update(removed="WRONG"),
    lambda d: d["events"][1].update(t_ms=-4),
    lambda d: d.update(events=[], snapshots=[]),
    lambda d: d.update(events="nope"),
    lambda d: d["events"][0].update(t_ms=1000),
])
def test_import_rejects_malformed_and_inconsistent_atomically(db, mut):
    import copy
    doc = copy.deepcopy(GOOD)
    mut(doc)
    with pytest.raises(ImportRejected):
        import_text(db, json.dumps(doc))
    assert db.counts()["sessions"] == 0 and db.counts()["datasets"] == 0


def test_import_not_json_and_limits(db, tmp_path):
    with pytest.raises(ImportRejected):
        import_text(db, "{not json")
    p = tmp_path / "x.txt"
    p.write_text("{}")
    with pytest.raises(ImportRejected):
        import_file(db, p)
    q = tmp_path / "bin.json"
    q.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(ImportRejected):
        import_file(db, q)


def test_import_snapshot_only_creates_no_events(db):
    doc = {"schema": "wpl.revision_history/1", "origin": "synthetic", "snapshots": [{"label": "v1", "text": "a"}, {"label": "v2", "text": "a b"}]}
    r = import_text(db, json.dumps(doc))
    assert r.events == 0 and r.snapshots == 2 and db.get_events(r.session_id) == []


def test_import_does_not_execute_content(db):
    doc = {**GOOD, "events": [{"t_ms": 0, "kind": "insert", "pos": 0, "inserted": "__import__('os').system('echo pwned')"}]}
    r = import_text(db, json.dumps(doc))
    assert db.get_session(r.session_id)["final_text"].startswith("__import__")


# ---------------------------------------------------------------- lab
@pytest.mark.parametrize("code", list(CONDITION_NAMES))
def test_each_condition_deterministic_and_replayable(code):
    from wpl.engine.events import replay
    p = ConditionParams(code=code, text=sample_text())
    a, b = generate(p, 123), generate(p, 123)
    assert a.events == b.events and a.final_text == replay(a.events).text and replay(a.events).problems == []
    assert generate(p, 124).events != a.events or code in ("C",)


def test_condition_signatures():
    t = sample_text()
    c = generate(ConditionParams(code="C", text=t), 1)
    assert [e.kind for e in c.events if e.kind != "checkpoint"] == ["insert"] and c.final_text == t
    assert c.ground_truth["contains_bulk_insert"] is True
    a = generate(ConditionParams(code="A", text=t), 1)
    assert a.ground_truth["contains_bulk_insert"] is False
    d = generate(ConditionParams(code="D", text=t, revision_frequency=10), 1)
    assert sum(1 for e in d.events if e.meta.get("category") == "revision") == round(10 * len(t) / 1000)
    d0 = generate(ConditionParams(code="D", text=t, revision_frequency=0), 1)
    assert d0.final_text == t


def test_params_validation():
    for bad in (dict(code="Z"), dict(text="  "), dict(duration_ms=0), dict(revision_frequency=-1),
                dict(checkpoint_interval_ms=0), dict(insert_chunks=0)):
        kw = dict(code="A", text="x") | bad
        with pytest.raises(ParamError if "duration_ms" not in bad else Exception):
            ConditionParams(**kw).validate()


def test_all_events_in_runs_labeled_simulated(db):
    eid = create_example_experiment(db)
    for r in db.experiment_runs(eid):
        evs = db.get_events(r["session_id"])
        assert evs and {e.source for e in evs} == {"simulated"}
        assert db.get_session(r["session_id"])["data_origin"] == "simulated"


def test_example_experiment_contents_and_features_stored(db):
    eid = create_example_experiment(db)
    runs = db.experiment_runs(eid)
    assert len(runs) == 25 and {r["code"] for r in runs} == set("ABCDE")
    f = db.latest_features(runs[0]["session_id"])
    assert "process_events.total_events" in f and "text_basic.words" in f
    assert db.list_experiments()[0]["is_example"] == 1


def test_experiment_reproducible_across_databases():
    from wpl.storage.db import Database
    def run():
        d = Database(":memory:")
        e = run_experiment(d, "r", [ConditionParams(code="E", text=sample_text())], 3, 77)
        return [[(x.kind, x.t_ms, x.pos, x.removed, x.inserted) for x in d.get_events(r["session_id"])] for r in d.experiment_runs(e)]
    assert run() == run()


def test_seed_derivation_stable_and_distinct():
    assert derive_seed(1, 0, 0) == derive_seed(1, 0, 0) != derive_seed(1, 0, 1) != derive_seed(1, 1, 0)


def test_experiment_input_validation(db):
    p = ConditionParams(code="A", text="hi there")
    with pytest.raises(ValueError):
        run_experiment(db, "", [p], 1, 1)
    with pytest.raises(ValueError):
        run_experiment(db, "x", [p], 0, 1)
    with pytest.raises(ValueError):
        run_experiment(db, "x", [], 1, 1)
    assert db.counts()["experiments"] == 0


def test_method_evaluation_train_test_separation_and_small_sample_warning(db):
    eid = create_example_experiment(db)
    samples = []
    for r in db.experiment_runs(eid):
        s = db.get_session(r["session_id"])
        truth = json.loads(s["config_json"])["ground_truth"]["contains_bulk_insert"]
        feat = db.latest_features(s["id"])["process_events.largest_single_event_share"]["value"]
        samples.append(methods.Sample(s["id"], feat, truth))
    out = methods.evaluate(samples, 0.4, 5)
    c = out["confusion"]
    assert sum(c.values()) == out["split"]["test_n"] == 10 and out["split"]["train_n"] == 15
    assert any("fewer than" in w for w in out["metrics"]["warnings"])
    assert methods.evaluate(samples[:1], 0.4, 5).get("error")


def test_evaluation_skips_missing_feature():
    out = methods.evaluate([methods.Sample("a", None, True), methods.Sample("b", 1.0, True), methods.Sample("c", 0.0, False)], 0.5, 1)
    assert out["split"]["skipped_missing_feature"] == 1


# ---------------------------------------------------------------- offline / safety
def test_no_network_apis_in_source():
    root = Path(__file__).resolve().parents[1] / "src" / "wpl"
    banned = ("import socket", "import urllib", "import http", "import requests", "import ftplib", "import smtplib",
              "import subprocess", "os.system", "eval(", "exec(", "pickle", "marshal", "QtNetwork", "webbrowser")
    for f in root.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        for b in banned:
            assert b not in text, f"{b!r} found in {f}"


def test_lab_runs_with_network_disabled(monkeypatch, db):
    def boom(*a, **k):
        raise AssertionError("network access attempted")
    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    eid = create_example_experiment(db)
    assert db.experiment_runs(eid)


def test_registry_has_versions():
    assert all(e.version and e.description for e in REGISTRY.values())
