"""Reading the action: preflop situation, baseline ranges and estimated opponent ranges.

These reads are style-agnostic on purpose: a bot doesn't know who it's up against,
it only sees the betting, the same as you would at a real table. The exception is
the hero, whom the regulars have notes on from past hands (a profile.Read).
"""

from dataclasses import dataclass

from ..engine import BB, STREETS, Decision
from ..equity import OpponentRange
from .profile import Read
from .styles import BASE_OPEN, HU_BUTTON_OPEN, STYLES, Style

BOARD_COUNT = {"flop": 3, "turn": 4, "river": 5}
TAG = STYLES["tag"]

# BB defence vs a standard open from each seat, relative to vs the CO
# (a TAG's 46% vs CO works out to ~35% vs UTG and ~55% vs BTN).
BB_DEFEND_SHAPE = {"UTG": 0.76, "HJ": 0.87, "CO": 1.0, "BTN": 1.2, "SB": 1.26}
HU_BB_DEFEND_SHAPE = 1.5


@dataclass
class PreflopSpot:
    raises: int                 # voluntary raises so far (blinds excluded)
    limpers: int
    callers_since_raise: int
    open_to: int                # size of the first raise
    last_raise_to: int
    last_raiser: int | None
    opener_position: str | None
    last_raiser_position: str | None
    i_raised: bool
    limper_seats: tuple[int, ...] = ()


def preflop_spot(ctx: Decision) -> PreflopSpot:
    raises = callers = 0
    open_to = last_to = 0
    last_raiser, opener_pos, last_pos, i_raised = None, None, None, False
    limper_seats = []
    for a in ctx.actions:
        if a["street"] != "preflop" or a["action"] in ("post_sb", "post_bb"):
            continue
        if a["action"] == "raise":
            raises += 1
            callers = 0
            last_to, last_raiser, last_pos = a["to"], a["seat"], a["position"]
            if raises == 1:
                open_to, opener_pos = a["to"], a["position"]
            if a["seat"] == ctx.seat:
                i_raised = True
        elif a["action"] == "call":
            if raises == 0:
                limper_seats.append(a["seat"])
            else:
                callers += 1
    return PreflopSpot(raises, len(limper_seats), callers, open_to, last_to, last_raiser, opener_pos, last_pos,
                       i_raised, tuple(limper_seats))


def open_width(position: str, n_players: int) -> float:
    if n_players == 2:
        return HU_BUTTON_OPEN
    return BASE_OPEN.get(position, 0.2)


def std_open_bb(position: str, n_players: int) -> float:
    """The open size the baseline assumes from a seat: 3bb from the SB, else 2.5bb."""
    return 3.0 if position == "SB" and n_players > 2 else 2.5


def vs_open(position: str, opener_position: str | None, open_bb: float, n_players: int,
            callers: int = 0, style: Style = TAG, opener_width: float | None = None) -> tuple[float, float]:
    """(3-bet width, total continuing width) facing a single open.

    Wider vs late-position openers and small opens, tighter vs early openers and big
    opens. The BB gets the best price and closes the action, so it defends far wider
    than anyone else. `opener_width` replaces the seat's standard opening range when
    there's a read on how wide this opener really opens. With the default style this
    is the TAG baseline the mistake finder grades against.
    """
    opener = opener_position or "CO"
    width = opener_width if opener_width is not None else open_width(opener, n_players)
    f = max(0.6, min(2.5, (width / BASE_OPEN["CO"]) ** 0.6))
    size = max(0.4, min(1.3, (std_open_bb(opener, n_players) / max(open_bb, 1.0)) ** 0.8))
    three = style.threebet * f * min(1.0, size) * 0.8 ** callers
    if position == "BB":
        shape = HU_BB_DEFEND_SHAPE if n_players == 2 else BB_DEFEND_SHAPE.get(opener, 1.0)
        return three, min(0.95, max(three, style.bb_defend * shape * size))
    call = style.call_open * f * size * (0.5 if position == "SB" and n_players > 2 else 1.0)
    return three, min(0.95, three + call)


