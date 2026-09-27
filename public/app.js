/* Equiti Trader app: talks to the bot's HTTP API (bot/trader/api.py). */
"use strict";

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k, d = "") { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage blocked */ } },
  del(k) { try { localStorage.removeItem(k); } catch { /* storage blocked */ } },
};
const cfg = { url: store.get("et.url"), token: store.get("et.token"), sample: store.get("et.sample") === "1" };
const state = { tab: "dashboard", status: null, positions: [], news: null, journal: [], currency: "USD" };

/* ---------- formatting ---------- */
const money = (v, cur = state.currency) => {
  if (v == null || isNaN(v)) return "-";
  try { return new Intl.NumberFormat(undefined, { style: "currency", currency: cur, maximumFractionDigits: 2 }).format(v); }
  catch { return Number(v).toFixed(2); }
};
const signed = (v, cur) => (v > 0 ? "+" : "") + money(v, cur);
const cls = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");
const price = (v, d = 5) => (v ? Number(v).toFixed(d) : "-");
function ago(t) {
  const s = (Date.now() - new Date(t).getTime()) / 1000;
  if (isNaN(s)) return "";
  if (s < 0) return "in " + dur(-s);
  return dur(s) + " ago";
}
function dur(s) {
  if (s < 90) return Math.round(s) + "s";
  if (s < 5400) return Math.round(s / 60) + " min";
  if (s < 172800) return Math.round(s / 3600) + " h";
  return Math.round(s / 86400) + " d";
}

