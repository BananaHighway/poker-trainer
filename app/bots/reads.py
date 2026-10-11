"""Reading the action: preflop situation and estimated opponent ranges.

These reads are style-agnostic on purpose: a bot doesn't know who it's up against,
it only sees the betting, the same as you would at a real table.
"""

from dataclasses import dataclass

from ..engine import Decision
from ..equity import OpponentRange
from .styles import BASE_OPEN, HU_BUTTON_OPEN

BOARD_COUNT = {"flop": 3, "turn": 4, "river": 5}


@dataclass
class PreflopSpot:
    raises: int                 # voluntary raises so far (blinds excluded)
    limpers: int
    callers_since_raise: int
    last_raise_to: int
    last_raiser: int | None
    opener_position: str | None
    i_raised: bool


def preflop_spot(ctx: Decision) -> PreflopSpot:
    raises = limpers = callers = 0
    last_to, last_raiser, opener_pos, i_raised = 0, None, None, False
    for a in ctx.actions:
        if a["street"] != "preflop" or a["action"] in ("post_sb", "post_bb"):
            continue
        if a["action"] == "raise":
            raises += 1
            callers = 0
            last_to, last_raiser = a["to"], a["seat"]
            if raises == 1:
                opener_pos = a["position"]
            if a["seat"] == ctx.seat:
                i_raised = True
        elif a["action"] == "call":
            if raises == 0:
                limpers += 1
            else:
                callers += 1
    return PreflopSpot(raises, limpers, callers, last_to, last_raiser, opener_pos, i_raised)


def open_width(position: str, n_players: int) -> float:
    if n_players == 2:
        return HU_BUTTON_OPEN
    return BASE_OPEN.get(position, 0.2)


def estimate_range(seat: int, ctx: Decision) -> OpponentRange:
    """Estimate a player's range from their own actions this hand."""
    rng = OpponentRange(preflop_pct=1.0)
    raises = 0
    for a in ctx.actions:
        if a["street"] == "preflop":
            if a["action"] in ("post_sb", "post_bb"):
                continue
            if a["seat"] == seat:
                if a["action"] == "raise":
                    if raises == 0:
                        rng.preflop_pct = min(1.0, open_width(a["position"], ctx.n_players) * 1.15)
                    else:
                        rng.preflop_pct = (0.08, 0.035, 0.02)[min(raises, 3) - 1]
                elif a["action"] == "call":
                    if raises == 0:
                        rng.preflop_pct = 0.65 if a["position"] == "SB" else 0.45
                    elif raises == 1:
                        if a["position"] == "BB":
                            rng.preflop_pct = 0.6 if ctx.n_players == 2 else 0.45
                        else:
                            rng.preflop_pct = 0.22
                    else:
                        rng.preflop_pct = 0.07 if raises == 2 else 0.03
            if a["action"] == "raise":
                raises += 1
        elif a["seat"] == seat:
            n = BOARD_COUNT[a["street"]]
            if a["action"] in ("bet", "raise"):
                rng.aggressive_on.append(n)
            elif a["action"] == "call":
                rng.called_on.append(n)
    return rng


def preflop_aggressor(ctx: Decision) -> int | None:
    last = None
    for a in ctx.actions:
        if a["street"] == "preflop" and a["action"] == "raise":
            last = a["seat"]
    return last


def street_actions(ctx: Decision) -> list[dict]:
    from ..engine import STREETS
    name = STREETS[ctx.street]
    return [a for a in ctx.actions if a["street"] == name and a["action"] not in ("post_sb", "post_bb")]
