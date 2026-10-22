"""Hero statistics in the usual tracker definitions (PokerTracker-style opportunities).

Every rate is returned as {"n": times done, "of": opportunities, "pct": ...} so the
UI can show sample size; small samples are noisy and the page says so.
"""

import itertools
import random
from collections import defaultdict

from ..cards import DECK, card_id, evaluate
from ..engine import BB
from .replay import HERO, hero_player

# Reasonable winning-regular ranges for 6-max / heads-up cash (rough guides, not GTO).
TARGETS = {
    "6max": {"vpip": (21, 27), "pfr": (17, 23), "three_bet": (6, 10), "fold_to_3bet": (40, 60),
             "steal": (30, 45), "cbet": (50, 70), "fold_to_cbet": (35, 55), "wtsd": (24, 31),
             "wsd": (49, 60), "af": (2.0, 4.0)},
    "hu": {"vpip": (60, 85), "pfr": (50, 75), "three_bet": (12, 22), "fold_to_3bet": (30, 50),
           "steal": (70, 90), "cbet": (55, 80), "fold_to_cbet": (30, 50), "wtsd": (30, 42),
           "wsd": (48, 58), "af": (2.0, 4.0)},
}


class Rate:
    def __init__(self):
        self.n = 0
        self.of = 0

    def add(self, opportunity: bool, done: bool) -> None:
        if opportunity:
            self.of += 1
            self.n += int(done)

    def out(self) -> dict:
        return {"n": self.n, "of": self.of, "pct": round(100 * self.n / self.of, 1) if self.of else None}


def _preflop(actions):
    return [a for a in actions if a["street"] == "preflop" and a["action"] not in ("post_sb", "post_bb")]


def hand_flags(rec: dict) -> dict:
    """Per-hand flags (booleans, or counts for multi-street stats) for every stat's
    opportunity/done pair. Uses only the hero's actions and public results, never hole
    cards, so the bots' reads on the hero (bots/profile.py) can share it."""
    f = defaultdict(bool)
    pre = _preflop(rec["actions"])
    raises = limpers = 0
    my_first = True
    opened = False
    last_raiser = None
    for a in pre:
        if a["seat"] == HERO:
            if a["action"] in ("call", "raise"):
                f["vpip"] = True
            if a["action"] == "raise":
                f["pfr"] = True
            if raises == 1 and last_raiser != HERO:
                f["3bet_opp"] = True
                f["3bet"] |= a["action"] == "raise"
            if raises == 2 and opened:
                f["f3b_opp"] = True
                f["f3b"] |= a["action"] == "fold"
            if my_first and raises == 0 and limpers == 0 and a["position"] in ("CO", "BTN", "SB"):
                f["steal_opp"] = True
                f["steal"] = a["action"] == "raise"
            if my_first and raises == 0 and limpers == 0 and a["position"] != "BB":
                f["first_in"] = a["position"]  # first in: raised, limped or folded
                f["rfi"] = a["action"] == "raise"
                f["limp"] = a["action"] == "call"
            if my_first and raises == 1 and a["position"] == "BB":
                f["bb_opp"] = True
                f["bb_fold"] = a["action"] == "fold"
            elif my_first and raises == 1:
                f["cold_opp"] = True
                f["cold_call"] = a["action"] == "call"
            if raises == 0 and a["action"] == "raise":
                opened = True
            my_first = False
        if a["action"] == "raise":
            raises += 1
            last_raiser = a["seat"]
        elif a["action"] == "call" and raises == 0:
            limpers += 1
    pfr_seat = last_raiser

    folded_pre = any(a["seat"] == HERO and a["action"] == "fold" for a in pre)
    post = [a for a in rec["actions"] if a["street"] != "preflop"]
    saw_flop = not folded_pre and (len(rec["board"]) >= 3 or any(a["seat"] == HERO for a in post))
    f["saw_flop"] = saw_flop
    flop = [a for a in post if a["street"] == "flop"]
    first_bet_seen = None
    acted = False
    for a in flop:
        if a["seat"] == HERO:
            if not acted and pfr_seat == HERO and first_bet_seen is None and a["to_call"] == 0:
                f["cbet_opp"] = True
                f["cbet"] = a["action"] == "bet"
            # Facing the c-bet, whether it came before the hero acted or after a check.
            if pfr_seat not in (None, HERO) and first_bet_seen == pfr_seat and not f["fcb_opp"]:
                f["fcb_opp"] = True
                f["fcb"] = a["action"] == "fold"
            acted = True
        if a["action"] in ("bet", "raise") and first_bet_seen is None:
            first_bet_seen = a["seat"]
    folded_ever = any(a["seat"] == HERO and a["action"] == "fold" for a in rec["actions"])
    res = rec["result"]
    net = res["net"].get(str(HERO), res["net"].get(HERO, 0))
    f["wtsd"] = saw_flop and res["showdown"] and not folded_ever
    f["wsd"] = f["wtsd"] and net > 0
    f["wwsf"] = saw_flop and net > 0
    for a in post:
        if a["seat"] == HERO:
            if a["action"] in ("bet", "raise"):
                f["aggr"] = f["aggr"] + 1 if f["aggr"] else 1
            elif a["action"] == "call":
                f["calls"] = f["calls"] + 1 if f["calls"] else 1
            elif a["action"] in ("check", "fold"):
                f["passive"] = f["passive"] + 1 if f["passive"] else 1
            if a["street"] != "flop" and a["to_call"] > 0:
                f["late_opp"] += 1
                f["late_fold"] += a["action"] == "fold"
    return f


