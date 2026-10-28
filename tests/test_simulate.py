import random

import pytest

from conftest import SAMPLE
from wpl.engine.events import replay
from wpl.engine.simulate import (
    ALL_CATEGORIES,
    CategoryConfig,
    ConfigError,
    MistakeConfig,
    SimConfig,
    simulate,
)


def cfg(**rates):
    m = MistakeConfig()
    for k, v in rates.items():
        m.categories[k] = CategoryConfig(True, v)
    return SimConfig(m)


def test_zero_rates_reproduce_source_exactly():
    r = simulate(SAMPLE, 1, cfg())
    assert r.final_text == SAMPLE
    assert all(s.applied == 0 for k, s in r.stats.items() if k != "correction")


def test_same_seed_same_events_different_seed_differs():
    c = cfg(substitution=0.1, missing_duplicated=0.05, repetition=0.05, correction=0.5)
    a, b, d = simulate(SAMPLE, 42, c), simulate(SAMPLE, 42, c), simulate(SAMPLE, 43, c)
    assert a.events == b.events and a.final_text == b.final_text and a.stats == b.stats
    assert a.events != d.events


def test_reproducible_through_config_round_trip():
    c = cfg(substitution=0.1, grammar=0.3, correction=1.0)
    c2 = SimConfig.from_dict(c.to_dict())
    assert simulate(SAMPLE, 5, c).events == simulate(SAMPLE, 5, c2).events


@pytest.mark.parametrize("text", [SAMPLE, "x", "Café naïve 😀 日本語. Ünï — done!\n\nÆsop's “quote” it's their there.", "a\n\n\nb"])
def test_replay_always_matches_final_text(text):
    c = cfg(grammar=0.5, misspelling=0.5, substitution=0.5, missing_duplicated=0.5, punctuation=0.5, repetition=0.5,
            abandonment=0.5, correction=0.7)
    for seed in range(40):
        r = simulate(text, seed, c)
        rp = replay(r.events)
        assert rp.problems == [] and rp.text == r.final_text


def test_substitution_realized_rate_matches_denominator():
    text = ("the quick brown fox jumps over the lazy dog. " * 400).strip()
    r = simulate(text, 11, cfg(substitution=0.2))
    s = r.stats["substitution"]
    assert s.opportunities > 8000
    assert abs(s.selected / s.opportunities - 0.2) < 0.02
    assert s.applied == s.selected  # nothing earlier in the order is enabled to block it


def test_full_correction_fixes_all_substitutions():
    r = simulate(SAMPLE * 3, 3, cfg(substitution=0.2, correction=1.0))
    assert r.final_text == SAMPLE * 3
    assert r.stats["correction"].applied == r.stats["correction"].opportunities > 0


def test_overlap_is_single_modification_per_word_and_counted_once():
    c = cfg(grammar=1.0, misspelling=0.5, substitution=0.5, repetition=0.5, punctuation=0.5)
    r = simulate("Their then it's to your words, were here.", 2, c)
    changed_words = sum(s.applied for k, s in r.stats.items() if k in ("grammar", "misspelling", "substitution", "missing_duplicated", "punctuation", "repetition"))
    inserts = [e for e in r.events if e.kind == "insert" and e.meta.get("category")]
    assert changed_words == len(inserts)  # each applied change corresponds to exactly one modified unit


def test_grammar_swaps_only_confusables():
    r = simulate("Their dog ran to the park.", 1, cfg(grammar=1.0))
    assert r.final_text.startswith("There dog ran too the park.")


def test_abandonment_types_then_deletes_then_retypes():
    r = simulate("Alpha beta gamma delta epsilon.", 4, cfg(abandonment=1.0))
    kinds = [e.kind for e in r.events if e.kind in ("insert", "delete")]
    assert kinds[:2] == ["insert", "delete"] and r.final_text == "Alpha beta gamma delta epsilon."


@pytest.mark.parametrize("bad", [-0.1, 0.51])
def test_rate_limits_enforced(bad):
    with pytest.raises(ConfigError):
        cfg(substitution=bad).mistakes.validate()
    with pytest.raises(ConfigError):
        cfg(missing_duplicated=bad).mistakes.validate()


def test_word_level_categories_allow_full_rate_but_not_more():
    cfg(grammar=1.0, repetition=1.0, abandonment=1.0).mistakes.validate()
    with pytest.raises(ConfigError):
        cfg(grammar=1.01).mistakes.validate()
    with pytest.raises(ConfigError):
        MistakeConfig.from_dict({"nonsense": {"enabled": True, "rate": 0.1}})




def test_global_random_state_not_used():
    random.seed(1)
    a = simulate(SAMPLE, 9, cfg(substitution=0.2)).events
    random.seed(2)
    random.random()
    assert a == simulate(SAMPLE, 9, cfg(substitution=0.2)).events


def test_categories_listed():
    assert set(MistakeConfig().categories) == set(ALL_CATEGORIES)
