"""Methods under evaluation. A 'method' is a descriptive rule over process features, evaluated against ground
truth known from how synthetic data was generated. It is NOT an authorship detector."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..analysis.evaluation import confusion, metrics, train_test_split

METHOD_ID = "bulk_insert_share_rule"
METHOD_VERSION = "1.0.0"
METHOD_DESCRIPTION = ("Flags a session when a single recorded event created at least `threshold` of the final text "
                      "(feature process_events.largest_single_event_share). It tests whether bulk insertion is visible "
                      "in event data. It says nothing about who or what wrote the text.")


@dataclass
class Sample:
    session_id: str
    feature: float | None
    truth: bool


def choose_threshold(train: list[Sample]) -> float:
    """Pick the threshold maximizing balanced accuracy on the TRAIN split only."""
    usable = [s for s in train if s.feature is not None]
    cands = sorted({s.feature for s in usable if s.feature is not None})
    best, best_score = 0.5, -1.0
    for t in cands or [0.5]:
        c = confusion([s.truth for s in usable], [(s.feature or 0) >= t for s in usable])
        m = metrics(c)
        score = ((m["recall"] or 0) + (m["specificity"] or 0)) / 2
        if score > best_score:
            best, best_score = t, score
    return best


def evaluate(samples: list[Sample], test_fraction: float, seed: int) -> dict[str, Any]:
    usable = [s for s in samples if s.feature is not None]
    skipped = len(samples) - len(usable)
    if len(usable) < 2:
        return {"error": "need at least 2 samples with the feature recorded", "skipped_missing_feature": skipped}
    train, test = train_test_split(usable, test_fraction, seed)
    thr = choose_threshold(train)
    c = confusion([s.truth for s in test], [(s.feature or 0) >= thr for s in test])
    m = metrics(c)
    return {"method_id": METHOD_ID, "method_version": METHOD_VERSION, "threshold": thr,
            "split": {"test_fraction": test_fraction, "seed": seed, "train_n": len(train), "test_n": len(test),
                      "skipped_missing_feature": skipped, "test_session_ids": [s.session_id for s in test]},
            "confusion": {"tp": c.tp, "fp": c.fp, "tn": c.tn, "fn": c.fn}, "metrics": m}
