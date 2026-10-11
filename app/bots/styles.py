"""Player-type presets for the heuristic bots.

Preflop numbers are range widths (fraction of all 1326 combos); `open_mult` scales
a TAG-baseline open-raise chart by position. Postflop numbers are probabilities /
thresholds on "per-opponent strength" (equity ** (1 / opponents)).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Style:
    key: str
    label: str
    blurb: str
    open_mult: float        # x TAG open chart
    limp: float             # extra range that limps/completes instead of folding
    threebet: float         # 3-bet range vs a CO-width open (scaled by opener's width)
    call_open: float        # flatting range beyond the 3-bet range
    fourbet: float
    call_3bet: float
    aggression: float       # 0..1 preference for betting/raising over checking/calling
    bluff: float            # base bluff frequency
    cbet: float             # flop continuation-bet frequency as preflop raiser
    looseness: float        # multiplier on required pot-odds equity (lower = calls more)
    value_bet: float        # strength needed to bet for value
    raise_value: float      # strength needed to raise for value
    sizing: tuple[float, ...]  # preferred bet sizes as fraction of pot


STYLES: dict[str, Style] = {s.key: s for s in (
    Style("nit", "Nit", "Plays few hands, folds to pressure, only bets with the goods.",
          open_mult=0.65, limp=0.0, threebet=0.03, call_open=0.06, fourbet=0.015, call_3bet=0.03,
          aggression=0.40, bluff=0.04, cbet=0.45, looseness=1.25, value_bet=0.70, raise_value=0.86,
          sizing=(0.5, 0.66)),
    Style("tag", "TAG", "Tight-aggressive regular: solid ranges, bets for value and c-bets often.",
          open_mult=1.0, limp=0.0, threebet=0.07, call_open=0.09, fourbet=0.03, call_3bet=0.06,
          aggression=0.65, bluff=0.14, cbet=0.65, looseness=1.0, value_bet=0.62, raise_value=0.80,
          sizing=(0.33, 0.5, 0.75)),
    Style("lag", "LAG", "Loose-aggressive: wide opens, lots of 3-bets and barrels.",
          open_mult=1.45, limp=0.0, threebet=0.12, call_open=0.13, fourbet=0.05, call_3bet=0.10,
          aggression=0.78, bluff=0.27, cbet=0.78, looseness=0.9, value_bet=0.57, raise_value=0.74,
          sizing=(0.5, 0.75, 1.0)),
    Style("station", "Calling station", "Loose-passive rec: limps and calls far too much, rarely bluffs.",
          open_mult=0.45, limp=0.42, threebet=0.025, call_open=0.38, fourbet=0.015, call_3bet=0.18,
          aggression=0.22, bluff=0.03, cbet=0.30, looseness=0.6, value_bet=0.72, raise_value=0.90,
          sizing=(0.33, 0.5)),
    Style("maniac", "Maniac", "Hyper-aggressive: raises everything, overbets, bluffs relentlessly.",
          open_mult=2.5, limp=0.0, threebet=0.28, call_open=0.18, fourbet=0.12, call_3bet=0.22,
          aggression=0.92, bluff=0.45, cbet=0.90, looseness=0.75, value_bet=0.50, raise_value=0.66,
          sizing=(0.75, 1.0, 1.4)),
)}

# Fraction of combos a TAG raises first-in from each position (6-max baseline).
BASE_OPEN = {"UTG": 0.15, "HJ": 0.19, "CO": 0.27, "BTN": 0.45, "SB": 0.38}
HU_BUTTON_OPEN = 0.75


def get_style(key: str) -> Style:
    return STYLES.get(key, STYLES["tag"])
