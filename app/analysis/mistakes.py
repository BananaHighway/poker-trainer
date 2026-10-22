"""Mistake finder: re-examines every hero decision with the information available then.

Two kinds of checks:
  * Chart checks (preflop, normal stack commitment): the hand vs a solid 6-max TAG
    baseline for that position/situation. Flagged by severity, no bb figure, since
    preflop EV depends on how the hand plays out.
  * Equity checks (postflop, and preflop all-in-sized decisions): Monte Carlo equity vs
    the opponents' ranges estimated from their betting, compared with the price.
    These give an estimated EV loss in bb for that decision alone.

It is a coach's first pass, not a solver: ranges are estimates and implied odds /
fold equity are only approximated. Thresholds are deliberately conservative so a
flag usually means a real leak.
"""

import random

from ..bots.reads import estimate_range, open_width, preflop_spot, threebet_continue, vs_open
from ..cards import card_name, hand_class, rank, suit
from ..engine import BB
from ..equity import equity
from ..ranges import percentile
from .replay import HERO, Spot, hero_spots

ITERATIONS = 700
# Stack commitment above which preflop decisions are judged by equity, not chart.
BIG_DECISION = 0.25


def _m(spot: Spot, kind: str, severity: str, title: str, detail: str, ev_loss: float | None = None, **numbers):
    return {
        "type": kind, "severity": severity, "title": title, "detail": detail,
        "ev_loss_bb": None if ev_loss is None else round(ev_loss / BB, 2),
        "action_index": spot.index, "street": spot.action["street"], "position": spot.position,
        "hole": [card_name(c) for c in spot.hole], "board": [card_name(c) for c in spot.board],
        "action": spot.action["action"], "numbers": numbers,
    }


def _severity_for(loss_chips: float) -> str:
    bb = loss_chips / BB
    return "major" if bb >= 6 else "moderate" if bb >= 2 else "minor"


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def _has_draw(hole, board) -> bool:
    cards = [*hole, *board]
    for s in range(4):
        if sum(1 for c in cards if suit(c) == s) == 4 and any(suit(c) == s for c in hole):
            return True
    ranks = {rank(c) for c in cards} | ({-1} if any(rank(c) == 12 for c in cards) else set())
    hole_r = {rank(c) for c in hole} | ({-1} if any(rank(c) == 12 for c in hole) else set())
    for lo in range(-1, 9):
        window = {r for r in range(lo, lo + 5) if r in ranks}
        if len(window) == 4 and window & hole_r:
            return True
    return False


def _equity(spot: Spot) -> float:
    ranges = [estimate_range(o["seat"], spot.ctx) for o in spot.opponents]
    seed = hash((spot.record["number"], spot.record["started_at"], spot.index))
    return equity(spot.hole, spot.board, ranges, iterations=ITERATIONS, rng=random.Random(seed))


def _villain_desc(spot: Spot) -> str:
    return ", ".join(f"{o['name']} ({o['position']})" for o in spot.opponents)


# ---------------------------------------------------------------------------
def _price_check(spot: Spot, implied: float = 0.0, margin: float = 0.07):
    """Fold/call decision vs the price. Returns a mistake dict or None."""
    a = spot.action["action"]
    if spot.to_call <= 0 or a not in ("fold", "call") or not spot.opponents:
        return None
    eq = _equity(spot)
    pot, call = spot.pot_before, spot.to_call
    need = spot.price
    ev_call = eq * (pot + call) - call
    nums = dict(equity=round(eq, 3), price=round(need, 3), pot_bb=pot / BB, call_bb=call / BB)
    if a == "fold" and eq > need + margin and ev_call > 0.3 * BB:
        return _m(spot, "overfold", _severity_for(ev_call), "Folded with the right price to call",
                  f"Facing {call / BB:g}bb into {pot / BB:g}bb you needed {_pct(need)} equity; vs {_villain_desc(spot)}'s "
                  f"likely range you had about {_pct(eq)}. Calling was worth ~{ev_call / BB:+.1f}bb.", ev_call, **nums)
    if a == "call" and eq + implied < need - margin and -ev_call > 0.3 * BB:
        return _m(spot, "loose_call", _severity_for(-ev_call), "Called without the odds",
                  f"Calling {call / BB:g}bb into {pot / BB:g}bb needs {_pct(need)} equity; vs {_villain_desc(spot)}'s "
                  f"likely range you had about {_pct(eq)}"
                  + (f" (+{_pct(implied)} credited for draws)" if implied else "")
                  + f". The call cost ~{-ev_call / BB:.1f}bb.", -ev_call, **nums)
    return None


