"""Generate data/preflop_strength.json: a strength ordering of the 169 starting hands.

Score blends equity vs one random hand with equity vs two random hands, so the
ordering doesn't overrate offsuit broadways the way pure heads-up equity does.
Run once (takes ~15s):  .\\run.ps1 scripts\\build_preflop.py
"""

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.cards import DECK, RANKS, evaluate  # noqa: E402

ITERATIONS = 3000
OUT = Path(__file__).resolve().parent.parent / "data" / "preflop_strength.json"


def representative(label: str) -> tuple[int, int]:
    r1, r2 = RANKS.index(label[0]), RANKS.index(label[1])
    if r1 == r2:
        return r1 * 4, r2 * 4 + 1
    if label[2] == "s":
        return r1 * 4, r2 * 4
    return r1 * 4, r2 * 4 + 1


def equity(hole: tuple[int, int], opponents: int, rng: random.Random) -> float:
    rest = [c for c in DECK if c not in hole]
    total = 0.0
    for _ in range(ITERATIONS):
        draw = rng.sample(rest, 5 + 2 * opponents)
        board = draw[:5]
        mine = evaluate([*hole, *board])
        ties = 1
        won = True
        for i in range(opponents):
            v = evaluate([draw[5 + 2 * i], draw[6 + 2 * i], *board])
            if v < mine:
                won = False
                break
            if v == mine:
                ties += 1
        if won:
            total += 1.0 / ties
    return total / ITERATIONS


def main() -> None:
    rng = random.Random(7)
    labels = []
    for i in range(12, -1, -1):
        for j in range(12, -1, -1):
            if i == j:
                labels.append(RANKS[i] * 2)
            elif i > j:
                labels.append(RANKS[i] + RANKS[j] + "s")
                labels.append(RANKS[i] + RANKS[j] + "o")
    rows = []
    for label in labels:
        hole = representative(label)
        eq1 = equity(hole, 1, rng)
        eq2 = equity(hole, 2, rng)
        rows.append({"hand": label, "eq_hu": round(eq1, 4), "eq_3way": round(eq2, 4),
                     "score": round(0.6 * eq1 + 0.4 * 1.5 * eq2, 4)})
    rows.sort(key=lambda r: r["score"], reverse=True)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(rows, indent=1))
    print(f"wrote {len(rows)} hands -> {OUT}")
    print("top 15:", [r["hand"] for r in rows[:15]])


if __name__ == "__main__":
    main()
