import { html, render, useState, useEffect, useRef, useCallback } from "./vendor/preact-htm.js";
import { BB, fmt, signed, api, load, save, Card, actionText, HandLog } from "./ui.js";
import { StatsPage, MistakesPage } from "./analysis.js";

// Rough per-seat cost per 100 hands with chart preflop + Claude postflop (see README).
const COST_HINT = { "claude-haiku-5-5": "~$0.03", "claude-sonnet-5-5": "~$0.60", "claude-opus-5-5": "~$1.10" };

const DEFAULT_NAMES = ["You", "Ava", "Ben", "Cleo", "Dax", "Eli"];
const DEFAULT_STYLES = ["tag", "tag", "lag", "station", "nit", "maniac"];
const defaultSeats = (n) => Array.from({ length: n }, (_, i) => ({
  kind: i === 0 ? "hero" : "bot", name: DEFAULT_NAMES[i], style: DEFAULT_STYLES[i],
  model: "claude-haiku-5-5", persona: "",
}));

const DEFAULT_SETUP = {
  count: 6, seats: defaultSeats(6), starting_bb: 100, sb_bb: 0.5, auto_topup: true,
  claude_enabled: false, reveal_all_at_end: false, speed: 700, auto_deal: true, show_styles: true,
};

// ---------- setup screen ----------
function Setup({ meta, onStart, error }) {
  const [cfg, setCfg] = useState(() => ({ ...DEFAULT_SETUP, ...load("setup", {}) }));
  useEffect(() => save("setup", cfg), [cfg]);
  const set = (patch) => setCfg((c) => ({ ...c, ...patch }));

  const setCount = (n) => {
    const seats = defaultSeats(n).map((d, i) => cfg.seats[i] || d);
    set({ count: n, seats: seats.slice(0, n) });
  };
  const setSeat = (i, patch) => set({ seats: cfg.seats.map((s, j) => (j === i ? { ...s, ...patch } : s)) });
  const randomize = () => {
    const keys = meta.styles.map((s) => s.key);
    set({ seats: cfg.seats.map((s, i) => (i === 0 ? s : { ...s, style: keys[Math.floor(Math.random() * keys.length)] })) });
  };
  const claudeSeats = cfg.seats.filter((s) => s.kind === "claude").length;

  return html`
  <div class="setup">
    <h1>Poker Trainer</h1>
    <p class="sub">No-Limit Hold'em cash game vs bots. Set up the table, then play.</p>

    <section>
      <h2>Table</h2>
      <div class="row">
        <button class=${cfg.count === 6 ? "seg on" : "seg"} onClick=${() => setCount(6)}>6-max</button>
        <button class=${cfg.count === 2 ? "seg on" : "seg"} onClick=${() => setCount(2)}>Heads-up</button>
        <label>Players
          <select value=${cfg.count} onChange=${(e) => setCount(+e.target.value)}>
            ${[2, 3, 4, 5, 6].map((n) => html`<option value=${n}>${n}</option>`)}
          </select>
        </label>
        <label>Starting stack (bb)
          <input type="number" min="10" max="1000" step="10" value=${cfg.starting_bb} onInput=${(e) => set({ starting_bb: +e.target.value })} />
        </label>
        <label>Small blind (bb)
          <select value=${cfg.sb_bb} onChange=${(e) => set({ sb_bb: +e.target.value })}>
            ${[0.5, 0.4, 0.25].map((v) => html`<option value=${v}>${v}</option>`)}
          </select>
        </label>
      </div>
      <div class="row">
        <label class="chk"><input type="checkbox" checked=${cfg.auto_topup} onChange=${(e) => set({ auto_topup: e.target.checked })} /> Auto top-up my stack each hand</label>
        <label class="chk"><input type="checkbox" checked=${cfg.reveal_all_at_end} onChange=${(e) => set({ reveal_all_at_end: e.target.checked })} /> Reveal mucked hands after each hand</label>
        <label class="chk"><input type="checkbox" checked=${cfg.auto_deal} onChange=${(e) => set({ auto_deal: e.target.checked })} /> Auto-deal next hand</label>
        <label>Bot speed
          <input type="range" min="100" max="2000" step="100" value=${2100 - cfg.speed} onInput=${(e) => set({ speed: 2100 - +e.target.value })} />
        </label>
      </div>
    </section>

    <section>
      <div class="row between">
        <h2>Seats</h2>
        <button class="ghost" onClick=${randomize}>Randomize styles</button>
      </div>
      <div class="seats">
        ${cfg.seats.map((s, i) => html`
          <div class="seatcfg">
            <div class="seatno">${i + 1}</div>
            ${i === 0 ? html`<div class="kind hero">You</div>` : html`
              <select class="kind" value=${s.kind} onChange=${(e) => setSeat(i, { kind: e.target.value })}>
                <option value="bot">Bot</option>
                <option value="claude">Claude</option>
                <option value="empty">Empty</option>
              </select>`}
            <input class="name" value=${s.name} onInput=${(e) => setSeat(i, { name: e.target.value })} disabled=${s.kind === "empty"} />
            ${i > 0 && s.kind !== "empty" && html`
              <select value=${s.style} onChange=${(e) => setSeat(i, { style: e.target.value })} title=${meta.styles.find((x) => x.key === s.style)?.blurb}>
                ${meta.styles.map((st) => html`<option value=${st.key}>${st.label}</option>`)}
              </select>`}
            ${s.kind === "claude" && html`
              <select value=${s.model} onChange=${(e) => setSeat(i, { model: e.target.value })}>
                ${meta.models.map((m) => html`<option value=${m.id}>${m.label}</option>`)}
              </select>
              <span class="hint">${COST_HINT[s.model]}/100 hands</span>
              <textarea class="persona" placeholder="Persona (optional) - e.g. 'Loose-passive rec who overvalues top pair and never folds a flush draw'. Blank = the selected style."
                value=${s.persona} onInput=${(e) => setSeat(i, { persona: e.target.value })}></textarea>`}
          </div>`)}
      </div>
      <p class="hint">${meta.styles.map((s) => html`<span><b>${s.label}</b>: ${s.blurb} </span>`)}</p>
    </section>

    <section>
      <h2>Claude seats</h2>
      <label class="chk big"><input type="checkbox" checked=${cfg.claude_enabled} onChange=${(e) => set({ claude_enabled: e.target.checked })} />
        Use Claude for Claude seats (${claudeSeats} configured)</label>
      <p class="hint">
        Off: Claude seats play as their selected style bot. No API calls, no tokens.
        On: they play preflop from their style chart and Claude decides postflop. You can switch this at the table at any time.
      </p>
      ${!meta.has_api_key && html`<p class="warn">No API key found. Add <code>ANTHROPIC_API_KEY=...</code> to <code>.env</code> and restart. Until then Claude seats play as bots.</p>`}
    </section>

    ${error && html`<p class="warn">${error}</p>`}
    <button class="primary" onClick=${() => onStart(cfg)}>Start session</button>
  </div>`;
}