def _preflop(spot: Spot):
    a = spot.action["action"]
    commit = (spot.to_call if a != "raise" else spot.action["amount"]) / max(1, spot.stack_before)
    if commit >= BIG_DECISION and a in ("fold", "call"):
        return _price_check(spot, margin=0.05)
    if a == "raise" and spot.action.get("all_in"):
        return None  # shove EV depends on fold equity; out of scope for chart checks

    sp = preflop_spot(spot.ctx)
    p = percentile(*spot.hole)
    pos, n = spot.position, spot.n_players
    hand = hand_class(*spot.hole)
    where = f"from the {pos}"

    if sp.raises == 0:
        if pos == "BB":
            return None
        width = open_width(pos, n)
        if a == "call" and pos != "SB":
            return _m(spot, "open_limp", "moderate", "Open-limped",
                      f"Limping {where} gives up the initiative and invites multiway pots with a capped range. "
                      f"Raise ({hand} is {'in' if p < width else 'outside'} a standard {pos} open) or fold.")
        if a == "call" and pos == "SB" and p > 0.7:
            return _m(spot, "sb_complete", "minor", "Completed junk from the SB",
                      f"{hand} is bottom-of-range; completing just bleeds out of position. Fold or raise.")
        if a == "raise" and p > width * 1.35 + 0.03:
            sev = "moderate" if p > width * 2 else "minor"
            return _m(spot, "loose_open", sev, "Opened too loose",
                      f"{hand} is about the top {_pct(p)} of hands; a standard {pos} open is ~{_pct(width)}.", p=p, width=width)
        if a == "fold" and p < width * 0.55 and sp.limpers == 0:
            sev = "moderate" if p < width * 0.25 else "minor"
            return _m(spot, "overfold_rfi", sev, "Folded a standard open",
                      f"Folded to you {where}: {hand} (top {_pct(p)}) is a clear raise; the {pos} opens ~{_pct(width)}.", p=p, width=width)
        return None

    open_bb = sp.open_to / BB
    if sp.raises == 1:
        three, cont = vs_open(pos, sp.opener_position, open_bb, n, sp.callers_since_raise)
        vs = f"vs a {open_bb:g}bb {sp.opener_position} open"
        if a == "call" and p < 0.025:
            return _m(spot, "flat_premium", "minor", "Flatted a premium",
                      f"{hand} {vs} should 3-bet for value; flatting lets everyone in cheaply.")
        if a == "call" and p > cont * 1.35 + 0.02:
            sev = "moderate" if p > cont * 2 else "minor"
            return _m(spot, "loose_call_open", sev, "Called an open too wide",
                      f"{hand} (top {_pct(p)}) {vs}: a solid continuing range {where} is ~{_pct(cont)}.", p=p, width=cont)
        if a == "raise" and p > three * 2 + 0.04 and p > cont:
            return _m(spot, "loose_3bet", "minor", "Loose 3-bet",
                      f"{hand} (top {_pct(p)}) {vs}: 3-betting ranges are ~{_pct(three)} value plus a few suited bluffs.", p=p, width=three)
        if a == "fold" and p < cont * 0.45:
            sev = "major" if p < 0.03 else "moderate" if p < three else "minor"
            return _m(spot, "overfold_open", sev, "Folded too much vs an open",
                      f"{hand} (top {_pct(p)}) {vs} is a clear {'3-bet' if p < three else 'continue'}; "
                      f"continuing range {where} is ~{_pct(cont)}.", p=p, width=cont)
        return None

    if sp.raises == 2:
        if sp.i_raised:
            # Hero opened and got 3-bet: defend part of the opening range, more vs wide 3-bettors.
            villain = vs_open(sp.last_raiser_position, sp.opener_position, open_bb, n)[0]
            cont = threebet_continue(open_width(pos, n), villain)
            fold_line, call_line = max(0.025, cont * 0.45), cont * 1.35 + 0.02
            vs = f"after opening {where}, vs a {sp.last_raiser_position} 3-bet"
        else:
            cont, fold_line, call_line = 0.06, 0.06, 0.15
            vs = "cold vs a 3-bet"
        if a == "fold" and p < fold_line:
            sev = "major" if p < 0.035 else "minor"
            return _m(spot, "overfold_3bet", sev, "Folded a strong hand to a 3-bet",
                      f"{hand} (top {_pct(p)}) {vs} is a 4-bet or call; continuing range is ~{_pct(cont)}.", p=p, width=cont)
        if a == "call" and p > call_line:
            return _m(spot, "loose_call_3bet", "moderate", "Called a 3-bet too wide",
                      f"{hand} (top {_pct(p)}) {vs} plays poorly in a bloated pot; continuing range is ~{_pct(cont)}.", p=p, width=cont)
        if a == "raise" and p > 0.08:
            return _m(spot, "loose_4bet", "minor", "Loose 4-bet",
                      f"{hand} (top {_pct(p)}): 4-bet ranges are ~top 3% plus a few blockers (A5s).", p=p)
        return None

    if a == "call" and p > 0.06:
        return _m(spot, "loose_call_4bet", "moderate", "Called a 4-bet+ too wide",
                  f"{hand} (top {_pct(p)}) vs a 4-bet or more: continue with roughly QQ+/AK.", p=p)
    if a == "fold" and p < 0.02:
        return _m(spot, "overfold_4bet", "major", "Folded a premium to a 4-bet", f"{hand} is a stack-off hand here.", p=p)
    return None


