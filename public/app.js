/* equiti/trader: control desk for the bot's HTTP API (bot/trader/api.py) */
"use strict";

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k, d = "") { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage blocked */ } },
  del(k) { try { localStorage.removeItem(k); } catch { /* storage blocked */ } },
};
const cfg = { url: store.get("et.url"), token: store.get("et.token"), sample: store.get("et.sample") === "1" };
const S = { tab: "desk", status: null, positions: [], news: null, journal: [], research: null, ccy: "USD", eq: [], latency: null, synced: 0, logFilter: "all" };
const TABS = ["desk", "book", "wire", "log", "lab"];

/* ---------- format ---------- */
const money = (v, cur = S.ccy) => {
  if (v == null || isNaN(v)) return "-";
  try { return new Intl.NumberFormat("en-US", { style: "currency", currency: cur, minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v); }
  catch { return Number(v).toFixed(2); }
};
const signed = (v) => (v > 0 ? "+" : v < 0 ? "−" : "") + money(Math.abs(v));
const tone = (v) => (v > 0 ? "up" : v < 0 ? "down" : "dim");
const fx = (v, d = 5) => (v ? Number(v).toFixed(d) : "-");
const pad = (n) => String(Math.floor(n)).padStart(2, "0");
const hms = (s) => `${pad(s / 3600)}:${pad((s % 3600) / 60)}:${pad(s % 60)}`;
const utc = (t) => { const d = new Date(t); return isNaN(d) ? "--:--:--" : d.toISOString().slice(11, 19); };
function ago(t) {
  const s = (Date.now() - new Date(t).getTime()) / 1000;
  if (isNaN(s)) return "";
  const a = Math.abs(s), txt = a < 60 ? Math.round(a) + "s" : a < 3600 ? Math.round(a / 60) + "m" : a < 172800 ? Math.round(a / 3600) + "h" : Math.round(a / 86400) + "d";
  return s < 0 ? "in " + txt : txt + " ago";
}
function tminus(t) {
  const s = Math.round((new Date(t).getTime() - Date.now()) / 1000);
  return (s >= 0 ? "T−" : "T+") + hms(Math.abs(s));
}
const sgn = (v, d = 2) => (v > 0 ? "+" : "") + Number(v).toFixed(d);

/* ---------- api ---------- */
async function api(path, method = "GET", conf = cfg) {
  if (conf.sample) return Sample.handle(path, method);
  const base = (conf.url || location.origin).replace(/\/+$/, "");
  const t0 = performance.now();
  const r = await fetch(base + "/api/" + path, {
    method, headers: { Authorization: "Bearer " + conf.token }, cache: "no-store",
    signal: AbortSignal.timeout ? AbortSignal.timeout(12000) : undefined,
  });
  if (conf === cfg) S.latency = Math.round(performance.now() - t0);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(data.error || "HTTP " + r.status), { status: r.status });
  return data;
}

function link(kind, text) {
  $("#link .led").className = "led " + kind;
  $("#linkText").textContent = text;
}
function err(msg) { const e = $("#error"); e.hidden = !msg; e.textContent = msg || ""; }

/* ---------- desk ---------- */
function spark() {
  const svg = $("#spark"), pts = S.eq;
  if (pts.length < 2) { svg.innerHTML = ""; return; }
  const vs = pts.map((p) => p[1]), lo = Math.min(...vs), hi = Math.max(...vs), span = hi - lo || 1;
  const xy = pts.map((p, i) => [(i / (pts.length - 1)) * 200, 56 - ((p[1] - lo) / span) * 52]);
  const d = xy.map(([x, y], i) => (i ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1)).join("");
  svg.classList.toggle("neg", vs[vs.length - 1] < vs[0]);
  svg.innerHTML = `<path class="a" d="${d}L200 60L0 60Z"/><path class="l" d="${d}"/>`;
}

function classify(j) {
  if (j.action === "open") return "ORDER SENT";
  if (j.action === "rejected") return "BROKER REJECT";
  if (j.action === "close" || j.action === "close_all") return "EXIT";
  const w = String(j.why || "");
  const map = [[/blackout/, "NEWS BLACKOUT"], [/news against/, "NEWS VETO"], [/already in/, "ALREADY IN"], [/market closed/, "MKT CLOSED"], [/max open/, "SLOTS FULL"],
    [/spread/, "SPREAD WIDE"], [/trading hours/, "OFF HOURS"], [/daily loss/, "DAILY HALT"], [/paused/, "PAUSED"], [/minimum/, "SIZE < MIN"]];
  for (const [rx, k] of map) if (rx.test(w)) return k;
  return "OTHER";
}

