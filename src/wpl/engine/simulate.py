"""Seeded, reproducible mistake and revision simulation.

Output is SYNTHETIC. These are experimental text transformations, not a model of real human behavior.

Reproducibility: all randomness comes from one ``random.Random(seed)`` (CPython Mersenne Twister, int seeds
are stable across versions). Draws happen in a fixed order: units in document order; within a unit in the order
of CATEGORY_ORDER. Same text + config + seed + ENGINE_VERSION => same events.

Rate denominators (per category), and what "opportunity / selected / applied" mean:
  substitution        letter positions examined: one draw per alphabetic char, in order, stopping at the
                      first hit in a word (so letters after a hit are not opportunities)
  missing_duplicated  same, with its own draws; a hit is then omitted or duplicated 50/50
  misspelling         words with >= 4 letters (adjacent-letter transposition)
  punctuation         punctuation marks . , ; : ! ? (the mark is dropped)
  grammar             words present in the confusables table (swapped for their pair member)
  repetition          words (the word is typed twice)
  abandonment         sentences (2-4 words typed, then deleted and retyped)
  correction          introduced errors (the fraction later fixed with a replace/delete event)
opportunity = a draw was made; selected = the draw succeeded; applied = selected and not blocked by an
earlier category having already modified the same word (at most one modification per word).
Realized rates therefore differ from configured rates by design; tests check realized/opportunity.
"""
from __future__ import annotations

import random
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .. import ENGINE_VERSION
from .builder import DocBuilder
from .events import Event
from .text import sentence_spans

CATEGORY_ORDER = ("grammar", "misspelling", "substitution", "missing_duplicated", "punctuation", "repetition")
ALL_CATEGORIES = CATEGORY_ORDER + ("abandonment", "correction")
CHAR_LEVEL = ("substitution", "missing_duplicated")
MAX_RATE = 0.5  # cap for per-letter categories (one draw per letter); all other categories may go to 1.0

CONFUSABLES = {"its": "it's", "it's": "its", "their": "there", "there": "their", "your": "you're", "you're": "your",
               "then": "than", "than": "then", "affect": "effect", "effect": "affect", "to": "too", "too": "to",
               "were": "where", "where": "were", "lose": "loose", "loose": "lose"}
_ROWS = ["qwertyuiop", "asdfghjkl", "zxcvbnm"]
NEIGHBORS: dict[str, str] = {}
for _r, _row in enumerate(_ROWS):
    for _i, _c in enumerate(_row):
        n = ""
        if _i > 0:
            n += _row[_i - 1]
        if _i < len(_row) - 1:
            n += _row[_i + 1]
        NEIGHBORS[_c] = n


class ConfigError(ValueError):
    pass


@dataclass
class CategoryConfig:
    enabled: bool = False
    rate: float = 0.0  # probability in [0, MAX_RATE] per opportunity (correction: [0, 1])


@dataclass
class MistakeConfig:
    categories: dict[str, CategoryConfig] = field(default_factory=lambda: {c: CategoryConfig() for c in ALL_CATEGORIES})

    @staticmethod
    def default() -> MistakeConfig:
        cfg = MistakeConfig()
        for c, r in {"substitution": 0.015, "missing_duplicated": 0.008, "misspelling": 0.01, "repetition": 0.005,
                     "correction": 0.6}.items():
            cfg.categories[c] = CategoryConfig(True, r)
        return cfg

    def validate(self) -> None:
        for name, c in self.categories.items():
            if name not in ALL_CATEGORIES:
                raise ConfigError(f"unknown category {name!r}")
            top = MAX_RATE if name in CHAR_LEVEL else 1.0
            if not (0.0 <= c.rate <= top):
                raise ConfigError(f"{name}: rate {c.rate} outside [0, {top}]")

    def rate(self, name: str) -> float:
        c = self.categories.get(name)
        return c.rate if c and c.enabled else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {k: asdict(v) for k, v in self.categories.items()}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> MistakeConfig:
        cfg = MistakeConfig()
        for k, v in d.items():
            cfg.categories[k] = CategoryConfig(bool(v.get("enabled")), float(v.get("rate", 0.0)))
        cfg.validate()
        return cfg


