"""Claude-controlled seat.

Preflop is played from the seat's style chart (charts beat LLMs preflop, and it
saves ~2/3 of the calls); Claude only decides postflop. When Claude seats are
switched off, or on any API/parse problem, the seat plays as its heuristic style.

The `anthropic` package is imported lazily on the first real call, so with Claude
switched off nothing from the SDK is loaded and no tokens can be spent.
"""

import json
import os

from ..cards import card_name
from ..engine import BB, STREETS, Decision
from .heuristic import HeuristicBot
from .pricing import cost_usd
from .styles import get_style

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["fold", "check", "call", "bet", "raise"]},
        "amount_bb": {"type": "number", "description": "For bet/raise: the total amount to bet or raise TO, in big blinds. 0 otherwise."},
        "reason": {"type": "string", "description": "One short sentence, in character, explaining the decision."},
    },
    "required": ["action", "amount_bb", "reason"],
    "additionalProperties": False,
}

# Models that support server-side refusal fallbacks (Claude API only).
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}

_client = None


def has_api_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _get_client():
    global _client
    if _client is None:
        import anthropic  # lazy: only reached when a Claude seat actually calls the API
        _client = anthropic.Anthropic(timeout=60.0, max_retries=1)
    return _client


def bb(chips: int) -> str:
    v = chips / BB
    return f"{v:g}bb"


class ClaudeBot:
    def __init__(self, name: str, style: str, model: str, persona: str = "", seed: int | None = None):
        self.name = name
        self.model = model
        self.style = get_style(style)
        self.persona = persona.strip() or f"{self.style.label}. {self.style.blurb}"
        self.fallback = HeuristicBot(style, seed)

    def hand_over(self, net: int, showdown: bool) -> None:
        self.fallback.hand_over(net, showdown)

    def _notes(self, ctx: Decision) -> str:
        """Tracker notes on players at the table, for characters that keep notes (regulars)."""
        if self.style.adapt <= 0:
            return ""
        lines = [f"Your notes on {p['name']} from {ctx.profiles[p['seat']].hands} past hands: "
                 f"{ctx.profiles[p['seat']].summary()}."
                 for p in ctx.players if p["seat"] in ctx.profiles and p["seat"] != ctx.seat
                 and ctx.profiles[p["seat"]].hands]
        return "\n\n" + "\n".join(lines) if lines else ""

    def system_prompt(self) -> str:
        return (
            f"You are {self.name}, a player at a No-Limit Texas Hold'em cash game (blinds 0.5/1bb). "
            "This is a practice table for a human who is studying poker, so play your character "
            "faithfully, including its leaks; don't drift towards perfect play.\n\n"
            f"Your character: {self.persona}\n\n"
            "Each message shows the hand so far and your legal actions. Choose one action. "
            "For bet/raise, amount_bb is the total amount to bet or raise TO (not the increment), "
            "within the legal range. Keep the reason to one short sentence."
        )

    def decide(self, ctx: Decision, claude_enabled: bool = False):
        if ctx.street == 0 or not claude_enabled or not has_api_key():
            kind, to, meta = self.fallback.decide(ctx)
            meta["source"] = "chart" if ctx.street == 0 and claude_enabled else "bot"
            return kind, to, meta
        try:
            return self._ask_claude(ctx)
        except Exception as e:  # network, auth, refusal, bad JSON: never stall the table
            kind, to, meta = self.fallback.decide(ctx)
            meta["source"] = "bot (Claude error)"
            usage = dict(e.usage) if isinstance(e, _Billed) else {"model": self.model, "cost_usd": 0.0}
            usage["error"] = f"{type(e).__name__}: {e}"[:300]
            meta["usage"] = usage
            return kind, to, meta

    def _ask_claude(self, ctx: Decision):
        client = _get_client()
        request = dict(
            model=self.model,
            max_tokens=4096,
            system=[{"type": "text", "text": self.system_prompt(), "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": describe(ctx) + self._notes(ctx)}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
        )
        if self.model in FALLBACK_MODELS:
            response = client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **request)
        else:
            response = client.messages.create(**request)

        u = response.usage
        served = getattr(response, "model", None) or self.model
        usage = {
            "model": served,
            "input_tokens": u.input_tokens or 0,
            "output_tokens": u.output_tokens or 0,
            "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
            "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
        }
        usage["cost_usd"] = cost_usd(served, usage["input_tokens"], usage["output_tokens"],
                                     usage["cache_read"], usage["cache_write"])

        if response.stop_reason in ("refusal", "max_tokens"):
            raise _Billed(f"stop_reason={response.stop_reason}", usage)
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            raise _Billed("unparseable JSON", usage)

        kind, to = self._legalise(ctx, data)
        if kind is None:
            raise _Billed(f"illegal action {data.get('action')}", usage)
        return kind, to, {"source": "claude", "reason": str(data.get("reason", ""))[:240], "usage": usage}

    @staticmethod
    def _legalise(ctx: Decision, data: dict):
        action = data.get("action")
        if action == "fold":
            return ("fold", None) if ctx.to_call else ("check", None)
        if action == "check":
            return ("check", None) if ctx.to_call == 0 else (None, None)
        if action == "call":
            return ("call", None) if ctx.to_call else ("check", None)
        if action in ("bet", "raise"):
            if not ctx.can_raise:
                return ("call", None) if ctx.to_call else ("check", None)
            to = int(round(float(data.get("amount_bb") or 0) * BB))
            return action, max(ctx.min_to, min(ctx.max_to, to))
        return None, None