function renderDesk() {
  const s = S.status;
  if (!s) return;
  const a = s.account;
  S.ccy = a.currency || "USD";
  const floating = S.positions.reduce((t, p) => t + (p.profit || 0), 0);
  $("#acctId").textContent = `#${a.login} · ${a.server}`;
  $("#server").textContent = "MT5 " + a.server;
  $("#equity").textContent = money(a.equity);
  const start = a.equity - s.day_pnl, dayPct = start ? (s.day_pnl / start) * 100 : 0;
  $("#dayLine").innerHTML = `<span class="${tone(s.day_pnl)}">${esc(signed(s.day_pnl))} (${sgn(dayPct)}%)</span> <span class="dim">today</span>`;
  $("#balance").textContent = money(a.balance);
  $("#floating").innerHTML = `<span class="${tone(floating)}">${esc(signed(floating))}</span>`;
  $("#dayPnl").innerHTML = `<span class="${tone(s.day_pnl)}">${esc(signed(s.day_pnl))}</span>`;
  $("#freeMargin").textContent = a.margin_free == null ? "n/a" : money(a.margin_free);
  spark();

  const mode = String(s.mode || "").toLowerCase();
  const tag = $("#modeTag");
  tag.textContent = mode === "live" ? "● LIVE" : mode === "demo" ? "DEMO" : mode === "paper" ? "PAPER" : mode.toUpperCase();
  tag.className = "tag " + (mode === "live" ? "live" : mode === "demo" ? "demo" : "paper");
  const [led, text, sub] = s.halted ? ["bad", "HALTED", "Daily loss limit hit. Bot sent to its room until 00:00 UTC."]
    : s.paused ? ["warn", "PAUSED", "Hands off the wheel. Stops and exits still run."]
    : ["ok", "RUNNING", `Scanning ${s.symbols.length} symbols on ${s.timeframe}. Acts only on closed bars.`];
  $("#stateLed").className = "led big " + led;
  $("#stateText").textContent = text;
  $("#stateSub").textContent = sub;
  $("#tf").textContent = s.timeframe;
  $("#thresh").textContent = s.signal_threshold ?? "-";
  $("#strats").innerHTML = (s.strategies || []).map((x) => `<span class="strat">${esc(x.name)} <b>×${Number(x.weight).toFixed(1)}</b></span>`).join("")
    + (s.news_enabled ? `<span class="strat">news-filter <b>on</b></span>` : `<span class="strat">news-filter <b class="down">off</b></span>`);
  const pb = $("#pauseBtn");
  pb.textContent = s.paused ? "Resume entries" : "Pause entries";
  pb.classList.toggle("primary", !!s.paused);

  const r = s.risk || {};
  const open = S.positions.length, slots = r.max_open || 0;
  $("#slotsTxt").textContent = `${open}/${slots}`;
  bar("#slotsBar", slots ? open / slots : 0);
  const budget = start * (r.max_daily_loss_pct || 0) / 100, used = Math.max(0, -s.day_pnl);
  $("#ddTxt").textContent = budget ? `${money(used)} / ${money(budget)}` : "-";
  bar("#ddBar", budget ? used / budget : 0);
  $("#rpt").textContent = r.risk_per_trade_pct != null ? r.risk_per_trade_pct + "%" : "-";
  $("#slx").textContent = r.sl_atr ? r.sl_atr + "×ATR" : "-";
  $("#tpx").textContent = r.tp_atr ? r.tp_atr + "×ATR" : "-";
  $("#rr").textContent = r.sl_atr && r.tp_atr ? "1:" + (r.tp_atr / r.sl_atr).toFixed(1) : "-";
  $("#spr").textContent = r.max_spread_points != null ? r.max_spread_points + "pt" : "-";

  const counts = {};
  S.journal.forEach((j) => { const k = classify(j); counts[k] = (counts[k] || 0) + 1; });
  const rows = Object.entries(counts).sort((x, y) => y[1] - x[1]), max = Math.max(1, ...rows.map((x) => x[1]));
  $("#pipeline").innerHTML = rows.map(([k, n]) => {
    const c = k === "ORDER SENT" ? "" : /VETO|BLACKOUT|HALT|REJECT/.test(k) ? "bad" : "warn";
    return `<div class="pipe"><span class="name">${esc(k)}</span><div class="bar"><span class="${c}" style="width:${(n / max) * 100}%"></span></div><span class="n">${n}</span></div>`;
  }).join("") || `<div class="empty">No decisions yet. Waiting for a bar to close.</div>`;

  const bias = Object.fromEntries(((S.news && S.news.symbols) || []).map((x) => [x.symbol, x]));
  $("#symRows").innerHTML = s.symbols.map((sym) => {
    const b = bias[sym], last = (s.last_action || {})[sym];
    const res = last ? last.result : "waiting for first closed bar";
    const inPos = S.positions.find((p) => p.symbol === sym);
    const st = b && b.blackout ? `<span class="blk">BLACKOUT</span>` : inPos ? `<span class="${inPos.side === "BUY" ? "up" : "down"}">${inPos.side}</span>` : `<span class="dim">flat</span>`;
    const cls = /^opened/.test(res) ? "up" : /^rejected/.test(res) ? "down" : "";
    return `<tr><td class="sym">${esc(sym)}</td><td class="num">${b ? miniMeter(b.bias) + `<span class="${tone(b.bias)}">${sgn(b.bias)}</span>` : "-"}</td>
      <td>${st}</td><td class="wide ${cls}">${esc(res)}</td></tr>`;
  }).join("");
}
function bar(sel, frac) {
  const el = $(sel), f = Math.max(0, Math.min(1, frac || 0));
  el.style.width = f * 100 + "%";
  el.className = f >= 0.85 ? "bad" : f >= 0.6 ? "warn" : "";
}
function miniMeter(v) {
  const w = Math.min(1, Math.abs(v)) * 50;
  return `<span class="mini"><span style="left:${v >= 0 ? 50 : 50 - w}%;width:${w}%;background:var(${v >= 0 ? "--up" : "--down"})"></span></span>`;
}

