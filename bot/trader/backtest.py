"""Replay historical bars through the exact same Engine used live."""
import csv
import math
import re
import random
from datetime import datetime, timedelta, timezone

from .broker import PaperBroker
from .engine import Engine
from .models import Bar


def load_csv(path):
    """Reads MT5 'Export bars' files (<DATE> <TIME> <OPEN>...), Yahoo-style Date,Open,High,Low,Close,Adj Close,Volume
    (split/dividend-adjusted using Adj Close), or plain time,open,high,low,close[,volume]."""
    with open(path, newline="") as f:
        sample = f.read(2048)
        f.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        rows = list(csv.reader(f, dialect))
    head = [h.strip("<>").lower() for h in rows[0]]
    idx = {k: head.index(k) for k in head}
    bars = []
    for r in rows[1:]:
        if not r:
            continue
        if "date" in idx and "time" in idx:
            ts = f"{r[idx['date']]} {r[idx['time']]}".replace(".", "-")
        else:
            ts = r[idx.get("time", idx.get("datetime", 0))].replace(".", "-")
        ts = ts.strip()
        if re.match(r"\d{1,2}/\d{1,2}/\d{4}", ts):                      # 11/5/2019 9:30
            t = datetime.strptime(ts, "%m/%d/%Y %H:%M" if " " in ts else "%m/%d/%Y")
        else:
            t = datetime.fromisoformat(ts)
        t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
        vol = r[idx["tickvol"]] if "tickvol" in idx else r[idx["volume"]] if "volume" in idx else 0
        o, h, l, c = (float(r[idx[k]]) for k in ("open", "high", "low", "close"))
        if "adj close" in idx and c:                 # back-adjust for splits and dividends
            f = float(r[idx["adj close"]]) / c
            o, h, l, c = o * f, h * f, l * f, c * f
        bars.append(Bar(t, o, h, l, c, float(vol or 0)))
    return bars


def synthetic_bars(n=3000, start=1.10, seed=7, minutes=15):
    """Random walk with alternating trending / ranging regimes - for trying the bot offline."""
    rnd, price, bars = random.Random(seed), start, []
    t = datetime(2025, 1, 6, tzinfo=timezone.utc)
    drift = 0.0
    for i in range(n):
        if i % 300 == 0:
            drift = rnd.choice([-1, 0, 0, 1]) * 0.00008
        o = price
        c = o + drift + rnd.gauss(0, 0.0006)
        h = max(o, c) + abs(rnd.gauss(0, 0.0003))
        l = min(o, c) - abs(rnd.gauss(0, 0.0003))
        bars.append(Bar(t, o, h, l, c, 100))
        price, t = c, t + timedelta(minutes=minutes)
    return bars


