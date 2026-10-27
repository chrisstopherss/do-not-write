import math

import pytest

from wpl.analysis.diff import summarize_ops, unified, word_diff
from wpl.analysis.evaluation import Confusion, confusion, metrics, train_test_split, wilson
from wpl.analysis.features import AnalysisInput, run_extractors
from wpl.analysis.finaltext import (lexical_diversity, readability, sentence_length_variation, similarity)
from wpl.analysis.revisions import build_versions
from wpl.engine.events import Event


def E(seq, kind, pos=None, rem="", ins="", t=None, src="simulated", meta=None):
    return Event(seq, kind, src, t, pos, rem, ins, None, meta or {})


HAND = [E(0, "insert", 0, "", "Hello", 0), E(1, "insert", 5, "", " world", 1000),
        E(2, "replace", 0, "Hello", "Howdy", 1500), E(3, "delete", 5, " world", "", 10000)]


def feats(res, ex, name):
    return next(f for f in res[ex]["features"] if f["name"] == name)


def test_process_metrics_hand_verified():
    res = run_extractors(AnalysisInput("", "Howdy", HAND))
    g = lambda n: feats(res, "process_events", n)  # noqa: E731
    assert g("total_events")["value"] == 4
    assert (g("insertions")["value"], g("deletions")["value"], g("replacements")["value"]) == (2, 1, 1)
    assert g("inserted_chars")["value"] == 16 and g("removed_chars")["value"] == 11
    assert g("revision_events")["value"] == 2 and g("changed_existing_chars")["value"] == 11
    assert g("mean_removed_chars_per_revision")["value"] == 5.5
    iv = g("event_intervals_ms")["value"]
    assert iv["n"] == 3 and iv["min"] == 500 and iv["max"] == 8500 and math.isclose(iv["mean"], 10000 / 3)
    assert g("active_ms")["value"] == 1500 and g("idle_ms")["value"] == 8500
    assert math.isclose(g("revisions_per_minute")["value"], 12.0)
    assert g("final_share_from_recorded_events")["value"] == 1.0
    assert g("largest_single_event_share")["value"] == 1.0
    assert g("text_growth")["value"] == [(0, 5), (1000, 11), (1500, 11), (10000, 5)]
    assert g("replay_problems")["value"] == 0


def test_missing_is_not_zero_when_no_timestamps():
    ev = [E(0, "insert", 0, "", "ab"), E(1, "insert", 2, "", "cd")]
    res = run_extractors(AnalysisInput("", "abcd", ev))
    for n in ("event_intervals_ms", "active_ms", "idle_ms", "revisions_per_minute", "text_growth"):
        f = feats(res, "process_events", n)
        assert f["status"] == "missing" and f["value"] is None
    assert feats(res, "process_events", "insertions")["value"] == 2  # counts are still real


def test_no_events_is_missing_not_empty_result():
    res = run_extractors(AnalysisInput("", "text", []))
    assert res["process_events"]["features"][0]["status"] == "missing"


def test_typing_speed_refused_for_coarse_data_allowed_for_keystrokes():
    res = run_extractors(AnalysisInput("", "Howdy", HAND))
    assert feats(res, "process_events", "chars_per_minute_inserted")["status"] == "insufficient_data"
    keys = [E(i, "insert", i, "", "a", i * 200) for i in range(40)]
    res = run_extractors(AnalysisInput("", "a" * 40, keys))
    f = feats(res, "process_events", "chars_per_minute_inserted")
    assert f["status"] == "ok" and math.isclose(f["value"], 40 / (39 * 200 / 60000))


def test_initial_text_share_and_bulk_share():
    ev = [E(0, "insert", 5, "", "0123456789", 100)]
    res = run_extractors(AnalysisInput("Hello", "Hello0123456789", ev))
    assert math.isclose(feats(res, "process_events", "final_share_from_recorded_events")["value"], 10 / 15)
    assert math.isclose(feats(res, "process_events", "largest_single_event_share")["value"], 10 / 15)


def test_inconsistent_events_reported():
    ev = [E(0, "insert", 0, "", "ab", 0), E(1, "delete", 0, "zz", "", 5)]
    res = run_extractors(AnalysisInput("", "ab", ev))
    assert feats(res, "process_events", "replay_problems")["value"] == 1


def test_text_extractors_known_values():
    t = "The cat sat. The cat sat again!\n\nNew paragraph here."
    res = run_extractors(AnalysisInput("", t))
    assert feats(res, "text_basic", "words")["value"] == 10
    assert feats(res, "text_basic", "sentences")["value"] == 3
    assert feats(res, "text_basic", "paragraphs")["value"] == 2
    sl = feats(res, "text_distributions", "sentence_length_words")["value"]
    assert (sl["n"], sl["min"], sl["max"]) == (3, 3, 4)
    assert feats(res, "repeated_phrases", "repeated_2grams")["value"]["top"][0] == ("the cat", 2)
    assert feats(res, "text_distributions", "punctuation_counts")["value"] == {"!": 1, ".": 2}