/* ---------- tape ---------- */
function renderTape() {
  const s = S.status;
  if (!s) { $("#tape").innerHTML = ""; return; }
  const bias = Object.fromEntries(((S.news && S.news.symbols) || []).map((x) => [x.symbol, x]));
  const items = s.symbols.map((sym) => {
    const b = bias[sym], p = S.positions.find((x) => x.symbol === sym);
    const arrow = b ? (b.bias > 0.05 ? "▲" : b.bias < -0.05 ? "▼" : "■") : "·";
    return `<span class="tape-item"><b>${esc(sym)}</b><span class="${b ? tone(b.bias) : "dim"}">${arrow} ${b ? sgn(b.bias) : "--"}</span>`
      + (b && b.blackout ? ` <span class="warn">BLK</span>` : "")
      + (p ? ` <span class="${tone(p.profit)}">${p.side} ${esc(signed(p.profit))}</span>` : "") + `</span>`;
  });
  const ev = ((S.news && S.news.events) || []).find((e) => new Date(e.time) > Date.now());
  if (ev) items.push(`<span class="tape-item"><b>NEXT</b><span class="warn">${esc(ev.currency)} ${esc(ev.title)} ${tminus(ev.time)}</span></span>`);
  const html = items.join("");
  $("#tape").innerHTML = html + html; // doubled for a seamless loop
}

/* ---------- book ---------- */
function renderBook() {
  const list = S.positions;
  $("#bookCount").textContent = list.length || "";
  $("#bookEmpty").hidden = list.length > 0;
  const net = list.reduce((t, p) => t + (p.profit || 0), 0);
  const longs = list.filter((p) => p.side === "BUY").length;
  $("#bookSummary").innerHTML = list.length ? `${list.length} open · ${longs}L/${list.length - longs}S · net <span class="${tone(net)}">${esc(signed(net))}</span>` : "";
  $("#positions").innerHTML = list.map((p) => {
    const d = p.digits ?? 5, side = p.side === "BUY" ? 1 : -1;
    const px = p.price || p.entry, risk = Math.abs(p.entry - p.sl);
    const R = risk ? ((px - p.entry) * side) / risk : null;
    let track = "";
    if (p.sl) {
      // strategies that exit by rule (tp = 0) get a notional 2R marker so the track still reads
      const tp = p.tp || p.entry + side * Math.max(2 * risk, Math.abs(px - p.entry) * 1.2);
      const f = (x) => Math.max(0, Math.min(1, side > 0 ? (x - p.sl) / (tp - p.sl) : (p.sl - x) / (p.sl - tp))) * 100;
      track = `<div class="track" title="stop → entry → target"><div class="rail"></div>
          <div class="mark" style="left:${f(p.entry)}%"></div><div class="now" style="left:${f(px)}%"></div></div>
        <div class="track-labels"><span>SL ${fx(p.sl, d)}</span><span>entry ${fx(p.entry, d)}</span><span>${p.tp ? "TP " + fx(p.tp, d) : "exit by rule"}</span></div>`;
    }
    return `<li class="pos ${side > 0 ? "long" : "short"}">
      <div class="pos-top"><span class="sym">${esc(p.symbol)}</span><span class="side ${side > 0 ? "up" : "down"}">${esc(p.side)} ${esc(p.volume)}</span>
        <span class="pl ${tone(p.profit)}">${esc(signed(p.profit))}</span></div>
      <div class="pos-meta"><span class="strat-tag">${esc(owner(p.comment))}</span><span>px <b>${fx(px, d)}</b></span><span>R <b class="${R == null ? "" : tone(R)}">${R == null ? "-" : sgn(R) + "R"}</b></span>
        <span>age <b>${esc(ago(p.opened).replace(" ago", ""))}</b></span><span>#${esc(p.ticket)}</span>${/^xt[: ]\w+$/.test(p.comment || "") ? "" : `<span>${esc(p.comment)}</span>`}</div>
      ${track}
    </li>`;
  }).join("");
}

const owner = (c) => { const m = /xt[: ](\w+)/.exec(c || ""); return m ? m[1] : "manual"; };

