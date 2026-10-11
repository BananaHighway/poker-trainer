// Stats and Mistakes pages.
import { html, useState, useEffect, useRef } from "./vendor/preact-htm.js";
import { api, Cards, HandLog, signedBB } from "./ui.js";

// ---------- shared ----------
function Scope({ scope, setScope }) {
  return html`
    <div class="row">
      <button class=${"seg" + (scope === "session" ? " on" : "")} onClick=${() => setScope("session")}>This session</button>
      <button class=${"seg" + (scope === "all" ? " on" : "")} onClick=${() => setScope("all")}>All sessions</button>
    </div>`;
}

function useData(path, scope) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    setData(null);
    api(`${path}?scope=${scope}`).then(setData).catch((e) => setErr(e.message));
  }, [path, scope]);
  return [data, err];
}

const Tile = ({ label, value, sub, tone }) => html`
  <div class="tile"><div class="tl">${label}</div><div class=${"tv " + (tone || "")}>${value}</div>${sub && html`<div class="ts">${sub}</div>`}</div>`;
const tone = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");

// ---------- stats ----------
const STAT_INFO = {
  vpip: ["VPIP", "Voluntarily put money in the pot preflop.",
    "Playing too many hands. Fold more preflop: tighten UTG/HJ opens and stop calling raises with weak offsuit hands.",
    "Too tight. Open wider from CO/BTN/SB and defend your BB more."],
  pfr: ["PFR", "Raised preflop.", "Raising very wide; trim the bottom of your opening and 3-bet ranges.",
    "Too passive preflop. Raise instead of limping or flatting; aim for PFR close to VPIP."],
  three_bet: ["3-bet", "Re-raised when facing a single open.",
    "3-betting far too often. Keep it to value (QQ+, AK, AQs, KQs vs late opens) plus a few suited bluffs.",
    "3-bet more for value; flatting strong hands lets opponents in cheaply."],
  fold_to_3bet: ["Fold to 3-bet", "Folded after opening and getting 3-bet.",
    "Folding too much to 3-bets; opponents can re-raise you with anything.",
    "Continuing too much vs 3-bets; dominated hands lose big pots out of position."],
  steal: ["Steal", "Opened from CO/BTN/SB when folded to.", "Stealing very wide.",
    "Not stealing enough; late-position opens are your most profitable spots."],
  cbet: ["C-bet", "Bet the flop as the preflop raiser when checked to.", "C-betting too automatically; check more on boards that favour the caller and multiway.",
    "Giving up too often as the raiser; small c-bets on dry boards print money."],
  fold_to_cbet: ["Fold to c-bet", "Folded to the preflop raiser's flop bet.", "Folding too much to c-bets.",
    "Rarely folding to c-bets; floating too light gets expensive on later streets."],
  wtsd: ["WTSD", "Went to showdown after seeing the flop.", "Calling down too light. If you reach showdown this often you're paying off value bets.",
    "Folding too much on later streets."],
  wsd: ["W$SD", "Won money at showdown.", "", "Losing at showdown: you're getting there with too many second-best hands. Fold more on the turn/river."],
  wwsf: ["WWSF", "Won the pot after seeing the flop.", "", ""],
  af: ["AF", "Postflop (bets+raises) / calls.", "Very aggressive postflop; make sure bluffs have equity or fold equity.",
    "Passive postflop: calling instead of betting/raising gives up initiative and value."],
};
const ORDER = ["vpip", "pfr", "three_bet", "fold_to_3bet", "steal", "cbet", "fold_to_cbet", "wtsd", "wsd", "wwsf", "af"];

function verdict(key, pct, target) {
  if (pct === null || !target) return null;
  if (pct > target[1]) return "high";
  if (pct < target[0]) return "low";
  return "ok";
}

