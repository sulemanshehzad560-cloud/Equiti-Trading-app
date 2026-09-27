import unittest
from datetime import datetime, timedelta, timezone

from trader.backtest import run_backtest, synthetic_bars
from trader.broker import PaperBroker
from trader.engine import Engine
from trader.models import Bar, NewsAssessment, BUY, SymbolInfo
from trader.risk import RiskManager

CFG = {
    "symbols": ["EURUSD"], "timeframe": "M15", "signal_threshold": 0.1,
    "strategies": {"trend": {"adx_min": 15}, "breakout": {}},
    "risk": {"risk_per_trade_pct": 1.0, "max_spread_points": 50},
    "news": {"veto_threshold": 0.35, "size_boost_max": 0.5},
}


def uptrend(n=150):
    t0 = datetime(2026, 1, 5, 8, tzinfo=timezone.utc)
    bars = []
    for i in range(n):
        c = 1.1 + i * 0.0005
        bars.append(Bar(t0 + timedelta(minutes=15 * i), c - 0.0002, c + 0.0003, c - 0.0004, c))
    return bars


class FixedNews:
    def __init__(self, a):
        self.a = a

    def assess(self, symbol, now):
        return self.a


class EngineTests(unittest.TestCase):
    def _run(self, news=None):
        bars = uptrend()
        broker = PaperBroker(10000, spread_points=5)
        for b in bars:
            broker.on_bar("EURUSD", b)
        eng = Engine(CFG, broker, news)
        return eng.on_bar("EURUSD", bars, now=bars[-1].time), broker

    def test_opens_buy_in_uptrend(self):
        msg, broker = self._run()
        self.assertTrue(msg.startswith("opened BUY"), msg)
        pos = broker.positions()[0]
        self.assertEqual(pos.side, BUY)
        self.assertLess(pos.sl, pos.entry)
        self.assertGreater(pos.tp, pos.entry)

    def test_news_blackout_blocks(self):
        msg, broker = self._run(FixedNews(NewsAssessment(blackout=True, reasons=["NFP in 10 min"])))
        self.assertIn("news blackout", msg)
        self.assertEqual(broker.positions(), [])

    def test_bearish_news_vetoes_buy(self):
        msg, _ = self._run(FixedNews(NewsAssessment(bias=-0.6)))
        self.assertIn("news against trade", msg)

    def test_bullish_news_sizes_up(self):
        _, neutral = self._run()
        _, bullish = self._run(FixedNews(NewsAssessment(bias=0.8)))
        self.assertGreater(bullish.positions()[0].volume, neutral.positions()[0].volume)

    def test_backtest_runs(self):
        res = run_backtest(CFG, "EURUSD", synthetic_bars(1200))
        self.assertIn("trades", res)
        self.assertGreater(res["trades"], 0)


class RiskTests(unittest.TestCase):
    def test_volume_risks_the_right_amount(self):
        rm = RiskManager({"risk_per_trade_pct": 1.0, "max_lots_per_trade": 10})
        info = SymbolInfo("EURUSD")          # $1 per point per lot
        lots = rm.volume(10000, 0.0020, info)   # 200-point stop, $100 risk => 0.5 lots
        self.assertAlmostEqual(lots, 0.5)

    def test_too_wide_stop_skips(self):
        rm = RiskManager({"risk_per_trade_pct": 0.1})
        self.assertEqual(rm.volume(100, 0.0500, SymbolInfo("EURUSD")), 0.0)

    def test_daily_halt(self):
        rm = RiskManager({"max_daily_loss_pct": 3})
        d = datetime(2026, 1, 5, 1, tzinfo=timezone.utc)
        self.assertFalse(rm.daily_halt(10000, d))
        self.assertTrue(rm.daily_halt(9690, d + timedelta(hours=5)))
        self.assertFalse(rm.daily_halt(9690, d + timedelta(days=1)))   # new day resets

    def test_paper_stop_loss(self):
        pb = PaperBroker(1000, 0)
        t = datetime(2026, 1, 5, tzinfo=timezone.utc)
        pb.on_bar("EURUSD", Bar(t, 1.1, 1.1, 1.1, 1.1))
        from trader.models import OrderRequest
        pb.open(OrderRequest("EURUSD", BUY, 0.1, 1.0990, 1.1020))
        pb.on_bar("EURUSD", Bar(t + timedelta(minutes=15), 1.1, 1.1005, 1.0985, 1.0995))
        self.assertEqual(pb.closed[0][4], "stop loss")
        self.assertAlmostEqual(pb.balance, 1000 - 10, places=2)


if __name__ == "__main__":
    unittest.main()
