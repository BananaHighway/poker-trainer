"""Table + hand management on top of pokerkit.

Seat indices (0..5) are fixed around the table; seat 0 is always the hero. Each
hand, the seats that are in are ordered starting left of the button (button last),
which is the player order pokerkit expects: index 0 = SB (or BB heads-up, where
pokerkit makes the button post the small blind).

All amounts are integer chips with BB = 100 chips, so 2.5bb opens are exact.
"""

import random
import threading
import time
from dataclasses import dataclass, field

from pokerkit import Automation, Mode, NoLimitTexasHoldem

from .cards import card_id, card_name, category, evaluate

BB = 100
STREETS = ("preflop", "flop", "turn", "river")

POSITIONS = {
    2: ("BB", "BTN"),
    3: ("SB", "BB", "BTN"),
    4: ("SB", "BB", "CO", "BTN"),
    5: ("SB", "BB", "HJ", "CO", "BTN"),
    6: ("SB", "BB", "UTG", "HJ", "CO", "BTN"),
}

AUTOMATIONS = (
    Automation.ANTE_POSTING,
    Automation.BET_COLLECTION,
    Automation.BLIND_OR_STRADDLE_POSTING,
    Automation.CARD_BURNING,
    Automation.HOLE_DEALING,
    Automation.BOARD_DEALING,
    Automation.RUNOUT_COUNT_SELECTION,   # cash-game "run it twice" prompt: always run once
    Automation.HOLE_CARDS_SHOWING_OR_MUCKING,
    Automation.HAND_KILLING,
    Automation.CHIPS_PUSHING,
    Automation.CHIPS_PULLING,
)


@dataclass
class SeatConfig:
    kind: str = "empty"          # hero | bot | claude | empty
    name: str = ""
    style: str = "tag"           # heuristic style; for Claude seats, its preflop chart + fallback
    model: str = "claude-haiku-5-5"
    persona: str = ""


@dataclass
class TableConfig:
    seats: list[SeatConfig]
    starting_bb: float = 100
    sb_bb: float = 0.5
    auto_topup: bool = True
    claude_enabled: bool = False
    reveal_all_at_end: bool = False


@dataclass
class Decision:
    """Everything a bot may look at when it's its turn (no hidden information)."""
    seat: int
    position: str
    n_players: int
    hole: tuple[int, int]
    board: list[int]
    street: int
    pot: int                 # all chips committed this hand, including current-street bets
    to_call: int
    can_raise: bool
    min_to: int
    max_to: int
    stack: int               # behind, before acting
    my_bet: int              # already committed on this street
    street_max_bet: int
    players: list[dict]
    actions: list[dict]
    bb: int = BB

    @property
    def live_opponents(self) -> list[dict]:
        return [p for p in self.players if p["seat"] != self.seat and not p["folded"]]


@dataclass
class Seat:
    index: int
    cfg: SeatConfig
    stack: int = 0
    bought_in: int = 0
    bot: object | None = None


@dataclass
class Hand:
    number: int
    state: object
    order: list[int]
    positions: dict[int, str]
    hole: dict[int, tuple[int, int]]
    start_stacks: dict[int, int]
    button: int
    started_at: float
    actions: list[dict] = field(default_factory=list)
    folded: set[int] = field(default_factory=set)
    result: dict | None = None


