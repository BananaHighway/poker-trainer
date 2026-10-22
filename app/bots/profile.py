"""What the regulars know about you: your tendencies, from every hand you've played.

A Profile keeps recency-weighted tracker counts for the hero, built from the stored
hand history (all sessions of the same format, 6-max or heads-up) and updated after
every hand. It uses only what an opponent at the table could see: the hero's actions
and public results, never the cards they folded.

Bots look at it through a Read. Each rate is shrunk toward a typical regular's until
there's enough sample, then blended by how much attention that bot pays
(Style.adapt). The Read turns leaks into counters (3-bet you light if you over-fold to
3-bets, steal more if you over-fold your big blind, ...) and into the range estimates
behind every equity calculation.
"""

from collections import defaultdict
from dataclasses import dataclass

from ..analysis.stats import hand_flags
from .styles import BASE_OPEN, HU_BUTTON_OPEN

HERO = 0
HALF_LIFE = 400  # hands: a hand this many hands ago counts half as much as the latest
DECAY = 0.5 ** (1 / HALF_LIFE)
OPEN_SEATS = ("UTG", "HJ", "CO", "BTN", "SB")
RFI_WEIGHT = 12  # prior weight for opening ranges, per position


@dataclass(frozen=True)
class Stat:
    label: str
    why: str          # how a bot words this read in the hand log
    prior: float      # a typical 6-max regular
    prior_hu: float   # a typical heads-up regular
    k: float          # prior weight, in opportunities


STATS = {
    "limp": Stat("Open-limp", "you open-limp {}", 0.03, 0.05, 20),
    "bb_fold": Stat("Fold BB to an open", "you fold your big blind to opens {}", 0.52, 0.35, 15),
    "cold_call": Stat("Cold-call an open", "you cold-call opens {}", 0.10, 0.10, 15),
    "three_bet": Stat("3-bet", "you 3-bet {}", 0.08, 0.12, 20),
    "fold_3bet": Stat("Fold to 3-bet", "you fold to 3-bets {}", 0.50, 0.45, 15),
    "fold_cbet": Stat("Fold to c-bet", "you fold to c-bets {}", 0.45, 0.40, 15),
    "fold_late": Stat("Fold to turn/river bets", "you fold to turn and river bets {}", 0.45, 0.42, 15),
    "wtsd": Stat("Went to showdown", "you go to showdown {}", 0.28, 0.35, 20),
    # Postflop bets+raises as a share of bets, raises and calls (AF 2.3 is about 0.7).
    "aggr": Stat("Postflop aggression", "{} of your postflop actions are bets or raises", 0.70, 0.70, 20),
}


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


