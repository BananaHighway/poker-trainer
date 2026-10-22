"""Player-type presets for the heuristic bots.

Preflop numbers are range widths (fraction of all 1326 combos); `open_mult` scales
a TAG-baseline open-raise chart by position. Facing an open, `threebet`, `call_open`
and `bb_defend` are widths vs a standard CO open; reads.vs_open rescales them for
the opener's seat and size. Postflop numbers are probabilities / thresholds on
"per-opponent strength" (equity ** (1 / opponents)).

No two bots play a style identically: each gets its own `personalize`d copy, the
way two regulars both play "TAG" but not the same TAG.
"""

import math
import random
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Style:
    key: str
    label: str
    blurb: str
    open_mult: float        # x TAG open chart
    open_size: float        # preferred open in bb (+0.5 from the SB, +1 per limper)
    limp: float             # extra range that limps/completes instead of folding
    threebet: float         # 3-bet range vs a CO open
    call_open: float        # flatting range beyond the 3-bet range (not in the BB)
    bb_defend: float        # total BB continue (call + 3-bet) vs a CO open
    fold_3bet: float        # share of its own opening range it folds to a 3-bet
    fourbet: float
    call_3bet: float        # cold-calling range vs a 3-bet it didn't open into
    aggression: float       # 0..1 preference for betting/raising over checking/calling
    bluff: float            # base bluff frequency
    cbet: float             # flop continuation-bet frequency as preflop raiser
    donk: float             # chance to lead into the last aggressor instead of checking to them
    looseness: float        # multiplier on required pot-odds equity (lower = calls more)
    value_bet: float        # strength needed to bet for value
    raise_value: float      # strength needed to raise for value
    sizing: tuple[float, ...]  # preferred bet sizes as fraction of pot
    wild: float             # chance per preflop decision to play a hand from outside its ranges
    tilt: float             # 0..1 how hard a big loss knocks it off its game
    adapt: float            # 0..1 how much it plays off its notes on you (regulars do, recs don't)


STYLES: dict[str, Style] = {s.key: s for s in (
    Style("nit", "Nit", "Plays few hands, folds to pressure, only bets with the goods.",
          open_mult=0.65, open_size=2.5, limp=0.0, threebet=0.03, call_open=0.06, bb_defend=0.30,
          fold_3bet=0.65, fourbet=0.015, call_3bet=0.03,
          aggression=0.40, bluff=0.04, cbet=0.40, donk=0.10, looseness=1.25, value_bet=0.70, raise_value=0.86,
          sizing=(0.5, 0.66), wild=0.002, tilt=0.2, adapt=0.5),
    Style("tag", "TAG", "Tight-aggressive regular: solid ranges, bets for value and c-bets often.",
          open_mult=1.0, open_size=2.5, limp=0.0, threebet=0.07, call_open=0.09, bb_defend=0.46,
          fold_3bet=0.5, fourbet=0.03, call_3bet=0.06,
          aggression=0.65, bluff=0.14, cbet=0.55, donk=0.12, looseness=1.0, value_bet=0.62, raise_value=0.80,
          sizing=(0.33, 0.5, 0.75), wild=0.01, tilt=0.35, adapt=1.0),
    Style("lag", "LAG", "Loose-aggressive: wide opens, lots of 3-bets and barrels.",
          open_mult=1.45, open_size=2.3, limp=0.0, threebet=0.12, call_open=0.13, bb_defend=0.55,
          fold_3bet=0.42, fourbet=0.05, call_3bet=0.10,
          aggression=0.78, bluff=0.27, cbet=0.70, donk=0.2, looseness=0.9, value_bet=0.57, raise_value=0.74,
          sizing=(0.5, 0.75, 1.0), wild=0.03, tilt=0.5, adapt=0.9),
    Style("station", "Calling station", "Loose-passive rec: limps and calls far too much, rarely bluffs.",
          open_mult=0.45, open_size=3.0, limp=0.42, threebet=0.025, call_open=0.38, bb_defend=0.70,
          fold_3bet=0.3, fourbet=0.015, call_3bet=0.18,
          aggression=0.22, bluff=0.03, cbet=0.30, donk=0.45, looseness=0.6, value_bet=0.72, raise_value=0.90,
          sizing=(0.33, 0.5), wild=0.04, tilt=0.4, adapt=0.0),
    Style("maniac", "Maniac", "Hyper-aggressive: raises everything, overbets, bluffs relentlessly.",
          open_mult=2.5, open_size=3.0, limp=0.0, threebet=0.28, call_open=0.18, bb_defend=0.65,
          fold_3bet=0.3, fourbet=0.12, call_3bet=0.22,
          aggression=0.92, bluff=0.45, cbet=0.90, donk=0.6, looseness=0.75, value_bet=0.50, raise_value=0.66,
          sizing=(0.75, 1.0, 1.4), wild=0.08, tilt=0.7, adapt=0.15),
)}

# Fraction of combos a TAG raises first-in from each position (6-max baseline).
BASE_OPEN = {"UTG": 0.15, "HJ": 0.19, "CO": 0.27, "BTN": 0.45, "SB": 0.38}
HU_BUTTON_OPEN = 0.75


def get_style(key: str) -> Style:
    return STYLES.get(key, STYLES["tag"])


# Per-bot variation: range widths and bluff rates scale, frequencies and thresholds shift.
_SCALE = ("open_mult", "limp", "threebet", "call_open", "bb_defend", "fourbet", "call_3bet", "bluff", "wild")
_SHIFT = {"fold_3bet": 0.06, "aggression": 0.06, "cbet": 0.07, "donk": 0.05,
          "value_bet": 0.025, "raise_value": 0.02, "tilt": 0.1}


def personalize(style: Style, rng: random.Random) -> Style:
    """This bot's own version of `style`, with every knob nudged a little."""
    change = {k: getattr(style, k) * math.exp(rng.gauss(0, 0.12)) for k in _SCALE}
    change |= {k: min(0.97, max(0.0, getattr(style, k) + rng.gauss(0, sd))) for k, sd in _SHIFT.items()}
    change["bb_defend"] = min(0.9, change["bb_defend"])
    change["looseness"] = style.looseness * math.exp(rng.gauss(0, 0.06))
    change["open_size"] = round(style.open_size * math.exp(rng.gauss(0, 0.08)), 1)
    change["sizing"] = tuple(round(s * math.exp(rng.gauss(0, 0.12)), 2) for s in style.sizing)
    change["adapt"] = min(1.0, style.adapt * math.exp(rng.gauss(0, 0.15)))
    return replace(style, **change)


def tilted(style: Style, t: float) -> Style:
    """How `style` plays at tilt level t (0..1): looser, more aggressive, more bluffs."""
    return replace(
        style,
        open_mult=style.open_mult * (1 + 0.5 * t), threebet=style.threebet * (1 + t),
        call_open=style.call_open * (1 + 0.5 * t), bb_defend=min(0.95, style.bb_defend * (1 + 0.25 * t)),
        fold_3bet=style.fold_3bet * (1 - 0.4 * t), aggression=min(0.97, style.aggression + 0.2 * t),
        bluff=min(0.8, style.bluff * (1 + 1.5 * t)), looseness=style.looseness * (1 - 0.15 * t),
        wild=style.wild + 0.06 * t,
    )