// ---------- table screen ----------
const LAYOUT = {
  2: [[50, 88], [50, 10]],
  3: [[50, 88], [12, 28], [88, 28]],
  4: [[50, 88], [7, 48], [50, 10], [93, 48]],
  5: [[50, 88], [7, 60], [22, 10], [78, 10], [93, 60]],
  6: [[50, 88], [7, 64], [14, 14], [50, 6], [86, 14], [93, 64]],
};

function Seat({ s, pos, showStyles, lastAction, finished, meta }) {
  if (s.kind === "empty") return null;
  const [x, y] = pos;
  const bx = x + (50 - x) * 0.42, by = y + (50 - y) * 0.42;
  const style = meta.styles.find((st) => st.key === s.style);
  const cls = ["seat", s.folded && "folded", s.is_actor && "actor", s.kind === "hero" && "me"].filter(Boolean).join(" ");
  return html`
    <div class=${cls} style=${`left:${x}%;top:${y}%`}>
      <div class="cards">
        ${s.in_hand && !s.folded ? (s.cards ? s.cards.map((c) => html`<${Card} c=${c} />`) : html`<${Card} /><${Card} />`) : null}
        ${s.in_hand && s.folded && s.cards && finished ? s.cards.map((c) => html`<${Card} c=${c} small />`) : null}
      </div>
      <div class="plate">
        <div class="nm">
          ${s.name}
          ${s.position && html`<span class=${"pos" + (s.is_button ? " btn" : "")}>${s.position}</span>`}
        </div>
        <div class="stk">${fmt(s.stack)}</div>
        <div class="tags">
          ${s.kind === "claude" && html`<span class="tag claude" title=${s.model}>Claude</span>`}
          ${showStyles && s.kind !== "hero" && html`<span class="tag" title=${style?.blurb}>${style?.label}</span>`}
        </div>
      </div>
      ${finished && s.net !== undefined && s.net !== 0 && html`<div class=${"net " + (s.net > 0 ? "up" : "down")}>${signed(s.net)}${s.hand_desc ? " · " + s.hand_desc : ""}</div>`}
      ${!finished && lastAction && html`<div class="bubble">${lastAction}</div>`}
    </div>
    ${s.bet > 0 && html`<div class="bet" style=${`left:${bx}%;top:${by}%`}>${fmt(s.bet)}</div>`}
    ${s.is_button && html`<div class="dealer" style=${`left:${x + (x <= 50 ? 9 : -9)}%;top:${y + (50 - y) * 0.18}%`}>D</div>`}
  `;
}