class Profile:
    def __init__(self, heads_up: bool):
        self.heads_up = heads_up
        self.hands = 0
        self._opp: dict[str, float] = defaultdict(float)
        self._done: dict[str, float] = defaultdict(float)

    @classmethod
    def from_records(cls, records, heads_up: bool) -> "Profile":
        """Build from stored hand records, oldest first."""
        profile = cls(heads_up)
        for rec in records:
            profile.add_hand(rec)
        return profile

    def add_hand(self, rec: dict) -> None:
        if (len(rec["players"]) == 2) != self.heads_up or all(p["seat"] != HERO for p in rec["players"]):
            return
        for key in self._opp:
            self._opp[key] *= DECAY
            self._done[key] *= DECAY
        self.hands += 1
        f = hand_flags(rec)
        if f["first_in"]:
            self._add(f"rfi_{f['first_in']}", 1, f["rfi"])
            self._add("limp", 1, f["limp"])
        self._add("bb_fold", f["bb_opp"], f["bb_fold"])
        self._add("cold_call", f["cold_opp"], f["cold_call"])
        self._add("three_bet", f["3bet_opp"], f["3bet"])
        self._add("fold_3bet", f["f3b_opp"], f["f3b"])
        self._add("fold_cbet", f["fcb_opp"], f["fcb"])
        self._add("fold_late", f["late_opp"], f["late_fold"])
        self._add("wtsd", f["saw_flop"], f["wtsd"])
        self._add("aggr", (f["aggr"] or 0) + (f["calls"] or 0), f["aggr"] or 0)

    def _add(self, key: str, opportunities, done) -> None:
        if opportunities:
            self._opp[key] += opportunities
            self._done[key] += done

    def prior(self, key: str) -> tuple[float, float]:
        """(typical rate, prior weight) for a stat."""
        if key.startswith("rfi_"):
            typical = HU_BUTTON_OPEN if self.heads_up else BASE_OPEN.get(key[4:], 0.2)
            return typical, RFI_WEIGHT
        s = STATS[key]
        return (s.prior_hu if self.heads_up else s.prior), s.k

    def spots(self, key: str) -> float:
        """Recency-weighted number of opportunities seen."""
        return self._opp.get(key, 0.0)

    def observed(self, key: str) -> float | None:
        n = self._opp.get(key, 0.0)
        return self._done.get(key, 0.0) / n if n else None

    def rate(self, key: str) -> float:
        """Best estimate: the observed rate, pulled toward typical until the sample is big."""
        typical, k = self.prior(key)
        return (self._done.get(key, 0.0) + k * typical) / (self.spots(key) + k)

    def read(self, adapt: float) -> "Read":
        return Read(self, adapt)

    def why(self, key: str) -> str:
        return STATS[key].why.format(_pct(self.rate(key)))

    def why_open(self, position: str) -> str:
        return f"you open {_pct(self.rate(f'rfi_{position}'))} from the {position}"

    # ---- presentation ----------------------------------------------------
    def describe(self) -> dict:
        """The reads for the Stats page, with what a fully attentive regular does about each."""
        r = self.read(1.0)

        def row(key, label, effect):
            typical, k = self.prior(key)
            obs = self.observed(key)
            return {"key": key, "label": label, "read": round(100 * self.rate(key)),
                    "observed": None if obs is None else round(100 * obs), "spots": round(self.spots(key)),
                    "typical": round(100 * typical), "confident": self.spots(key) >= k, "effect": effect}

        def more_less(mult, more, less, neutral="no adjustment yet"):
            if mult > 1.05:
                return more.format(f"{mult:.1f}×")
            return less if mult < 0.95 else neutral

        seats = [s for s in OPEN_SEATS if not self.heads_up or s == "BTN"]
        opens = [(s, self.rate(f"rfi_{s}"), self.prior(f"rfi_{s}")[0]) for s in seats]
        wide = "/".join(s for s, rate, typ in opens if rate > typ * 1.15)
        tight = "/".join(s for s, rate, typ in opens if rate < typ * 0.87)
        open_effect = "; ".join(filter(None, (
            wide and f"defend and 3-bet wider against your {wide} opens",
            tight and f"respect your {tight} opens more"))) or "treat your opens as standard"
        rows = [{
            "key": "rfi", "label": "Opening range (" + " / ".join(seats) + ")",
            "read": " / ".join(f"{100 * rate:.0f}" for _, rate, _ in opens),
            "observed": " / ".join("–" if self.observed(f"rfi_{s}") is None else f"{100 * self.observed(f'rfi_{s}'):.0f}" for s in seats),
            "spots": round(sum(self.spots(f"rfi_{s}") for s in seats)),
            "typical": " / ".join(f"{100 * typ:.0f}" for _, _, typ in opens),
            "confident": all(self.spots(f"rfi_{s}") >= RFI_WEIGHT for s in seats),
            "effect": open_effect,
        }]
        rows += [
            row("limp", "Open-limp", more_less(r.iso_mult, "raise your limps {} as wide", "")),
            row("bb_fold", "Fold BB to an open", more_less(
                r.steal_mult, "open {} as wide when you're in the blinds", "steal less when you're in the blinds")),
            row("three_bet", "3-bet", "call your 3-bets wider" if r.scale("three_bet") > 1.2 else
                "fold more to your 3-bets" if r.scale("three_bet") < 0.8 else "no adjustment yet"),
            row("fold_3bet", "Fold to 3-bet", more_less(
                r.threebet_mult, "3-bet your opens {} as often", "3-bet you with fewer bluffs")),
            row("fold_cbet", "Fold to c-bet", more_less(
                r.cbet_mult, "c-bet and stab the flop {} as often", "c-bet you less, mostly for value")),
            row("fold_late", "Fold to turn/river bets", more_less(
                r.barrel_mult, "bet turns and rivers {} as often", "bluff you less on the turn and river")),
            row("wtsd", "Went to showdown", "value-bet you thinner: you call down with weak hands"
                if r.value_shift > 0.02 else "value-bet you only with strong hands" if r.value_shift < -0.02
                else "no adjustment yet"),
            row("aggr", "Postflop aggression (bets+raises vs calls)", "assume more bluffs in your bets: call you lighter"
                if r.air_bet > 0.36 else "respect your bets: fold more to them" if r.air_bet < 0.24 else "no adjustment yet"),
        ]
        return {"hands": self.hands, "heads_up": self.heads_up, "half_life": HALF_LIFE, "rows": rows}

    def summary(self) -> str:
        """Tracker notes in the third person, for a Claude seat's prompt."""
        def stat(key, text):
            return text.format(f"{_pct(self.rate(key))} (typical {_pct(self.prior(key)[0])})")
        return ", ".join((
            stat("fold_cbet", "folds to c-bets {}"),
            stat("fold_late", "folds to turn and river bets {}"),
            stat("wtsd", "goes to showdown after {} of flops seen"),
            stat("aggr", "bets or raises rather than calls {} of the time postflop"),
            stat("fold_3bet", "folds to 3-bets {}"),
        ))