/* ---------- lab ---------- */
function renderLab() {
  const s = S.status, R = S.research || { rows: [], strategies: {} };
  if (!s) return;
  const indep = s.strategy_mode === "independent";
  $("#labMode").textContent = `${indep ? "independent: each strategy owns its trades" : "vote: strategies combine into one signal"} · ${s.timeframe}`;
  const rows = R.rows || [];
  const bh = (u) => rows.find((r) => r.universe === u && r.sizing === "-");
  const evidence = (name) => rows.filter((r) => r.strategies && r.strategies.length === 1 && r.strategies[0] === name && /^alloc/.test(r.sizing));
  const pct = (v) => `${v > 0 ? "+" : ""}${Number(v).toFixed(1)}%`;
  const dd = (v) => `${Number(v).toFixed(1)}%`;
  $("#playbook").innerHTML = (s.strategies || []).map((st) => {
    const ev = evidence(st.name).map((r) => {
      const b = bh(r.universe);
      const variant = r.test.replace(st.name, "").replace(/[()]/g, "").trim();
      return `<div>${esc(r.universe)}${variant ? ` <span class="dim">(${esc(variant)})</span>` : ""}: CAGR <b class="${tone(r.cagr_pct)}">${pct(r.cagr_pct)}</b> · Sharpe <b>${r.sharpe}</b> · DD <b>${dd(r.max_drawdown_pct)}</b>${r.trades ? ` · win ${Math.round(r.win_rate_pct)}%` : ""}
        ${b ? `<span class="vs">· vs B&amp;H ${pct(b.cagr_pct)} / ${b.sharpe} / DD ${dd(b.max_drawdown_pct)}</span>` : ""}</div>`;
    }).join("") || `<div class="dim">No offline evidence yet. Backtest it on your own MT5 history (see RESEARCH.md).</div>`;
    return `<div class="strat-card">
      <div class="strat-head"><b>${esc(st.title || st.name)}</b><span class="key">${esc(st.name)}</span><span class="pill-s ${esc(st.style)}">${esc(st.style || "")}</span>
        <span class="open">${st.open ? `${st.open} open` : "flat"}${indep ? "" : ` · ×${Number(st.weight).toFixed(1)}`}</span></div>
      <p>${esc(st.summary || "")}</p>
      <div class="src">markets: ${esc(st.markets || "any")}</div>
      <div class="src">source: ${esc(st.source || "-")}</div>
      <div class="evid">${ev}</div></div>`;
  }).join("") || `<div class="empty">No strategies enabled.</div>`;
  $("#evidence").innerHTML = rows.filter((r) => r.sizing === "-" || /^alloc/.test(r.sizing)).map((r) => `<tr class="${r.sizing === "-" ? "bh" : ""}">
      <td class="u">${esc(r.universe)}</td><td>${esc(r.test)}</td><td class="r num ${r.sizing === "-" ? "" : tone(r.cagr_pct)}">${pct(r.cagr_pct)}</td>
      <td class="r num">${r.sharpe}</td><td class="r num">${dd(r.max_drawdown_pct)}</td><td class="r num opt">${r.trades ?? "-"}</td>
      <td class="r num opt">${r.trades ? Math.round(r.win_rate_pct) + "%" : "-"}</td><td class="r num opt">${Math.round(r.exposure_pct)}%</td></tr>`).join("")
    || `<tr><td colspan="8" class="dim">No research results on the bot yet. Run <code>python main.py research --fetch</code>.</td></tr>`;
  $("#evGen").textContent = R.generated ? `Generated ${R.generated.slice(0, 10)}.` : "";
  const on = new Set((s.strategies || []).map((x) => x.name));
  $("#bench").innerHTML = Object.entries(R.strategies || {}).filter(([k]) => !on.has(k)).map(([k, v]) =>
    `<div class="bench-row"><code>${esc(k)}</code><span><b>${esc(v.title)}</b>: ${esc(v.summary)} <span class="dim">(${esc(v.source)})</span></span></div>`).join("")
    || `<div class="dim small">Every researched strategy is enabled.</div>`;
}

/* ---------- wire ---------- */
function renderWire() {
  const n = S.news;
  if (!n) return;
  if (!n.enabled) { $("#bias").innerHTML = `<li class="empty">News filter is off in the bot config. Trading blind, by choice.</li>`; return; }
  const veto = (S.status && S.status.news_veto) || 0.35;
  $("#vetoTxt").textContent = veto;
  $("#bias").innerHTML = n.symbols.map((s) => {
    const w = Math.min(1, Math.abs(s.bias)) * 50;
    return `<li><div class="head"><b>${esc(s.symbol)}</b>${s.blackout ? `<span class="blk">BLACKOUT</span>` : ""}<span class="v ${tone(s.bias)}">${sgn(s.bias)}</span></div>
      <div class="meter" style="--veto-l:${50 - veto * 50}%;--veto-r:${50 + veto * 50}%"><span style="left:${s.bias >= 0 ? 50 : 50 - w}%;width:${w}%;background:var(${s.bias >= 0 ? "--up" : "--down"})"></span><i></i></div>
      ${s.reasons.map((r) => `<div class="why">› ${esc(r)}</div>`).join("")}</li>`;
  }).join("");
  renderEvents();
  const cur = Object.entries(n.currencies).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  $("#mood").innerHTML = cur.map(([c, v]) => {
    const a = Math.min(1, Math.abs(v)) * 28;
    return `<div class="cell" style="background:color-mix(in srgb, var(${v >= 0 ? "--up" : "--down"}) ${a}%, transparent)"><b>${esc(c)}</b><span class="${tone(v)}">${sgn(v)}</span></div>`;
  }).join("") || `<div class="empty">No scored headlines yet.</div>`;
  $("#headlines").innerHTML = n.headlines.map((h) => {
    const tags = Object.entries(h.scores || {}).map(([c, v]) => `<span class="ccy ${tone(v)}">${esc(c)}${v > 0 ? "▲" : "▼"}</span>`).join("");
    return `<li><div class="hl">${esc(h.title)}</div><div class="hl-meta">${esc(utc(h.time))}Z · ${esc(ago(h.time))} ${tags || '<span class="dim">no signal</span>'}</div></li>`;
  }).join("") || `<li class="empty">Wire's silent. Either nothing happened or the feeds are down.</li>`;
}
function renderEvents() {
  const n = S.news;
  if (!n || !n.enabled) return;
  $("#events").innerHTML = n.events.map((e) => {
    const past = new Date(e.time) < Date.now();
    return `<li><div class="ev"><span class="t ${past ? "past" : ""}">${tminus(e.time)}</span><span class="c ${esc(String(e.impact).toLowerCase())}">${esc(e.currency)}</span><span class="n">${esc(e.title)}</span></div>
      ${e.forecast || e.previous ? `<div class="why">fcst ${esc(e.forecast || "-")} · prev ${esc(e.previous || "-")}</div>` : ""}</li>`;
  }).join("") || `<li class="empty">Calendar's quiet. Suspiciously quiet.</li>`;
}

