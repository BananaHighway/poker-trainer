"""Card encoding shared by the bots and the equity engine.

Cards are ints 0..51 = rank_index * 4 + suit_index (ranks 2..A, suits c d h s),
which is the native encoding of phevaluator, so evaluation needs no conversion.
"""

from phevaluator import evaluate_cards

RANKS = "23456789TJQKA"
SUITS = "cdhs"
DECK = tuple(range(52))


def card_id(name: str) -> int:
    return RANKS.index(name[0].upper()) * 4 + SUITS.index(name[1].lower())


def card_name(cid: int) -> str:
    return RANKS[cid // 4] + SUITS[cid % 4]


def rank(cid: int) -> int:
    return cid // 4


def suit(cid: int) -> int:
    return cid % 4


def hand_class(c1: int, c2: int) -> str:
    """169-class label: 'AA', 'AKs', 'T9o'."""
    r1, r2 = rank(c1), rank(c2)
    if r1 < r2:
        r1, r2 = r2, r1
    if r1 == r2:
        return RANKS[r1] * 2
    return RANKS[r1] + RANKS[r2] + ("s" if suit(c1) == suit(c2) else "o")


def evaluate(cards) -> int:
    """phevaluator rank for 5-7 cards: lower is stronger (1 = royal flush)."""
    return evaluate_cards(*cards)


# phevaluator rank boundaries (inclusive upper bound of each category).
_CATEGORIES = (
    (10, "Straight flush"),
    (166, "Four of a kind"),
    (322, "Full house"),
    (1599, "Flush"),
    (1609, "Straight"),
    (2467, "Three of a kind"),
    (3325, "Two pair"),
    (6185, "One pair"),
    (7462, "High card"),
)


def category(value: int) -> str:
    if value == 1:
        return "Royal flush"
    for bound, label in _CATEGORIES:
        if value <= bound:
            return label
    return "High card"
