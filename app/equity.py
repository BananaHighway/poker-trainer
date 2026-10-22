"""Monte Carlo equity against opponents' estimated ranges.

Each opponent is described by an OpponentRange: a preflop range width plus the
postflop streets on which they bet/raised or merely called. Sampled hands that
don't plausibly fit that postflop line (no pair, no draw) are rejected with some
probability, which narrows ranges the way real betting does without a solver.
"""

import random
from dataclasses import dataclass, field

from .cards import DECK, evaluate, rank, suit
from .ranges import sample_hand


@dataclass
class OpponentRange:
    preflop_pct: float = 1.0
    # board-card counts (3 = flop, 4 = turn, 5 = river) at which they showed aggression / called
    aggressive_on: list[int] = field(default_factory=list)
    called_on: list[int] = field(default_factory=list)
    # Share of no-pair, no-draw hands kept after a bet/raise or a call (a read on the
    # player moves these: bluffier players keep more air when betting, stations when calling).
    air_bet: float = 0.3
    air_call: float = 0.45


def _connects(hole: tuple[int, int], board: list[int]) -> bool:
    """Does the hand plausibly continue on this board? Pair+, or a real draw."""
    h_ranks = [rank(c) for c in hole]
    b_ranks = [rank(c) for c in board]
    if h_ranks[0] == h_ranks[1]:
        return True
    if any(r in b_ranks for r in h_ranks):
        return True
    cards = [*hole, *board]
    if len(board) == 5:
        # River: only made hands count; a straight/flush that uses a hole card.
        made = evaluate(cards)
        return made <= 1609 and made < evaluate(board)
    for s in range(4):
        if sum(1 for c in cards if suit(c) == s) >= 4 and any(suit(c) == s for c in hole):
            return True
    ranks = {rank(c) for c in cards}
    if 12 in ranks:
        ranks.add(-1)  # ace plays low for wheel draws
    hole_ranks = set(h_ranks) | ({-1} if 12 in h_ranks else set())
    for lo in range(-1, 9):
        window = [r for r in range(lo, lo + 5) if r in ranks]
        if len(window) >= 4 and hole_ranks & set(window):
            return True
    return False


def _fits_line(hole: tuple[int, int], board: list[int], opp: OpponentRange, rng: random.Random) -> bool:
    for n in opp.aggressive_on:
        if n <= len(board) and not _connects(hole, board[:n]) and rng.random() < 1 - opp.air_bet:
            return False
    for n in opp.called_on:
        if n <= len(board) and not _connects(hole, board[:n]) and rng.random() < 1 - opp.air_call:
            return False
    return True


def equity(hole: tuple[int, int], board: list[int], opponents: list[OpponentRange],
           iterations: int = 400, rng: random.Random | None = None) -> float:
    """Share of the pot won on average (ties split) vs all opponents at once."""
    rng = rng or random.Random()
    if not opponents:
        return 1.0
    known = {*hole, *board}
    total = 0.0
    for _ in range(iterations):
        dead = set(known)
        opp_hands = []
        for opp in opponents:
            h = None
            for _attempt in range(12):
                cand = sample_hand(opp.preflop_pct, dead, rng)
                if _fits_line(cand, board, opp, rng):
                    h = cand
                    break
            if h is None:
                h = cand
            dead.update(h)
            opp_hands.append(h)
        live = [c for c in DECK if c not in dead]
        full_board = board + rng.sample(live, 5 - len(board))
        mine = evaluate([*hole, *full_board])
        ties, won = 1, True
        for h in opp_hands:
            v = evaluate([*h, *full_board])
            if v < mine:
                won = False
                break
            if v == mine:
                ties += 1
        if won:
            total += 1.0 / ties
    return total / iterations