function WinChart({ curve }) {
  const ref = useRef(null);
  const [hover, setHover] = useState(null);
  if (curve.length < 2) return html`<p class="hint">Play a few more hands to see the graph.</p>`;
  const W = 800, H = 240, L = 48, R = 76, T = 12, B = 26;
  const xs = (i) => L + (i / (curve.length - 1)) * (W - L - R);
  const vals = curve.flatMap((p) => [p.net, p.ev]).concat(0);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const step = niceStep((hi - lo) / 4);
  lo = Math.floor(lo / step) * step; hi = Math.ceil(hi / step) * step;
  const ys = (v) => T + (1 - (v - lo) / (hi - lo || 1)) * (H - T - B);
  const path = (k) => curve.map((p, i) => `${i ? "L" : "M"}${xs(i).toFixed(1)},${ys(p[k]).toFixed(1)}`).join("");
  const ticks = [];
  for (let v = lo; v <= hi + 1e-9; v += step) ticks.push(+v.toFixed(6));
  const last = curve[curve.length - 1];
  const [netY, evY] = spread(ys(last.net), ys(last.ev), 14);

  const onMove = (e) => {
    const r = ref.current.getBoundingClientRect();
    const x = ((e.clientX - r.left) / r.width) * W;
    const i = Math.round(((x - L) / (W - L - R)) * (curve.length - 1));
    setHover(i >= 0 && i < curve.length ? i : null);
  };
  const hp = hover !== null ? curve[hover] : null;
  return html`
    <div class="chart">
      <div class="legend"><span><i class="sw net"></i>Net won</span><span><i class="sw ev"></i>All-in EV (luck-adjusted)</span></div>
      <div class="plot">
        <svg ref=${ref} viewBox=${`0 0 ${W} ${H}`} onMouseMove=${onMove} onMouseLeave=${() => setHover(null)} role="img"
             aria-label=${`Cumulative winnings over ${curve.length} hands: net ${last.net}bb, all-in EV ${last.ev}bb`}>
          ${ticks.map((v) => html`
            <line x1=${L} x2=${W - R} y1=${ys(v)} y2=${ys(v)} class=${v === 0 ? "zero" : "grid"} />
            <text x=${L - 6} y=${ys(v) + 4} class="axis" text-anchor="end">${v}</text>`)}
          <text x=${L} y=${H - 6} class="axis">hand 1</text>
          <text x=${W - R} y=${H - 6} class="axis" text-anchor="end">${curve.length}</text>
          <path d=${path("ev")} class="line ev" />
          <path d=${path("net")} class="line net" />
          <text x=${W - R + 6} y=${netY + 4} class="dlabel">Net ${last.net}</text>
          <text x=${W - R + 6} y=${evY + 4} class="dlabel">EV ${last.ev}</text>
          ${hp && html`
            <line x1=${xs(hover)} x2=${xs(hover)} y1=${T} y2=${H - B} class="cross" />
            <circle cx=${xs(hover)} cy=${ys(hp.ev)} r="4" class="dot ev" />
            <circle cx=${xs(hover)} cy=${ys(hp.net)} r="4" class="dot net" />`}
        </svg>
        ${hp && html`
          <div class=${"tip" + (xs(hover) / W > 0.65 ? " flip" : "")} style=${`left:${(xs(hover) / W) * 100}%`}>
            <div class="th">Hand ${hover + 1}</div>
            <div><i class="sw net"></i>Net <b>${hp.net}bb</b></div>
            <div><i class="sw ev"></i>EV <b>${hp.ev}bb</b></div>
          </div>`}
      </div>
    </div>`;
}

function niceStep(raw) {
  const p = Math.pow(10, Math.floor(Math.log10(Math.max(raw, 1e-9))));
  const m = raw / p;
  return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p;
}
function spread(a, b, gap) {
  // Keep the two end labels from colliding.
  if (Math.abs(a - b) >= gap) return [a, b];
  const mid = (a + b) / 2, s = a <= b ? -1 : 1;
  return [mid + (s * gap) / 2, mid - (s * gap) / 2];
}

