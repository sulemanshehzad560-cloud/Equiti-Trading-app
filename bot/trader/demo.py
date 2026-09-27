"""Simulated market for trying the app without MetaTrader 5: `python main.py demo`."""
from datetime import datetime, timedelta, timezone

from .backtest import synthetic_bars
from .broker import default_symbol_info
from .models import Bar

STARTS = {"EURUSD": 1.08, "GBPUSD": 1.27, "USDJPY": 149.0, "XAUUSD": 2350.0}


class SyntheticFeed:
    """Acts like a broker's data side. Each bars() call reveals one more bar, so the demo
    moves one 15-minute bar every few seconds."""

    def __init__(self, symbols, history=300, seed=11):
        self.series, self.pos = {}, {}
        for i, s in enumerate(symbols):
            base = STARTS.get(s.upper()[:6], 1.0)
            raw = synthetic_bars(4000, 1.0, seed + i)
            self.series[s] = [Bar(b.time, b.open * base, b.high * base, b.low * base, b.close * base) for b in raw]
            self.pos[s] = history
        self.history = history

    def connect(self): ...
    def shutdown(self): ...

    def symbol_info(self, symbol):
        return default_symbol_info(symbol)

    def bars(self, symbol, timeframe, count):
        i = self.pos[symbol] = min(self.pos[symbol] + 1, len(self.series[symbol]))
        return self.series[symbol][max(0, i - count):i]

    def quote(self, symbol):
        c = self.series[symbol][self.pos[symbol] - 1].close
        info = self.symbol_info(symbol)
        return c, c + 8 * info.point


def seed_news(news):
    """Sample headlines and calendar so the app's news tab has something to show."""
    now = datetime.now(timezone.utc)
    news.cfg = {**news.cfg, "calendar_url": None, "feeds": []}
    news.headlines = [
        {"title": "Fed officials signal another rate hike as inflation stays sticky", "time": now - timedelta(minutes=40)},
        {"title": "ECB turns dovish, euro slides against the dollar", "time": now - timedelta(hours=2)},
        {"title": "Gold rallies as dollar slumps on weak jobs data", "time": now - timedelta(hours=5)},
        {"title": "Yen weakens as BoJ keeps policy unchanged", "time": now - timedelta(hours=7)},
        {"title": "Sterling steady ahead of Bank of England decision", "time": now - timedelta(hours=9)},
    ]
    news.events = [
        {"title": "CPI m/m", "currency": "USD", "impact": "High", "time": now + timedelta(hours=3), "forecast": "0.3%", "previous": "0.2%"},
        {"title": "ECB President Lagarde Speaks", "currency": "EUR", "impact": "Medium", "time": now + timedelta(hours=6), "forecast": "", "previous": ""},
        {"title": "Official Bank Rate", "currency": "GBP", "impact": "High", "time": now + timedelta(hours=20), "forecast": "4.75%", "previous": "5.00%"},
    ]
    return news