@dataclass
class SimConfig:
    mistakes: MistakeConfig = field(default_factory=MistakeConfig)
    pause_rate: float = 0.15       # probability of a pause at each sentence boundary
    correction_lag: tuple[int, int] = (1, 3)  # units typed before a correction is made

    def to_dict(self) -> dict[str, Any]:
        return {"mistakes": self.mistakes.to_dict(), "pause_rate": self.pause_rate,
                "correction_lag": list(self.correction_lag)}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> SimConfig:
        lag = d.get("correction_lag", [1, 3])
        return SimConfig(MistakeConfig.from_dict(d.get("mistakes", {})), float(d.get("pause_rate", 0.15)),
                         (int(lag[0]), int(lag[1])))


@dataclass
class CategoryStats:
    opportunities: int = 0
    selected: int = 0
    applied: int = 0


@dataclass
class SimulationResult:
    events: list[Event]
    final_text: str
    stats: dict[str, CategoryStats]
    seed: int
    engine_version: str = ENGINE_VERSION


_UNIT_RE = re.compile(r"\S+\s*|\s+")
_WORD_CORE = re.compile(r"^(\W*)(.*?)(\W*)$", re.DOTALL)


def _mutate_word(word: str, cat: str, rng: random.Random) -> str | None:
    """Return the erroneous variant of ``word`` or None when no variation is possible."""
    if cat == "grammar":
        low = word.lower()
        if low in CONFUSABLES:
            new = CONFUSABLES[low]
            return new.capitalize() if word[:1].isupper() else new
        return None
    letters = [i for i, c in enumerate(word) if c.isalpha()]
    if cat == "misspelling":
        pairs = [i for i in range(len(word) - 1) if word[i].isalpha() and word[i + 1].isalpha() and word[i] != word[i + 1]]
        if len(letters) < 4 or not pairs:
            return None
        i = pairs[rng.randrange(len(pairs))]
        return word[:i] + word[i + 1] + word[i] + word[i + 2:]
    return None


