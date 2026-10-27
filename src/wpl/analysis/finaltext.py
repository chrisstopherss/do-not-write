"""Final-text descriptive statistics. These describe a text. They do NOT indicate how it was produced
and must never be presented as an authorship or AI-detection score."""
from __future__ import annotations

import math
import re
import statistics
from collections import Counter
from difflib import SequenceMatcher
from typing import Any

from ..engine.text import sentences, words

MATTR_WINDOW = 50


def lexical_diversity(text: str) -> dict[str, Any]:
    ws = [w.lower() for w in words(text)]
    n = len(ws)
    if n == 0:
        return {"tokens": 0, "types": 0, "ttr": None, "mattr50": None, "guiraud_r": None}
    types = len(set(ws))
    mattr = None
    if n >= MATTR_WINDOW:
        c = Counter(ws[:MATTR_WINDOW])
        vals = [len(c)]
        for i in range(MATTR_WINDOW, n):
            out, inn = ws[i - MATTR_WINDOW], ws[i]
            c[out] -= 1
            if c[out] == 0:
                del c[out]
            c[inn] += 1
            vals.append(len(c))
        mattr = statistics.fmean(vals) / MATTR_WINDOW
    return {"tokens": n, "types": types, "ttr": types / n, "mattr50": mattr, "guiraud_r": types / math.sqrt(n)}


def sentence_length_variation(text: str) -> dict[str, Any]:
    ls = [len(words(s)) for s in sentences(text)]
    if len(ls) < 2:
        return {"sentences": len(ls), "mean": ls[0] if ls else None, "stdev": None, "cv": None}
    m, sd = statistics.fmean(ls), statistics.stdev(ls)
    return {"sentences": len(ls), "mean": m, "stdev": sd, "cv": sd / m if m else None}


_VOWELS = re.compile(r"[aeiouy]+")


def _syllables(w: str) -> int:
    w = w.lower()
    n = len(_VOWELS.findall(w))
    if w.endswith("e") and not w.endswith(("le", "ee")) and n > 1:
        n -= 1
    return max(1, n)


def readability(text: str) -> dict[str, Any]:
    ws, ss = words(text), sentences(text)
    if not ws or not ss:
        return {"flesch_reading_ease": None, "flesch_kincaid_grade": None, "note": "no text"}
    syl = sum(_syllables(w) for w in ws)
    wps, spw = len(ws) / len(ss), syl / len(ws)
    return {"flesch_reading_ease": 206.835 - 1.015 * wps - 84.6 * spw,
            "flesch_kincaid_grade": 0.39 * wps + 11.8 * spw - 15.59,
            "note": "English-only; syllables estimated by a vowel-group heuristic, so values are approximate"}


def common_phrases(text: str, n: int = 2, top: int = 10) -> list[tuple[str, int]]:
    ws = [w.lower() for w in words(text)]
    grams = Counter(" ".join(ws[i:i + n]) for i in range(len(ws) - n + 1))
    return [(g, c) for g, c in grams.most_common(top) if c >= 2]


def similarity(a: str, b: str) -> dict[str, Any]:
    wa, wb = [w.lower() for w in words(a)], [w.lower() for w in words(b)]
    if not wa or not wb:
        return {"jaccard_words": None, "cosine_counts": None, "char_ratio": None}
    sa, sb = set(wa), set(wb)
    ca, cb = Counter(wa), Counter(wb)
    dot = sum(ca[k] * cb[k] for k in ca.keys() & cb.keys())
    cos = dot / (math.sqrt(sum(v * v for v in ca.values())) * math.sqrt(sum(v * v for v in cb.values())))
    return {"jaccard_words": len(sa & sb) / len(sa | sb), "cosine_counts": cos,
            "char_ratio": SequenceMatcher(None, a, b, autojunk=False).ratio()}


def describe(text: str) -> dict[str, Any]:
    return {"lexical_diversity": lexical_diversity(text), "sentence_length_variation": sentence_length_variation(text),
            "readability": readability(text), "common_bigrams": common_phrases(text, 2), "common_trigrams": common_phrases(text, 3)}