/* ---------- log ---------- */
function renderLog() {
  const f = S.logFilter;
  const rows = S.journal.filter((j) => f === "all" || (f === "close" ? /close/.test(j.action) : j.action === f));
  $("#log").innerHTML = rows.map((j) => {
    let lvl, cls, msg;
    if (j.action === "open") { lvl = "OPEN"; cls = j.side > 0 ? "up" : "down"; msg = `${j.side > 0 ? "BUY" : "SELL"} ${j.volume} @ ${j.price} sl ${j.sl} tp ${j.tp}${j.size_mult && j.size_mult !== 1 ? ` ×${j.size_mult}` : ""}`; }
    else if (j.action === "rejected") { lvl = "REJ"; cls = "down"; msg = `broker rejected ${j.side > 0 ? "BUY" : "SELL"} ${j.volume}`; }
    else if (j.action === "skip") { lvl = "SKIP"; cls = "dim"; msg = j.why || ""; }
    else if (j.action === "close_all") { lvl = "SYS"; cls = "info"; msg = `flatten: ${j.closed ?? "all"} closed (${j.reason || "manual"})`; }
    else { lvl = "EXIT"; cls = "warn"; msg = j.reason || ""; }
    const sub = [/close/.test(j.action) ? "" : j.reason, ...(j.news || []).map((x) => "news › " + x)].filter(Boolean);
    if (j.strategy) msg = `[${j.strategy}] ` + msg;
    return `<div class="ln"><span class="ts">${esc(utc(j.t))}</span><span class="lvl ${cls}">${lvl}</span><span class="sy">${esc(j.symbol || "*")}</span><span class="msg">${esc(msg)}</span>${sub.map((x) => `<span class="sub">${esc(x)}</span>`).join("")}</div>`;
  }).join("") || `<div class="empty">No decisions yet. Waiting for a bar to close.</div>`;
}

/* ---------- refresh ---------- */
let timer = null, lastNews = 0, busy = false;
const configured = () => cfg.sample || !!cfg.token;
const eqKey = () => "et.eq." + (cfg.sample ? "sample" : cfg.url || location.origin);

async function refresh(full = false) {
  if (busy || !configured()) return;
  busy = true;
  try {
    const [status, positions] = await Promise.all([api("status"), api("positions")]);
    S.status = status; S.positions = positions;
    S.eq.push([Date.now(), status.account.equity]);
    if (S.eq.length > 240) S.eq.splice(0, S.eq.length - 240);
    store.set(eqKey(), JSON.stringify(S.eq));
    if (full || !S.news || Date.now() - lastNews > (S.tab === "wire" ? 30000 : 90000)) { S.news = await api("news"); lastNews = Date.now(); }
    S.journal = await api("journal?n=100");
    if (!S.research || full) S.research = await api("research").catch(() => null);
    renderAll();
    S.synced = Date.now();
    link("ok", cfg.sample ? "SAMPLE" : `LINK ${S.latency ?? "-"}ms`);
    err("");
  } catch (e) {
    link("bad", navigator.onLine ? "NO LINK" : "OFFLINE");
    err(e.status === 401 ? "Token rejected. Check it in Settings (,)."
      : e.message === "Failed to fetch" || e.name === "TimeoutError" ? "Can't reach the bot. PC asleep? Tunnel down? Wrong address in Settings?"
      : e.message);
  } finally { busy = false; }
}
function renderAll() { renderDesk(); renderBook(); renderWire(); renderLog(); renderLab(); renderTape(); }
function schedule() {
  clearInterval(timer);
  if (document.visibilityState === "visible" && configured()) timer = setInterval(refresh, 5000);
}
setInterval(() => {
  const now = new Date();
  $("#clock").textContent = "UTC " + now.toISOString().slice(11, 19);
  if (S.status && S.status.started) $("#uptime").textContent = hms(Math.max(0, (now - new Date(S.status.started)) / 1000));
  if (S.tab === "wire") renderEvents();
  $("#lastSync").textContent = S.synced ? `synced ${Math.round((Date.now() - S.synced) / 1000)}s ago` : "never synced";
}, 1000);