def type_text(b: DocBuilder, text: str, rng: random.Random, cfg: SimConfig,
              stats: dict[str, CategoryStats] | None = None, base_ms_per_char: float = 180.0,
              sentence_offset: int = 0) -> dict[str, CategoryStats]:
    """Append ``text`` to the builder's document as a stream of word-sized insert events with simulated mistakes."""
    cfg.mistakes.validate()
    st = stats if stats is not None else {c: CategoryStats() for c in ALL_CATEGORIES}
    spans = sentence_spans(text)
    units = [(m.start(), m.group()) for m in _UNIT_RE.finditer(text)]
    pending: list[dict[str, Any]] = []  # corrections waiting to happen: {pos, bad, good, due}

    def sentence_of(offset: int) -> int:
        for i, (_a, z) in enumerate(spans):
            if offset < z:
                return i
        return max(0, len(spans) - 1)

    def flush(due_idx: int, force: bool = False) -> None:
        for p in [p for p in pending if force or p["due"] <= due_idx]:
            pending.remove(p)
            bad, good = p["bad"], p["good"]
            pre = 0
            while pre < min(len(bad), len(good)) and bad[pre] == good[pre]:
                pre += 1
            suf = 0
            while suf < min(len(bad), len(good)) - pre and bad[-1 - suf] == good[-1 - suf]:
                suf += 1
            rem_n = len(bad) - pre - suf
            ins = good[pre:len(good) - suf]
            if rem_n == 0 and not ins:
                continue
            b.advance(rng.uniform(300, 900))
            meta = {"category": "correction", "fix_of": p["category"]}
            pos = p["pos"] + pre
            if not ins:
                b.delete(pos, rem_n, meta)
            elif rem_n == 0:
                b.insert(pos, ins, meta=meta)
            else:
                b.replace(pos, rem_n, ins, meta)
            delta = len(good) - len(bad)
            for q in pending:
                if q["pos"] > p["pos"]:
                    q["pos"] += delta

    last_sentence = -1
    abandon_done: set[int] = set()
    for ui, (off, unit) in enumerate(units):
        sidx = sentence_of(off)
        b.sentence = sentence_offset + sidx
        if sidx != last_sentence:
            if last_sentence >= 0 and rng.random() < cfg.pause_rate:
                flush(ui, force=True)
                b.pause(rng.lognormvariate(8.5, 0.6))  # median ~5 s raw units
            last_sentence = sidx
        # abandonment: at the first unit of a sentence
        if sidx not in abandon_done and off == spans[sidx][0]:
            abandon_done.add(sidx)
            st["abandonment"].opportunities += 1
            if rng.random() < cfg.mistakes.rate("abandonment"):
                st["abandonment"].selected += 1
                k = rng.randint(2, 4)
                chunk = "".join(u for _, u in units[ui:ui + k])
                if chunk.strip():
                    start = len(b.text)
                    b.advance(len(chunk) * base_ms_per_char * rng.uniform(0.6, 1.4))
                    b.insert(start, chunk, meta={"category": "abandonment"})
                    b.advance(rng.uniform(400, 1200))
                    b.delete(start, len(chunk), {"category": "abandonment"})
                    st["abandonment"].applied += 1
        m = _WORD_CORE.match(unit.rstrip())
        trail = unit[len(unit.rstrip()):]
        lead, core, tail = m.groups() if m else ("", unit.rstrip(), "")
        bad_core = core
        modified: str | None = None
        for cat in CATEGORY_ORDER:
            rate = cfg.mistakes.rate(cat)
            new: str | None = None
            if cat in ("grammar", "misspelling"):
                cand = _mutate_word(core, cat, rng) if core else None
                if cat == "grammar" and core.lower() not in CONFUSABLES:
                    continue
                if cat == "misspelling" and sum(c.isalpha() for c in core) < 4:
                    continue
                st[cat].opportunities += 1
                if rng.random() < rate:
                    st[cat].selected += 1
                    new = cand
            elif cat in ("substitution", "missing_duplicated"):
                idxs = [i for i, c in enumerate(core) if c.isalpha()]
                hit = None
                for i in idxs:  # one draw per letter until the first hit; only examined letters are opportunities
                    st[cat].opportunities += 1
                    if rng.random() < rate:
                        hit = i
                        break
                if hit is not None:
                    st[cat].selected += 1
                    c = core[hit]
                    if cat == "substitution":
                        nb = NEIGHBORS.get(c.lower())
                        if nb:
                            r = nb[rng.randrange(len(nb))]
                            new = core[:hit] + (r.upper() if c.isupper() else r) + core[hit + 1:]
                    else:
                        new = core[:hit] + core[hit + 1:] if rng.random() < 0.5 else core[:hit + 1] + core[hit:]
                        if not new:
                            new = None
            elif cat == "punctuation":
                marks = [i for i, c in enumerate(tail) if c in ".,;:!?"]
                st[cat].opportunities += len(marks)
                if marks and rng.random() < rate:
                    st[cat].selected += 1
                    j = marks[0]
                    if modified is None:
                        tail = tail[:j] + tail[j + 1:]
                        modified = cat
                        st[cat].applied += 1
                continue
            elif cat == "repetition":
                if core:
                    st[cat].opportunities += 1
                    if rng.random() < rate:
                        st[cat].selected += 1
                        new = core + " " + core
            if new is not None and new != core and modified is None:
                bad_core = new
                modified = cat
                st[cat].applied += 1
        bad_unit = lead + bad_core + tail + trail
        b.advance(max(1, len(unit)) * base_ms_per_char * rng.lognormvariate(0, 0.35))
        pos = len(b.text)
        b.insert(pos, bad_unit, meta={"category": modified} if modified else None)
        if modified:
            st["correction"].opportunities += 1
            if rng.random() < cfg.mistakes.rate("correction"):
                st["correction"].selected += 1
                st["correction"].applied += 1
                lo, hi = cfg.correction_lag
                pending.append({"pos": pos, "bad": bad_unit.rstrip() if trail else bad_unit,
                                "good": unit.rstrip() if trail else unit, "due": ui + rng.randint(lo, hi),
                                "category": modified})
        flush(ui)
    flush(len(units), force=True)
    return st


def simulate(text: str, seed: int, cfg: SimConfig | None = None, duration_ms: int | None = None,
             fill: float = 0.97) -> SimulationResult:
    """Condition-A style incremental composition of ``text`` (see lab.conditions for the other conditions)."""
    from .schedule import fit_to_duration

    cfg = cfg or SimConfig(MistakeConfig.default())
    rng = random.Random(seed)
    b = DocBuilder("")
    stats = {c: CategoryStats() for c in ALL_CATEGORIES}
    type_text(b, text, rng, cfg, stats)
    events = b.events
    if duration_ms:
        events = fit_to_duration(events, duration_ms, fill)
    return SimulationResult(events, b.text, stats, seed)
