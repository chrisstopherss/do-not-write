"""Seeded revision operations (replace a word, delete a sentence, add a synthetic sentence) shared by the
revision-heavy condition and the writing-rhythm generator. Pure; uses only the supplied RNG."""
from __future__ import annotations

import random

from .builder import DocBuilder
from .text import WORD_RE, sentence_spans

SYNTH_SENTENCES = ["The matter was noted for the record.", "A follow-up review was scheduled.",
                   "Several attendees asked for further detail.", "No objections were raised at that point.",
                   "The summary was circulated afterwards."]


def revise(b: DocBuilder, rng: random.Random, n: int) -> None:
    for _ in range(n):
        b.advance(rng.uniform(2000, 20000))
        spans = sentence_spans(b.text)
        b.sentence = max(0, len(spans) - 1)
        kind = rng.choice(["replace", "replace", "delete", "add"])
        meta = {"category": "revision", "revision_kind": kind}
        words = list(WORD_RE.finditer(b.text))
        if kind == "replace" and len(words) > 3:
            m = words[rng.randrange(len(words))]
            pool = sorted({w.group().lower() for w in words if len(w.group()) >= 3 and w.group().lower() != m.group().lower()})
            if pool:
                new = rng.choice(pool)
                b.replace(m.start(), m.end() - m.start(), new.capitalize() if m.group()[0].isupper() else new, meta)
                continue
            kind = "add"
        if kind == "delete" and len(spans) > 2:
            a, z = spans[rng.randrange(len(spans))]
            end = z + 1 if z < len(b.text) and b.text[z] == " " else z
            b.delete(a, end - a, meta)
            continue
        a, z = spans[rng.randrange(len(spans))] if spans else (0, 0)
        b.insert(z, " " + rng.choice(SYNTH_SENTENCES), meta=meta)


