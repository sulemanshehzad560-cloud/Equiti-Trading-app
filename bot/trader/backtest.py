"""Replay historical bars through the exact same Engine used live."""
import csv
import math
import random
from datetime import datetime, timedelta, timezone

from .broker import PaperBroker
from .engine import Engine
from .models import Bar


def load_csv(path):
    """Reads MT5 'Export bars' files (<DATE> <TIME> <OPEN>...) or plain time,open,high,low,close[,volume]."""
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
        t = datetime.fromisoformat(ts.strip())
        t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
        vol = r[idx["tickvol"]] if "tickvol" in idx else r[idx["volume"]] if "volume" in idx else 0
        bars.append(Bar(t, float(r[idx["open"]]), float(r[idx["high"]]), float(r[idx["low"]]),
                        float(r[idx["close"]]), float(vol or 0)))
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


def run_backtest(cfg, symbol, bars, balance=10000.0, spread_points=10, window=300):
    broker = PaperBroker(balance, spread_points)
    engine = Engine({**cfg, "symbols": [symbol]}, broker, news=None)
    start = engine.ensemble.min_bars() + 5
    curve = []
    for i, bar in enumerate(bars):
        broker.on_bar(symbol, bar)
        if i >= start:
            engine.on_bar(symbol, bars[max(0, i - window + 1):i + 1], now=bar.time)
        curve.append(broker.equity())
    for p in list(broker.positions()):
        broker.close(p, "end of test")
    return report(broker, curve, balance)


def report(broker, curve, balance):
    pnls = [c[3] for c in broker.closed]
    wins, losses = [p for p in pnls if p > 0], [p for p in pnls if p <= 0]
    peak, max_dd = balance, 0.0
    for e in curve:
        peak = max(peak, e)
        max_dd = max(max_dd, (peak - e) / peak * 100)
    rets = [(curve[i] - curve[i - 1]) / curve[i - 1] for i in range(1, len(curve)) if curve[i - 1]]
    mean = sum(rets) / len(rets) if rets else 0
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / len(rets)) if rets else 0
    return {
        "trades": len(pnls),
        "win_rate_pct": round(100 * len(wins) / len(pnls), 1) if pnls else 0.0,
        "net_profit": round(sum(pnls), 2),
        "profit_factor": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) else None,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "max_drawdown_pct": round(max_dd, 2),
        "final_balance": round(broker.balance, 2),
        "sharpe_per_bar": round(mean / sd, 4) if sd else 0.0,
        "exits": {r: sum(1 for c in broker.closed if c[4] == r) for r in {c[4] for c in broker.closed}},
    }
