import unittest
from datetime import datetime, timedelta, timezone

from trader.markets import SESSIONS, classify, session_for
from trader.models import Bar, BUY, SELL
from trader.strategies_research import (CrossSectionalMomentum, IntradayMomentum, OpeningRangeBreakout,
                                        RSI2Pullback, TimeSeriesMomentum, TurnOfMonth)
from trader.news import score_headline, register_aliases, symbol_currencies


def daily(closes, start=datetime(2024, 1, 1, tzinfo=timezone.utc)):
    out, d = [], start
    for c in closes:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append(Bar(d, c, c * 1.005, c * 0.995, c, 1000))
        d += timedelta(days=1)
    return out


def intraday(day_paths, minutes=5, vol=None):
    """day_paths: list of per-day close lists for the NY cash session starting 09:30."""
    out, SES = [], SESSIONS["us_equity"]
    d = datetime(2026, 3, 2).date()        # a Monday
    prev = day_paths[0][0]
    for k, path in enumerate(day_paths):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        t0 = SES.open_at(d)
        for i, c in enumerate(path):
            v = (vol[k] if vol else 100)
            out.append(Bar(t0 + timedelta(minutes=minutes * i), prev, max(prev, c) + 0.01, min(prev, c) - 0.01, c, v))
            prev = c
        d += timedelta(days=1)
    return out


class MarketsTests(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(classify("AAPL.US"), ("stock", "AAPL", None))
        self.assertEqual(classify("EURUSD.m")[0], "fx")
        self.assertEqual(classify("US500")[0], "index")
        self.assertEqual(session_for("NVDA").name, "us_equity")
        self.assertIsNone(session_for("EURUSD"))

    def test_stock_news(self):
        register_aliases({"AAPL": ["apple", "iphone"]})
        self.assertEqual(symbol_currencies("AAPL.US"), ("AAPL", None))
        self.assertGreater(score_headline("Apple beats estimates as iPhone sales surge")["AAPL"], 0)
        self.assertLess(score_headline("$AAPL cuts guidance, shares slide")["AAPL"], 0)


class RSI2Tests(unittest.TestCase):
    def test_buys_dip_in_uptrend_and_exits_on_rebound(self):
        closes = [100 + i * 0.2 for i in range(230)] + [143, 140, 137]   # sharp 3-day dip in an uptrend
        s = RSI2Pullback()
        sig = s.evaluate(daily(closes))
        self.assertEqual(sig.side, BUY)
        self.assertEqual(sig.tp_atr, 0)
        self.assertIsNone(s.should_exit(daily(closes), BUY))
        self.assertIsNotNone(s.should_exit(daily(closes + [146]), BUY))

    def test_no_trade_below_200sma(self):
        closes = [200 - i * 0.2 for i in range(230)] + [150, 148, 146]
        self.assertEqual(RSI2Pullback().evaluate(daily(closes)).side, 0)


class MomentumTests(unittest.TestCase):
    def test_tsmom_direction_and_flip(self):
        up = [100 * 1.001 ** i for i in range(300)]
        s = TimeSeriesMomentum()
        self.assertEqual(s.evaluate(daily(up)).side, BUY)
        down = [100 * 0.999 ** i for i in range(300)]
        self.assertEqual(s.evaluate(daily(down)).side, SELL)
        self.assertEqual(TimeSeriesMomentum(allow_short=False).evaluate(daily(down)).side, 0)
        self.assertIsNotNone(s.should_exit(daily(down), BUY))

    def test_xsmom_ranks_universe(self):
        uni = {f"S{k}": daily([100 * (1 + g) ** i for i in range(300)]) for k, g in enumerate([0.002, 0.001, 0.0005, -0.001])}
        s = CrossSectionalMomentum(top_n=1, hold_rank=2)
        ctx = lambda me: {"symbol": me, "universe": {k: v for k, v in uni.items() if k != me}}
        self.assertEqual(s.evaluate(uni["S0"], ctx("S0")).side, BUY)
        self.assertEqual(s.evaluate(uni["S1"], ctx("S1")).side, 0)
        self.assertIsNone(s.should_exit(uni["S1"], BUY, ctx("S1")))           # rank 2 is inside hold band
        self.assertIsNotNone(s.should_exit(uni["S3"], BUY, ctx("S3")))


class TurnOfMonthTests(unittest.TestCase):
    def test_window(self):
        bars = daily([100] * 60, start=datetime(2024, 1, 1, tzinfo=timezone.utc))
        s = TurnOfMonth()
        hits = [b.time.date() for i, b in enumerate(bars) if i > 5 and s.evaluate(bars[:i + 1]).side == BUY]
        self.assertIn(datetime(2024, 1, 30).date(), hits)       # Jan 31 2024 is the last trading day
        self.assertIn(datetime(2024, 2, 28).date(), hits)       # Feb 29 is the last trading day
        exits = [b.time.date() for i, b in enumerate(bars) if i > 5 and s.should_exit(bars[:i + 1], BUY)]
        self.assertIn(datetime(2024, 2, 5).date(), exits)        # 3rd trading day of Feb
        self.assertNotIn(datetime(2024, 1, 31).date(), exits)    # hold through the last day


class IntradayTests(unittest.TestCase):
    def test_orb_first_breakout_in_candle_direction(self):
        flat = [100 + (i % 3) * 0.05 for i in range(78)]
        up_open = [100.4] + [100.3] * 4 + [100.6] + [100.7] * 72          # bullish first candle, breaks at bar 5
        bars = intraday([flat] * 15 + [up_open], vol=[100] * 15 + [300])
        s = OpeningRangeBreakout()
        ctx = {"session": SESSIONS["us_equity"]}
        signals = [(i, s.evaluate(bars[:i + 1], ctx)) for i in range(len(bars) - 78, len(bars))]
        fired = [(i, g) for i, g in signals if g.side]
        self.assertEqual(len(fired), 1)                         # only the first close through the high
        self.assertEqual(fired[0][1].side, BUY)
        self.assertGreater(fired[0][1].sl_dist, 0)

    def test_orb_needs_relative_volume(self):
        flat = [100 + (i % 3) * 0.05 for i in range(78)]
        up_open = [100.4] + [100.3] * 4 + [100.6] + [100.7] * 72
        bars = intraday([flat] * 15 + [up_open], vol=[100] * 15 + [50])   # quiet open: not in play
        ctx = {"session": SESSIONS["us_equity"]}
        self.assertFalse(any(OpeningRangeBreakout().evaluate(bars[:i + 1], ctx).side for i in range(len(bars) - 78, len(bars))))

    def test_orb_flat_at_close(self):
        ses = SESSIONS["us_equity"]
        bars = intraday([[100] * 78])
        self.assertIsNotNone(OpeningRangeBreakout().should_exit(bars, BUY, {"session": ses, "now": ses.close_at(bars[-1].time.date()) - timedelta(minutes=3)}))

    def test_intraday_momentum_last_half_hour(self):
        ses = SESSIONS["us_equity"]
        day1 = [100] * 78
        day2 = [100.2, 100.5, 100.8, 101, 101.1, 101.2] + [101] * 72     # strong first half-hour
        bars = intraday([day1, day2])
        s = IntradayMomentum()
        hits = []
        for i in range(78, len(bars)):
            now = bars[i].time + timedelta(minutes=5)
            if s.evaluate(bars[:i + 1], {"session": ses, "now": now}).side == BUY:
                hits.append(ses.minutes_to_close(now))
        self.assertEqual(hits, [30.0])


if __name__ == "__main__":
    unittest.main()
