import pytest

from wpl.engine.events import Event, EventError, apply_event, check_order, replay
from wpl.engine.text import paragraphs, sentence_spans, sentences, word_count


def test_counts_unicode_and_paragraphs():
    t = "Café naïve 日本語. Done!\n\nSecond paragraph? Yes… ok."
    assert word_count("don't stop") == 2
    assert len(paragraphs(t)) == 2
    assert sentences(t) == ["Café naïve 日本語.", "Done!", "Second paragraph?", "Yes…", "ok."]


def test_sentence_spans_cover_all_nonspace_chars_once():
    t = "One. Two  three!\n\nFour without end"
    covered = sorted(i for a, z in sentence_spans(t) for i in range(a, z))
    assert covered == [i for i, c in enumerate(t) if not c.isspace() or any(a <= i < z for a, z in sentence_spans(t))]
    assert "".join(t[a:z] for a, z in sentence_spans(t)).replace(" ", "").replace("\n", "") == t.replace(" ", "").replace("\n", "")


def test_empty_and_single_char():
    assert sentence_spans("") == []
    assert sentences("x") == ["x"]


E = lambda seq, kind, pos=None, rem="", ins="", t=None: Event(seq, kind, "simulated", t, pos, rem, ins)


def test_replay_insert_delete_replace_and_origin():
    ev = [E(0, "insert", 0, "", "Hello", 0), E(1, "insert", 5, "", " world", 1000),
          E(2, "replace", 0, "Hello", "Howdy", 1500), E(3, "delete", 5, " world", "", 10000)]
    r = replay(ev)
    assert r.text == "Howdy" and r.problems == []
    assert r.origin == [2] * 5
    assert r.lengths == [(0, 0, 5), (1000, 1, 11), (1500, 2, 11), (10000, 3, 5)]


def test_replay_reports_mismatch_instead_of_guessing():
    r = replay([E(0, "insert", 0, "", "abc"), E(1, "delete", 0, "xyz", "")])
    assert r.text == "abc" and len(r.problems) == 1


def test_validation_rejects_bad_events():
    for bad in (E(0, "insert", None, "", "a"), E(0, "insert", 0, "x", "a"), E(0, "delete", 0, "", ""),
                E(0, "replace", 0, "a", ""), E(0, "nope"), Event(0, "insert", "alien", 0, 0, "", "a"),
                Event(0, "insert", "simulated", -5, 0, "", "a")):
        with pytest.raises(EventError):
            bad.validate()


def test_check_order():
    assert check_order([E(0, "insert", 0, "", "a", 5), E(1, "insert", 1, "", "b", 9)]) == []
    assert check_order([E(0, "insert", 0, "", "a", 9), E(1, "insert", 1, "", "b", 5)])
    assert check_order([E(1, "insert", 0, "", "a"), E(1, "insert", 1, "", "b")])


def test_apply_event_pause_is_noop():
    assert apply_event("abc", E(0, "pause")) == "abc"
