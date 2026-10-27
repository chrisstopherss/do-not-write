"""Evaluation of a labeled method against ground truth. Descriptive harness only.

Warnings are attached whenever the sample is too small to support conclusions. Results on synthetic data say
nothing about performance on real documents.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, TypeVar

T = TypeVar("T")
MIN_PER_CLASS = 30


@dataclass
class Confusion:
    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0

    @property
    def n(self) -> int:
        return self.tp + self.fp + self.tn + self.fn


def confusion(truth: list[bool], pred: list[bool]) -> Confusion:
    if len(truth) != len(pred):
        raise ValueError("truth and pred must have the same length")
    c = Confusion()
    for t, p in zip(truth, pred, strict=True):
        if t and p:
            c.tp += 1
        elif not t and p:
            c.fp += 1
        elif t and not p:
            c.fn += 1
        else:
            c.tn += 1
    return c


def _ratio(num: int, den: int) -> float | None:
    return num / den if den else None


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """Wilson score interval for a binomial proportion (95% by default)."""
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def metrics(c: Confusion) -> dict[str, Any]:
    pos, neg = c.tp + c.fn, c.tn + c.fp
    out: dict[str, Any] = {
        "n": c.n, "positives": pos, "negatives": neg,
        "precision": _ratio(c.tp, c.tp + c.fp), "recall": _ratio(c.tp, pos), "specificity": _ratio(c.tn, neg),
        "false_positive_rate": _ratio(c.fp, neg), "false_negative_rate": _ratio(c.fn, pos),
        "accuracy": _ratio(c.tp + c.tn, c.n),
    }
    out["ci95"] = {"recall": wilson(c.tp, pos), "specificity": wilson(c.tn, neg),
                   "precision": wilson(c.tp, c.tp + c.fp)}
    warnings: list[str] = []
    if c.n == 0:
        warnings.append("empty dataset: no metrics can be computed")
    if 0 < pos < MIN_PER_CLASS or 0 < neg < MIN_PER_CLASS:
        warnings.append(f"fewer than {MIN_PER_CLASS} samples in a class; estimates are unreliable (see intervals)")
    if c.n and (pos == 0 or neg == 0):
        warnings.append("only one class present; recall/specificity undefined for the missing class")
    warnings.append("synthetic-on-synthetic results do not measure performance on real-world documents")
    out["warnings"] = warnings
    return out


def train_test_split(items: list[T], test_fraction: float, seed: int) -> tuple[list[T], list[T]]:
    """Deterministic shuffle split. Any threshold or parameter must be chosen on ``train`` only."""
    if not 0 < test_fraction < 1:
        raise ValueError("test_fraction must be in (0, 1)")
    idx = list(range(len(items)))
    random.Random(seed).shuffle(idx)
    k = max(1, round(len(items) * test_fraction)) if items else 0
    test_idx = set(idx[:k])
    return [items[i] for i in range(len(items)) if i not in test_idx], [items[i] for i in sorted(test_idx)]
