"""Equiti Trader command line.

  python main.py backtest --synthetic              try the strategies offline
  python main.py backtest --csv EURUSD_M15.csv --symbol EURUSD
  python main.py news --symbol EURUSD              show calendar blackouts + headline bias
  python main.py run --mode paper                  live prices from MT5, simulated orders
  python main.py run --mode demo                   real orders on an Equiti DEMO account
  python main.py run --mode live --confirm-live    real orders on a LIVE account
  python main.py demo                              try the app on simulated prices (no MT5 needed)

While `run` is going, the app (../public) and its API are served on http://localhost:8787.
"""
import argparse
import json
import logging
import os
import secrets
import sys
from pathlib import Path

from trader.backtest import load_csv, run_backtest, synthetic_bars
from trader.config import load_config, load_env
from trader.news import NewsEngine


def mt5_from_env(cfg):
    from trader.broker import MT5Broker
    missing = [k for k in ("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER") if not os.environ.get(k)]
    if missing:
        sys.exit(f"Missing {', '.join(missing)} - copy .env.example to .env and fill in your Equiti MT5 details")
    return MT5Broker(os.environ["MT5_LOGIN"], os.environ["MT5_PASSWORD"], os.environ["MT5_SERVER"],
                     os.environ.get("MT5_TERMINAL_PATH") or None, cfg.get("magic", 260927))


def dashboard_token():
    """Token the app uses to talk to the bot. Generated once and saved in .env."""
    tok = os.environ.get("DASHBOARD_TOKEN", "").strip()
    if len(tok) >= 16:
        return tok
    tok = secrets.token_urlsafe(24)
    with open(".env", "a") as f:
        f.write(f"\nDASHBOARD_TOKEN={tok}\n")
    os.environ["DASHBOARD_TOKEN"] = tok
    print(f"\nNew app access token (saved in .env):\n\n    {tok}\n\nPaste it into the app's Settings.\n")
    return tok


def cmd_run(args, cfg):
    from trader.broker import PaperBroker
    from trader.engine import Engine
    if args.mode == "live" and not args.confirm_live:
        sys.exit("Live trading uses real money. Re-run with --confirm-live once you've tested on demo.")
    mt5 = mt5_from_env(cfg)
    if args.mode == "paper":
        broker = PaperBroker(cfg.get("paper_balance", 10000), data_source=mt5)
    else:
        broker = mt5
        mt5.connect()
        if args.mode == "demo" and not mt5.is_demo():
            mt5.shutdown()
            sys.exit("--mode demo but the MT5 account is REAL. Use a demo login or --mode live --confirm-live.")
    news = NewsEngine(cfg.get("news", {})) if cfg.get("news", {}).get("enabled", True) else None
    engine = Engine({**cfg, "mode": args.mode}, broker, news, journal_path=cfg.get("journal", "logs/journal.jsonl"))
    start_app(engine, cfg, args)
    engine.run(once=args.once, connect=args.mode == "paper")


def start_app(engine, cfg, args):
    app_cfg = cfg.get("app", {})
    if not app_cfg.get("enabled", True) or getattr(args, "no_app", False):
        return
    from trader.api import TraderAPI
    host = getattr(args, "host", None) or app_cfg.get("host", "127.0.0.1")
    TraderAPI(engine, dashboard_token(), host, getattr(args, "port", None) or app_cfg.get("port", 8787),
              static_dir=Path(__file__).resolve().parent.parent / "public",
              allow_origin=os.environ.get("DASHBOARD_ORIGIN") or app_cfg.get("allow_origin", "*")).start()


def cmd_demo(args, cfg):
    from trader.broker import PaperBroker
    from trader.demo import SyntheticFeed, seed_news
    from trader.engine import Engine
    symbols = cfg["symbols"]
    broker = PaperBroker(cfg.get("paper_balance", 10000), data_source=SyntheticFeed(symbols))
    news = seed_news(NewsEngine(cfg.get("news", {})))
    engine = Engine({**cfg, "mode": "demo-sim", "trading_hours_utc": [0, 24]}, broker, news,
                    journal_path="logs/demo-journal.jsonl")
    start_app(engine, cfg, args)
    engine.run(poll_seconds=args.speed, once=False)


def cmd_backtest(args, cfg):
    if args.csv:
        bars = load_csv(args.csv)
    else:
        bars = synthetic_bars(args.bars)
    print(f"{len(bars)} bars, {bars[0].time:%Y-%m-%d} -> {bars[-1].time:%Y-%m-%d}")
    res = run_backtest(cfg, args.symbol, bars, args.balance, args.spread)
    print(json.dumps(res, indent=2))


def cmd_news(args, cfg):
    ne = NewsEngine({**cfg.get("news", {}), "enabled": True})
    ne.refresh(force=True)
    print(f"{len(ne.events)} calendar events, {len(ne.headlines)} headlines")
    for cur, s in sorted(ne.currency_scores().items(), key=lambda x: -abs(x[1])):
        print(f"  {cur:6s} {s:+.2f}")
    for sym in args.symbol or cfg["symbols"]:
        a = ne.assess(sym)
        print(f"{sym}: blackout={a.blackout} bias={a.bias:+.2f}")
        for r in a.reasons:
            print("   -", r)


def main():
    p = argparse.ArgumentParser(description="Automated trading for Equiti MT5 accounts")
    p.add_argument("--config", default="config.json")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--mode", choices=["paper", "demo", "live"], default="paper")
    r.add_argument("--confirm-live", action="store_true")
    r.add_argument("--once", action="store_true", help="one pass over the symbols, then exit")
    r.add_argument("--no-app", action="store_true", help="don't start the app/API server")
    d = sub.add_parser("demo")
    d.add_argument("--speed", type=float, default=3, help="seconds per simulated bar")
    d.add_argument("--host")
    d.add_argument("--port", type=int)
    b = sub.add_parser("backtest")
    b.add_argument("--csv")
    b.add_argument("--symbol", default="EURUSD")
    b.add_argument("--synthetic", action="store_true")
    b.add_argument("--bars", type=int, default=3000)
    b.add_argument("--balance", type=float, default=10000)
    b.add_argument("--spread", type=float, default=10, help="spread in points")
    n = sub.add_parser("news")
    n.add_argument("--symbol", action="append")
    args = p.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO if args.cmd in ("run", "demo") else logging.WARNING,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    load_env()
    cfg_path = args.config if os.path.exists(args.config) else "config.example.json"
    cfg = load_config(cfg_path)
    {"run": cmd_run, "demo": cmd_demo, "backtest": cmd_backtest, "news": cmd_news}[args.cmd](args, cfg)


if __name__ == "__main__":
    main()
