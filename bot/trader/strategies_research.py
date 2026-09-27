"""Strategies with published evidence, implemented to their original rules.

Each class cites where the rules come from. Published results were measured on
specific markets and periods, often before costs, and may not repeat. Run
`python main.py research` to see what they do on real data with CFD costs.

ctx (optional) carries: symbol, now (UTC close time of the newest bar),
session (markets.Session or None) and universe ({symbol: bars} for cross-sectional ranking).
"""
import math

from . import indicators as ind
from .markets import daily_bars, next_weekday, session_days
from .models import BUY, SELL, Signal
from .strategies import Strategy


def _bar_minutes(bars):
    """Timeframe in minutes, from the smallest gap between recent bars."""
    gaps = [(bars[i].time - bars[i - 1].time).total_seconds() / 60 for i in range(max(1, len(bars) - 20), len(bars))]
    gaps = [g for g in gaps if g > 0]
    return min(gaps) if gaps else 0


class RSI2Pullback(Strategy):
    name = "rsi2"
    title = "Connors RSI(2) pullback"
    markets = "US indices, large-cap stocks (daily)"
    source = "Connors & Alvarez, 'Short Term Trading Strategies That Work' (2008)"
    summary = ("Buy a sharp 1-3 day dip (RSI(2) < 10) while price is above its 200-day average. "
               "Exit when price closes back above the 5-day average. Mean reversion inside an uptrend.")

    def __init__(self, entry=10, trend_sma=200, exit_sma=5, allow_short=False, short_entry=90, sl_atr=4.0):
        self.entry, self.trend_sma, self.exit_sma = entry, trend_sma, exit_sma
        self.allow_short, self.short_entry, self.sl_atr = allow_short, short_entry, sl_atr

    def min_bars(self):
        return self.trend_sma + 2

    def evaluate(self, bars, ctx=None):
        c = [b.close for b in bars]
        r, trend = ind.rsi(c, 2)[-1], ind.sma(c, self.trend_sma)[-1]
        if r is None or trend is None:
            return Signal()
        if c[-1] > trend and r < self.entry:
            return Signal(BUY, min(1.0, 0.6 + (self.entry - r) / (2 * self.entry)), f"RSI2 {r:.1f} above SMA{self.trend_sma}",
                          sl_atr=self.sl_atr, tp_atr=0)
        if self.allow_short and c[-1] < trend and r > self.short_entry:
            return Signal(SELL, min(1.0, 0.6 + (r - self.short_entry) / (2 * (100 - self.short_entry))),
                          f"RSI2 {r:.1f} below SMA{self.trend_sma}", sl_atr=self.sl_atr, tp_atr=0)
        return Signal()

    def should_exit(self, bars, side, ctx=None):
        c = [b.close for b in bars]
        m = ind.sma(c, self.exit_sma)[-1]
        if m is None:
            return None
        if (side == BUY and c[-1] > m) or (side == SELL and c[-1] < m):
            return f"closed {'above' if side == BUY else 'below'} SMA{self.exit_sma} (reverted)"
        return None


class TimeSeriesMomentum(Strategy):
    name = "tsmom"
    title = "Time-series momentum (vol-scaled)"
    markets = "Indices, stocks, metals, FX (daily)"
    source = "Moskowitz, Ooi & Pedersen, 'Time Series Momentum', JFE (2012)"
    summary = ("Hold the direction of the past 12-month return, sized by volatility "
               "(the ATR-based sizing does the vol-scaling). Exit when the 12-month return flips sign.")

    def __init__(self, lookback=252, vol_window=63, z_min=0.25, allow_short=True, sl_atr=4.0):
        self.lookback, self.vol_window, self.z_min = lookback, vol_window, z_min
        self.allow_short, self.sl_atr = allow_short, sl_atr

    def min_bars(self):
        return self.lookback + 2

    def _z(self, bars):
        c = [b.close for b in bars]
        ret = c[-1] / c[-1 - self.lookback] - 1
        lr = [math.log(c[i] / c[i - 1]) for i in range(len(c) - self.vol_window, len(c)) if c[i - 1] > 0]
        mu = sum(lr) / len(lr)
        vol = math.sqrt(sum((x - mu) ** 2 for x in lr) / len(lr)) * math.sqrt(self.lookback)
        return ret, (ret / vol if vol else 0.0)

    def evaluate(self, bars, ctx=None):
        ret, z = self._z(bars)
        if abs(z) < self.z_min:
            return Signal()
        side = BUY if z > 0 else SELL
        if side == SELL and not self.allow_short:
            return Signal()
        return Signal(side, min(1.0, 0.4 + abs(z) / 3), f"12m return {ret:+.1%} (z {z:+.2f})", sl_atr=self.sl_atr, tp_atr=0)

    def should_exit(self, bars, side, ctx=None):
        if len(bars) < self.min_bars():
            return None
        ret, _ = self._z(bars)
        return f"12m return flipped to {ret:+.1%}" if ret * side < 0 else None