function ActionBar({ st, onAct }) {
  const L = st.legal;
  const pot = st.hand.pot;
  const toCall = L.to_call;
  const streetMax = L.my_bet + toCall;
  const preflop = st.hand.street === "preflop";
  const [amt, setAmt] = useState(L.min_to);
  useEffect(() => setAmt(L.min_to), [L.min_to, L.max_to]);
  const clamp = (v) => Math.max(L.min_to, Math.min(L.max_to, Math.round(v)));

  const presets = preflop
    ? (streetMax <= BB
        ? [["2.5x", 2.5 * BB], ["3x", 3 * BB], ["4x", 4 * BB]]
        : [["3x", 3 * streetMax], ["4x", 4 * streetMax]])
    : [["33%", 0.33], ["50%", 0.5], ["75%", 0.75], ["Pot", 1]].map(([l, f]) =>
        [l, L.raise_kind === "bet" ? f * pot : streetMax + f * (pot + toCall)]);

  const doRaise = () => onAct({ action: L.raise_kind, amount_bb: clamp(amt) / BB });
  useEffect(() => {
    const onKey = (e) => {
      if (e.target.tagName === "INPUT" && e.key !== "Enter") return;
      if (e.key === "f" && toCall > 0) onAct({ action: "fold" });
      else if (e.key === "c") onAct({ action: toCall > 0 ? "call" : "check" });
      else if ((e.key === "r" || e.key === "Enter") && L.can_raise) doRaise();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  return html`
    <div class="actionbar">
      <div class="btns">
        ${toCall > 0 && html`<button class="fold" onClick=${() => onAct({ action: "fold" })}>Fold <kbd>F</kbd></button>`}
        <button class="call" onClick=${() => onAct({ action: toCall > 0 ? "call" : "check" })}>
          ${toCall > 0 ? `Call ${fmt(toCall)}` : "Check"} <kbd>C</kbd>
        </button>
        ${L.can_raise && html`<button class="raise" onClick=${doRaise}>
          ${clamp(amt) >= L.max_to ? "All-in " + fmt(L.max_to) : (L.raise_kind === "bet" ? "Bet " : "Raise to ") + fmt(clamp(amt))} <kbd>R</kbd>
        </button>`}
      </div>
      ${L.can_raise && html`
        <div class="sizing">
          ${presets.map(([label, v]) => html`<button class="ghost sm" onClick=${() => setAmt(clamp(v))}>${label}</button>`)}
          <button class="ghost sm" onClick=${() => setAmt(L.max_to)}>All-in</button>
          <input type="range" min=${L.min_to} max=${L.max_to} step="10" value=${amt} onInput=${(e) => setAmt(+e.target.value)} />
          <input type="number" class="amt" step="0.5" min=${L.min_to / BB} max=${L.max_to / BB}
            value=${+(amt / BB).toFixed(2)} onInput=${(e) => setAmt(Math.round(+e.target.value * BB))} />
          <span class="hint">bb</span>
        </div>`}
    </div>`;
}

function History({ meta }) {
  const [hands, setHands] = useState([]);
  const [open, setOpen] = useState(null);
  const [showMucked, setShowMucked] = useState(false);
  useEffect(() => { api("/hands?limit=40").then(setHands).catch(() => {}); }, []);
  return html`
    <div class="history">
      <label class="chk"><input type="checkbox" checked=${showMucked} onChange=${(e) => setShowMucked(e.target.checked)} /> Show mucked hands</label>
      ${hands.map((h) => {
        const me = h.players.find((p) => p.seat === 0);
        const net = h.result.net["0"] ?? 0;
        return html`
          <div class="hrow" onClick=${() => setOpen(open === h.number ? null : h.number)}>
            <span>#${h.number}</span>
            <span class="p">${me?.position}</span>
            <span class="cards">${me?.hole.map((c) => html`<${Card} c=${c} small />`)}</span>
            <span class="bd">${h.board.map((c) => html`<${Card} c=${c} small />`)}</span>
            <span class=${net > 0 ? "up" : net < 0 ? "down" : ""}>${signed(net)}</span>
          </div>
          ${open === h.number && html`
            <div class="hdetail">
              ${h.players.map((p) => html`<div class="shown">${p.name} <span class="p">${p.position}</span>
                ${(p.seat === 0 || showMucked || h.result.shown[p.seat]) ? p.hole.map((c) => html`<${Card} c=${c} small />`) : html`<span class="hint">mucked</span>`}
                <span class="hint">${meta.styles.find((s) => s.key === p.style)?.label}${p.kind === "claude" ? " · Claude" : ""}</span></div>`)}
              <${HandLog} log=${h.actions} result=${h.result} names=${Object.fromEntries(h.players.map((p) => [p.seat, p.name]))} finished=${true} />
            </div>`}`;
      })}
    </div>`;
}

function TableView({ meta, setup, initial, onExit }) {
  const [st, setSt] = useState(initial);
  const [err, setErr] = useState(null);
  const [tab, setTab] = useState("hand");
  const [showStyles, setShowStyles] = useState(setup.show_styles);
  const busy = useRef(false);

  const run = useCallback(async (fn) => {
    if (busy.current) return;
    busy.current = true;
    try { const r = await fn(); setSt(r.state || r); setErr(null); }
    catch (e) { setErr(e.message); }
    finally { busy.current = false; }
  }, []);

  // Drive bots: one action per tick until it's the hero's turn or the hand ends.
  useEffect(() => {
    const h = st.hand;
    if (!h) return;
    let t;
    if (!h.finished && h.actor !== null && h.actor !== 0) {
      t = setTimeout(() => run(() => api("/step", {})), setup.speed);
    } else if (h.finished && setup.auto_deal && !st.hero_busted) {
      t = setTimeout(() => run(() => api("/hand/start", {})), Math.max(1800, setup.speed * 2.5));
    }
    return () => clearTimeout(t);
  }, [st]);

  const lastActions = {};
  if (st.hand && !st.hand.finished) {
    const street = st.hand.street;
    for (const a of st.log) if (a.street === street) lastActions[a.seat] = actionText(a).replace(/^posts /, "");
  }
  const n = st.seats.length;
  const s = st.session;

  return html`
  <div class="tablepage">
    <header>
      <div class="stats">
        <span>Hand <b>#${st.hand?.number ?? 0}</b></span>
        <span>Hands <b>${s.hands}</b></span>
        <span>Net <b class=${s.hero_net > 0 ? "up" : s.hero_net < 0 ? "down" : ""}>${signed(s.hero_net)}</b></span>
        <span>bb/100 <b>${s.bb_per_100}</b></span>
      </div>
      <div class="controls">
        <label class="switch" title="Off = Claude seats play as bots; no API calls">
          <input type="checkbox" checked=${s.claude_enabled} onChange=${(e) => run(() => api("/claude", { enabled: e.target.checked }))} />
          <span>Claude ${s.claude_enabled ? "on" : "off"}</span>
        </label>
        <span class="cost" title=${`${s.claude.input_tokens} in / ${s.claude.output_tokens} out tokens`}>
          $${s.claude.cost_usd.toFixed(4)} · ${s.claude.calls} calls${s.claude.errors ? ` · ${s.claude.errors} errors` : ""}
        </span>
        <label class="chk"><input type="checkbox" checked=${showStyles} onChange=${(e) => setShowStyles(e.target.checked)} /> Show styles</label>
        <button class="ghost" onClick=${onExit}>New session</button>
      </div>
    </header>
    ${(err || s.last_error) && html`<div class="warn slim">${err || "Claude: " + s.last_error}</div>`}

    <main>
      <div class="felt-wrap">
        <div class="felt">
          <div class="center">
            <div class="board">
              ${[0, 1, 2, 3, 4].map((i) => st.hand?.board[i] ? html`<${Card} c=${st.hand.board[i]} />` : html`<div class="card slot"></div>`)}
            </div>
            <div class="pot">Pot ${fmt(st.hand?.pot ?? 0)}</div>
          </div>
          ${st.seats.map((seat, i) => html`<${Seat} s=${seat} pos=${LAYOUT[n][i]} showStyles=${showStyles}
            lastAction=${lastActions[seat.seat]} finished=${st.hand?.finished} meta=${meta} />`)}
        </div>
        <div class="under">
          ${st.legal && html`<${ActionBar} st=${st} onAct=${(a) => run(() => api("/act", a))} />`}
          ${st.hand?.finished && html`
            <div class="actionbar">
              ${st.hero_busted
                ? html`<button class="primary" onClick=${() => run(() => api("/rebuy", {}))}>Rebuy ${fmt(setup.starting_bb * BB)}</button>`
                : html`<button class="primary" onClick=${() => run(() => api("/hand/start", {}))}>Next hand${setup.auto_deal ? " (auto)" : ""}</button>`}
            </div>`}
          ${!st.legal && !st.hand?.finished && html`<div class="waiting">Waiting for ${st.seats[st.hand?.actor]?.name ?? "..."}${st.seats[st.hand?.actor]?.kind === "claude" && s.claude_enabled ? " (Claude is thinking)" : ""}</div>`}
        </div>
      </div>

      <aside>
        <div class="tabs">
          <button class=${tab === "hand" ? "on" : ""} onClick=${() => setTab("hand")}>This hand</button>
          <button class=${tab === "history" ? "on" : ""} onClick=${() => setTab("history")}>History</button>
        </div>
        ${tab === "hand"
          ? html`<${HandLog} log=${st.log} result=${st.hand?.result} names=${Object.fromEntries(st.seats.map((x) => [x.seat, x.name]))} finished=${st.hand?.finished} />`
          : html`<${History} key=${s.hands} meta=${meta} />`}
      </aside>
    </main>
  </div>`;
}

// ---------- root ----------
function App() {
  const [meta, setMeta] = useState(null);
  const [state, setState] = useState(null);
  const [setup, setSetup] = useState(null);
  const [error, setError] = useState(null);
  const [page, setPage] = useState("table");

  useEffect(() => {
    api("/meta").then(setMeta);
    const saved = load("setup", null);
    api("/state").then((s) => { setSetup({ ...DEFAULT_SETUP, ...saved }); setState(s); }).catch(() => {});
  }, []);

  const start = async (cfg) => {
    try {
      const body = {
        seats: cfg.seats.map(({ kind, name, style, model, persona }) => ({ kind, name, style, model, persona })),
        starting_bb: cfg.starting_bb, sb_bb: cfg.sb_bb, auto_topup: cfg.auto_topup,
        claude_enabled: cfg.claude_enabled, reveal_all_at_end: cfg.reveal_all_at_end,
      };
      const s = await api("/table", body);
      setSetup(cfg); setState(s); setError(null);
    } catch (e) { setError(e.message); }
  };

  if (!meta) return html`<div class="loading">Loading…</div>`;
  const live = !!(state && setup);
  const nav = html`
    <nav class="topnav">
      ${[["table", live ? "Table" : "Setup"], ["stats", "Stats"], ["mistakes", "Mistakes"]].map(([k, label]) => html`
        <button class=${page === k ? "on" : ""} onClick=${() => setPage(k)}>${label}</button>`)}
    </nav>`;
  const scope = live ? "session" : "all";
  return html`
    ${nav}
    <div style=${page === "table" ? "" : "display:none"}>
      ${live
        ? html`<${TableView} meta=${meta} setup=${setup} initial=${state} onExit=${() => setState(null)} />`
        : html`<${Setup} meta=${meta} onStart=${start} error=${error} />`}
    </div>
    ${page === "stats" && html`<${StatsPage} meta=${meta} defaultScope=${scope} />`}
    ${page === "mistakes" && html`<${MistakesPage} meta=${meta} defaultScope=${scope} />`}`;
}

render(html`<${App} />`, document.getElementById("app"));