/* ---------- API ---------- */
async function api(path, method = "GET", conf = cfg) {
  if (conf.sample) return Sample.handle(path, method);
  const base = (conf.url || location.origin).replace(/\/+$/, "");
  const r = await fetch(base + "/api/" + path, {
    method, headers: { Authorization: "Bearer " + conf.token }, cache: "no-store",
    signal: AbortSignal.timeout ? AbortSignal.timeout(12000) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || "Bot replied " + r.status);
  return data;
}

function setConn(kind, text) {
  const el = $("#conn");
  el.className = "pill " + kind;
  el.textContent = text;
}
function showError(msg) {
  const el = $("#error");
  el.hidden = !msg;
  el.textContent = msg || "";
}

/* ---------- rendering ---------- */
function renderStatus() {
  const s = state.status;
  if (!s) return;
  state.currency = s.account.currency || "USD";
  $("#equity").textContent = money(s.account.equity);
  $("#balance").textContent = money(s.account.balance);
  const d = $("#dayPnl");
  d.textContent = signed(s.day_pnl);
  d.className = "num " + cls(s.day_pnl);
  $("#openCount").textContent = state.positions.length;
  $("#accountLine").textContent = `Account ${s.account.login} · ${s.account.server} · ${s.symbols.length} symbols on ${s.timeframe}`;

  const mode = String(s.mode || "").toLowerCase();
  const badge = $("#modeBadge");
  badge.textContent = mode === "live" ? "LIVE MONEY" : mode === "demo" ? "Demo account" : mode === "paper" ? "Paper" : mode;
  badge.className = "badge " + (mode === "live" ? "live" : mode === "demo" ? "demo" : "paper");

  const st = $("#botState");
  const [dot, label] = s.halted ? ["halt", "Stopped for today (daily loss limit)"]
    : s.paused ? ["pause", "Paused: no new trades"] : ["run", "Running"];
  st.innerHTML = `<span class="dot ${dot}"></span>${esc(label)}`;
  const pb = $("#pauseBtn");
  pb.textContent = s.paused ? "Resume trading" : "Pause new trades";
  pb.classList.toggle("primary", s.paused);

  $("#symbolList").innerHTML = s.symbols.map((sym) => {
    const a = (s.last_action || {})[sym];
    const r = a ? a.result : "waiting for the first closed bar";
    const tone = /^opened/.test(r) ? "up" : /^rejected/.test(r) ? "down" : "muted";
    return `<li><div class="line"><b>${esc(sym)}</b><span class="grow small ${tone}">${esc(r)}</span></div></li>`;
  }).join("");
}

function renderPositions() {
  const list = state.positions;
  $("#positionsEmpty").hidden = list.length > 0;
  $("#openCount").textContent = list.length;
  $("#positionList").innerHTML = list.map((p) => {
    const d = p.digits ?? 5;
    return `<li class="card">
      <div class="pos-head"><b>${esc(p.symbol)}</b><span class="badge ${p.side === "BUY" ? "buy" : "sell"}">${esc(p.side)} ${esc(p.volume)}</span></div>
      <div class="pos-pl ${cls(p.profit)}">${signed(p.profit)}</div>
      <div class="pos-grid">
        <div><span class="muted">Entry</span><b>${price(p.entry, d)}</b></div>
        <div><span class="muted">Stop</span><b>${price(p.sl, d)}</b></div>
        <div><span class="muted">Target</span><b>${price(p.tp, d)}</b></div>
        <div><span class="muted">Opened</span><b>${esc(ago(p.opened))}</b></div>
      </div>
      <div class="muted small wrap">#${esc(p.ticket)} ${esc(p.comment)}</div>
    </li>`;
  }).join("");
}

function meter(bias) {
  const w = Math.min(1, Math.abs(bias)) * 50;
  const color = bias >= 0 ? "var(--up)" : "var(--down)";
  const left = bias >= 0 ? 50 : 50 - w;
  return `<div class="meter" role="img" aria-label="bias ${bias.toFixed(2)}"><span style="left:${left}%;width:${w}%;background:${color}"></span></div>`;
}

function renderNews() {
  const n = state.news;
  if (!n) return;
  if (!n.enabled) {
    $("#newsSymbols").innerHTML = `<li class="muted">News filter is turned off in the bot's config.</li>`;
    return;
  }
  $("#newsSymbols").innerHTML = n.symbols.map((s) => `<li>
      <div class="line"><b>${esc(s.symbol)}</b>
        ${s.blackout ? '<span class="badge high">Blackout</span>' : ""}
        <span class="grow"></span><span class="num small ${cls(s.bias)}">${s.bias > 0 ? "+" : ""}${s.bias.toFixed(2)}</span></div>
      ${meter(s.bias)}
      ${s.reasons.map((r) => `<div class="muted small wrap">${esc(r)}</div>`).join("")}
    </li>`).join("") || `<li class="muted">No symbols.</li>`;

  $("#events").innerHTML = n.events.map((e) => `<li>
      <div class="line"><span class="badge ${esc(String(e.impact).toLowerCase())}">${esc(e.currency)}</span>
        <span class="grow">${esc(e.title)}</span><span class="small muted">${esc(ago(e.time))}</span></div>
      ${e.forecast || e.previous ? `<div class="muted small">Forecast ${esc(e.forecast || "-")} · Previous ${esc(e.previous || "-")}</div>` : ""}
    </li>`).join("") || `<li class="muted">No high or medium impact events in the next 24 h.</li>`;

  const cur = Object.entries(n.currencies).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  $("#currencies").innerHTML = cur.map(([c, v]) => `<span class="chip ${cls(v)}">${esc(c)} ${v > 0 ? "+" : ""}${Number(v).toFixed(2)}</span>`).join("")
    || `<span class="muted small">No scored headlines yet.</span>`;

  $("#headlines").innerHTML = n.headlines.map((h) => {
    const chips = Object.entries(h.scores || {}).map(([c, v]) => `<span class="chip ${cls(v)}">${esc(c)} ${v > 0 ? "▲" : "▼"}</span>`).join("");
    return `<li><div class="wrap">${esc(h.title)}</div><div class="line small muted">${esc(ago(h.time))} ${chips}</div></li>`;
  }).join("") || `<li class="muted">No headlines loaded.</li>`;
}

function renderJournal() {
  const hide = $("#hideSkips").checked;
  const rows = state.journal.filter((j) => !(hide && j.action === "skip"));
  $("#journal").innerHTML = rows.map((j) => {
    const tone = j.action === "open" ? (j.side > 0 ? "buy" : "sell") : j.action === "skip" ? "" : "medium";
    const label = j.action === "open" ? (j.side > 0 ? "BUY" : "SELL") : j.action;
    const detail = j.action === "open"
      ? `${j.volume} lots @ ${j.price} · SL ${j.sl} · TP ${j.tp}${j.size_mult && j.size_mult !== 1 ? ` · news size ×${j.size_mult}` : ""}`
      : j.why || "";
    return `<li>
      <div class="line"><span class="badge ${tone}">${esc(label)}</span><b>${esc(j.symbol || "")}</b>
        <span class="grow"></span><span class="small muted">${esc(ago(j.t))}</span></div>
      ${detail ? `<div class="small wrap">${esc(detail)}</div>` : ""}
      ${j.reason ? `<div class="small muted wrap">${esc(j.reason)}</div>` : ""}
      ${(j.news || []).map((r) => `<div class="small muted wrap">📰 ${esc(r)}</div>`).join("")}
    </li>`;
  }).join("") || `<li class="muted">Nothing yet.</li>`;
}

/* ---------- refresh loop ---------- */
let timer = null, lastNews = 0, busy = false;
function configured() { return cfg.sample || cfg.token; }

async function refresh(full = false) {
  if (busy || !configured()) return;
  busy = true;
  try {
    const [status, positions] = await Promise.all([api("status"), api("positions")]);
    state.status = status; state.positions = positions;
    renderStatus(); renderPositions();
    if (full || state.tab === "news" && Date.now() - lastNews > 60000 || !state.news) {
      state.news = await api("news"); lastNews = Date.now(); renderNews();
    }
    if (full || state.tab === "activity" || state.tab === "dashboard") {
      state.journal = await api("journal?n=100"); renderJournal();
    }
    setConn(cfg.sample ? "sample" : "ok", cfg.sample ? "Sample data" : "Connected");
    showError("");
  } catch (e) {
    setConn("bad", navigator.onLine ? "Can't reach bot" : "Offline");
    showError(e.message === "Failed to fetch"
      ? "Can't reach the bot. Check it's running and the address in Settings is right (HTTPS when not on localhost)."
      : e.message);
  } finally { busy = false; }
}
function schedule() {
  clearInterval(timer);
  if (document.visibilityState === "visible" && configured()) timer = setInterval(refresh, 5000);
}

function showOnboarding() {
  const need = !configured();
  $("#onboard").hidden = !need;
  document.querySelectorAll(".tab").forEach((t) => { if (need) t.hidden = true; });
  if (!need) selectTab(state.tab);
  else setConn("", "Not connected");
}

/* ---------- navigation ---------- */
function selectTab(name) {
  state.tab = name;
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((t) => { t.hidden = !configured() || t.id !== "tab-" + name; });
  if (name === "news" && configured()) refresh();
}
document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => {
  selectTab(b.dataset.tab);
  const u = new URL(location.href); u.searchParams.set("tab", b.dataset.tab); history.replaceState(null, "", u);
}));
$("#hideSkips").addEventListener("change", renderJournal);