class CrossSectionalMomentum(Strategy):
    name = "xsmom"
    title = "Cross-sectional 12-1 momentum"
    markets = "A basket of stocks (daily)"
    source = "Jegadeesh & Titman, JF (1993); absolute-momentum filter from Antonacci, 'Dual Momentum' (2014)"
    summary = ("Rank the basket by return from 12 months ago to 1 month ago. Own the top N when their "
               "momentum is also positive. Sell when a name drops out of the top 2N.")

    def __init__(self, lookback=252, skip=21, top_n=2, hold_rank=None, min_universe=4, sl_atr=4.0):
        self.lookback, self.skip, self.top_n = lookback, skip, top_n
        self.hold_rank = hold_rank or top_n * 2
        self.min_universe, self.sl_atr = min_universe, sl_atr

    def min_bars(self):
        return self.lookback + 2

    def _mom(self, bars):
        if len(bars) < self.lookback + 1:
            return None
        return bars[-1 - self.skip].close / bars[-1 - self.lookback].close - 1

    def _rank(self, bars, ctx):
        if not ctx or not ctx.get("universe"):
            return None, None, 0
        me = ctx["symbol"]
        scores = {s: self._mom(b) for s, b in ctx["universe"].items()}
        scores[me] = self._mom(bars)
        scores = {s: v for s, v in scores.items() if v is not None}
        if me not in scores or len(scores) < self.min_universe:
            return None, None, len(scores)
        order = sorted(scores, key=scores.get, reverse=True)
        return order.index(me), scores[me], len(order)

    def evaluate(self, bars, ctx=None):
        rank, mom, n = self._rank(bars, ctx)
        if rank is None or rank >= self.top_n or mom <= 0:
            return Signal()
        return Signal(BUY, min(1.0, 0.6 + mom), f"rank {rank + 1}/{n}, 12-1 mom {mom:+.1%}", sl_atr=self.sl_atr, tp_atr=0)

    def should_exit(self, bars, side, ctx=None):
        rank, mom, n = self._rank(bars, ctx)
        if rank is None:
            return None
        if rank >= self.hold_rank:
            return f"dropped to rank {rank + 1}/{n}"
        if mom <= 0:
            return f"12-1 momentum turned negative ({mom:+.1%})"
        return None


class TurnOfMonth(Strategy):
    name = "tom"
    title = "Turn-of-the-month"
    style = "calendar"
    markets = "US indices, broad stocks (daily)"
    source = "Lakonishok & Smidt, RFS (1988); McConnell & Xu, FAJ (2008)"
    summary = ("Equity returns cluster from the last trading day of the month to the 3rd trading day of "
               "the next. Buy the close before that window and exit after day 3.")

    def __init__(self, hold_days=3, trend_sma=0, sl_atr=3.0):
        self.hold_days, self.trend_sma, self.sl_atr = hold_days, trend_sma, sl_atr

    def min_bars(self):
        return max(25, self.trend_sma + 2)

    def evaluate(self, bars, ctx=None):
        d = bars[-1].time.date()
        nxt = next_weekday(d)
        # entry at the close of the second-to-last trading day, so the last day is held
        if not (nxt.month == d.month and next_weekday(nxt).month != d.month):
            return Signal()
        if self.trend_sma:
            m = ind.sma([b.close for b in bars], self.trend_sma)[-1]
            if m is None or bars[-1].close < m:
                return Signal()
        return Signal(BUY, 0.7, f"turn-of-month window opens ({d:%b} close)", sl_atr=self.sl_atr, tp_atr=0)

    def should_exit(self, bars, side, ctx=None):
        month = bars[-1].time.month
        day_n = 0                       # trading days so far in the current month
        for b in reversed(bars):
            if b.time.month != month:
                break
            day_n += 1
        # day_n is large on the entry month's last day (keep holding) and small after the turn
        if self.hold_days <= day_n <= 10:
            return f"trading day {day_n} of the new month, window closed"
        return None