def test_immediate_repeats():
    res = run_extractors(AnalysisInput("", "the the end end of the"))
    assert feats(res, "repeated_phrases", "immediate_word_repeats")["value"] == 2


def test_extractor_error_is_reported_not_swallowed():
    from wpl.analysis import features as F
    F.register(F.Extractor("boom", "0", "x", "text", lambda a: 1 / 0))
    try:
        res = run_extractors(AnalysisInput("", "x"), ["boom"])
        assert "ZeroDivisionError" in res["boom"]["error"]
    finally:
        del F.REGISTRY["boom"]


def test_word_diff_and_unified():
    ops = word_diff("the quick brown fox", "the slow brown fox jumps")
    tags = [(o.tag, o.a, o.b) for o in ops if o.tag != "equal"]
    assert tags == [("replace", "quick", "slow"), ("insert", "", " jumps")]
    s = summarize_ops(ops)
    assert s == {"added_chars": 4 + 6, "removed_chars": 5, "changed_segments": 2}
    assert "-the quick" in unified("the quick\n", "the slow\n")
    assert word_diff("same", "same")[0].tag == "equal"


def test_versions_from_checkpoints():
    ev = [E(0, "insert", 0, "", "Hello", 0), E(1, "checkpoint", t=1000, meta={"index": 1}), E(2, "insert", 5, "", " world", 2500)]
    v = build_versions("", ev)
    assert [x.label for x in v] == ["Initial", "Checkpoint 1", "Final"]
    assert [x.text for x in v] == ["", "Hello", "Hello world"]
    assert [x.elapsed_since_prev_ms for x in v] == [None, 1000, 1500]
    assert v[1].added_chars == 5 and v[2].added_chars == 6 and v[2].removed_chars == 0
    assert all(x.data_source == "simulated" for x in v)


def test_versions_from_snapshots_only_have_no_invented_times():
    v = build_versions("", [], snapshots=[(None, "a", "x y"), (None, "b", "x y z")], final_text="x y z")
    assert [x.t_ms for x in v] == [None, None, None, None] and v[1].elapsed_since_prev_ms is None


def test_confusion_and_metrics_hand_verified():
    truth = [True] * 6 + [False] * 4
    pred = [True] * 4 + [False] * 2 + [True] * 1 + [False] * 3
    c = confusion(truth, pred)
    assert (c.tp, c.fn, c.fp, c.tn) == (4, 2, 1, 3)
    m = metrics(c)
    assert m["precision"] == 0.8 and math.isclose(m["recall"], 4 / 6) and m["specificity"] == 0.75
    assert m["false_positive_rate"] == 0.25 and math.isclose(m["false_negative_rate"], 1 / 3) and m["accuracy"] == 0.7
    assert any("fewer than" in w for w in m["warnings"])
    assert any("synthetic" in w for w in m["warnings"])


def test_metrics_empty_and_single_class():
    m = metrics(Confusion())
    assert m["precision"] is None and m["accuracy"] is None and any("empty" in w for w in m["warnings"])
    m = metrics(confusion([True, True], [True, False]))
    assert m["specificity"] is None and any("one class" in w for w in m["warnings"])


def test_wilson_interval_known_value():
    lo, hi = wilson(8, 10)
    assert math.isclose(lo, 0.4902, abs_tol=1e-3) and math.isclose(hi, 0.9433, abs_tol=1e-3)
    assert wilson(0, 0) is None


def test_train_test_split_disjoint_deterministic():
    items = list(range(20))
    tr, te = train_test_split(items, 0.25, 7)
    assert sorted(tr + te) == items and not set(tr) & set(te) and len(te) == 5
    assert (tr, te) == train_test_split(items, 0.25, 7)
    with pytest.raises(ValueError):
        train_test_split(items, 1.0, 1)


def test_final_text_stats_known_values():
    ld = lexical_diversity("the cat the dog")
    assert ld["tokens"] == 4 and ld["types"] == 3 and ld["ttr"] == 0.75 and ld["mattr50"] is None
    assert lexical_diversity("")["ttr"] is None
    sv = sentence_length_variation("One two. One two three four.")
    assert sv["mean"] == 3 and math.isclose(sv["stdev"], math.sqrt(2)) and sentence_length_variation("Only one.")["cv"] is None
    sim = similarity("a b c", "a b d")
    assert math.isclose(sim["jaccard_words"], 2 / 4) and math.isclose(sim["cosine_counts"], 2 / 3)
    assert readability("")["flesch_reading_ease"] is None
    r = readability("The cat sat on the mat.")
    assert 90 < r["flesch_reading_ease"] < 125
    big = " ".join(f"w{i % 60}" for i in range(120))
    assert lexical_diversity(big)["mattr50"] == 1.0  # every 50-token window holds 50 distinct words
    assert lexical_diversity(" ".join(["a"] * 60))["mattr50"] == pytest.approx(1 / 50)