/* ---------- controls ---------- */
function confirmBox(title, text) {
  return new Promise((resolve) => {
    const d = $("#confirm");
    $("#confirmTitle").textContent = title;
    $("#confirmText").textContent = text;
    d.returnValue = "";
    d.addEventListener("close", () => resolve(d.returnValue === "yes"), { once: true });
    d.showModal();
  });
}
$("#pauseBtn").addEventListener("click", async (e) => {
  const paused = state.status && state.status.paused;
  e.target.disabled = true;
  try { await api(paused ? "resume" : "pause", "POST"); await refresh(); }
  catch (err) { showError(err.message); }
  finally { e.target.disabled = false; }
});
$("#closeAllBtn").addEventListener("click", async (e) => {
  const n = state.positions.length;
  if (!n) return showError("There are no open positions to close.");
  const live = state.status && state.status.mode === "live";
  if (!await confirmBox(`Close ${n} position${n > 1 ? "s" : ""}?`,
    `This closes every trade the bot opened at market price${live ? " on your LIVE account" : ""}. Trades you opened by hand are not touched.`)) return;
  e.target.disabled = true;
  try { const r = await api("close-all", "POST"); showError(""); await refresh(true); if (r.closed < n) showError(`Closed ${r.closed} of ${n}. Check MT5.`); }
  catch (err) { showError(err.message); }
  finally { e.target.disabled = false; }
});