def _net(rec: dict, seat: int) -> int:
    n = rec["result"]["net"]
    return n.get(str(seat), n.get(seat, 0))


def allin_ev_net(rec: dict) -> float | None:
    """Hero's expected net (chips) for a 2-way all-in that ran out, else None."""
    res = rec["result"]
    if not res["showdown"] or len(res["shown"]) != 2 or str(HERO) not in {str(k) for k in res["shown"]}:
        return None
    acts = [a for a in rec["actions"] if a["action"] not in ("post_sb", "post_bb")]
    last = acts[-1] if acts else None
    if not last or len(last["board"]) >= 5 or not any(a.get("all_in") for a in acts):
        return None
    villain = next(int(s) for s in res["shown"] if int(s) != HERO)
    paid = defaultdict(int)
    for a in rec["actions"]:
        paid[a["seat"]] += a["amount"]
    matched = min(paid[HERO], paid[villain])
    dead = sum(v for s, v in paid.items() if s not in (HERO, villain))
    pot = 2 * matched + dead
    me = tuple(card_id(c) for c in hero_player(rec)["hole"])
    vh = tuple(card_id(c) for c in next(p for p in rec["players"] if p["seat"] == villain)["hole"])
    board = [card_id(c) for c in last["board"]]
    eq = _exact_equity(me, vh, board)
    return eq * pot - matched


def _exact_equity(a: tuple, b: tuple, board: list[int]) -> float:
    live = [c for c in DECK if c not in (*a, *b, *board)]
    need = 5 - len(board)
    if need <= 2:
        runs = itertools.combinations(live, need)
    else:
        rng = random.Random(hash((a, b, tuple(board))))
        runs = (rng.sample(live, need) for _ in range(6000))
    won = total = 0.0
    for run in runs:
        full = [*board, *run]
        va, vb = evaluate([*a, *full]), evaluate([*b, *full])
        won += 1.0 if va < vb else 0.5 if va == vb else 0.0
        total += 1
    return won / total


