# Poker Trainer

A local web app for practising No-Limit Hold'em cash games against bots. It supports 2–6 players, including heads-up, and you can make any seat Claude-controlled. Every hand is saved for later analysis.

## Run

```powershell
.\run.ps1                         # Windows → http://127.0.0.1:8765
```
```bash
chmod +x run.sh && ./run.sh       # macOS / Linux
```

First time on a new machine:

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # macOS/Linux: .venv/bin/python
```

## Playing

- **Setup**: choose 6-max or heads-up (or anything from 2 to 6 players), your starting stack in bb, and the small blind. For each seat pick **Bot**, **Claude** or **Empty**, a name and a style.
- **Bot styles**:
  - **Nit**: plays very few hands and only bets with strong ones.
  - **TAG** (tight-aggressive): solid regular.
  - **LAG** (loose-aggressive): wide ranges, lots of 3-bets and barrels.
  - **Calling station**: limps and calls far too often, rarely bluffs.
  - **Maniac**: raises and bluffs relentlessly.

  Before the flop, bots follow position-based range charts scaled by their style. They defend the big blind wider against late-position and small opens. When 3-bet, they keep playing part of the range they opened. After the flop, they estimate equity against each opponent's likely range (inferred from that opponent's betting) and decide using pot odds plus style-tuned value-bet and bluff frequencies. Position matters there too: bots usually check to the last street's aggressor, need more equity to call out of position, stab when checked to in position, and barrel some turns and rivers.

  No two bots of a style play alike. Each bot gets its own slightly different version of its style (opening width, 3-bet and defence frequencies, c-bet rate, bet sizes). Hands near a range border are mixed, and once in a while a bot plays a hand from well outside its range. After losing a big pot, a bot plays looser and more aggressively for a few hands. Bots top up to the starting stack, like regulars on auto-rebuy.
- **Keyboard**: `F` fold, `C` check/call, `R` or `Enter` bet/raise. Use the preset buttons or the slider to set the size.
- **Show styles**: shows each bot's player type. Turn it off to practise reading opponents from their play.
- **History** tab: the last 40 hands. Tick *Show mucked hands* to see what the bots folded.

## Stats and Mistakes

Use the tabs at the top. Each page can show **this session** or **all sessions**.

- **Stats**:
  - Net, bb/100, and all-in EV (luck-adjusted for 2-way all-ins), with a cumulative graph of net vs EV.
  - VPIP / PFR / 3-bet / fold to 3-bet / steal / c-bet / fold to c-bet / WTSD / W$SD / AF, each compared with typical winning-regular ranges for 6-max or heads-up.
  - Results by position and by opponent type.
- **Mistakes**: every one of your decisions is re-checked using only what you could see at the time.
  - **Preflop**: checked against a solid 6-max TAG chart.
  - **Postflop**, plus big preflop all-ins: calls and folds are judged by your equity against the opponents' likely ranges vs the price you were getting, with an estimated EV loss in bb.
  - **Also flagged**: bluffing calling stations, and missed river value.
  - **Leak view**: leaks are grouped and ranked. Click a leak to filter the list, and click any decision to replay the hand with that action highlighted.

It's a first-pass coach, not a solver: ranges are estimates, and fold equity and implied odds are approximated.

## Offline

Everything runs locally and the app works with no internet connection: the UI library is vendored, there are no CDNs or fonts to load, and the server binds to 127.0.0.1. The only thing that needs the internet is Claude seats while they're switched on.

## Claude seats

Claude seats are controlled by **one on/off switch**. It's set during setup and can be changed live in the table header.

- **Off**: Claude seats play as their selected style bot. The `anthropic` SDK isn't even imported, so **no tokens are used**.
- **On**: Claude seats play preflop from their style chart and Claude decides every postflop action, following the persona you write (or the style description if you leave it blank). Claude's one-line reasoning appears in the hand log **after the hand ends**, so it can't leak information while you're playing.

To turn it on, put your key in `.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
```

The header shows live spend and the number of calls. Every call is logged to the `claude_calls` table. Rough cost per Claude seat per 100 hands, with chart preflop and Claude postflop:

| Model | ≈ cost / seat / 100 hands |
|---|---|
| Haiku 5.5 | $0.03 |
| Sonnet 5.5 | $0.60 |
| Opus 5.5 | $1.10 |

Requests use `effort: "low"` and a JSON schema for the action. Opus and Sonnet requests opt into server-side refusal fallbacks (`fallbacks: "default"`). If anything goes wrong (network error, refusal, unparseable output), that decision falls back to the heuristic bot and the error is counted in the header.

## Data

Hands are stored in `data/hands.db` (SQLite; gitignored). Each row holds a full-information JSON record: every player's hole cards, positions, all actions with pot and stack sizes, and the result. The planned stats and mistake-finder features will read from it.

## Dev scripts

```powershell
.\run.ps1 scripts\simulate.py 1000 6   # bot-vs-bot: engine invariants, how hands end, tracker stats per seat
.\run.ps1 scripts\build_preflop.py     # regenerate data/preflop_strength.json
```

## Layout

```
app/
  main.py          FastAPI routes + static serving
  engine.py        Table/hand management on pokerkit (seating, positions, logging, results)
  cards.py         int card encoding (phevaluator native), hand categories
  ranges.py        169-hand strength percentiles, range sampling
  equity.py        Monte Carlo equity vs estimated ranges
  storage.py       SQLite
  bots/
    styles.py      player-type presets
    reads.py       preflop situation, baseline ranges (shared with the mistake finder), opponent range estimates
    heuristic.py   style-driven bot (per-bot personality, mixing, tilt)
    claude_bot.py  Claude seat (lazy SDK import, heuristic fallback)
    pricing.py     model prices for the cost meter
static/            Preact + htm UI (no build step; vendored in static/vendor)
```
