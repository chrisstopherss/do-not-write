"""Version diffs. Word-level opcodes (for UI/exports) and a unified text diff."""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

_TOK = re.compile(r"\s+|\w+|[^\w\s]", re.UNICODE)


@dataclass(frozen=True)
class DiffOp:
    tag: str  # equal | insert | delete | replace
    a: str
    b: str


def word_diff(a: str, b: str) -> list[DiffOp]:
    ta, tb = _TOK.findall(a), _TOK.findall(b)
    sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
    return [DiffOp(tag, "".join(ta[i1:i2]), "".join(tb[j1:j2])) for tag, i1, i2, j1, j2 in sm.get_opcodes()]


def unified(a: str, b: str, a_label: str = "original", b_label: str = "final") -> str:
    return "".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True), a_label, b_label))


def summarize_ops(ops: list[DiffOp]) -> dict[str, int]:
    return {"added_chars": sum(len(o.b) for o in ops if o.tag in ("insert", "replace")),
            "removed_chars": sum(len(o.a) for o in ops if o.tag in ("delete", "replace")),
            "changed_segments": sum(1 for o in ops if o.tag != "equal")}
