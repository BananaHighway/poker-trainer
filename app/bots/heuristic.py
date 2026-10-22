"""Style-driven heuristic bot.

Preflop: hand percentile vs position/situation range widths from its Style.
Postflop: range-aware Monte Carlo equity, then pot odds / value / bluff rules,
all scaled by the Style and by position. Each bot plays its own personalized copy
of the style, mixes its play near range borders, now and then plays a hand from
well outside its ranges, and loosens up for a while after losing a big pot.
"""

import math
import random

from ..engine import BB, Decision
from ..equity import equity
from ..ranges import percentile
from .reads import (estimate_range, initiative, open_width, preflop_spot, threebet_continue,
                    vs_open)
from .styles import Style, get_style, personalize, tilted


class HeuristicBot:
    def __init__(self, style: str, seed: int | None = None):
        self.rng = random.Random(seed)
        self.base: Style = personalize(get_style(style), self.rng)
        self.tilt = 0.0

    @property
    def style(self) -> Style:
        return tilted(self.base, self.tilt) if self.tilt > 0.05 else self.base

    def decide(self, ctx: Decision, claude_enabled: bool = False):
        if ctx.street == 0:
            kind, to = self.preflop(ctx)
        else:
            kind, to = self.postflop(ctx)
        return kind, to, {"source": "bot"}

    def hand_over(self, net: int, showdown: bool) -> None:
        """Called by the table after each hand: a big loss tilts the bot, which wears off."""
        self.tilt *= 0.8
        if net <= -25 * BB:
            hit = self.base.tilt * (-net / (50 * BB)) * (1.4 if showdown else 1.0)
            self.tilt = min(1.0, self.tilt + hit)

    # ---- helpers ---------------------------------------------------------
    def _noisy(self, x: float, sd: float = 0.08) -> float:
        return x * math.exp(self.rng.gauss(0, sd))

    def _hand(self, ctx: Decision) -> float:
        """Hand percentile as the bot sees it this time. The noise mixes hands near a
        range border; `wild` occasionally plays one from well outside."""
        p = self._noisy(percentile(*ctx.hole), 0.15)
        if self.rng.random() < self.style.wild:
            p *= self.rng.random()
        return p

    def _raise_to(self, ctx: Decision, to: int):
        """Raise to `to`, shoving instead if it commits most of the stack."""
        if not ctx.can_raise:
            return ("call", None) if ctx.to_call else ("check", None)
        total = ctx.stack + ctx.my_bet
        if to >= 0.45 * total:
            to = ctx.max_to
        return ("raise" if ctx.street_max_bet else "bet"), max(ctx.min_to, min(ctx.max_to, int(to)))

    def _passive(self, ctx: Decision, call_ok: bool):
        if ctx.to_call == 0:
            return "check", None
        return ("call", None) if call_ok else ("fold", None)

    @staticmethod
    def _action_order(ctx: Decision) -> list[int]:
        """Seats that can still act, in postflop order (ctx.players runs SB ... BTN)."""
        return [p["seat"] for p in ctx.players if not p["folded"] and not p["all_in"]]

    # ---- preflop ---------------------------------------------------------
    def preflop(self, ctx: Decision):
        st, spot = self.style, preflop_spot(ctx)
        p = self._hand(ctx)
        pos, n = ctx.position, ctx.n_players
        pot_odds = ctx.to_call / (ctx.pot + ctx.to_call) if ctx.to_call else 0
        commit = ctx.to_call / max(1, ctx.stack + ctx.my_bet)

        if spot.raises == 0:
            if pos == "BB":
                if ctx.to_call == 0 and p < open_width("CO", n) * st.open_mult * 0.6:
                    return self._raise_to(ctx, BB * (3.5 + spot.limpers))
                return self._passive(ctx, False)
            width = min(1.0, open_width(pos, n) * st.open_mult) * (0.8 if spot.limpers else 1.0)
            if p < width:
                size = st.open_size + (0.5 if pos == "SB" and n > 2 else 0)
                return self._raise_to(ctx, BB * (size + spot.limpers))
            if p < width + st.limp or (pos == "SB" and p < width * 1.5 and st.limp > 0):
                return self._passive(ctx, True)
            return self._passive(ctx, False)

        if spot.raises == 1:
            three, cont = vs_open(pos, spot.opener_position, spot.open_to / BB, n,
                                  spot.callers_since_raise, style=st)
            if p < three or (p < 0.5 and self.rng.random() < st.bluff * 0.25):
                mult = 3.0 if pos not in ("SB", "BB") else 3.8
                return self._raise_to(ctx, spot.last_raise_to * mult + spot.last_raise_to * spot.callers_since_raise)
            if p < cont and commit < 0.35:
                return self._passive(ctx, True)
            return self._passive(ctx, False)

        if spot.raises == 2:
            if spot.i_raised:
                # Opened and got 3-bet: keep defending part of the range we opened.
                villain = vs_open(spot.last_raiser_position, spot.opener_position, spot.open_to / BB, n)[0]
                cont = threebet_continue(min(1.0, open_width(pos, n) * st.open_mult), villain, st.fold_3bet)
            else:
                cont = st.call_3bet
            if p < st.fourbet:
                return self._raise_to(ctx, spot.last_raise_to * 2.3)
            if p < cont and (commit < 0.3 or p < st.fourbet * 2):
                return self._passive(ctx, True)
            return self._passive(ctx, False)

        # Facing a 4-bet or more: continue only with the top of range.
        if p < st.fourbet * 0.5:
            return self._raise_to(ctx, ctx.max_to)
        if p < st.fourbet and pot_odds < 0.4:
            return self._passive(ctx, True)
        return self._passive(ctx, False)

    # ---- postflop --------------------------------------------------------
    def postflop(self, ctx: Decision):
        st = self.style
        opps = ctx.live_opponents
        if not opps:
            return self._passive(ctx, True)
        ranges = [estimate_range(o["seat"], ctx) for o in opps]
        eq = equity(ctx.hole, ctx.board, ranges, iterations=300 if len(opps) > 2 else 400, rng=self.rng)
        strength = eq ** (1 / len(opps))
        draw = ctx.street < 3 and self._has_draw(ctx)
        pot = ctx.pot
        rnd = self.rng.random
        order = self._action_order(ctx)
        ip = bool(order) and order[-1] == ctx.seat

        if ctx.to_call > 0:
            pot_odds = ctx.to_call / (pot + ctx.to_call)
            # With streets to come, raw equity overstates what a call wins, more so out of position.
            realise = 1.0 if ctx.street == 3 else 0.95 if ip else 0.82
            need = pot_odds * st.looseness / realise - (0.05 if draw else 0)
            if ctx.can_raise and strength > self._noisy(st.raise_value, 0.03) and rnd() < 0.45 + 0.5 * st.aggression:
                return self._raise_to(ctx, ctx.street_max_bet + (pot + ctx.to_call) * self._size(strength))
            if ctx.can_raise and draw and rnd() < st.bluff * 0.5:
                return self._raise_to(ctx, ctx.street_max_bet + (pot + ctx.to_call) * 0.8)
            return self._passive(ctx, eq >= need)

        # Nobody has bet yet this street.
        agg = initiative(ctx)
        live = {o["seat"] for o in opps}
        # Out of position to last street's aggressor, most players check to them.
        check_to_agg = agg in live and agg in order and order.index(agg) > order.index(ctx.seat)
        lead = 1.0 if not check_to_agg else st.donk
        value = self._noisy(st.value_bet, 0.035) - (0.06 * st.aggression if ip and ctx.street == 3 else 0)
        if strength > value and rnd() < (0.55 + 0.45 * st.aggression) * lead:
            return self._raise_to(ctx, pot * self._size(strength))
        if agg == ctx.seat:
            # Keep the initiative: c-bet the flop, barrel some turns, bluff some rivers.
            freq = st.cbet * (1.0 if len(opps) == 1 else 0.6) * (1.0 if ip else 0.8)
            if ctx.street == 2:
                freq *= 0.7 if draw or strength > 0.4 else 0.45
            elif ctx.street == 3:
                freq *= 0.4 if strength < 0.3 else 0.0
            if rnd() < freq:
                return self._raise_to(ctx, pot * (min(st.sizing) if ctx.street == 1 else self.rng.choice(st.sizing)))
        elif agg is None and ctx.street > 1 and strength > 0.5 and rnd() < (0.3 + 0.4 * st.aggression) * lead:
            # Last street checked through: take a stab with a decent hand.
            return self._raise_to(ctx, pot * min(st.sizing))
        # Checked to in position: a stab takes down a lot of pots nobody wants.
        stab = 1.6 if ip and agg != ctx.seat else lead
        if strength < 0.45 and rnd() < st.bluff * (1.3 if draw else 0.6) * stab / len(opps):
            return self._raise_to(ctx, pot * self.rng.choice(st.sizing))
        return "check", None

    def _size(self, strength: float) -> float:
        sizes = sorted(self.style.sizing)
        if strength > 0.85:
            return sizes[-1]
        return sizes[len(sizes) // 2] if self.rng.random() < 0.6 else self.rng.choice(sizes)

    @staticmethod
    def _has_draw(ctx: Decision) -> bool:
        from ..cards import rank, suit
        cards = [*ctx.hole, *ctx.board]
        for s in range(4):
            if sum(1 for c in cards if suit(c) == s) == 4 and any(suit(c) == s for c in ctx.hole):
                return True
        ranks = {rank(c) for c in cards} | ({-1} if any(rank(c) == 12 for c in cards) else set())
        hole = {rank(c) for c in ctx.hole}
        for lo in range(-1, 9):
            window = {r for r in range(lo, lo + 5) if r in ranks}
            if len(window) == 4 and window & (hole | ({-1} if 12 in hole else set())):
                return True
        return False