class OpeningRangeBreakout(Strategy):
    name = "orb"
    title = "5-min opening range breakout"
    style = "intraday"
    markets = "US stocks in play, US index CFDs (M1/M5)"
    source = "Zarattini, Barbon & Aziz, 'A Profitable Day Trading Strategy for the U.S. Equity Market' (SSRN 4729284, 2024)"
    summary = ("Trade in the direction of the first 5-minute candle once price breaks that candle's high "
               "or low. Stop at 10% of the 14-day ATR, flat by the close. Only on 'stocks in play' "
               "(opening volume well above normal).")

    def __init__(self, or_minutes=5, stop_atr_frac=0.10, atr_days=14, rel_vol_min=1.0, rel_vol_days=14,
                 max_entry_minutes=120, eod_exit_min=5):
        self.or_minutes, self.stop_atr_frac, self.atr_days = or_minutes, stop_atr_frac, atr_days
        self.rel_vol_min, self.rel_vol_days = rel_vol_min, rel_vol_days
        self.max_entry_minutes, self.eod_exit_min = max_entry_minutes, eod_exit_min

    def min_bars(self):
        return 30

    def evaluate(self, bars, ctx=None):
        sess = (ctx or {}).get("session")
        if not sess or not sess.is_open(bars[-1].time):
            return Signal()
        since = sess.minutes_since_open
        days = session_days(bars, sess)
        today = sess.trading_day(bars[-1].time)
        tb = days.get(today, [])
        orb = [b for b in tb if since(b.time) < self.or_minutes]
        post = [b for b in tb if since(b.time) >= self.or_minutes]
        if not orb or not post or since(bars[-1].time) > self.max_entry_minutes or post[-1] is not bars[-1]:
            return Signal()
        o, c = orb[0].open, orb[-1].close
        hi, lo = max(b.high for b in orb), min(b.low for b in orb)
        direction = BUY if c > o else SELL if c < o else 0
        if not direction:
            return Signal()
        level = hi if direction == BUY else lo
        beyond = [(b.close - level) * direction > 0 for b in post]
        if not beyond[-1] or any(beyond[:-1]):
            return Signal()           # only the first close through the level counts
        prior = [d for d in sorted(days) if d < today]
        if self.rel_vol_min:
            vols = [sum(b.volume for b in days[d] if since(b.time) < self.or_minutes) for d in prior[-self.rel_vol_days:]]
            avg = sum(vols) / len(vols) if vols else 0
            today_vol = sum(b.volume for b in orb)
            if avg > 0 and today_vol / avg < self.rel_vol_min:
                return Signal()
        daily = daily_bars([b for d in prior[-(self.atr_days + 1):] for b in days[d]], sess)
        if len(daily) < 3:
            return Signal()
        a = ind.atr([b.high for b in daily], [b.low for b in daily], [b.close for b in daily],
                    min(self.atr_days, len(daily) - 1))[-1]
        if not a:
            return Signal()
        return Signal(direction, 0.8, f"first {self.or_minutes}m candle {'up' if direction > 0 else 'down'}, "
                                      f"broke {'high' if direction > 0 else 'low'} {level:g}",
                      sl_dist=a * self.stop_atr_frac, tp_atr=0)

    def should_exit(self, bars, side, ctx=None):
        sess = (ctx or {}).get("session")
        now = (ctx or {}).get("now") or bars[-1].time
        if sess and (not sess.is_open(now) or sess.minutes_to_close(now) <= self.eod_exit_min):
            return "end of session (flat overnight)"
        return None


class IntradayMomentum(Strategy):
    name = "imom"
    title = "Market intraday momentum"
    style = "intraday"
    markets = "US index CFDs such as US500 and US100 (M5-M30)"
    source = "Gao, Han, Li & Zhou, 'Market Intraday Momentum', JFE (2018)"
    summary = ("The return from yesterday's close to 10:00 NY predicts the last half-hour. "
               "Enter in that direction 30 minutes before the close and exit at the close.")

    def __init__(self, first_minutes=30, last_minutes=30, min_move=0.0, eod_exit_min=2, sl_atr=1.5):
        self.first_minutes, self.last_minutes, self.min_move = first_minutes, last_minutes, min_move
        self.eod_exit_min, self.sl_atr = eod_exit_min, sl_atr

    def min_bars(self):
        return 30

    def evaluate(self, bars, ctx=None):
        sess = (ctx or {}).get("session")
        if not sess:
            return Signal()
        tf = _bar_minutes(bars)
        now = (ctx or {}).get("now") or bars[-1].time
        mtc = sess.minutes_to_close(now)
        if not (self.eod_exit_min < mtc <= self.last_minutes < mtc + tf) or not sess.is_open(bars[-1].time):
            return Signal()
        days = session_days(bars, sess)
        today = sess.trading_day(bars[-1].time)
        prior = [d for d in sorted(days) if d < today]
        first = [b for b in days.get(today, []) if sess.minutes_since_open(b.time) < self.first_minutes]
        if not prior or not first:
            return Signal()
        r = first[-1].close / days[prior[-1]][-1].close - 1
        if abs(r) <= self.min_move:
            return Signal()
        side = BUY if r > 0 else SELL
        return Signal(side, min(1.0, 0.5 + abs(r) * 50), f"first half-hour {r:+.2%}, riding the last half-hour",
                      sl_atr=self.sl_atr, tp_atr=0)

    def should_exit(self, bars, side, ctx=None):
        sess = (ctx or {}).get("session")
        now = (ctx or {}).get("now") or bars[-1].time
        if sess and (not sess.is_open(now) or sess.minutes_to_close(now) <= self.eod_exit_min):
            return "closing bell"
        return None