export function StatsPage({ meta, defaultScope }) {
  const [scope, setScope] = useState(defaultScope);
  const [d, err] = useData("/stats", scope);
  const styleLabel = (k) => {
    const [s, c] = k.split(":");
    return (meta.styles.find((x) => x.key === s)?.label || s) + (c ? " (Claude)" : "");
  };
  return html`
    <div class="page">
      <div class="row between"><h1>Stats</h1><${Scope} scope=${scope} setScope=${setScope} /></div>
      ${err && html`<p class="warn">${err}</p>`}
      ${!d ? html`<p class="hint">Loading…</p>` : d.hands === 0 ? html`<p class="hint">No hands yet.</p>` : html`
        <div class="tiles">
          <${Tile} label="Hands" value=${d.hands} sub=${d.format === "hu" ? "heads-up targets" : "6-max targets"} />
          <${Tile} label="Net" value=${signedBB(d.net_bb)} tone=${tone(d.net_bb)} />
          <${Tile} label="bb/100" value=${d.bb_per_100} tone=${tone(d.bb_per_100)} />
          <${Tile} label="All-in EV bb/100" value=${d.ev_bb_per_100} tone=${tone(d.ev_bb_per_100)}
            sub=${`${d.allin_hands} all-ins luck-adjusted: you ran ${Math.abs(d.ev_net_bb - d.net_bb).toFixed(1)}bb ${d.ev_net_bb >= d.net_bb ? "below" : "above"} EV`} />
        </div>
        ${d.hands < 1000 && html`<p class="hint">Small sample: ${d.hands} hands. Win rates swing wildly under several thousand hands; the frequency stats below stabilise much sooner (a few hundred hands).</p>`}
        <section class="card-panel"><${WinChart} curve=${d.curve} /></section>

        <section class="card-panel">
          <h2>Your tendencies</h2>
          <table class="tbl">
            <thead><tr><th>Stat</th><th>You</th><th>Sample</th><th>Target</th><th></th><th>What it means</th></tr></thead>
            <tbody>
              ${ORDER.map((k) => {
                const r = d.rates[k];
                const t = d.targets[k];
                const v = verdict(k, r.pct, t);
                const [label, help, hiTip, loTip] = STAT_INFO[k];
                const tip = v === "high" ? hiTip : v === "low" ? loTip : help;
                const unit = k === "af" ? "" : "%";
                return html`<tr>
                  <td title=${help}><b>${label}</b></td>
                  <td class="num">${r.pct === null ? "–" : r.pct + unit}</td>
                  <td class="num hint">${k === "af" ? `${r.n}/${r.of}` : `${r.n}/${r.of}`}</td>
                  <td class="num hint">${t ? `${t[0]}–${t[1]}${unit}` : ""}</td>
                  <td>${v && html`<span class=${"verdict " + v}>${v === "ok" ? "✓ in range" : v === "high" ? "▲ high" : "▼ low"}</span>`}</td>
                  <td class="hint">${r.of < 15 && r.pct !== null ? html`<i>(few opportunities) </i>` : ""}${tip}</td>
                </tr>`;
              })}
            </tbody>
          </table>
        </section>

        <div class="two">
          <section class="card-panel">
            <h2>By position</h2>
            <table class="tbl">
              <thead><tr><th>Pos</th><th>Hands</th><th>VPIP</th><th>PFR</th><th>Net</th><th>bb/100</th></tr></thead>
              <tbody>${d.by_position.map((p) => html`<tr>
                <td><b>${p.position}</b></td><td class="num">${p.hands}</td><td class="num">${p.vpip}%</td><td class="num">${p.pfr}%</td>
                <td class=${"num " + tone(p.net_bb)}>${signedBB(p.net_bb)}</td><td class=${"num " + tone(p.bb_per_100)}>${p.bb_per_100}</td></tr>`)}
              </tbody>
            </table>
          </section>
          <section class="card-panel">
            <h2>By opponent type</h2>
            <table class="tbl">
              <thead><tr><th>Opponent</th><th>Hands</th><th>Net vs them</th><th>bb/100</th></tr></thead>
              <tbody>${d.by_style.map((s) => html`<tr>
                <td><b>${styleLabel(s.style)}</b></td><td class="num">${s.hands}</td>
                <td class=${"num " + tone(s.net_bb)}>${signedBB(s.net_bb)}</td><td class=${"num " + tone(s.bb_per_100)}>${s.bb_per_100}</td></tr>`)}
              </tbody>
            </table>
            <p class="hint">Chips you won are credited to the players who lost them; chips you lost, to the players who won them.</p>
          </section>
        </div>`}
    </div>`;
}

