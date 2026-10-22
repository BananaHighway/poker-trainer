"""Style-driven heuristic bot.

Preflop: hand percentile vs position/situation range widths from its Style.
Postflop: range-aware Monte Carlo equity, then pot odds / value / bluff rules,
all scaled by the Style and by position. Each bot plays its own personalized copy
of the style, mixes its play near range borders, now and then plays a hand from
well outside its ranges, and loosens up for a while after losing a big pot.

Against a player it has notes on (the hero's profile, see profile.py) it reads their
ranges from their real tendencies and leans on their leaks: 3-betting players who
over-fold to 3-bets, stealing from blinds that over-fold, isolating limpers, and
c-betting and barrelling players who give up. An action taken only because of a
read carries a "read" label, shown in the hand log once the hand is over.
"""

import math
import random

from ..engine import BB, Decision
from ..equity import equity
from ..ranges import percentile
from .profile import Read
from .reads import (estimate_range, initiative, open_width, preflop_spot, threebet_continue,
                    vs_open)
from .styles import Style, get_style, personalize, tilted


class HeuristicBot:
    def __init__(self, style: str, seed: int | None = None):
        self.rng = random.Random(seed)
        self.base: Style = personalize(get_style(style), self.rng)
        self.tilt = 0.0
        self._why: str | None = None  # label for the current action, if a read decided it

    @property
    def style(self) -> Style:
        return tilted(self.base, self.tilt) if self.tilt > 0.05 else self.base

    def decide(self, ctx: Decision, claude_enabled: bool = False):
        self._why = None
        if ctx.street == 0:
            kind, to = self.preflop(ctx)
        else:
            kind, to = self.postflop(ctx)
        meta = {"source": "bot"}
        if self._why and kind not in ("fold", "check"):
            meta["read"] = self._why
        return kind, to, meta

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
            self._why = None
            return ("call", None) if ctx.to_call else ("check", None)
        total = ctx.stack + ctx.my_bet
        if to >= 0.45 * total:
            to = ctx.max_to
        return ("raise" if ctx.street_max_bet else "bet"), max(ctx.min_to, min(ctx.max_to, int(to)))

    def _passive(self, ctx: Decision, call_ok: bool):
        if ctx.to_call == 0:
            return "check", None
        return ("call", None) if call_ok else ("fold", None)

    def _read(self, ctx: Decision, seat: int | None) -> Read | None:
        """This bot's read on `seat`, if it has notes on that player and pays attention to them."""
        profile = ctx.profiles.get(seat) if seat is not None and seat != ctx.seat else None
        return profile.read(self.style.adapt) if profile and self.style.adapt > 0 else None

    def _first_read(self, ctx: Decision, seats) -> Read | None:
        return next((r for r in (self._read(ctx, s) for s in seats) if r), None)

    def _beats(self, p: float, base: float, adjusted: float, why: str | None) -> bool:
        """p < adjusted, labelling the action `why` when only the read (base -> adjusted)
        made it so and the read moved the line noticeably."""
        if p >= adjusted:
            return False
        if p >= base and why and adjusted > base * 1.05:
            self._why = why
        return True

    def _roll(self, base: float, adjusted: float, why: str | None) -> bool:
        """A random event with probability `adjusted`, labelled `why` when it only
        happened because a read raised the probability above `base`."""
        return self._beats(self.rng.random(), base, adjusted, why)

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
            # Notes on a limper widen the isolation raise; folded to, notes on a blind
            # that over-folds widen the steal.
            mult, why = 1.0, None
            blinds = [o["seat"] for o in ctx.live_opponents if o["position"] in ("SB", "BB")]
            limper = self._first_read(ctx, spot.limper_seats)
            blind = self._first_read(ctx, blinds) if not spot.limpers and pos in ("CO", "BTN", "SB") else None
            if limper:
                mult, why = limper.iso_mult, f"Isolation raise: {limper.why('limp')}"
            elif blind:
                mult, why = blind.steal_mult, f"Steal: {blind.why('bb_fold')}"
            if pos == "BB":
                width = open_width("CO", n) * st.open_mult * 0.6
                if ctx.to_call == 0 and self._beats(p, width, width * mult, why):
                    return self._raise_to(ctx, BB * (3.5 + spot.limpers))
                return self._passive(ctx, False)
            width = min(1.0, open_width(pos, n) * st.open_mult) * (0.8 if spot.limpers else 1.0)
            if self._beats(p, width, min(1.0, width * mult), why):
                size = st.open_size + (0.5 if pos == "SB" and n > 2 else 0)
                return self._raise_to(ctx, BB * (size + spot.limpers))
            if p < width + st.limp or (pos == "SB" and p < width * 1.5 and st.limp > 0):
                return self._passive(ctx, True)
            return self._passive(ctx, False)

        if spot.raises == 1:
            args = (pos, spot.opener_position, spot.open_to / BB, n, spot.callers_since_raise)
            three, cont = vs_open(*args, style=st)
            three_r, cont_r, light, why3, why_def = three, cont, 1.0, None, None
            opener = self._read(ctx, spot.last_raiser)
            if opener:
                # Defend against how wide they really open; 3-bet light if they over-fold to 3-bets.
                three_r, cont_r = vs_open(*args, style=st, opener_width=opener.open_range(spot.opener_position))
                light = opener.threebet_mult
                three_r *= max(1.0, light)
                why_def = f"Wider defence: {opener.why_open(spot.opener_position)}"
                why3 = (f"Light 3-bet: {opener.why('fold_3bet')}" if light > 1.05
                        else f"3-bet wider: {opener.why_open(spot.opener_position)}")
            bluff3 = st.bluff * 0.25
            if self._beats(p, three, three_r, why3) or (p < 0.5 and self._roll(bluff3, bluff3 * light, why3)):
                mult = 3.0 if pos not in ("SB", "BB") else 3.8
                return self._raise_to(ctx, spot.last_raise_to * mult + spot.last_raise_to * spot.callers_since_raise)
            if commit < 0.35 and self._beats(p, cont, cont_r, why_def):
                return self._passive(ctx, True)
            return self._passive(ctx, False)

        if spot.raises == 2:
            why = None
            if spot.i_raised:
                # Opened and got 3-bet: keep defending part of the range we opened, wider
                # against a player we know 3-bets a lot.
                villain = vs_open(spot.last_raiser_position, spot.opener_position, spot.open_to / BB, n)[0]
                mine = min(1.0, open_width(pos, n) * st.open_mult)
                cont = cont_r = threebet_continue(mine, villain, st.fold_3bet)
                three_bettor = self._read(ctx, spot.last_raiser)
                if three_bettor:
                    cont_r = threebet_continue(mine, villain * three_bettor.scale("three_bet"), st.fold_3bet)
                    why = f"Defending vs your 3-bet: {three_bettor.why('three_bet')}"
            else:
                cont = cont_r = st.call_3bet
            if p < st.fourbet:
                return self._raise_to(ctx, spot.last_raise_to * 2.3)
            if (commit < 0.3 or p < st.fourbet * 2) and self._beats(p, cont, cont_r, why):
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
        reads = [self._read(ctx, o["seat"]) for o in opps]
        ranges = [estimate_range(o["seat"], ctx, r) for o, r in zip(opps, reads)]
        eq = equity(ctx.hole, ctx.board, ranges, iterations=300 if len(opps) > 2 else 400, rng=self.rng)
        strength = eq ** (1 / len(opps))
        draw = ctx.street < 3 and self._has_draw(ctx)
        pot = ctx.pot
        rnd = self.rng.random
        order = self._action_order(ctx)
        ip = bool(order) and order[-1] == ctx.seat
        # Leaning on someone's folds only makes sense heads-up against them.
        solo = reads[0] if len(opps) == 1 else None

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
        thin = solo.value_shift if solo else 0.0
        if strength > value - thin and rnd() < (0.55 + 0.45 * st.aggression) * lead:
            if strength <= value and thin > 0.02:
                self._why = f"Thin value bet: {solo.why('wtsd')}"
            return self._raise_to(ctx, pot * self._size(strength))
        # Notes on how often they fold: to c-bets for the flop, to bets on later streets.
        folds, why = 1.0, None
        if solo:
            folds, why = ((solo.cbet_mult, solo.why("fold_cbet")) if ctx.street == 1
                          else (solo.barrel_mult, solo.why("fold_late")))
        if agg == ctx.seat:
            # Keep the initiative: c-bet the flop, barrel some turns, bluff some rivers.
            freq = st.cbet * (1.0 if len(opps) == 1 else 0.6) * (1.0 if ip else 0.8)
            if ctx.street == 2:
                freq *= 0.7 if draw or strength > 0.4 else 0.45
            elif ctx.street == 3:
                freq *= 0.4 if strength < 0.3 else 0.0
            if self._roll(freq, freq * folds, why and f"{'C-bet' if ctx.street == 1 else 'Barrel'}: {why}"):
                return self._raise_to(ctx, pot * (min(st.sizing) if ctx.street == 1 else self.rng.choice(st.sizing)))
        elif agg is None and ctx.street > 1 and strength > 0.5 and rnd() < (0.3 + 0.4 * st.aggression) * lead:
            # Last street checked through: take a stab with a decent hand.
            return self._raise_to(ctx, pot * min(st.sizing))
        # Checked to in position: a stab takes down a lot of pots nobody wants.
        stab = 1.6 if ip and agg != ctx.seat else lead
        if strength < 0.45:
            freq = st.bluff * (1.3 if draw else 0.6) * stab / len(opps)
            if self._roll(freq, freq * folds, why and f"Bluff: {why}"):
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
