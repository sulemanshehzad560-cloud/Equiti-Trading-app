"""Evidence check: run each researched strategy on real daily stock data with CFD costs.

    python main.py research --fetch          # download the sample data (public GitHub mirrors of Yahoo data)
    python main.py research                  # run and print the table, save research_results.json

Sample data (split/dividend adjusted):
  SPY 2008-2017 and AAPL, GOOG, NVDA, ORCL, YHOO 2004-2014.
Caveat: that basket is survivorship-biased (famous winners). It checks mechanics and relative
behaviour, not future profits. Use your own MT5 history exports for the symbols you trade.
"""
import json
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .backtest import buy_and_hold, load_csv, run_portfolio_backtest
from .strategies import STRATEGIES

MPL = "https://raw.githubusercontent.com/matplotlib/mplfinance/master/examples/data/"
BT = "https://raw.githubusercontent.com/mementum/backtrader/master/datas/"
SAMPLE = {
    "SPY": MPL + "yahoofinance-SPY-20080101-20180101.csv",
    "AAPL": MPL + "yahoofinance-AAPL-20040819-20180120.csv",
    "GOOG": MPL + "yahoofinance-GOOG-20040819-20180120.csv",
    "NVDA": BT + "nvda-1999-2014.txt",
    "ORCL": BT + "orcl-1995-2014.txt",
    "YHOO": BT + "yhoo-1996-2015.txt",
    "SP500_monthly": "https://raw.githubusercontent.com/datasets/s-and-p-500/main/data/data.csv",
}
COSTS = {"spread_pct": 0.05, "commission_pct": 0.0, "financing_pct_annual": 3.0}
NO_COSTS = {"spread_pct": 0.0, "commission_pct": 0.0, "financing_pct_annual": 0.0}
BASE = {"strategy_mode": "independent", "exit_on_opposite_signal": True, "trading_hours_utc": [0, 24],
        "risk": {"risk_per_trade_pct": 1.0, "max_open_positions": 6, "max_positions_per_symbol": 2,
                 "max_daily_loss_pct": 100, "max_spread_points": 10 ** 9, "max_notional_pct": 50,
                 "max_lots_per_trade": 10 ** 9, "sl_atr_mult": 3.0, "tp_atr_mult": 0}}


def fetch(data_dir="data"):
    d = Path(data_dir)
    d.mkdir(exist_ok=True)
    for sym, url in SAMPLE.items():
        dest = d / f"{sym}.csv"
        if not dest.exists():
            print("downloading", sym)
            req = urllib.request.Request(url, headers={"User-Agent": "equiti-trader"})
            dest.write_bytes(urllib.request.urlopen(req, timeout=60).read())
    return d


def _load_monthly_sp500(data_dir):
    """Shiller S&P 500 monthly price series (1871-): closes only, so OHLC = close."""
    import csv
    from .models import Bar
    out = []
    with open(Path(data_dir) / "SP500_monthly.csv") as f:
        for r in csv.DictReader(f):
            p = float(r["SP500"] or 0)
            if p > 0:
                t = datetime.fromisoformat(r["Date"]).replace(tzinfo=timezone.utc)
                out.append(Bar(t, p, p, p, p, 0))
    return {"SPX": out}


def _load(data_dir, syms, start=None, end=None):
    out = {}
    for s in syms:
        bars = load_csv(Path(data_dir) / f"{s}.csv")
        out[s] = [b for b in bars if (not start or b.time >= start) and (not end or b.time <= end)]
    return out


def _cfg(strategies, alloc=None):
    risk = dict(BASE["risk"])
    if alloc:
        risk.update(sizing="allocation", allocation_pct=alloc, max_notional_pct=alloc)
    return {**BASE, "risk": risk, "strategies": strategies}


