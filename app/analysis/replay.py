"""Rebuild the hero's decision points from stored hand records.

A stored record has every action with the pot/price/stack at that moment, so each
hero decision can be re-examined later with exactly the information they had.
"""

from dataclasses import dataclass
from types import SimpleNamespace

from ..cards import card_id
from ..engine import STREETS

HERO = 0


@dataclass
class Spot:
    record: dict
    index: int               # position of this action in record["actions"]
    action: dict
    prior: list[dict]        # actions before this one
    street: int
    hole: tuple[int, int]
    board: list[int]
    position: str
    n_players: int
    to_call: int
    pot_before: int
    stack_before: int
    opponents: list[dict]    # players still live at this moment (excluding hero)

    @property
    def ctx(self):
        """Duck-typed stand-in for engine.Decision, for bots.reads helpers."""
        return SimpleNamespace(actions=self.prior, n_players=self.n_players, seat=HERO,
                               street=self.street)

    @property
    def price(self) -> float:
        return self.to_call / (self.pot_before + self.to_call) if self.to_call else 0.0


def hero_player(record: dict) -> dict | None:
    return next((p for p in record["players"] if p["seat"] == HERO), None)


def hero_spots(record: dict):
    me = hero_player(record)
    if not me:
        return
    hole = tuple(card_id(c) for c in me["hole"])
    folded: set[int] = set()
    actions = record["actions"]
    for i, a in enumerate(actions):
        if a["action"] in ("post_sb", "post_bb"):
            continue
        if a["seat"] == HERO:
            yield Spot(
                record=record, index=i, action=a, prior=actions[:i],
                street=STREETS.index(a["street"]), hole=hole,
                board=[card_id(c) for c in a["board"]], position=me["position"],
                n_players=len(record["players"]), to_call=a["to_call"],
                pot_before=a["pot_before"], stack_before=a["stack_after"] + a["amount"],
                opponents=[p for p in record["players"] if p["seat"] != HERO and p["seat"] not in folded],
            )
        if a["action"] == "fold":
            folded.add(a["seat"])