/* ---------- settings ---------- */
const dlg = $("#settings");
function openSettings() {
  $("#botUrl").value = cfg.url; $("#botToken").value = cfg.token; $("#testResult").textContent = "";
  dlg.showModal();
}
$("#openSettings").addEventListener("click", openSettings);
document.querySelector('[data-action="settings"]').addEventListener("click", openSettings);
function useSample() {
  cfg.sample = true; store.set("et.sample", "1");
  dlg.close(); showOnboarding(); refresh(true); schedule();
}
document.querySelector('[data-action="sample"]').addEventListener("click", useSample);
$("#sampleBtn").addEventListener("click", useSample);
$("#testBtn").addEventListener("click", async () => {
  const out = $("#testResult");
  out.textContent = "Testing…"; out.className = "small";
  const conf = { url: $("#botUrl").value.trim(), token: $("#botToken").value.trim(), sample: false };
  try {
    const s = await api("status", "GET", conf);
    out.textContent = `✓ Connected: ${s.account.server}, account ${s.account.login}, mode ${s.mode}`;
    out.className = "small up";
  } catch (e) { out.textContent = "✗ " + (e.message === "Failed to fetch" ? "Can't reach that address" : e.message); out.className = "small down"; }
});
$("#forgetBtn").addEventListener("click", () => {
  cfg.token = ""; cfg.sample = false; store.del("et.token"); store.del("et.sample");
  $("#botToken").value = ""; dlg.close(); showOnboarding();
});
dlg.addEventListener("close", () => {
  if (dlg.returnValue !== "save") return;
  const url = $("#botUrl").value.trim();
  if (url && !/^https:\/\//i.test(url) && !/^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?/i.test(url)) {
    showError("Use an https:// address (or http://localhost on the trading PC). Browsers block plain http from an installed app.");
  }
  cfg.url = url; cfg.token = $("#botToken").value.trim(); cfg.sample = false;
  store.set("et.url", cfg.url); store.set("et.token", cfg.token); store.del("et.sample");
  showOnboarding(); refresh(true); schedule();
});