def _postflop(spot: Spot):
    a = spot.action["action"]
    if not spot.opponents:
        return None
    if spot.to_call > 0 and a in ("fold", "call"):
        draw = spot.street < 3 and _has_draw(spot.hole, spot.board)
        implied = (0.07 if spot.street == 1 else 0.04) if draw else 0.0
        return _price_check(spot, implied=implied)

    stations = [o for o in spot.opponents if o["style"] == "station" and o["kind"] == "bot"]
    if a in ("bet", "raise") and stations:
        eq = _equity(spot)
        s = eq ** (1 / len(spot.opponents))
        if s < 0.3 and not (spot.street < 3 and _has_draw(spot.hole, spot.board)):
            amt = spot.action["amount"]
            return _m(spot, "bluff_station", "moderate" if spot.street == 3 or amt > spot.pot_before * 0.6 else "minor",
                      "Bluffed a calling station",
                      f"{', '.join(o['name'] for o in stations)} calls far too often; with ~{_pct(eq)} equity this "
                      f"{amt / BB:g}bb bluff mostly gets called. Value-bet stations relentlessly, bluff them rarely.",
                      equity=round(eq, 3))
        return None

    if a == "check" and spot.street == 3 and spot.to_call == 0:
        # Checked back the river as the last to act: was there value left?
        river = [x for x in spot.prior if x["street"] == "river"]
        acted = {x["seat"] for x in river}
        if all(o["seat"] in acted for o in spot.opponents):
            eq = _equity(spot)
            s = eq ** (1 / len(spot.opponents))
            if s > 0.78:
                return _m(spot, "missed_value", "moderate" if s > 0.88 else "minor", "Missed river value",
                          f"Checked back with ~{_pct(eq)} equity vs their likely range; a value bet "
                          f"(~half pot) gets called by worse often enough to be clearly profitable.",
                          equity=round(eq, 3))
    return None


def analyse_hand(record: dict) -> list[dict]:
    out = []
    for spot in hero_spots(record):
        m = _preflop(spot) if spot.street == 0 else _postflop(spot)
        if m:
            out.append(m)
    return out


SEVERITY_ORDER = {"major": 0, "moderate": 1, "minor": 2}
# Rough bb-equivalent weight per flag, used only to rank leaks that have no EV estimate.
SEVERITY_POINTS = {"major": 5.0, "moderate": 2.0, "minor": 0.7}


def summarise(per_hand: list[tuple[dict, list[dict]]]) -> dict:
    """per_hand: [(hand_meta, mistakes)] -> leak summary + flat sorted mistake list."""
    flat, groups = [], {}
    for meta, ms in per_hand:
        for m in ms:
            m = {**m, **meta}
            flat.append(m)
            g = groups.setdefault(m["type"], {"type": m["type"], "title": m["title"], "count": 0, "ev_loss_bb": 0.0,
                                              "result_bb": 0.0, "hands": set(),
                                              "severity": {"major": 0, "moderate": 0, "minor": 0}})
            g["count"] += 1
            g["ev_loss_bb"] += m["ev_loss_bb"] or 0
            g["severity"][m["severity"]] += 1
            if m["hand_id"] not in g["hands"]:  # a hand's result counts once per leak
                g["hands"].add(m["hand_id"])
                g["result_bb"] += m["hand_result_bb"]
    for g in groups.values():
        g["ev_loss_bb"] = round(g["ev_loss_bb"], 1)
        g["result_bb"] = round(g["result_bb"], 1)
        g["priority"] = round(g["ev_loss_bb"] + sum(SEVERITY_POINTS[k] * v for k, v in g["severity"].items()), 1)
        del g["hands"]
    leaks = sorted(groups.values(), key=lambda g: -g["priority"])
    flat.sort(key=lambda m: (SEVERITY_ORDER[m["severity"]], -(m["ev_loss_bb"] or 0)))
    return {"leaks": leaks, "mistakes": flat,
            "total_ev_loss_bb": round(sum(m["ev_loss_bb"] or 0 for m in flat), 1)}