def compute(records: list[dict], styles: dict[str, str] | None = None) -> dict:
    rates = {k: Rate() for k in ("vpip", "pfr", "three_bet", "fold_to_3bet", "steal", "cbet",
                                 "fold_to_cbet", "wtsd", "wsd", "wwsf")}
    aggr = calls = passive = 0
    net_total = 0
    ev_total = 0.0
    allin_hands = 0
    by_pos: dict[str, dict] = defaultdict(lambda: {"hands": 0, "net": 0, "vpip": 0, "pfr": 0})
    by_style: dict[str, dict] = defaultdict(lambda: {"hands": 0, "net": 0.0})
    sizes = defaultdict(int)
    curve = []

    for rec in records:
        f = hand_flags(rec)
        me = hero_player(rec)
        if not me:
            continue
        net = _net(rec, HERO)
        net_total += net
        ev = allin_ev_net(rec)
        if ev is not None:
            allin_hands += 1
            ev_total += ev
        else:
            ev_total += net
        curve.append({"net": round(net_total / BB, 2), "ev": round(ev_total / BB, 2)})
        sizes[len(rec["players"])] += 1

        rates["vpip"].add(True, f["vpip"])
        rates["pfr"].add(True, f["pfr"])
        rates["three_bet"].add(f["3bet_opp"], f["3bet"])
        rates["fold_to_3bet"].add(f["f3b_opp"], f["f3b"])
        rates["steal"].add(f["steal_opp"], f["steal"])
        rates["cbet"].add(f["cbet_opp"], f["cbet"])
        rates["fold_to_cbet"].add(f["fcb_opp"], f["fcb"])
        rates["wtsd"].add(f["saw_flop"], f["wtsd"])
        rates["wsd"].add(f["wtsd"], f["wsd"])
        rates["wwsf"].add(f["saw_flop"], f["wwsf"])
        aggr += f["aggr"] or 0
        calls += f["calls"] or 0
        passive += f["passive"] or 0

        pos = by_pos[me["position"]]
        pos["hands"] += 1
        pos["net"] += net
        pos["vpip"] += int(f["vpip"])
        pos["pfr"] += int(f["pfr"])

        # Attribute the hero's result to opponent types: winnings are split across the
        # players who lost chips, losses across the players who won them.
        others = [p for p in rec["players"] if p["seat"] != HERO]
        if net:
            counter = [(p, -_net(rec, p["seat"])) for p in others if (_net(rec, p["seat"]) < 0) == (net > 0)]
            total = sum(abs(v) for _, v in counter) or 1
            for p, v in counter:
                by_style[p["style"] + (":claude" if p["kind"] == "claude" else "")]["net"] += net * abs(v) / total
        for key in {p["style"] + (":claude" if p["kind"] == "claude" else "") for p in others}:
            by_style[key]["hands"] += 1  # hands where at least one such opponent was dealt in

    hands = len(curve)
    fmt = "hu" if sizes and max(sizes, key=sizes.get) == 2 else "6max"
    out = {k: r.out() for k, r in rates.items()}
    out["af"] = {"n": aggr, "of": calls, "pct": round(aggr / calls, 2) if calls else None}
    out["afq"] = {"n": aggr, "of": aggr + calls + passive,
                  "pct": round(100 * aggr / (aggr + calls + passive), 1) if aggr + calls + passive else None}
    return {
        "hands": hands,
        "format": fmt,
        "targets": TARGETS[fmt],
        "net_bb": round(net_total / BB, 2),
        "bb_per_100": round(net_total / BB / hands * 100, 1) if hands else 0,
        "ev_net_bb": round(ev_total / BB, 2),
        "ev_bb_per_100": round(ev_total / BB / hands * 100, 1) if hands else 0,
        "allin_hands": allin_hands,
        "rates": out,
        "by_position": [{"position": k, "hands": v["hands"], "net_bb": round(v["net"] / BB, 1),
                         "bb_per_100": round(v["net"] / BB / v["hands"] * 100, 1),
                         "vpip": round(100 * v["vpip"] / v["hands"], 1), "pfr": round(100 * v["pfr"] / v["hands"], 1)}
                        for k, v in sorted(by_pos.items(), key=lambda kv: ["UTG", "HJ", "CO", "BTN", "SB", "BB"].index(kv[0]) if kv[0] in ["UTG", "HJ", "CO", "BTN", "SB", "BB"] else 9)],
        "by_style": [{"style": k, "hands": v["hands"], "net_bb": round(v["net"] / BB, 1),
                      "bb_per_100": round(v["net"] / BB / v["hands"] * 100, 1) if v["hands"] else 0}
                     for k, v in sorted(by_style.items(), key=lambda kv: kv[1]["net"])],
        "curve": curve,
    }