// ---------- mistakes ----------
const LEAK_TIPS = {
  loose_call: "Before calling, count it out: you need equity ≥ call ÷ (pot + call). Big bets on the turn and river are mostly value; weak pairs and missed draws can't call.",
  overfold: "When the price is good, a bluff-catcher with enough equity must call. Folding too often lets anyone bluff you.",
  open_limp: "Never open-limp. Raise to ~2.5bb or fold; limping gives up the initiative and invites multiway pots.",
  sb_complete: "From the SB, raise or fold. Completing with junk loses money out of position.",
  loose_open: "Each position has an opening range: UTG ~15%, HJ ~19%, CO ~27%, BTN ~45%, SB ~38%. Weak offsuit hands from early seats are folds.",
  overfold_rfi: "When it's folded to you, standard hands are raises. Late position is where you make most of your money.",
  flat_premium: "3-bet your premiums. It builds the pot while you're ahead and thins the field.",
  loose_call_open: "Facing a raise you're often dominated. Continue with hands that 3-bet well or play well in position (pairs, suited broadways, suited connectors), and fold the rest.",
  loose_3bet: "Keep 3-bets to value (QQ+, AK, AQs…) plus a few suited blocker bluffs (A5s, KTs). Random 3-bets get called or 4-bet by better.",
  overfold_open: "Strong hands need to continue vs an open, by 3-betting or calling. The BB especially gets a great price.",
  overfold_3bet: "Don't fold the top of your range to a 3-bet; 4-bet or call with it.",
  loose_call_3bet: "Calling 3-bets with weak hands means playing a bloated pot with a dominated range. Continue with ~top 9–12%.",
  loose_4bet: "4-bet ranges are narrow: ~QQ+/AK plus a couple of blocker bluffs.",
  loose_call_4bet: "Facing a 4-bet you're up against a very strong range: continue with roughly QQ+/AK.",
  overfold_4bet: "Premiums are stack-off hands.",
  bluff_station: "Calling stations don't fold. Against them, value-bet thinner and bigger, and stop bluffing.",
  missed_value: "Strong hands on the river should bet: worse hands call more often than you think.",
};
const SEV = { major: "●●● major", moderate: "●● moderate", minor: "● minor" };

function Replay({ m }) {
  const [rec, setRec] = useState(null);
  useEffect(() => { api(`/hand/${m.hand_id}`).then(setRec); }, [m.hand_id]);
  if (!rec) return html`<div class="replay hint">Loading hand…</div>`;
  const names = Object.fromEntries(rec.players.map((p) => [p.seat, p.name]));
  const n = m.numbers || {};
  return html`
    <div class="replay">
      <div class="players">
        ${rec.players.map((p) => html`<div class=${"pl" + (p.seat === 0 ? " mine" : "")}>
          <b>${p.name}</b> <span class="p">${p.position}</span> <${Cards} cs=${p.hole} />
          <span class="hint">${p.seat === 0 ? "you" : p.style}${p.kind === "claude" ? " · Claude" : ""}</span></div>`)}
      </div>
      ${n.equity !== undefined && html`<div class="nums">
        Your equity vs estimated range <b>${Math.round(n.equity * 100)}%</b>
        ${n.price !== undefined && html` · price needed <b>${Math.round(n.price * 100)}%</b> · pot <b>${+n.pot_bb.toFixed(1)}bb</b> · to call <b>${+n.call_bb.toFixed(1)}bb</b>`}
      </div>`}
      <p class="hint">Opponents' actual cards are shown for review; the judgement above used only what you could see at the time.</p>
      <${HandLog} log=${rec.actions} result=${rec.result} names=${names} finished=${true} highlight=${m.action_index} />
    </div>`;
}