def threebet_continue(open_w: float, threebet_w: float, fold: float = TAG.fold_3bet) -> float:
    """Width an opener continues with (call or 4-bet) after being 3-bet: the part of its
    own opening range it doesn't fold, wider vs wide 3-bettors and tighter vs tight ones."""
    return open_w * (1 - fold) * max(0.7, min(1.3, (threebet_w / TAG.threebet) ** 0.5))


def estimate_range(seat: int, ctx: Decision, read: Read | None = None) -> OpponentRange:
    """Estimate a player's range from their own actions this hand. With a read on the
    player (bots/profile.py), their real tendencies replace the typical regular's."""
    rng = OpponentRange(preflop_pct=1.0)
    if read:
        rng.air_bet, rng.air_call = read.air_bet, read.air_call
    n = ctx.n_players
    raises = callers = open_to = 0
    opener = opener_pos = None
    three_w = TAG.threebet
    for a in ctx.actions:
        if a["street"] == "preflop":
            if a["action"] in ("post_sb", "post_bb"):
                continue
            if a["seat"] == seat:
                pos = a["position"]
                if a["action"] == "raise":
                    if raises == 0:
                        rng.preflop_pct = read.open_range(pos) if read else min(1.0, open_width(pos, n) * 1.15)
                    elif raises == 1:
                        three = max(0.04, vs_open(pos, opener_pos, open_to / BB, n, callers)[0] * 1.25)
                        rng.preflop_pct = min(1.0, three * read.scale("three_bet")) if read else three
                    else:
                        rng.preflop_pct = (0.035, 0.02)[min(raises, 3) - 2]
                elif a["action"] == "call":
                    if raises == 0:
                        limp = 0.65 if pos == "SB" else 0.45
                        rng.preflop_pct = min(1.0, read.open_range(pos) + read.rate("limp")) if read else limp
                    elif raises == 1:
                        cont = vs_open(pos, opener_pos, open_to / BB, n, callers)[1]
                        rng.preflop_pct = min(1.0, cont * read.defend_scale(pos)) if read else cont
                    elif raises == 2 and seat == opener:
                        if read:
                            rng.preflop_pct = threebet_continue(read.open_range(pos), three_w, read.rate("fold_3bet"))
                        else:
                            rng.preflop_pct = threebet_continue(open_width(pos, n), three_w)
                    else:
                        rng.preflop_pct = 0.07 if raises == 2 else 0.03
            if a["action"] == "raise":
                raises += 1
                callers = 0
                if raises == 1:
                    opener, opener_pos, open_to = a["seat"], a["position"], a["to"]
                elif raises == 2:
                    three_w = vs_open(a["position"], opener_pos, open_to / BB, n)[0]
            elif a["action"] == "call" and raises:
                callers += 1
        elif a["seat"] == seat:
            n_board = BOARD_COUNT[a["street"]]
            if a["action"] in ("bet", "raise"):
                rng.aggressive_on.append(n_board)
            elif a["action"] == "call":
                rng.called_on.append(n_board)
    return rng


def preflop_aggressor(ctx: Decision) -> int | None:
    last = None
    for a in ctx.actions:
        if a["street"] == "preflop" and a["action"] == "raise":
            last = a["seat"]
    return last


def initiative(ctx: Decision) -> int | None:
    """Who bet or raised last on the previous street: the player the others usually
    check to. None if that street went through without a bet."""
    prev = STREETS[ctx.street - 1]
    last = None
    for a in ctx.actions:
        if a["street"] == prev and a["action"] in ("bet", "raise"):
            last = a["seat"]
    return last


def street_actions(ctx: Decision) -> list[dict]:
    name = STREETS[ctx.street]
    return [a for a in ctx.actions if a["street"] == name and a["action"] not in ("post_sb", "post_bb")]
