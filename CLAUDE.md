# CLAUDE.md

Personal, local-only poker training app. The user is seriously studying **6-max No-Limit Hold'em cash games**, and heads-up must stay supported. See README.md for usage and layout.

## Stack

- Python 3.12 venv (`.venv/`), FastAPI + uvicorn, **pokerkit** for game state, and **phevaluator** for fast hand evaluation. pokerkit's evaluator is about 60x too slow for Monte Carlo.
- Frontend: Preact + htm ES modules served as static files. **No Node/npm on this machine**, so there's no build step. The bundle is vendored at `static/vendor/preact-htm.js`.
- Launch: `.\run.ps1` (no args = server on 127.0.0.1:8765; with args = run a script in the venv).

## Conventions and gotchas

- All amounts are integer chips with **BB = 100** (`engine.BB`). The UI formats chips as bb.
- Seat 0 is always the hero. pokerkit player order is the seats starting left of the button, with the button last. In heads-up, pokerkit makes index 1 (the button) post the SB.
- Cards are ints `rank*4 + suit` (ranks 2..A, suits c d h s). This is phevaluator's native encoding.
- `Automation.RUNOUT_COUNT_SELECTION` must be in the automations. Cash-game mode otherwise waits forever for a run-it-twice choice after an all-in.
- In `Table.apply`, snapshot street, board, pot and stack **before** acting. A street-closing action advances pokerkit, and a hand-ending action pays out immediately.
- **Claude seats must cost zero tokens when switched off.** `anthropic` is imported lazily inside `claude_bot._get_client()`, and only `ClaudeBot._ask_claude` reaches it. Keep it that way: no top-level SDK imports.
- Claude only decides postflop. Preflop comes from the style chart. Claude's reasons are hidden in the snapshot until the hand ends (they'd leak hand strength).
- Model IDs: `claude-haiku-5-5` (default), `claude-sonnet-5-5`, `claude-opus-5-5`. Opus and Sonnet go through `client.beta.messages.create` with `betas=["server-side-fallback-2026-07-01"], fallbacks="default"`. Haiku uses plain `messages.create`. Prices are in `bots/pricing.py`.
- `app/analysis/replay.py` rebuilds each hero decision (price, pot, live opponents) from stored records. Analysis only uses information the hero had at that point.
- Verify engine and bot changes with `.\run.ps1 scripts\simulate.py 1000 6 [style,style,...]`. It asserts chip conservation and log sanity. It then prints how hands ended (walks, uncontested raises, flops seen) and tracker stats per seat. Its docstring lists real-player reference ranges to calibrate against.
- `bots/reads.vs_open` and `threebet_continue` are the TAG baseline for facing an open or a 3-bet. Bots call them with their own style, and the mistake finder calls them with the default (TAG). Change the baseline there, not in either caller.
- Each `HeuristicBot` plays a `personalize`d copy of its style (`self.base`), and `self.style` adds tilt on top. The table calls `bot.hand_over(net, showdown)` after every hand. A seeded bot is reproducible, but two bots of one style are not identical.
- Adaptive bots. `main.create_table` builds a `bots/profile.Profile` of the hero from every stored hand of the same format (heads-up vs 3+ players) and puts it in `Table.profiles[0]`. `Table._finish` adds each new hand to it, and bots see it as `Decision.profiles`. Profiles use `analysis/stats.hand_flags`, the same stat definitions as the Stats page, so add new tracked stats there. They must stay limited to what an opponent could see (actions and showdowns, never folded hole cards).
- Bots look at a profile through `Read` (shrunk toward typical, blended by `Style.adapt`). Counters are compared with and without the read, and an action that happened only because of the read gets a `read` label. The snapshot hides `read` (like Claude's `reason`) until the hand ends, because it leaks hand strength. Check exploit changes with a leaky seat 0 against `--static`, e.g. `simulate.py 8000 6 nit,tag,tag,lag,tag,tag` vs the same command with `--static`. A single run's bb/100 is noisy (about ±12), so also look at the read-driven action counts.

## Roadmap

1. Done: table with 2–6 seats and heads-up, five heuristic styles, Claude seats with an on/off switch and cost meter, SQLite hand storage, history tab.
2. Done: Stats page (`app/analysis/stats.py`): tracker-style rates with opportunities, 6-max/HU targets, all-in EV (2-way only), by position and opponent type.
3. Done (first pass): Mistakes page (`app/analysis/mistakes.py`): preflop vs a TAG baseline chart; postflop calls/folds (and preflop all-in-sized decisions) by equity vs estimated ranges against the price, with an EV-loss estimate; bluffing stations; missed river value. Results are cached per hand id in `main._mistake_cache`, so bump or clear it if the rules change (it's in-memory, so a restart clears it). Next: send flagged spots to a solver (postflop-solver / TexasSolver), add Claude "explain this hand", add sizing checks.
4. Done: realistic bots. Calibrated to real 6-max frequencies, with per-bot personalities, tilt, positional postflop play, and regulars that adapt to the hero across sessions (`bots/profile.py`). Next: persistent bot identities (the same "Ava" every session), and showdown-based reads (did the hero's river bets turn out to be bluffs?).
5. Slumbot API as a heads-up opponent.
6. PokerStars-format hand history export.