def experiments(data_dir):
    utc = lambda *a: datetime(*a, tzinfo=timezone.utc)
    spy = _load(data_dir, ["SPY"])
    basket = _load(data_dir, ["AAPL", "GOOG", "NVDA", "ORCL", "YHOO"], utc(2004, 8, 19), utc(2014, 12, 31))
    spx = _load_monthly_sp500(data_dir) if (Path(data_dir) / "SP500_monthly.csv").exists() else None
    monthly = [("S&P500 monthly 1872-", spx, 100, [
        ("tsmom 12m (long only)", {"tsmom": {"lookback": 12, "vol_window": 12, "z_min": 0.0, "allow_short": False}}),
        ("tsmom 12m (long/short)", {"tsmom": {"lookback": 12, "vol_window": 12, "z_min": 0.0}}),
    ])] if spx else []
    return monthly + [          # (universe, data, capital per position when sizing by allocation, tests)
        ("SPY 2008-17", spy, 100, [
            ("rsi2", {"rsi2": {}}),
            ("tom", {"tom": {}}),
            ("tsmom (long only)", {"tsmom": {"allow_short": False}}),
            ("tsmom (long/short)", {"tsmom": {}}),
            ("trend (EMA/ADX)", {"trend": {}}),
            ("playbook: rsi2+tom+tsmom", {"rsi2": {}, "tom": {}, "tsmom": {"allow_short": False}}),
        ]),
        ("5 stocks 2004-14", basket, 25, [
            ("rsi2", {"rsi2": {}}),
            ("xsmom top-2", {"xsmom": {"top_n": 2}}),
            ("tsmom (long only)", {"tsmom": {"allow_short": False}}),
            ("tom", {"tom": {}}),
            ("playbook: rsi2+xsmom+tsmom", {"rsi2": {}, "xsmom": {"top_n": 2}, "tsmom": {"allow_short": False}}),
        ]),
    ]


def run(data_dir="data", out="research_results.json", costs=COSTS):
    rows = []
    for universe, data, alloc, tests in experiments(data_dir):
        first = min(bars[0].time for bars in data.values())
        eval_start = first.replace(year=first.year + 1) + timedelta(days=45)   # ~13 months of warm-up for 12m lookbacks
        bh = buy_and_hold(data, eval_start=eval_start)
        rows.append({"universe": universe, "test": "buy & hold (equal weight)", "sizing": "-", **bh, "trades": None,
                     "exposure_pct": 100.0})
        for name, strategies in tests:
            n = len(strategies)
            sleeve = round(alloc / n, 1)          # a playbook splits the same capital between its strategies
            for sizing, a in (("risk 1%", None), (f"alloc {sleeve:g}%", sleeve)):
                cfg = _cfg(strategies, a)
                cfg["risk"]["max_positions_per_symbol"] = n
                r = run_portfolio_backtest(cfg, data, eval_start=eval_start, **costs)
                rows.append({"universe": universe, "test": name, "sizing": sizing, "strategies": list(strategies),
                             **{k: r[k] for k in ("cagr_pct", "sharpe", "sortino", "max_drawdown_pct", "total_return_pct",
                                                  "trades", "win_rate_pct", "profit_factor", "exposure_pct", "costs_paid",
                                                  "period")}})
    result = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "costs": costs,
              "risk": BASE["risk"], "rows": rows,
              "strategies": {k: v().info() for k, v in STRATEGIES.items()}}
    if out:
        Path(out).write_text(json.dumps(result, indent=2))
    return result


def table(result):
    hdr = f"{'universe':18} {'test':28} {'sizing':10} {'CAGR%':>7} {'Sharpe':>6} {'MaxDD%':>7} {'trades':>6} {'win%':>5} {'PF':>5} {'expo%':>6}"
    lines = [hdr, "-" * len(hdr)]
    for r in result["rows"]:
        lines.append(f"{r['universe']:18} {r['test']:28} {r['sizing']:10} {r['cagr_pct']:>7.2f} {r['sharpe']:>6.2f} {r['max_drawdown_pct']:>7.1f} "
                     f"{'' if r['trades'] is None else r['trades']:>6} {'' if r['trades'] is None else r['win_rate_pct']:>5} "
                     f"{'' if not r.get('profit_factor') else r['profit_factor']:>5} {r['exposure_pct']:>6}")
    return "\n".join(lines)
