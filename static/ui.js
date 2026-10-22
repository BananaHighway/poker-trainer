// Shared UI helpers: formatting, API, cards, the hand log.
import { html } from "./vendor/preact-htm.js";

export const BB = 100;
export const fmt = (chips) => {
  const v = chips / BB;
  return (Number.isInteger(v) ? v : +v.toFixed(2)) + "bb";
};
export const signed = (chips) => (chips > 0 ? "+" : "") + fmt(chips);
export const signedBB = (bb) => (bb > 0 ? "+" : "") + (+bb.toFixed(1)) + "bb";

export async function api(path, body) {
  const res = await fetch("/api" + path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : res.statusText);
  return data;
}

export const load = (k, d) => { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } };
export const save = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} };

const SUIT = { s: "♠", h: "♥", d: "♦", c: "♣" };
export function Card({ c, small }) {
  if (!c) return html`<div class=${"card back" + (small ? " small" : "")}></div>`;
  const r = c[0] === "T" ? "10" : c[0];
  return html`<div class=${"card s-" + c[1] + (small ? " small" : "")}><span class="r">${r}</span><span class="su">${SUIT[c[1]]}</span></div>`;
}
export const Cards = ({ cs }) => html`<span class="cardrow">${(cs || []).map((c) => html`<${Card} c=${c} small />`)}</span>`;

export function actionText(a) {
  switch (a.action) {
    case "post_sb": return `posts SB ${fmt(a.amount)}`;
    case "post_bb": return `posts BB ${fmt(a.amount)}`;
    case "fold": return "folds";
    case "check": return "checks";
    case "call": return `calls ${fmt(a.amount)}${a.all_in ? " (all-in)" : ""}`;
    case "bet": return `bets ${fmt(a.to)}${a.all_in ? " (all-in)" : ""}`;
    case "raise": return `raises to ${fmt(a.to)}${a.all_in ? " (all-in)" : ""}`;
  }
  return a.action;
}

export function HandLog({ log, result, names, finished, highlight }) {
  const byStreet = [];
  log.forEach((a, idx) => {
    let g = byStreet[byStreet.length - 1];
    if (!g || g.street !== a.street) { g = { street: a.street, board: a.board, items: [] }; byStreet.push(g); }
    g.items.push({ ...a, idx });
  });
  return html`
    <div class="log">
      ${byStreet.map((g) => html`
        <div class="street">
          <div class="sh">${g.street}${g.board.length ? html` <${Cards} cs=${g.board} />` : ""}</div>
          ${g.items.map((a) => html`
            <div class=${"li" + (a.seat === 0 ? " mine" : "") + (a.idx === highlight ? " flagged" : "")}>
              <span class="who">${a.name} <span class="p">${a.position}</span></span> ${actionText(a)}
              ${a.source === "claude" && html` <span class="tag claude">C</span>`}
              ${finished && a.reason && html`<div class="reason">“${a.reason}”</div>`}
              ${finished && a.read && html`<div class="read">Read on you: ${a.read}</div>`}
            </div>`)}
        </div>`)}
      ${finished && result && html`
        <div class="result">
          ${result.winners.map((w) => html`<div><b>${w.name}</b> wins ${fmt(w.amount)}${w.hand ? ` with ${w.hand}` : ""}</div>`)}
          ${Object.entries(result.shown).map(([seat, cards]) => html`
            <div class="shown">${names[seat]}: <${Cards} cs=${cards} /></div>`)}
        </div>`}
    </div>`;
}