class Read:
    """A Profile as one bot sees it: each rate moved from typical toward the profile's
    estimate by `adapt` (0 = ignores the notes, 1 = trusts them fully)."""

    def __init__(self, profile: Profile, adapt: float):
        self.profile = profile
        self.adapt = adapt

    def rate(self, key: str) -> float:
        typical = self.profile.prior(key)[0]
        return typical + self.adapt * (self.profile.rate(key) - typical)

    def dev(self, key: str) -> float:
        return self.rate(key) - self.profile.prior(key)[0]

    def scale(self, key: str) -> float:
        """Their rate relative to a typical regular's (1.0 = typical)."""
        return self.rate(key) / self.profile.prior(key)[0]

    def why(self, key: str) -> str:
        return self.profile.why(key)

    def why_open(self, position: str) -> str:
        return self.profile.why_open(position)

    # ---- counters: how hard to lean on a leak (1.0 = play normally) -------
    @property
    def threebet_mult(self) -> float:
        """3-bet their opens: more when they over-fold to 3-bets."""
        return _clamp(1 + 3 * self.dev("fold_3bet"), 0.5, 2.2)

    @property
    def steal_mult(self) -> float:
        """Open wider when they're in the blinds and over-fold the big blind."""
        return _clamp(1 + 2.5 * self.dev("bb_fold"), 0.7, 1.8)

    @property
    def iso_mult(self) -> float:
        """Raise their limps wider the more they limp (their limping range is weak)."""
        return _clamp(1 + 2.5 * self.dev("limp"), 1.0, 1.5)

    @property
    def value_shift(self) -> float:
        """How much less strength to want for a value bet: thinner against players who
        go to showdown a lot, thicker against ones who rarely do."""
        return _clamp(0.6 * self.dev("wtsd"), -0.04, 0.08)

    @property
    def cbet_mult(self) -> float:
        """C-bet and stab the flop against them."""
        return _clamp(1 + 2.5 * self.dev("fold_cbet"), 0.5, 1.8)

    @property
    def barrel_mult(self) -> float:
        """Bet the turn and river against them."""
        return _clamp(1 + 2.5 * self.dev("fold_late"), 0.4, 1.8)

    # ---- range reads -------------------------------------------------------
    def open_range(self, position: str) -> float:
        return self.rate(f"rfi_{position}")

    def defend_scale(self, position: str) -> float:
        """How much wider than typical they continue against an open from this seat."""
        typical = self.profile.prior
        if position == "BB":
            return (1 - self.rate("bb_fold")) / (1 - typical("bb_fold")[0])
        return (self.rate("cold_call") + self.rate("three_bet")) / (typical("cold_call")[0] + typical("three_bet")[0])

    @property
    def air_bet(self) -> float:
        """Share of no-pair, no-draw hands left in their range after they bet or raise."""
        return _clamp(0.3 + 1.2 * self.dev("aggr"), 0.12, 0.6)

    @property
    def air_call(self) -> float:
        """Share of no-pair, no-draw hands left in their range after they call."""
        return _clamp(0.45 + 1.5 * self.dev("wtsd"), 0.25, 0.75)
