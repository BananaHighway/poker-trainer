"""Headless bot-vs-bot simulation: engine smoke test + style sanity check.

Seat 0 (normally you) is played by a TAG bot. Verifies chips are conserved and
prints VPIP / PFR / bb per 100 per style, which should look like the player types.

    .\\run.ps1 scripts\\simulate.py [hands] [players]
"""

import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bots.heuristic import HeuristicBot  # noqa: E402
from app.engine import BB, SeatConfig, Table, TableConfig  # noqa: E402


def main(hands: int = 300, players: int = 6) -> None:
    styles = ["tag", "nit", "lag", "station", "maniac", "tag"][:players]
    seats = [SeatConfig(kind="hero" if i == 0 else "bot", name=f"{s}{i}", style=s) for i, s in enumerate(styles)]
    seats += [SeatConfig()] * (6 - players)
    bots = {i: HeuristicBot(s.style, seed=i) for i, s in enumerate(seats) if s.kind != "empty"}
    table = Table(TableConfig(seats=seats), bots)
    hero_bot = bots.pop(0)
    vpip, pfr, dealt, net = defaultdict(int), defaultdict(int), defaultdict(int), defaultdict(int)
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
        for seat in h.order:
            st = table.seats[seat].cfg.style + str(seat)
            dealt[st] += 1
            pre = [a for a in h.actions if a["seat"] == seat and a["street"] == "preflop"]
            if any(a["action"] in ("call", "raise") for a in pre):
                vpip[st] += 1
            if any(a["action"] == "raise" for a in pre):
                pfr[st] += 1
            net[st] += h.result["net"][seat]
    dt = time.time() - t0
    print(f"{hands} hands, {players}-handed, {dt:.1f}s ({dt / hands * 1000:.0f} ms/hand)")
    print(f"{'player':<10}{'VPIP':>7}{'PFR':>7}{'bb/100':>9}")
    for st in dealt:
        print(f"{st:<10}{vpip[st] / dealt[st]:>7.0%}{pfr[st] / dealt[st]:>7.0%}{net[st] / BB / dealt[st] * 100:>9.1f}")


if __name__ == "__main__":
    main(*(int(a) for a in sys.argv[1:3]))