class _Billed(Exception):
    """A call that cost tokens but produced no usable action; carries the usage."""

    def __init__(self, msg: str, usage: dict):
        super().__init__(msg)
        self.usage = usage


def describe(ctx: Decision) -> str:
    """Plain-text state of the hand from this seat's point of view."""
    me = next(p for p in ctx.players if p["seat"] == ctx.seat)
    lines = [
        f"{ctx.n_players}-handed. You are {me['name']} in the {ctx.position}.",
        f"Your cards: {card_name(ctx.hole[0])} {card_name(ctx.hole[1])}",
        f"Board ({STREETS[ctx.street]}): {' '.join(card_name(c) for c in ctx.board)}",
        f"Pot: {bb(ctx.pot)} (includes bets this street). Your stack behind: {bb(ctx.stack)}.",
        "",
        "Players still in the hand:",
    ]
    for p in ctx.players:
        if p["folded"]:
            continue
        tag = " (you)" if p["seat"] == ctx.seat else ""
        allin = ", all-in" if p["all_in"] else ""
        lines.append(f"- {p['name']}{tag} ({p['position']}): {bb(p['stack'])} behind, {bb(p['bet'])} bet this street{allin}")
    lines += ["", "Action so far:"]
    current = None
    for a in ctx.actions:
        if a["street"] != current:
            current = a["street"]
            board = f" [{' '.join(a['board'])}]" if a["board"] else ""
            lines.append(f"{current.capitalize()}{board}:")
        verb = {
            "post_sb": f"posts SB {bb(a['amount'])}", "post_bb": f"posts BB {bb(a['amount'])}",
            "fold": "folds", "check": "checks", "call": f"calls {bb(a['amount'])}",
            "bet": f"bets {bb(a['to'])}", "raise": f"raises to {bb(a['to'])}",
        }[a["action"]]
        lines.append(f"  {a['name']} ({a['position']}) {verb}{' (all-in)' if a.get('all_in') else ''}")
    legal = []
    if ctx.to_call:
        legal += ["fold", f"call {bb(ctx.to_call)}"]
    else:
        legal.append("check")
    if ctx.can_raise:
        verb = "raise to" if ctx.street_max_bet else "bet"
        legal.append(f"{verb} between {bb(ctx.min_to)} and {bb(ctx.max_to)} (all-in)")
    lines += ["", "Legal actions: " + "; ".join(legal)]
    return "\n".join(lines)