export function MistakesPage({ meta, defaultScope }) {
  const [scope, setScope] = useState(defaultScope);
  const [d, err] = useData("/mistakes", scope);
  const [leak, setLeak] = useState(null);
  const [sev, setSev] = useState("all");
  const [open, setOpen] = useState(null);
  const [limit, setLimit] = useState(40);
  useEffect(() => { setLeak(null); setOpen(null); }, [scope]);

  const list = d ? d.mistakes.filter((m) => (!leak || m.type === leak) && (sev === "all" || m.severity === sev)) : [];
  const handsFlagged = d ? new Set(d.mistakes.map((m) => m.hand_id)).size : 0;
  return html`
    <div class="page">
      <div class="row between"><h1>Mistakes</h1><${Scope} scope=${scope} setScope=${setScope} /></div>
      ${err && html`<p class="warn">${err}</p>`}
      ${!d ? html`<p class="hint">Analysing hands…</p>` : d.hands === 0 ? html`<p class="hint">No hands yet.</p>` : html`
        <div class="tiles">
          <${Tile} label="Hands reviewed" value=${d.hands} />
          <${Tile} label="Decisions flagged" value=${d.mistakes.length} sub=${`in ${handsFlagged} hands`} />
          <${Tile} label="Est. EV lost (equity-checked)" value=${`${d.total_ev_loss_bb}bb`} tone=${d.total_ev_loss_bb > 0 ? "down" : ""}
            sub="calls/folds vs price; preflop chart flags have no bb figure" />
        </div>
        <p class="hint">Preflop decisions are compared with a solid 6-max TAG baseline. Postflop calls/folds (and big preflop all-ins) are judged on your equity vs the opponents' likely ranges, estimated from their betting, against the price you were getting. It's a coach's first pass, not a solver: treat flags as "review this", not gospel.</p>

        <section class="card-panel">
          <h2>Biggest leaks</h2>
          <div class="leaks">
            ${d.leaks.map((g) => html`
              <button class=${"leak" + (leak === g.type ? " on" : "")} onClick=${() => setLeak(leak === g.type ? null : g.type)}>
                <div class="lt"><b>${g.title}</b><span class="cnt">×${g.count}</span></div>
                <div class="lsev">${["major", "moderate", "minor"].filter((k) => g.severity[k]).map((k) => html`<span class=${"sev " + k}>${g.severity[k]} ${k}</span>`)}</div>
                <div class="lnum">
                  ${g.ev_loss_bb > 0 && html`<span>est. <b class="down">−${g.ev_loss_bb}bb</b> EV</span>`}
                  <span class="hint">these hands: <b class=${tone(g.result_bb)}>${signedBB(g.result_bb)}</b></span>
                </div>
                <div class="ltip">${LEAK_TIPS[g.type] || ""}</div>
              </button>`)}
          </div>
        </section>

        <section class="card-panel">
          <div class="row between">
            <h2>${leak ? d.leaks.find((g) => g.type === leak)?.title : "All flagged decisions"} <span class="hint">(${list.length})</span></h2>
            <div class="row">
              ${leak && html`<button class="ghost sm" onClick=${() => setLeak(null)}>Clear filter</button>`}
              <select value=${sev} onChange=${(e) => setSev(e.target.value)}>
                <option value="all">All severities</option><option value="major">Major</option>
                <option value="moderate">Moderate</option><option value="minor">Minor</option>
              </select>
            </div>
          </div>
          <div class="mlist">
            ${list.slice(0, limit).map((m) => {
              const key = m.hand_id + ":" + m.action_index;
              return html`
                <div class=${"mrow" + (open === key ? " open" : "")}>
                  <div class="mhead" onClick=${() => setOpen(open === key ? null : key)}>
                    <span class=${"sev " + m.severity}>${SEV[m.severity]}</span>
                    <span class="hint">#${m.hand_no}</span>
                    <span class="p">${m.position}</span>
                    <${Cards} cs=${m.hole} />
                    <span class="mboard">${m.board.length ? html`<${Cards} cs=${m.board} />` : html`<span class="hint">preflop</span>`}</span>
                    <span class="mtitle">${m.title}</span>
                    <span class="mev">${m.ev_loss_bb ? html`<b class="down">−${m.ev_loss_bb}bb</b>` : ""}</span>
                  </div>
                  <div class="mdetail">${m.detail}</div>
                  ${open === key && html`<${Replay} m=${m} />`}
                </div>`;
            })}
            ${list.length > limit && html`<button class="ghost" onClick=${() => setLimit(limit + 40)}>Show more (${list.length - limit} left)</button>`}
            ${list.length === 0 && html`<p class="hint">Nothing flagged here.</p>`}
          </div>
        </section>`}
    </div>`;
}