class Table:
    def __init__(self, config: TableConfig, bots: dict[int, object], store=None, session_id=None):
        self.config = config
        self.store = store
        self.session_id = session_id
        self.lock = threading.RLock()
        self.seats = [Seat(i, cfg) for i, cfg in enumerate(config.seats)]
        for i, bot in bots.items():
            self.seats[i].bot = bot
        start = self.starting_chips
        for s in self.seats:
            if s.cfg.kind != "empty":
                s.stack = start
                s.bought_in = start
        occupied = [s.index for s in self.seats if s.cfg.kind != "empty"]
        self.button = random.choice(occupied)
        self.hand: Hand | None = None
        self.hands_played = 0
        self.hero_net = 0
        self.claude = {"calls": 0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "errors": 0}
        self.last_error: str | None = None

    # ---- setup helpers -------------------------------------------------
    @property
    def starting_chips(self) -> int:
        return int(round(self.config.starting_bb * BB))

    @property
    def sb_chips(self) -> int:
        return int(round(self.config.sb_bb * BB))

    @property
    def hero(self) -> Seat:
        return self.seats[0]

    def rebuy_hero(self) -> None:
        with self.lock:
            add = self.starting_chips - self.hero.stack
            if add > 0 and (self.hand is None or self.hand.result is not None):
                self.hero.stack += add
                self.hero.bought_in += add

    def _top_up(self) -> None:
        start = self.starting_chips
        for s in self.seats:
            if s.cfg.kind == "empty":
                continue
            # Bots top up to the starting stack like regulars on auto-rebuy, so stacks
            # stay near the table depth; the hero does when auto top-up is on.
            if s.cfg.kind == "hero" and not self.config.auto_topup:
                continue
            if s.stack < start:
                s.bought_in += start - s.stack
                s.stack = start

    # ---- hand lifecycle ------------------------------------------------
    def start_hand(self) -> None:
        with self.lock:
            if self.hand and self.hand.result is None:
                return
            self._top_up()
            occupied = [s.index for s in self.seats if s.cfg.kind != "empty" and s.stack > 0]
            if len(occupied) < 2:
                raise ValueError("Need at least two players with chips")
            # Move the button to the next occupied seat clockwise.
            ring = sorted(occupied)
            nxt = [i for i in ring if i > self.button]
            self.button = nxt[0] if nxt else ring[0]
            b = ring.index(self.button)
            order = ring[b + 1:] + ring[:b + 1]
            stacks = [self.seats[i].stack for i in order]
            state = NoLimitTexasHoldem.create_state(
                AUTOMATIONS, True, 0, (self.sb_chips, BB), BB, stacks, len(order),
                mode=Mode.CASH_GAME,
            )
            positions = dict(zip(order, POSITIONS[len(order)]))
            hole = {seat: tuple(card_id(repr(c)) for c in state.hole_cards[i]) for i, seat in enumerate(order)}
            self.hand = Hand(
                number=self.hands_played + 1, state=state, order=order, positions=positions,
                hole=hole, start_stacks={seat: self.seats[seat].stack for seat in order},
                button=self.button, started_at=time.time(),
            )
            sb_seat, bb_seat = (order[1], order[0]) if len(order) == 2 else (order[0], order[1])
            self._log(sb_seat, "post_sb", min(self.sb_chips, stacks[order.index(sb_seat)]), 0, 0)
            self._log(bb_seat, "post_bb", min(BB, stacks[order.index(bb_seat)]), 0, 0,
                      pot_before=min(self.sb_chips, stacks[order.index(sb_seat)]))

    def actor_seat(self) -> int | None:
        h = self.hand
        if not h or h.result is not None or h.state.actor_index is None:
            return None
        return h.order[h.state.actor_index]

    def _committed(self, seat: int) -> int:
        h = self.hand
        return h.start_stacks[seat] - h.state.stacks[h.order.index(seat)]

    def _pot(self) -> int:
        return sum(self._committed(seat) for seat in self.hand.order)

    def _board(self) -> list[int]:
        return [card_id(repr(c[0])) for c in self.hand.state.board_cards]

    def _street(self) -> int:
        idx = self.hand.state.street_index
        return 3 if idx is None else min(idx, 3)

    def _log(self, seat, action, amount, to, to_call, street=0, board=(), pot_before=0,
             stack_after=None, **extra) -> None:
        h = self.hand
        h.actions.append({
            "street": STREETS[street], "seat": seat, "name": self.seats[seat].cfg.name,
            "position": h.positions[seat], "action": action, "amount": amount, "to": to,
            "to_call": to_call, "pot_before": pot_before,
            "stack_after": h.state.stacks[h.order.index(seat)] if stack_after is None else stack_after,
            "board": [card_name(c) for c in board],
            **extra,
        })

    def decision_for(self, seat: int) -> Decision:
        h, s = self.hand, self.hand.state
        i = h.order.index(seat)
        can_raise = s.can_complete_bet_or_raise_to()
        players = [{
            "seat": st, "name": self.seats[st].cfg.name, "position": h.positions[st],
            "stack": s.stacks[j], "bet": s.bets[j], "folded": st in h.folded,
            "all_in": s.stacks[j] == 0, "kind": self.seats[st].cfg.kind,
        } for j, st in enumerate(h.order)]
        return Decision(
            seat=seat, position=h.positions[seat], n_players=len(h.order), hole=h.hole[seat],
            board=self._board(), street=self._street(), pot=self._pot(),
            to_call=s.checking_or_calling_amount or 0, can_raise=can_raise,
            min_to=s.min_completion_betting_or_raising_to_amount if can_raise else 0,
            max_to=s.max_completion_betting_or_raising_to_amount if can_raise else 0,
            stack=s.stacks[i], my_bet=s.bets[i], street_max_bet=max(s.bets),
            players=players, actions=list(h.actions),
        )

    def apply(self, seat: int, kind: str, to: int | None = None, **meta) -> None:
        """Apply an action for `seat`, coercing anything illegal to the nearest legal move."""
        with self.lock:
            h = self.hand
            if self.actor_seat() != seat:
                raise ValueError("Not this seat's turn")
            s = h.state
            to_call = s.checking_or_calling_amount or 0
            i = h.order.index(seat)
            before, my_bet = s.stacks[i], s.bets[i]
            street_had_bet = max(s.bets) > 0
            # Snapshot context *before* acting: a street-closing call advances pokerkit,
            # and a hand-ending action pays out immediately, so stacks after are unusable.
            meta.update(street=self._street(), board=self._board(), pot_before=self._pot())
            if kind == "fold" and to_call > 0:
                s.fold()
                h.folded.add(seat)
                self._log(seat, "fold", 0, 0, to_call, stack_after=before, **meta)
            elif kind in ("bet", "raise") and s.can_complete_bet_or_raise_to() and to is not None:
                lo = s.min_completion_betting_or_raising_to_amount
                hi = s.max_completion_betting_or_raising_to_amount
                to = max(lo, min(hi, int(to)))
                s.complete_bet_or_raise_to(to)
                label = "raise" if street_had_bet else "bet"
                added = to - my_bet
                self._log(seat, label, added, to, to_call, stack_after=before - added,
                          all_in=added == before, **meta)
            else:
                s.check_or_call()
                added = min(to_call, before)
                label = "call" if to_call > 0 else "check"
                self._log(seat, label, added, 0, to_call, stack_after=before - added,
                          all_in=to_call > 0 and added == before, **meta)
            if not s.status:
                self._finish()

    def bot_step(self) -> dict | None:
        """If a bot is to act, let it act once. Returns the action taken (or None)."""
        with self.lock:
            seat = self.actor_seat()
            if seat is None or self.seats[seat].cfg.kind in ("hero", "empty"):
                return None
            bot = self.seats[seat].bot
            ctx = self.decision_for(seat)
            kind, to, meta = bot.decide(ctx, claude_enabled=self.config.claude_enabled)
            usage = meta.pop("usage", None)
            if usage:
                self._record_claude(seat, usage, meta)
            self.apply(seat, kind, to, **meta)
            return self.hand.actions[-1]

    def _record_claude(self, seat: int, usage: dict, meta: dict) -> None:
        self.claude["calls"] += 1
        self.claude["cost_usd"] += usage.get("cost_usd", 0.0)
        self.claude["input_tokens"] += usage.get("input_tokens", 0) + usage.get("cache_read", 0) + usage.get("cache_write", 0)
        self.claude["output_tokens"] += usage.get("output_tokens", 0)
        if usage.get("error"):
            self.claude["errors"] += 1
            self.last_error = usage["error"]
        if self.store:
            self.store.save_claude_call(self.session_id, self.hand.number, seat, usage, meta)

    def _finish(self) -> None:
        h, s = self.hand, self.hand.state
        board = self._board()
        net = {seat: s.stacks[i] - h.start_stacks[seat] for i, seat in enumerate(h.order)}
        live = [seat for seat in h.order if seat not in h.folded]
        showdown = len(live) >= 2
        shown, hand_desc = {}, {}
        if showdown:
            for seat in live:
                shown[seat] = [card_name(c) for c in h.hole[seat]]
                if len(board) == 5:
                    hand_desc[seat] = category(evaluate([*h.hole[seat], *board]))
        winners = [{"seat": seat, "name": self.seats[seat].cfg.name, "amount": net[seat],
                    "hand": hand_desc.get(seat)} for seat in h.order if net[seat] > 0]
        for i, seat in enumerate(h.order):
            self.seats[seat].stack = s.stacks[i]
            # Bots can react to how the hand went (a big loss tilts them for a while).
            hand_over = getattr(self.seats[seat].bot, "hand_over", None)
            if hand_over:
                hand_over(net[seat], showdown and seat in live)
        # Pot actually awarded (excludes any uncalled bet returned to its owner).
        pot = sum(sum(op.amounts) for op in s.operations if type(op).__name__ == "ChipsPushing")
        h.result = {"net": net, "showdown": showdown, "shown": shown, "winners": winners,
                    "hand_desc": hand_desc, "pot": pot}
        self.hands_played += 1
        if 0 in net:
            self.hero_net += net[0]
        if self.store:
            self.store.save_hand(self.session_id, self.hand_record())

    # ---- serialisation ---------------------------------------------------
    def hand_record(self) -> dict:
        """Full-information record of the current/last hand (for storage + analysis)."""
        h = self.hand
        return {
            "number": h.number, "started_at": h.started_at, "button": h.button,
            "bb": BB, "sb": self.sb_chips,
            "players": [{
                "seat": seat, "name": self.seats[seat].cfg.name, "kind": self.seats[seat].cfg.kind,
                "style": self.seats[seat].cfg.style, "model": self.seats[seat].cfg.model if self.seats[seat].cfg.kind == "claude" else None,
                "position": h.positions[seat], "start_stack": h.start_stacks[seat],
                "hole": [card_name(c) for c in h.hole[seat]],
            } for seat in h.order],
            "board": [card_name(c) for c in self._board()],
            "actions": h.actions,
            "result": h.result,
        }

    def snapshot(self) -> dict:
        """Hero's view of the table: hides other players' cards and Claude reasoning mid-hand."""
        with self.lock:
            h = self.hand
            finished = h is None or h.result is not None
            actor = self.actor_seat()
            seats = []
            for seat in self.seats:
                info = {"seat": seat.index, "kind": seat.cfg.kind, "name": seat.cfg.name,
                        "style": seat.cfg.style, "model": seat.cfg.model if seat.cfg.kind == "claude" else None,
                        "stack": seat.stack, "in_hand": False}
                if seat.cfg.kind != "empty" and h and seat.index in h.order:
                    i = h.order.index(seat.index)
                    info.update({
                        "in_hand": True, "position": h.positions[seat.index],
                        "stack": seat.stack if finished else h.state.stacks[i],
                        "bet": 0 if finished else h.state.bets[i],
                        "folded": seat.index in h.folded,
                        "is_button": seat.index == h.button,
                        "is_actor": seat.index == actor,
                    })
                    show = seat.index == 0 or (finished and h.result and (
                        seat.index in h.result["shown"] or self.config.reveal_all_at_end))
                    info["cards"] = [card_name(c) for c in h.hole[seat.index]] if show else None
                    if finished and h.result:
                        info["net"] = h.result["net"].get(seat.index, 0)
                        info["hand_desc"] = h.result["hand_desc"].get(seat.index)
                seats.append(info)
            log = []
            if h:
                for a in h.actions:
                    entry = {k: v for k, v in a.items() if k != "reason" or finished}
                    log.append(entry)
            legal = None
            if h and actor == 0:
                d = self.decision_for(0)
                legal = {"to_call": d.to_call, "can_check": d.to_call == 0,
                         "can_raise": d.can_raise, "min_to": d.min_to, "max_to": d.max_to,
                         "raise_kind": "raise" if d.street_max_bet > 0 else "bet",
                         "my_bet": d.my_bet, "stack": d.stack}
            return {
                "bb": BB,
                "session": {
                    "hands": self.hands_played,
                    "hero_net": self.hero_net,
                    "bb_per_100": round(self.hero_net / BB / self.hands_played * 100, 1) if self.hands_played else 0.0,
                    "hero_bought_in": self.hero.bought_in,
                    "claude_enabled": self.config.claude_enabled,
                    "claude": self.claude,
                    "last_error": self.last_error,
                },
                "hand": None if not h else {
                    "number": h.number, "finished": finished,
                    "street": None if finished else STREETS[self._street()],
                    "board": [card_name(c) for c in self._board()],
                    "pot": h.result["pot"] if finished else self._pot(),
                    "actor": actor, "result": h.result if finished else None,
                },
                "seats": seats, "log": log, "legal": legal,
                "hero_busted": self.hero.stack <= 0 and finished,
            }
