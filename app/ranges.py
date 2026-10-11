"""Preflop hand-strength percentiles and range sampling.

A "range" throughout this app is a single number P in (0, 1]: the top P fraction
of the 1326 starting combos by the precomputed strength ordering. Crude next to a
solver range, but cheap, and plenty to drive style-based bots and equity estimates.
"""

import json
import random
from functools import lru_cache
from pathlib import Path

from .cards import RANKS, hand_class

_DATA = Path(__file__).resolve().parent.parent / "data" / "preflop_strength.json"


def _combos_for(label: str) -> list[tuple[int, int]]:
    r1, r2 = RANKS.index(label[0]), RANKS.index(label[1])
    out = []
    for s1 in range(4):
        for s2 in range(4):
            c1, c2 = r1 * 4 + s1, r2 * 4 + s2
            if r1 == r2:
                if s1 < s2:
                    out.append((c1, c2))
            elif (label[2] == "s") == (s1 == s2):
                out.append((c1, c2))
    return out


@lru_cache(maxsize=1)
def _tables() -> tuple[list[tuple[int, int]], dict[str, float]]:
    order = [row["hand"] for row in json.loads(_DATA.read_text())]
    combos: list[tuple[int, int]] = []
    percentile: dict[str, float] = {}
    for label in order:
        cs = _combos_for(label)
        # Percentile of a class = midpoint of the combo span it occupies.
        percentile[label] = (len(combos) + len(cs) / 2) / 1326
        combos.extend(cs)
    return combos, percentile


def percentile(c1: int, c2: int) -> float:
    """0.0 = AA ... 1.0 = 72o. A hand is 'in' a range P if percentile < P."""
    return _tables()[1][hand_class(c1, c2)]


def class_percentile(label: str) -> float:
    return _tables()[1][label]


def sample_hand(range_pct: float, dead: set[int], rng: random.Random) -> tuple[int, int]:
    """Uniformly sample a combo from the top `range_pct` of hands, avoiding dead cards."""
    combos = _tables()[0]
    n = max(6, min(1326, int(range_pct * 1326)))
    for _ in range(200):
        c1, c2 = combos[rng.randrange(n)]
        if c1 not in dead and c2 not in dead:
            return c1, c2
    # Range almost entirely blocked: fall back to any live combo.
    live = [c for c in range(52) if c not in dead]
    c1, c2 = rng.sample(live, 2)
    return c1, c2
