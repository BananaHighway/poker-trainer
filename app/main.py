"""FastAPI server: serves the UI and a small JSON API for the single local table."""

import random
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from .bots.claude_bot import ClaudeBot, has_api_key  # noqa: E402
from .bots.heuristic import HeuristicBot  # noqa: E402
from .bots.profile import Profile  # noqa: E402
from .bots.pricing import MODELS, PRICES  # noqa: E402
from .bots.styles import STYLES  # noqa: E402
from .engine import BB, SeatConfig, Table, TableConfig  # noqa: E402
from .storage import Store  # noqa: E402

app = FastAPI(title="Poker Trainer")
store = Store()
table: Table | None = None


class SeatIn(BaseModel):
    kind: str = Field(pattern="^(hero|bot|claude|empty)$")
    name: str = ""
    style: str = "tag"
    model: str = "claude-haiku-5-5"
    persona: str = ""


class TableIn(BaseModel):
    seats: list[SeatIn] = Field(min_length=2, max_length=6)
    starting_bb: float = Field(100, ge=10, le=1000)
    sb_bb: float = Field(0.5, ge=0.1, le=1.0)
    auto_topup: bool = True
    claude_enabled: bool = False
    reveal_all_at_end: bool = False
    adaptive: bool = True


class ActIn(BaseModel):
    action: str = Field(pattern="^(fold|check|call|bet|raise)$")
    amount_bb: float | None = None


class ClaudeToggleIn(BaseModel):
    enabled: bool


def _table() -> Table:
    if table is None:
        raise HTTPException(409, "No table yet - set one up first")
    return table


@app.get("/api/meta")
def meta():
    return {
        "styles": [{"key": s.key, "label": s.label, "blurb": s.blurb} for s in STYLES.values()],
        "models": [m | {"price": PRICES[m["id"]]} for m in MODELS],
        "has_api_key": has_api_key(),
        "bb": BB,
    }


@app.post("/api/table")
def create_table(cfg: TableIn):
    global table
    if cfg.seats[0].kind != "hero" or any(s.kind == "hero" for s in cfg.seats[1:]):
        raise HTTPException(422, "Seat 1 must be you, and only seat 1")
    if sum(1 for s in cfg.seats if s.kind != "empty") < 2:
        raise HTTPException(422, "Need at least one opponent")
    seats, bots = [], {}
    for i, s in enumerate(cfg.seats):
        name = s.name.strip() or ("You" if s.kind == "hero" else f"Seat {i + 1}")
        sc = SeatConfig(kind=s.kind, name=name, style=s.style if s.style in STYLES else "tag",
                        model=s.model if s.model in PRICES else "claude-haiku-5-5", persona=s.persona)
        seats.append(sc)
        seed = random.randrange(1 << 30)
        if s.kind == "bot":
            bots[i] = HeuristicBot(sc.style, seed)
        elif s.kind == "claude":
            bots[i] = ClaudeBot(sc.name, sc.style, sc.model, sc.persona, seed)
    config = TableConfig(seats=seats, starting_bb=cfg.starting_bb, sb_bb=cfg.sb_bb,
                         auto_topup=cfg.auto_topup, claude_enabled=cfg.claude_enabled,
                         reveal_all_at_end=cfg.reveal_all_at_end, adaptive=cfg.adaptive)
    session_id = store.new_session(cfg.model_dump())
    table = Table(config, bots, store=store, session_id=session_id)
    if cfg.adaptive:
        table.profiles[0] = _hero_profile(heads_up=sum(1 for s in seats if s.kind != "empty") == 2)
    table.start_hand()
    return table.snapshot()


def _hero_profile(heads_up: bool) -> Profile:
    """The regulars' notes on you: every stored hand of this format, across sessions."""
    return Profile.from_records(store.hand_records(heads_up), heads_up)


@app.get("/api/state")
def state():
    return _table().snapshot()


@app.post("/api/hand/start")
def start_hand():
    t = _table()
    try:
        t.start_hand()
    except ValueError as e:
        raise HTTPException(409, str(e))
    return t.snapshot()


@app.post("/api/step")
def step():
    """Let the next bot act (one action). The UI calls this repeatedly with a delay."""
    t = _table()
    acted = t.bot_step()
    return {"acted": acted, "state": t.snapshot()}


@app.post("/api/act")
def act(body: ActIn):
    t = _table()
    if t.actor_seat() != 0:
        raise HTTPException(409, "Not your turn")
    to = int(round(body.amount_bb * BB)) if body.amount_bb is not None else None
    t.apply(0, body.action, to, source="hero")
    return t.snapshot()


@app.post("/api/claude")
def toggle_claude(body: ClaudeToggleIn):
    t = _table()
    t.config.claude_enabled = body.enabled
    return t.snapshot()


@app.post("/api/rebuy")
def rebuy():
    t = _table()
    t.rebuy_hero()
    return t.snapshot()


@app.get("/api/hands")
def hands(limit: int = 30):
    t = _table()
    return store.recent_hands(t.session_id, limit)


# ---- analysis ---------------------------------------------------------------
_mistake_cache: dict[int, list[dict]] = {}  # hand id -> mistakes (hand records never change)


def _scope_rows(scope: str):
    if scope == "all":
        return store.hand_rows()
    session_id = table.session_id if table else store.latest_session()
    return store.hand_rows(session_id) if session_id is not None else []


@app.get("/api/reads")
def reads():
    """What the regulars know about you, for the Stats page."""
    if table and 0 in table.profiles:
        profile = table.profiles[0]
    else:
        heads_up = bool(table) and sum(1 for s in table.seats if s.cfg.kind != "empty") == 2
        profile = _hero_profile(heads_up)
    out = profile.describe()
    out["active"] = bool(table) and 0 in table.profiles
    return out


@app.get("/api/stats")
def stats(scope: str = "session"):
    from .analysis import stats as stats_mod
    rows = _scope_rows(scope)
    return stats_mod.compute([r for _, _, r in rows])


@app.get("/api/mistakes")
def mistakes(scope: str = "session"):
    from .analysis import mistakes as mistakes_mod
    per_hand = []
    for hand_id, session_id, rec in _scope_rows(scope):
        if hand_id not in _mistake_cache:
            _mistake_cache[hand_id] = mistakes_mod.analyse_hand(rec)
        net = rec["result"]["net"].get("0", rec["result"]["net"].get(0, 0))
        meta = {"hand_id": hand_id, "session_id": session_id, "hand_no": rec["number"],
                "hand_result_bb": round(net / BB, 2)}
        per_hand.append((meta, _mistake_cache[hand_id]))
    out = mistakes_mod.summarise(per_hand)
    out["hands"] = len(per_hand)
    return out


@app.get("/api/hand/{hand_id}")
def hand(hand_id: int):
    rec = store.hand(hand_id)
    if rec is None:
        raise HTTPException(404, "No such hand")
    return rec


STATIC = ROOT / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
