"""Style-driven heuristic bot.

Preflop: hand percentile vs position/situation range widths from its Style.
Postflop: range-aware Monte Carlo equity, then pot odds / value / bluff rules,
all scaled by the Style. Small random noise on every threshold keeps it from
being perfectly predictable at range borders.
"""

import random

from ..engine import BB, Decision
from ..equity import equity
from ..ranges import percentile
from .reads import estimate_range, open_width, preflop_aggressor, preflop_spot, street_actions
from .styles import Style, get_style


class HeuristicBot:
    def __init__(self, style: str, seed: int | None = None):
        self.style: Style = get_style(style)
        self.rng = random.Random(seed)

    def decide(self, ctx: Decision, claude_enabled: bool = False):
        if ctx.street == 0:
            kind, to = self.preflop(ctx)
        else:
            kind, to = self.postflop(ctx)
        return kind, to, {"source": "bot"}

    # ---- helpers ---------------------------------------------------------
    def _noisy(self, x: float, spread: float = 0.12) -> float:
        return x * (1 + self.rng.uniform(-spread, spread))

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

    # ---- preflop ---------------------------------------------------------
    def preflop(self, ctx: Decision):
        st, spot = self.style, preflop_spot(ctx)
        p = self._noisy(percentile(*ctx.hole))
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
                size = 3.0 if pos == "SB" and n > 2 else 2.5
                return self._raise_to(ctx, BB * (size + spot.limpers))
            if p < width + st.limp or (pos == "SB" and p < width * 1.5 and st.limp > 0):
                return self._passive(ctx, True)
            return self._passive(ctx, False)

        open_bb = spot.last_raise_to / BB
        if spot.raises == 1:
            # Wider openers get defended wider; bigger opens get defended tighter.
            f = (open_width(spot.opener_position or "CO", n) / 0.27) ** 0.6
            f = max(0.6, min(2.5, f)) * min(1.0, (2.5 / max(open_bb, 2.5)) ** 0.7)
            three = st.threebet * f * (0.8 ** spot.callers_since_raise)
            call = st.call_open * f
            if pos == "BB":
                call *= 2.0 if n > 2 else 3.0
            elif pos == "SB" and n > 2:
                call *= 0.5
            if p < three or (p < 0.5 and self.rng.random() < st.bluff * 0.25):
                ip = pos in ("BTN", "CO") and n > 2
                mult = 3.0 if ip else 3.8
                return self._raise_to(ctx, spot.last_raise_to * mult + spot.last_raise_to * spot.callers_since_raise)
            if p < three + call and commit < 0.35:
                return self._passive(ctx, True)
            return self._passive(ctx, False)

        if spot.raises == 2:
            if p < st.fourbet:
                return self._raise_to(ctx, spot.last_raise_to * 2.3)
            if p < st.call_3bet and (commit < 0.3 or p < st.fourbet * 2):
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

        if ctx.to_call > 0:
            pot_odds = ctx.to_call / (pot + ctx.to_call)
            need = pot_odds * st.looseness - (0.05 if draw else 0)
            if ctx.can_raise and strength > self._noisy(st.raise_value, 0.05) and rnd() < 0.45 + 0.5 * st.aggression:
                return self._raise_to(ctx, ctx.street_max_bet + (pot + ctx.to_call) * self._size(strength))
            if ctx.can_raise and draw and rnd() < st.bluff * 0.5:
                return self._raise_to(ctx, ctx.street_max_bet + (pot + ctx.to_call) * 0.8)
            return self._passive(ctx, eq >= need)

        pfr = preflop_aggressor(ctx) == ctx.seat
        checked_to = not any(a["action"] in ("bet", "raise") for a in street_actions(ctx))
        if strength > self._noisy(st.value_bet, 0.06) and rnd() < 0.55 + 0.45 * st.aggression:
            return self._raise_to(ctx, pot * self._size(strength))
        if pfr and ctx.street == 1 and checked_to and rnd() < st.cbet * (1.0 if len(opps) == 1 else 0.6):
            return self._raise_to(ctx, pot * min(st.sizing))
        if strength < 0.45 and rnd() < st.bluff * (1.3 if draw else 0.6) / len(opps):
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
