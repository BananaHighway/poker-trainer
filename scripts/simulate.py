"""Headless bot-vs-bot simulation: engine smoke test + style calibration check.

Seat 0 (normally you) is played by a bot too. Verifies chips are conserved, then
prints how hands ended and tracker-style stats per seat, to compare with real
player types. A solid 6-max regular is roughly VPIP 22-26, PFR 17-21, 3-bet 6-9,
BB defence 45-55, fold to 3-bet 40-60, c-bet 55-70, WTSD 25-31; a reg-heavy table
sees a flop in roughly 40-45% of hands.

Like the app, the other bots keep notes on seat 0 (a profile built from scratch as
the hands are played) and say how often a read changed what they did. Pass
--static to turn that off, e.g. to see how much a leaky seat 0 loses to the reads.

    .\\run.ps1 scripts\\simulate.py [hands] [players] [style,style,...] [--static]
"""

import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bots.heuristic import HeuristicBot  # noqa: E402
from app.bots.profile import Profile  # noqa: E402
from app.engine import BB, SeatConfig, Table, TableConfig  # noqa: E402

COLUMNS = ("vpip", "pfr", "3bet", "steal", "bb_def", "f_3bet", "cbet", "f_cbet", "wtsd")


class Rate:
    def __init__(self):
        self.n = self.of = 0

    def add(self, done: bool) -> None:
        self.of += 1
        self.n += bool(done)

    def __str__(self) -> str:
        return f"{100 * self.n / self.of:.0f}" if self.of else "-"


def tally(h, stats, flow) -> None:
    """Add one finished hand to per-player stats (keyed by seat name) and table flow."""
    acts = [a for a in h.actions if a["action"] not in ("post_sb", "post_bb")]
    pre = [a for a in acts if a["street"] == "preflop"]
    flop = [a for a in acts if a["street"] == "flop"]
    raises = sum(a["action"] == "raise" for a in pre)
    if flop:
        flow["saw flop"] += 1
    elif h.result["showdown"]:
        flow["all-in preflop"] += 1
    elif all(a["action"] == "fold" for a in pre):
        flow["walk"] += 1
    else:
        flow["won preflop by a raise" if raises <= 1 else "won preflop by a 3-bet+"] += 1
    flow["showdown"] += h.result["showdown"]

    raises = limpers = 0
    opener = last_raiser = None
    seen = set()
    for a in pre:
        st, seat = stats[a["name"]], a["seat"]
        if seat not in seen:
            seen.add(seat)
            mine = [b["action"] for b in pre if b["seat"] == seat]
            st["vpip"].add("call" in mine or "raise" in mine)
            st["pfr"].add("raise" in mine)
            if raises == 0 and limpers == 0 and a["position"] in ("CO", "BTN", "SB"):
                st["steal"].add(a["action"] == "raise")
        if raises == 1 and last_raiser != seat:
            st["3bet"].add(a["action"] == "raise")
            if a["position"] == "BB" and limpers == 0:
                st["bb_def"].add(a["action"] != "fold")
        if raises == 2 and seat == opener:
            st["f_3bet"].add(a["action"] == "fold")
        if a["action"] == "raise":
            raises += 1
            opener = seat if raises == 1 else opener
            last_raiser = seat
        elif a["action"] == "call" and raises == 0:
            limpers += 1

    first_bet = None
    for a in flop:
        st = stats[a["name"]]
        if a["seat"] == last_raiser and first_bet is None and a["to_call"] == 0:
            st["cbet"].add(a["action"] == "bet")
        if first_bet == last_raiser and a["seat"] != last_raiser and a["to_call"] > 0:
            st["f_cbet"].add(a["action"] == "fold")
        if a["action"] in ("bet", "raise") and first_bet is None:
            first_bet = a["seat"]
    for seat, name in {a["seat"]: a["name"] for a in flop}.items():
        folded = any(a["seat"] == seat and a["action"] == "fold" for a in acts)
        stats[name]["wtsd"].add(h.result["showdown"] and not folded)


def main(hands: int = 300, players: int = 6, lineup: str = "tag,nit,lag,station,maniac,tag",
         adaptive: bool = True) -> None:
    styles = lineup.split(",")[:players]
    seats = [SeatConfig(kind="hero" if i == 0 else "bot", name=f"{s}{i}", style=s) for i, s in enumerate(styles)]
    seats += [SeatConfig()] * (6 - len(styles))
    bots = {i: HeuristicBot(s.style, seed=i) for i, s in enumerate(seats) if s.kind != "empty"}
    table = Table(TableConfig(seats=seats), bots)
    if adaptive:
        table.profiles[0] = Profile(heads_up=len(styles) == 2)
    hero_bot = bots[0]
    stats = defaultdict(lambda: defaultdict(Rate))
    flow, net, reads = Counter(), Counter(), Counter()
    t0 = time.time()
    for _ in range(hands):
        table.start_hand()
        h = table.hand
        chips_before = sum(h.start_stacks.values())
        while h.result is None:
            seat = table.actor_seat()
            if seat == 0:
                kind, to, _ = hero_bot.decide(table.decision_for(0))
                table.apply(0, kind, to)
            else:
                table.bot_step()
        chips_after = sum(table.seats[s].stack for s in h.order)
        assert chips_before == chips_after, f"chip leak in hand {h.number}: {chips_before} -> {chips_after}"
        logged = sum(a["amount"] for a in h.actions)
        assert all(a["amount"] >= 0 for a in h.actions), f"negative amount in hand {h.number}"
        assert logged >= h.result["pot"], f"log ({logged}) < pot ({h.result['pot']}) in hand {h.number}"
        tally(h, stats, flow)
        reads.update(a["read"].split(":")[0] for a in h.actions if a.get("read"))
        for seat in h.order:
            net[table.seats[seat].cfg.name] += h.result["net"][seat]
    dt = time.time() - t0
    print(f"{hands} hands, {len(styles)}-handed, {dt:.1f}s ({dt / hands * 1000:.0f} ms/hand)")
    for k in ("walk", "won preflop by a raise", "won preflop by a 3-bet+", "all-in preflop", "saw flop", "showdown"):
        print(f"  {k:<26}{flow[k] / hands:>5.0%}")
    print(f"{'player':<11}" + "".join(f"{c:>7}" for c in COLUMNS) + f"{'bb/100':>9}")
    for name in (s.name for s in seats if s.kind != "empty"):
        print(f"{name:<11}" + "".join(f"{str(stats[name][c]):>7}" for c in COLUMNS)
              + f"{net[name] / BB / hands * 100:>9.1f}")
    if adaptive:
        print("read-driven actions vs seat 0: " + (", ".join(f"{k} {v}" for k, v in reads.most_common()) or "none"))
        for r in table.profiles[0].describe()["rows"]:
            print(f"  {r['label']:<44}{str(r['read']):>22}  (typical {r['typical']}) -> {r['effect']}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(*(int(a) for a in args[:2]), *args[2:3], adaptive="--static" not in sys.argv)