/* ---------- boot / tabs ---------- */
function typeBoot() {
  const lines = ["> equiti/trader v0.2", "> probing bot ........ no config", "> mode: awaiting operator", "> run the bot, then connect. or poke the sample data."];
  const el = $("#bootLog");
  el.textContent = "";
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) { el.textContent = lines.join("\n"); return; }
  let i = 0;
  const t = setInterval(() => { el.textContent += (i ? "\n" : "") + lines[i++]; if (i >= lines.length) clearInterval(t); }, 260);
}
function applyConfigured() {
  const on = configured();
  $("#boot").hidden = on;
  document.body.classList.toggle("booting", !on);
  if (!on) { $$(".tab").forEach((t) => (t.hidden = true)); link("", "OFFLINE"); typeBoot(); }
  else selectTab(S.tab, false);
}
function selectTab(name, push = true) {
  S.tab = name;
  $$(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab").forEach((t) => (t.hidden = !configured() || t.id !== "tab-" + name));
  if (push) { const u = new URL(location.href); u.searchParams.set("tab", name); history.replaceState(null, "", u); }
  if (name === "wire" && configured() && Date.now() - lastNews > 30000) refresh(true);
}
$$(".tabs button").forEach((b) => b.addEventListener("click", () => selectTab(b.dataset.tab)));
$$(".f").forEach((b) => b.addEventListener("click", () => {
  S.logFilter = b.dataset.f; $$(".f").forEach((x) => x.classList.toggle("active", x === b)); renderLog();
}));

/* ---------- controls ---------- */
function confirmBox(title, text, typed) {
  return new Promise((resolve) => {
    const d = $("#confirm"), inp = $("#confirmType"), yes = $("#confirmYes");
    $("#confirmTitle").textContent = title;
    $("#confirmText").textContent = text;
    $("#confirmTypeWrap").hidden = !typed;
    inp.value = "";
    yes.disabled = !!typed;
    inp.oninput = () => { yes.disabled = typed && inp.value.trim() !== typed; };
    d.returnValue = "";
    d.addEventListener("close", () => resolve(d.returnValue === "yes"), { once: true });
    d.showModal();
  });
}
async function togglePause() {
  if (!S.status) return;
  const b = $("#pauseBtn");
  b.disabled = true;
  try { await api(S.status.paused ? "resume" : "pause", "POST"); await refresh(); }
  catch (e) { err(e.message); } finally { b.disabled = false; }
}
$("#pauseBtn").addEventListener("click", togglePause);
$("#flattenBtn").addEventListener("click", async (ev) => {
  const n = S.positions.length;
  if (!n) return err("Nothing to flatten. You're already flat.");
  const live = S.status && S.status.mode === "live";
  const ok = await confirmBox(`[ FLATTEN ${n} POSITION${n > 1 ? "S" : ""} ]`,
    `Market-close every position the bot opened${live ? " on your LIVE account" : ""}. Your manual trades are untouched. There is no undo.`, live ? "FLATTEN" : null);
  if (!ok) return;
  ev.target.disabled = true;
  try { const r = await api("close-all", "POST"); await refresh(true); if (r.closed < n) err(`Closed ${r.closed} of ${n}. Check MT5 for the rest.`); }
  catch (e) { err(e.message); } finally { ev.target.disabled = false; }
});

/* ---------- settings ---------- */
const dlg = $("#settings");
function openSettings() { $("#botUrl").value = cfg.url; $("#botToken").value = cfg.token; $("#testResult").textContent = ""; dlg.showModal(); }
$("#openSettings").addEventListener("click", openSettings);
$('[data-action="settings"]').addEventListener("click", openSettings);
function useSample() {
  cfg.sample = true; store.set("et.sample", "1");
  if (dlg.open) dlg.close();
  loadEq(); applyConfigured(); refresh(true); schedule();
}
$('[data-action="sample"]').addEventListener("click", useSample);
$("#sampleBtn").addEventListener("click", useSample);
$("#testBtn").addEventListener("click", async () => {
  const out = $("#testResult"), conf = { url: $("#botUrl").value.trim(), token: $("#botToken").value.trim(), sample: false };
  const base = (conf.url || location.origin).replace(/\/+$/, "");
  out.textContent = `> handshake ${base}`;
  try {
    const t0 = performance.now();
    const ping = await fetch(base + "/api/ping", { cache: "no-store" }).then((r) => r.json());
    out.textContent += `\n> GET /api/ping ...... ok ${Math.round(performance.now() - t0)}ms (v${ping.version})`;
    const s = await api("status", "GET", conf);
    out.textContent += `\n> GET /api/status .... ok\n> ${s.account.server} #${s.account.login} · mode=${s.mode} · ${s.symbols.join(",")}\n> ready. hit Save.`;
  } catch (e) {
    out.textContent += `\n> FAIL: ${e.status === 401 ? "token rejected" : e.message === "Failed to fetch" ? "unreachable (bot down, wrong address, or plain http from https)" : e.message}`;
  }
});
$("#forgetBtn").addEventListener("click", () => {
  cfg.token = ""; cfg.sample = false; store.del("et.token"); store.del("et.sample");
  S.status = null; S.eq = []; dlg.close(); applyConfigured(); renderTape();
});
dlg.addEventListener("close", () => {
  if (dlg.returnValue !== "save") return;
  const url = $("#botUrl").value.trim();
  cfg.url = url; cfg.token = $("#botToken").value.trim(); cfg.sample = false;
  store.set("et.url", cfg.url); store.set("et.token", cfg.token); store.del("et.sample");
  if (url && !/^https:\/\//i.test(url) && !/^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?/i.test(url))
    err("Use an https:// address, or http://localhost on the trading PC. Installed apps can't call plain http.");
  S.status = null; loadEq(); applyConfigured(); refresh(true); schedule();
});

/* ---------- keys ---------- */
document.addEventListener("keydown", (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey || /INPUT|TEXTAREA/.test(document.activeElement.tagName) || document.querySelector("dialog[open]")) return;
  if (/^[1-5]$/.test(e.key) && configured()) selectTab(TABS[+e.key - 1]);
  else if (e.key === "p" && configured()) togglePause();
  else if (e.key === "r") refresh(true);
  else if (e.key === ",") { e.preventDefault(); openSettings(); }
  else if (e.key === "?") $("#help").showModal();
});

/* ---------- sample data (a stocks playbook on a demo account) ---------- */
const Sample = (() => {
  const t0 = Date.now(), iso = (m) => new Date(t0 + m * 60000).toISOString(), day = 1440;
  let paused = false, balance = 10342.55, research = null;
  let positions = [
    { ticket: 51230981, symbol: "US500", side: "BUY", volume: 2.4, entry: 5610.2, price: 5652.8, sl: 5388.0, tp: 0, opened: iso(-9 * day), comment: "xt:tsmom", digits: 2, pv: 2.4 },
    { ticket: 51231544, symbol: "AAPL", side: "BUY", volume: 45, entry: 218.4, price: 221.95, sl: 205.1, tp: 0, opened: iso(-2 * day), comment: "xt:rsi2", digits: 2, pv: 45 },
    { ticket: 51232010, symbol: "NVDA", side: "BUY", volume: 60, entry: 118.2, price: 116.9, sl: 104.5, tp: 0, opened: iso(-21 * day), comment: "xt:xsmom", digits: 2, pv: 60 },
  ];
  const journal = [
    { t: iso(-18), symbol: "MSFT", action: "skip", strategy: "tsmom", why: "news against trade (bias -0.41)", reason: "tsmom: 12m return +14.2% (z +0.61)", news: ["headline bias -0.41 (MSFT -0.41)"] },
    { t: iso(-19), symbol: "US500", action: "skip", strategy: "rsi2", why: "already in a position", reason: "rsi2: RSI2 8.7 above SMA200" },
    { t: iso(-2 * day - 12), symbol: "AAPL", action: "open", strategy: "rsi2", side: 1, volume: 45, price: 218.4, sl: 205.1, tp: 0, size_mult: 1.26, reason: "rsi2: RSI2 6.3 above SMA200", news: ["headline bias +0.52 (AAPL +0.52)"] },
    { t: iso(-3 * day - 11), symbol: "MSFT", action: "close", strategy: "rsi2", reason: "closed above SMA5 (reverted)" },
    { t: iso(-5 * day - 12), symbol: "MSFT", action: "open", strategy: "rsi2", side: 1, volume: 24, price: 412.3, sl: 389.6, tp: 0, size_mult: 1.0, reason: "rsi2: RSI2 4.1 above SMA200" },
    { t: iso(-6 * day - 10), symbol: "NVDA", action: "skip", strategy: "rsi2", why: "news blackout: High impact USD 'CPI m/m' in 20 min", reason: "rsi2: RSI2 9.2 above SMA200" },
    { t: iso(-9 * day - 11), symbol: "US500", action: "open", strategy: "tsmom", side: 1, volume: 2.4, price: 5610.2, sl: 5388.0, tp: 0, size_mult: 1.1, reason: "tsmom: 12m return +21.4% (z +1.12)", news: ["headline bias +0.18 (USIDX +0.18)"] },
    { t: iso(-14 * day - 12), symbol: "AAPL", action: "close", strategy: "xsmom", reason: "dropped to rank 5/4" },
    { t: iso(-21 * day - 11), symbol: "NVDA", action: "open", strategy: "xsmom", side: 1, volume: 60, price: 118.2, sl: 104.5, tp: 0, size_mult: 1.0, reason: "xsmom: rank 1/4, 12-1 mom +84.2%" },
    { t: iso(-21 * day - 12), symbol: "MSFT", action: "skip", strategy: "xsmom", why: "spread 0.21% too wide", reason: "xsmom: rank 2/4, 12-1 mom +11.9%" },
  ];
  const news = {
    enabled: true,
    symbols: [
      { symbol: "US500", blackout: false, bias: 0.18, reasons: ["headline bias +0.18 (USIDX +0.18)"] },
      { symbol: "AAPL", blackout: false, bias: 0.52, reasons: ["headline bias +0.52 (AAPL +0.52)"] },
      { symbol: "NVDA", blackout: true, bias: 0.31, reasons: ["High impact USD 'CPI m/m' in 24 min", "headline bias +0.31 (NVDA +0.31)"] },
      { symbol: "MSFT", blackout: false, bias: -0.41, reasons: ["headline bias -0.41 (MSFT -0.41)"] },
    ],
    events: [
      { title: "CPI m/m", currency: "USD", impact: "High", time: iso(24), forecast: "0.3%", previous: "0.2%" },
      { title: "FOMC Member Speaks", currency: "USD", impact: "Medium", time: iso(190), forecast: "", previous: "" },
      { title: "Unemployment Claims", currency: "USD", impact: "Medium", time: iso(1200), forecast: "228K", previous: "231K" },
    ],
    currencies: { AAPL: 0.52, MSFT: -0.41, NVDA: 0.31, USIDX: 0.18, USD: 0.45 },
    headlines: [
      { title: "Apple beats estimates as iPhone sales surge in China", time: iso(-40), scores: { AAPL: 1.4 } },
      { title: "Microsoft faces EU antitrust probe over cloud bundling", time: iso(-150), scores: { MSFT: -0.5 } },
      { title: "Stocks rally to record high as Nvidia soars on AI demand", time: iso(-300), scores: { USIDX: 1.1, NVDA: 0.7 } },
      { title: "Fed officials signal another rate hike as inflation stays sticky", time: iso(-420), scores: { USD: 1.3 } },
    ],
  };
  const info = {
    rsi2: { title: "Connors RSI(2) pullback", style: "swing", markets: "US indices, large-cap stocks (daily)", source: "Connors & Alvarez, 'Short Term Trading Strategies That Work' (2008)", summary: "Buy a sharp 1-3 day dip (RSI(2) < 10) while price is above its 200-day average. Exit when price closes back above the 5-day average." },
    tsmom: { title: "Time-series momentum (vol-scaled)", style: "swing", markets: "Indices, stocks, metals, FX (daily)", source: "Moskowitz, Ooi & Pedersen, 'Time Series Momentum', JFE (2012)", summary: "Hold the direction of the past 12-month return, sized by volatility. Exit when the 12-month return flips sign." },
    xsmom: { title: "Cross-sectional 12-1 momentum", style: "swing", markets: "A basket of stocks (daily)", source: "Jegadeesh & Titman, JF (1993); Antonacci, 'Dual Momentum' (2014)", summary: "Rank the basket by return from 12 months ago to 1 month ago. Own the top N when positive; sell when a name drops out of the top 2N." },
  };
  const pnl = (p) => Math.round((p.price - p.entry) * (p.side === "BUY" ? 1 : -1) * p.pv * 100) / 100;
  function step() {
    positions.forEach((p) => { const vol = p.entry * 0.0012; p.price = +(p.price + (Math.random() - 0.47) * vol).toFixed(p.digits); p.profit = pnl(p); });
  }
  function status() {
    const open = positions.reduce((a, p) => a + p.profit, 0);
    return {
      mode: "demo", paused, halted: false, started: iso(-372), strategy_mode: "independent",
      symbols: ["US500", "AAPL", "NVDA", "MSFT"], timeframe: "D1", signal_threshold: 0.2, news_enabled: true, news_veto: 0.35,
      strategies: ["rsi2", "tsmom", "xsmom"].map((name) => ({ name, weight: 1, ...info[name], open: positions.filter((p) => p.comment === "xt:" + name).length })),
      risk: { risk_per_trade_pct: 0.5, max_open: 8, max_per_symbol: 3, max_daily_loss_pct: 3, max_spread_points: 1000000, sl_atr: 3, tp_atr: 0 },
      account: { login: 90412877, server: "Equiti-Demo", currency: "USD", balance, equity: balance + open, margin_free: balance + open - 2612.4, demo: true },
      day_pnl: 87.3 + open,
      last_action: {
        US500: { result: "skip: already in a position" }, AAPL: { result: "opened BUY 45 lots (rsi2: RSI2 6.3 above SMA200)" },
        NVDA: { result: "skip: news blackout: High impact USD 'CPI m/m' in 24 min" }, MSFT: { result: "skip: news against trade (bias -0.41)" },
      },
    };
  }
  positions.forEach((p) => (p.profit = pnl(p)));
  return {
    seedEquity() {
      const out = [], end = status().account.equity;
      let v = end - 260;
      for (let i = 0; i < 120; i++) { v += (end - v) * 0.03 + (Math.random() - 0.45) * 14; out.push([t0 - (120 - i) * 60000, +v.toFixed(2)]); }
      return out;
    },
    async handle(path, method) {
      await new Promise((r) => setTimeout(r, 60 + Math.random() * 60));
      S.latency = 40 + Math.round(Math.random() * 30);
      if (path === "status") { step(); return status(); }
      if (path === "positions") return positions.map(({ pv, ...p }) => p);
      if (path === "news") return news;
      if (path === "research") return research || (research = await fetch("research-sample.json").then((r) => r.json()).catch(() => ({ rows: [], strategies: {} })));
      if (path.startsWith("journal")) return journal;
      if (path === "pause") { paused = true; return { paused }; }
      if (path === "resume") { paused = false; return { paused }; }
      if (path === "close-all") {
        const n = positions.length;
        balance += positions.reduce((a, p) => a + p.profit, 0);
        positions.forEach((p) => journal.unshift({ t: new Date().toISOString(), symbol: p.symbol, action: "close", strategy: owner(p.comment), reason: "flatten from the app" }));
        journal.unshift({ t: new Date().toISOString(), action: "close_all", closed: n, reason: "from the app" });
        positions = [];
        return { closed: n };
      }
      throw new Error("Not found");
    },
  };
})();

/* ---------- start ---------- */
function loadEq() {
  try { S.eq = JSON.parse(store.get(eqKey(), "[]")) || []; } catch { S.eq = []; }
  if (cfg.sample && S.eq.length < 10) S.eq = Sample.seedEquity();
}
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") refresh(); schedule(); });
addEventListener("online", () => refresh());
const q = new URLSearchParams(location.search).get("tab");
if (TABS.includes(q)) S.tab = q;
loadEq();
applyConfigured();
if (configured()) refresh(true);
schedule();
if ("serviceWorker" in navigator) addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => {}));
