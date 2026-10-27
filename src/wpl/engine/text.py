"""Unicode-aware text segmentation and counting. Pure functions, no I/O."""
from __future__ import annotations

import re

WORD_RE = re.compile(r"\w+(?:['’]\w+)*", re.UNICODE)
# A sentence ends at . ! ? (or the ellipsis char) plus optional closing quotes/brackets, followed by space or end.
_SENT_END = re.compile(r"""(?<=[.!?…])["'”’)\]]*(?=\s|$)""")
PARA_SPLIT = re.compile(r"\n\s*\n")


def words(text: str) -> list[str]:
    return WORD_RE.findall(text)


def word_count(text: str) -> int:
    return len(WORD_RE.findall(text))


def paragraphs(text: str) -> list[str]:
    return [p for p in PARA_SPLIT.split(text) if p.strip()]


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Return (start, end) spans covering all non-whitespace text, split at sentence ends.

    Whitespace between sentences is not part of a span. Every non-space character belongs to exactly one span.
    A paragraph break always ends a sentence even without terminal punctuation.
    """
    spans: list[tuple[int, int]] = []
    pos = 0
    n = len(text)
    boundaries: list[int] = []
    for m in _SENT_END.finditer(text):
        boundaries.append(m.end())
    for m in PARA_SPLIT.finditer(text):
        boundaries.append(m.start())
    boundaries = sorted(set(boundaries))
    boundaries.append(n)
    for b in boundaries:
        if b <= pos:
            continue
        seg = text[pos:b]
        stripped_start = pos + (len(seg) - len(seg.lstrip()))
        stripped_end = pos + len(seg.rstrip())
        if stripped_end > stripped_start:
            spans.append((stripped_start, stripped_end))
        pos = b
    return spans


def sentences(text: str) -> list[str]:
    return [text[a:b] for a, b in sentence_spans(text)]