def _tf_of(bars):
    gaps = sorted((bars[i].time - bars[i - 1].time).total_seconds() for i in range(1, min(len(bars), 200)))
    g = gaps[len(gaps) // 4] if gaps else 86400
    return g, ("D1" if g >= 20 * 3600 else "H1" if g >= 3600 else "M15" if g >= 900 else "M5" if g >= 300 else "M1")


def run_portfolio_backtest(cfg, data, balance=10000.0, spread_points=10, spread_pct=None, commission_pct=0.0,
                           financing_pct_annual=0.0, window=None, eval_start=None):
    """Replay several symbols bar-by-bar through the live Engine, with the universe visible to
    cross-sectional strategies. data = {symbol: [Bar, ...]} (may have different start/end dates).
    Bars before eval_start only warm up indicators: no trading, not counted in the stats."""
    any_bars = next(iter(data.values()))
    gap, tf = _tf_of(any_bars)
    broker = PaperBroker(balance, spread_points, spread_pct=spread_pct, commission_pct=commission_pct,
                         financing_pct_annual=financing_pct_annual)
    engine = Engine({**cfg, "symbols": list(data), "timeframe": cfg.get("timeframe_override", tf)}, broker, news=None)
    window = window or max(300, engine.ensemble.min_bars() + 5)
    start = min((s.min_bars() for s in engine.ensemble.strategies), default=0)
    idx = {s: -1 for s in data}
    times = sorted({b.time for bars in data.values() for b in bars})
    pos_of = {s: {b.time: i for i, b in enumerate(bars)} for s, bars in data.items()}
    curve, exposure = [], 0
    step = timedelta(seconds=0 if tf == "D1" else gap)
    for t in times:
        moved = [s for s in data if t in pos_of[s]]
        for s in moved:
            idx[s] = pos_of[s][t]
            broker.on_bar(s, data[s][idx[s]])
        if eval_start and t < eval_start:
            continue
        views = {s: data[s][max(0, idx[s] - window + 1):idx[s] + 1] for s in data if idx[s] >= 0}
        for s in moved:
            if idx[s] + 1 >= start:
                engine.on_bar(s, views[s], now=t + step, universe={k: v for k, v in views.items() if k != s})
        curve.append((t, broker.equity()))
        exposure += bool(broker.positions())
    for p in list(broker.positions()):
        broker.close(p, "end of test")
    curve.append((times[-1], broker.balance))
    times = [t for t in times if not eval_start or t >= eval_start]
    rep = report(broker, [e for _, e in curve], balance)
    years = max((times[-1] - times[0]).days / 365.25, 1e-9)
    rep.update(annual_stats([e for _, e in curve], years, balance))
    rep["exposure_pct"] = round(100 * exposure / max(1, len(curve) - 1), 1)
    rep["costs_paid"] = round(broker.costs_paid, 2)
    rep["period"] = f"{times[0]:%Y-%m-%d} to {times[-1]:%Y-%m-%d}"
    rep["by_strategy"] = by_strategy(broker)
    return rep


def run_backtest(cfg, symbol, bars, balance=10000.0, spread_points=10, window=300, **costs):
    return run_portfolio_backtest(cfg, {symbol: bars}, balance, spread_points, window=window, **costs)


def buy_and_hold(data, balance=10000.0, eval_start=None):
    """Equal-weight buy and hold of every symbol (each from its first bar in the window), for comparison."""
    if eval_start:
        data = {s: [b for b in bars if b.time >= eval_start] for s, bars in data.items()}
    times = sorted({b.time for bars in data.values() for b in bars})
    first = {s: bars[0].close for s, bars in data.items()}
    last = {s: None for s in data}
    lookup = {s: {b.time: b.close for b in bars} for s, bars in data.items()}
    curve = []
    for t in times:
        for s in data:
            if t in lookup[s]:
                last[s] = lookup[s][t]
        vals = [last[s] / first[s] for s in data if last[s] is not None]
        curve.append(balance * sum(vals) / len(vals))
    years = (times[-1] - times[0]).days / 365.25
    out = annual_stats(curve, years, balance)
    peak, dd = balance, 0.0
    for e in curve:
        peak = max(peak, e)
        dd = max(dd, (peak - e) / peak * 100)
    out["max_drawdown_pct"] = round(dd, 2)
    return out


def annual_stats(curve, years, balance):
    rets = [(curve[i] - curve[i - 1]) / curve[i - 1] for i in range(1, len(curve)) if curve[i - 1]]
    n = len(rets) / years if years else 252
    mean = sum(rets) / len(rets) if rets else 0
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / len(rets)) if rets else 0
    downside = [r for r in rets if r < 0]
    dsd = math.sqrt(sum(r * r for r in downside) / len(rets)) if rets else 0
    cagr = (curve[-1] / balance) ** (1 / years) - 1 if years and curve[-1] > 0 else -1
    return {"cagr_pct": round(cagr * 100, 2), "sharpe": round(mean / sd * math.sqrt(n), 2) if sd else 0.0,
            "sortino": round(mean / dsd * math.sqrt(n), 2) if dsd else 0.0,
            "total_return_pct": round((curve[-1] / balance - 1) * 100, 1)}


def by_strategy(broker):
    import re
    out = {}
    for pos, _, _, pnl, _ in broker.closed:
        m = re.match(r"xt[: ](\w+)", pos.comment or "")
        k = m.group(1) if m else "?"
        d = out.setdefault(k, {"trades": 0, "wins": 0, "pnl": 0.0})
        d["trades"] += 1
        d["wins"] += pnl > 0
        d["pnl"] = round(d["pnl"] + pnl, 2)
    return out


def report(broker, curve, balance):
    pnls = [c[3] for c in broker.closed]
    wins, losses = [p for p in pnls if p > 0], [p for p in pnls if p <= 0]
    peak, max_dd = balance, 0.0
    for e in curve:
        peak = max(peak, e)
        max_dd = max(max_dd, (peak - e) / peak * 100)
    return {
        "trades": len(pnls),
        "win_rate_pct": round(100 * len(wins) / len(pnls), 1) if pnls else 0.0,
        "net_profit": round(sum(pnls), 2),
        "profit_factor": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) else None,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "max_drawdown_pct": round(max_dd, 2),
        "final_balance": round(broker.balance, 2),
        "exits": {r: sum(1 for c in broker.closed if c[4] == r) for r in {c[4] for c in broker.closed}},
    }