/* ---------- sample data (for trying the app without a bot) ---------- */
const Sample = (() => {
  const now = Date.now(), iso = (m) => new Date(now + m * 60000).toISOString();
  let paused = false;
  let positions = [
    { ticket: 51230981, symbol: "EURUSD", side: "SELL", volume: 0.42, entry: 1.08412, sl: 1.08655, tp: 1.07926, profit: 118.4, opened: iso(-95), comment: "xt trend", digits: 5 },
    { ticket: 51231544, symbol: "XAUUSD", side: "BUY", volume: 0.08, entry: 2351.18, sl: 2338.9, tp: 2375.74, profit: 64.8, opened: iso(-40), comment: "xt breakout", digits: 2 },
    { ticket: 51232010, symbol: "USDJPY", side: "BUY", volume: 0.3, entry: 149.312, sl: 148.902, tp: 150.132, profit: -22.1, opened: iso(-15), comment: "xt trend", digits: 3 },
  ];
  const journal = [
    { t: iso(-15), symbol: "USDJPY", action: "open", side: 1, volume: 0.3, price: 149.312, sl: 148.902, tp: 150.132, size_mult: 1.28, reason: "trend: trend up ADX 31", news: ["headline bias +0.56 (USD +0.45, JPY -0.11)"] },
    { t: iso(-30), symbol: "GBPUSD", action: "skip", why: "news against trade (bias -0.45)", signal: 1, reason: "breakout: breakout above 20-bar high", news: ["headline bias -0.45 (GBP +0.00, USD +0.45)"] },
    { t: iso(-40), symbol: "XAUUSD", action: "open", side: 1, volume: 0.08, price: 2351.18, sl: 2338.9, tp: 2375.74, size_mult: 1.07, reason: "breakout: breakout above 20-bar high", news: ["headline bias +0.14 (XAU +0.59, USD +0.45)"] },
    { t: iso(-60), symbol: "EURUSD", action: "close", reason: "opposite signal: trend: trend down ADX 27" },
    { t: iso(-95), symbol: "EURUSD", action: "open", side: -1, volume: 0.42, price: 1.08412, sl: 1.08655, tp: 1.07926, size_mult: 1.48, reason: "trend: trend down ADX 29; breakout: breakdown below 20-bar low", news: ["headline bias -0.96 (EUR -0.50, USD +0.45)"] },
    { t: iso(-130), symbol: "GBPUSD", action: "skip", why: "news blackout: High impact GBP 'Official Bank Rate' in 25 min", reason: "trend: trend up ADX 24", news: [] },
  ];
  const news = {
    enabled: true,
    symbols: [
      { symbol: "EURUSD", blackout: false, bias: -0.96, reasons: ["headline bias -0.96 (EUR -0.50, USD +0.45)"] },
      { symbol: "GBPUSD", blackout: false, bias: -0.45, reasons: ["headline bias -0.45 (GBP +0.00, USD +0.45)"] },
      { symbol: "USDJPY", blackout: false, bias: 0.56, reasons: ["headline bias +0.56 (USD +0.45, JPY -0.11)"] },
      { symbol: "XAUUSD", blackout: true, bias: 0.14, reasons: ["High impact USD 'CPI m/m' in 24 min", "headline bias +0.14 (XAU +0.59, USD +0.45)"] },
    ],
    events: [
      { title: "CPI m/m", currency: "USD", impact: "High", time: iso(24), forecast: "0.3%", previous: "0.2%" },
      { title: "ECB President Lagarde Speaks", currency: "EUR", impact: "Medium", time: iso(190), forecast: "", previous: "" },
      { title: "Official Bank Rate", currency: "GBP", impact: "High", time: iso(1200), forecast: "4.75%", previous: "5.00%" },
    ],
    currencies: { EUR: -0.5, USD: 0.45, XAU: 0.59, JPY: -0.11 },
    headlines: [
      { title: "Fed officials signal another rate hike as inflation stays sticky", time: iso(-40), scores: { USD: 1.3 } },
      { title: "ECB turns dovish, euro slides against the dollar", time: iso(-120), scores: { EUR: -1.4 } },
      { title: "Gold rallies as dollar slumps on weak jobs data", time: iso(-300), scores: { XAU: 0.5, USD: -0.9 } },
      { title: "Yen weakens as BoJ keeps policy unchanged", time: iso(-420), scores: { JPY: -0.5 } },
    ],
  };
  function status() {
    const open = positions.reduce((a, p) => a + p.profit, 0);
    return {
      mode: "demo", paused, halted: false, started: iso(-600), symbols: ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"], timeframe: "M15",
      account: { login: 90412877, server: "Equiti-Demo (sample)", currency: "USD", balance: 10342.55, equity: 10342.55 + open, demo: true },
      day_pnl: 187.3 + open,
      last_action: {
        EURUSD: { result: "skip: already in a position" }, GBPUSD: { result: "skip: news against trade (bias -0.45)" },
        USDJPY: { result: "opened BUY 0.3 lots (trend: trend up ADX 31)" }, XAUUSD: { result: "skip: news blackout: High impact USD 'CPI m/m' in 24 min" },
      },
    };
  }
  return {
    async handle(path, method) {
      await new Promise((r) => setTimeout(r, 120));
      positions.forEach((p) => { p.profit = Math.round((p.profit + (Math.random() - 0.48) * 6) * 100) / 100; });
      if (path === "status") return status();
      if (path === "positions") return positions;
      if (path === "news") return news;
      if (path.startsWith("journal")) return journal;
      if (path === "pause") { paused = true; return { paused }; }
      if (path === "resume") { paused = false; return { paused }; }
      if (path === "close-all") { const n = positions.length; positions = []; journal.unshift({ t: new Date().toISOString(), action: "close_all", reason: "from the app" }); return { closed: n }; }
      throw new Error("Not found");
    },
  };
})();

/* ---------- start ---------- */
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") refresh(); schedule(); });
window.addEventListener("online", () => refresh());
const startTab = new URLSearchParams(location.search).get("tab");
if (["dashboard", "positions", "news", "activity"].includes(startTab)) state.tab = startTab;
showOnboarding();
if (configured()) refresh(true);
schedule();
if ("serviceWorker" in navigator) addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => {}));
