"""What kind of instrument a symbol is, and when its exchange is open.

Equiti MT5 symbols come in several shapes: 'EURUSD', 'XAUUSD', 'US500', share CFDs
such as 'AAPL' / 'AAPL.US' / '#AAPL'. Suffixes differ by account type, so we
look at the leading ticker and classify from that.
"""
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

CURRENCIES = {"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD", "SEK", "NOK", "DKK", "SGD", "HKD",
              "ZAR", "MXN", "TRY", "PLN", "CNH", "HUF", "CZK"}
METALS = {"XAU", "XAG", "XPT", "XPD"}
INDEXES = {"US30": "USIDX", "US100": "USIDX", "NAS100": "USIDX", "USTEC": "USIDX", "US500": "USIDX", "SPX500": "USIDX",
           "DJ30": "USIDX", "US2000": "USIDX", "UK100": "GBIDX", "GER40": "EUIDX", "DE40": "EUIDX", "EU50": "EUIDX",
           "FRA40": "EUIDX", "JP225": "JPIDX", "AUS200": "AUIDX", "HK50": "HKIDX"}
ENERGY = {"USOIL": "OIL", "UKOIL": "OIL", "WTI": "OIL", "BRENT": "OIL", "XTIUSD": "OIL", "XBRUSD": "OIL", "NGAS": "GAS"}
US_INDEX_PREFIX = ("US30", "US100", "NAS100", "USTEC", "US500", "SPX500", "DJ30", "US2000")


def ticker(symbol):
    """'AAPL.US' -> 'AAPL', '#TSLA' -> 'TSLA', 'EURUSD.m' -> 'EURUSD', 'US500.cash' -> 'US500'."""
    m = re.match(r"[^A-Za-z0-9]*([A-Za-z0-9]+)", symbol)
    return (m.group(1) if m else symbol).upper()


def classify(symbol):
    """Return (kind, base, quote). kind is fx | metal | index | energy | stock."""
    t = ticker(symbol)
    for root, cur in INDEXES.items():
        if t.startswith(root):
            return "index", cur, None
    for root, cur in ENERGY.items():
        if t.startswith(root):
            return "energy", cur, None
    letters = re.sub(r"[^A-Z]", "", t)
    if len(letters) >= 6 and letters[:3] in METALS and letters[3:6] in CURRENCIES:
        return "metal", letters[:3], letters[3:6]
    if len(letters) >= 6 and letters[:3] in CURRENCIES and letters[3:6] in CURRENCIES:
        return "fx", letters[:3], letters[3:6]
    return "stock", t, None


@dataclass(frozen=True)
class Session:
    name: str
    tz: str
    open: time
    close: time

    def _z(self):
        return ZoneInfo(self.tz)

    def local(self, dt):
        return dt.astimezone(self._z())

    def trading_day(self, dt):
        return self.local(dt).date()

    def open_at(self, d: date):
        return datetime.combine(d, self.open, self._z()).astimezone(timezone.utc)

    def close_at(self, d: date):
        return datetime.combine(d, self.close, self._z()).astimezone(timezone.utc)

    def is_open(self, dt):
        loc = self.local(dt)
        return loc.weekday() < 5 and self.open <= loc.time() < self.close

    def minutes_since_open(self, dt):
        return (dt - self.open_at(self.trading_day(dt))).total_seconds() / 60

    def minutes_to_close(self, dt):
        return (self.close_at(self.trading_day(dt)) - dt).total_seconds() / 60


SESSIONS = {
    "us_equity": Session("us_equity", "America/New_York", time(9, 30), time(16, 0)),
    "uk_equity": Session("uk_equity", "Europe/London", time(8, 0), time(16, 30)),
    "eu_equity": Session("eu_equity", "Europe/Berlin", time(9, 0), time(17, 30)),
}


def session_for(symbol, overrides=None):
    """Cash session used for stock / index logic (ORB, intraday momentum, entry gating).
    FX, metals and energy return None (they trade round the clock)."""
    overrides = overrides or {}
    for key in (symbol, ticker(symbol)):
        if key in overrides:
            return SESSIONS.get(overrides[key])
    kind, base, _ = classify(symbol)
    if kind == "stock" or (kind == "index" and ticker(symbol).startswith(US_INDEX_PREFIX)):
        return SESSIONS["us_equity"]
    if kind == "index" and base == "GBIDX":
        return SESSIONS["uk_equity"]
    if kind == "index" and base == "EUIDX":
        return SESSIONS["eu_equity"]
    return None


def session_days(bars, session):
    """Group intraday bars by trading day -> {date: [bars in the cash session]}."""
    days = {}
    for b in bars:
        if session.is_open(b.time):
            days.setdefault(session.trading_day(b.time), []).append(b)
    return days


def daily_bars(bars, session=None):
    """Aggregate intraday bars into daily OHLC (session hours only when a session is given)."""
    from .models import Bar
    groups = {}
    for b in bars:
        if session and not session.is_open(b.time):
            continue
        d = session.trading_day(b.time) if session else b.time.date()
        groups.setdefault(d, []).append(b)
    out = []
    for d in sorted(groups):
        g = groups[d]
        out.append(Bar(g[0].time, g[0].open, max(x.high for x in g), min(x.low for x in g), g[-1].close,
                       sum(x.volume for x in g)))
    return out


def next_weekday(d):
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d
