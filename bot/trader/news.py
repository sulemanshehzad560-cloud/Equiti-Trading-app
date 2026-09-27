"""News reading on the backend - no API keys needed.

Two free sources are combined:

1. Economic calendar (public JSON feed of the week's scheduled releases).
   High-impact events (NFP, CPI, rate decisions...) create a *blackout* window
   around them for the currencies involved: no new trades, and optionally
   existing positions are closed first.

2. Headline feeds (public RSS/Atom).  Each headline is split into clauses,
   the subject currency of each clause is found, and a small finance lexicon
   scores it bullish/bearish ("hawkish", "rate hike", "slumps", "misses"...).
   Scores decay with age and are turned into a -1..+1 *bias* per symbol
   (base currency score minus quote currency score).  The engine uses the bias
   to skip trades that fight the news and to size up trades that agree with it.

If a feed cannot be downloaded the last good data is kept; the bot never
crashes because a news site is down.
"""
import json
import logging
import math
import re
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape

from .markets import classify
from .models import NewsAssessment

log = logging.getLogger("news")
UA = "Mozilla/5.0 (compatible; EquitiTrader/0.1)"

# Subject detection. Patterns are matched on lower-cased text unless listed in CASED.
CURRENCY_TERMS = {
    "USD": ["dollar", "greenback", "fed", "fomc", "powell", "treasury", "treasuries", "nonfarm", "payrolls", "wall street jobs"],
    "EUR": ["euro", "eurozone", "euro area", "ecb", "lagarde", "german", "germany", "bund"],
    "GBP": ["pound", "sterling", "cable", "boe", "bank of england", "britain", "british", "gilt", "gilts"],
    "JPY": ["yen", "boj", "bank of japan", "japan", "japanese", "ueda"],
    "CHF": ["franc", "snb", "swiss"],
    "AUD": ["aussie", "australian", "australia", "rba"],
    "NZD": ["kiwi", "rbnz", "new zealand"],
    "CAD": ["loonie", "canadian", "canada", "bank of canada"],
    "XAU": ["gold", "bullion"],
    "XAG": ["silver"],
    "OIL": ["oil", "crude", "opec", "brent", "wti"],
    "USIDX": ["stocks", "wall street", "s&p", "s&p 500", "nasdaq", "dow", "equities"],
}
CASED = {"USD": [r"\bUS\b", r"\bU\.S\.", r"\bUSD\b"], "GBP": [r"\bUK\b", r"\bGBP\b"], "EUR": [r"\bEUR\b"],
         "JPY": [r"\bJPY\b"], "AUD": [r"\bAUD\b"], "CAD": [r"\bCAD\b", r"\bBoC\b"], "NZD": [r"\bNZD\b"],
         "CHF": [r"\bCHF\b"]}

BULLISH = {
    "hawkish": 1.0, "rate hike": 1.0, "rate hikes": 1.0, "hikes rates": 1.0, "raises rates": 1.0, "hike": 0.6,
    "beats": 0.6, "beat expectations": 0.8, "stronger than expected": 0.8, "better than expected": 0.8,
    "hotter than expected": 0.6, "surge": 0.6, "surges": 0.6, "soars": 0.7, "rally": 0.5, "rallies": 0.5,
    "jumps": 0.5, "climbs": 0.4, "gains": 0.4, "rises": 0.3, "rebounds": 0.4, "firmer": 0.4, "strong": 0.3,
    "strengthens": 0.5, "upbeat": 0.5, "robust": 0.5, "record high": 0.6, "multi-year high": 0.6, "bullish": 0.6,
    # company / stock language
    "beats estimates": 0.8, "tops estimates": 0.8, "raises guidance": 1.0, "raises outlook": 0.9, "upgrade": 0.6,
    "upgraded": 0.6, "buyback": 0.5, "record revenue": 0.7, "outperform": 0.5, "all-time high": 0.6,
}
BEARISH = {
    "dovish": 1.0, "rate cut": 1.0, "rate cuts": 1.0, "cuts rates": 1.0, "lowers rates": 1.0, "cut": 0.5,
    "misses": 0.6, "weaker than expected": 0.8, "worse than expected": 0.8, "softer than expected": 0.6,
    "slump": 0.6, "slumps": 0.6, "plunge": 0.7, "plunges": 0.7, "tumbles": 0.6, "sinks": 0.5, "falls": 0.4,
    "drops": 0.4, "slides": 0.4, "declines": 0.4, "dips": 0.3, "eases": 0.3, "weak": 0.3, "weakens": 0.5,
    "recession": 0.8, "contraction": 0.6, "downgrade": 0.6, "sell-off": 0.6, "selloff": 0.6, "bearish": 0.6,
    "multi-year low": 0.6, "record low": 0.6,
    "misses estimates": 0.8, "cuts guidance": 1.0, "lowers guidance": 1.0, "downgraded": 0.6, "lawsuit": 0.4,
    "probe": 0.5, "investigation": 0.5, "recall": 0.5, "fraud": 0.9, "underperform": 0.5, "warns": 0.6,
}
NEGATIONS = re.compile(r"\b(not|no|fails to|failed to|unlikely to|despite)\b")
CLAUSE_SPLIT = re.compile(r"\s+(?:as|while|but|after|amid|whereas|and|;)\s+|[;,:–—|]\s*", re.I)

INDEX_SYMBOLS = {"US30": "USIDX", "US100": "USIDX", "NAS100": "USIDX", "USTEC": "USIDX", "US500": "USIDX",
                 "SPX500": "USIDX", "DJ30": "USIDX", "USOIL": "OIL", "UKOIL": "OIL", "WTI": "OIL", "BRENT": "OIL"}


def _compile(terms):
    return [re.compile(r"(?<![a-z])" + re.escape(t) + r"(?![a-z])") for t in terms]


_CUR_RX = {c: _compile(t) for c, t in CURRENCY_TERMS.items()}
_CASED_RX = {c: [re.compile(p) for p in ps] for c, ps in CASED.items()}
_BULL_RX = [(rx, w) for rx, w in zip(_compile(BULLISH), BULLISH.values())]
_BEAR_RX = [(rx, w) for rx, w in zip(_compile(BEARISH), BEARISH.values())]


def symbol_currencies(symbol):
    """'EURUSD.m' -> ('EUR', 'USD'); 'XAUUSD' -> ('XAU', 'USD'); 'US30' -> ('USIDX', None);
    share CFDs -> ('AAPL', None) so headlines about the company drive the bias."""
    _, base, quote = classify(symbol)
    return base, quote


def register_aliases(aliases):
    """Teach the headline parser company names: {"AAPL": ["apple", "iphone"], ...}.
    The ticker itself is always matched in capitals (e.g. 'AAPL', '$AAPL')."""
    for tick, names in (aliases or {}).items():
        tick = tick.upper()
        _CUR_RX[tick] = _compile([n.lower() for n in names])
        _CASED_RX[tick] = [re.compile(r"(?<![A-Za-z])\$?" + re.escape(tick) + r"(?![A-Za-z])")]


def _first_subject(clause):
    """Currency named first in the clause (the grammatical subject, usually)."""
    low, best = clause.lower(), None
    for cur, rxs in _CUR_RX.items():
        for rx in rxs:
            m = rx.search(low)
            if m and (best is None or m.start() < best[0]):
                best = (m.start(), cur)
    for cur, rxs in _CASED_RX.items():
        for rx in rxs:
            m = rx.search(clause)
            if m and (best is None or m.start() < best[0]):
                best = (m.start(), cur)
    return best[1] if best else None


def _polarity(clause):
    low = clause.lower()
    score = sum(w for rx, w in _BULL_RX if rx.search(low)) - sum(w for rx, w in _BEAR_RX if rx.search(low))
    if score and NEGATIONS.search(low):
        score = -score * 0.5
    return score


def score_headline(text):
    """Return {currency: score} for one headline."""
    out = {}
    for clause in CLAUSE_SPLIT.split(text):
        if not clause or not clause.strip():
            continue
        cur = _first_subject(clause)
        if not cur:
            continue
        p = _polarity(clause)
        if p:
            out[cur] = out.get(cur, 0.0) + p
    return out


def _http_get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _strip_html(s):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def parse_feed(raw):
    """Parse RSS 2.0 or Atom bytes into [{'title', 'time'}]."""
    root = ET.fromstring(raw)
    items = []
    atom = "{http://www.w3.org/2005/Atom}"
    for it in root.iter("item"):
        title = _strip_html(it.findtext("title"))
        when = it.findtext("pubDate") or it.findtext("{http://purl.org/dc/elements/1.1/}date")
        items.append({"title": title, "time": _parse_time(when)})
    for it in root.iter(atom + "entry"):
        title = _strip_html(it.findtext(atom + "title"))
        when = it.findtext(atom + "updated") or it.findtext(atom + "published")
        items.append({"title": title, "time": _parse_time(when)})
    return [i for i in items if i["title"]]


def _parse_time(s):
    if not s:
        return datetime.now(timezone.utc)
    s = s.strip()
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(timezone.utc)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def parse_calendar(raw):
    """Parse the ForexFactory-style weekly JSON into [{'title','currency','impact','time'}]."""
    events = []
    for e in json.loads(raw):
        try:
            t = datetime.fromisoformat(str(e["date"]).replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if not t.tzinfo:
            t = t.replace(tzinfo=timezone.utc)
        events.append({"title": e.get("title", ""), "currency": str(e.get("country", "")).upper(),
                       "impact": e.get("impact", ""), "time": t.astimezone(timezone.utc),
                       "forecast": e.get("forecast", ""), "previous": e.get("previous", "")})
    return events


class NewsEngine:
    def __init__(self, cfg, fetch=_http_get):
        self.cfg = cfg
        register_aliases(cfg.get("stock_aliases"))
        self.fetch = fetch
        self.events, self.headlines = [], []
        self._cal_at, self._feeds_at = 0.0, 0.0
        self._lock = threading.Lock()

    # ---------- refresh ----------
    def refresh(self, force=False):
        with self._lock:
            self._refresh(force)

    def _refresh(self, force):
        now = time.time()
        if self.cfg.get("calendar_url") and (force or now - self._cal_at > self.cfg.get("calendar_refresh_min", 60) * 60):
            self._cal_at = now
            try:
                self.events = parse_calendar(self.fetch(self.cfg["calendar_url"]))
                log.info("calendar: %d events loaded", len(self.events))
            except Exception as e:  # keep last good data
                log.warning("calendar fetch failed (%s); keeping %d cached events", e, len(self.events))
        if self.cfg.get("feeds") and (force or now - self._feeds_at > self.cfg.get("feed_refresh_min", 5) * 60):
            self._feeds_at = now
            seen, fresh = set(), []
            for url in self.cfg["feeds"]:
                try:
                    for item in parse_feed(self.fetch(url)):
                        key = item["title"].lower()
                        if key not in seen:
                            seen.add(key)
                            fresh.append(item)
                except Exception as e:
                    log.warning("feed %s failed: %s", url, e)
            if fresh:
                self.headlines = fresh
                log.info("headlines: %d loaded", len(fresh))

    # ---------- analysis ----------
    def currency_scores(self, now=None):
        now = now or datetime.now(timezone.utc)
        half_life = self.cfg.get("half_life_hours", 6)
        max_age = self.cfg.get("max_age_hours", 24)
        totals = {}
        for h in self.headlines:
            age = (now - h["time"]).total_seconds() / 3600
            if age < -1 or age > max_age:
                continue
            decay = 0.5 ** (max(age, 0) / half_life)
            for cur, s in score_headline(h["title"]).items():
                totals[cur] = totals.get(cur, 0.0) + s * decay
        # squash so a flood of headlines can't dominate: tanh(x/2) keeps -1..1
        return {c: math.tanh(v / 2) for c, v in totals.items()}

    def upcoming(self, currencies, now=None):
        now = now or datetime.now(timezone.utc)
        before = timedelta(minutes=self.cfg.get("blackout_before_min", 30))
        after = timedelta(minutes=self.cfg.get("blackout_after_min", 30))
        impacts = set(self.cfg.get("blackout_impacts", ["High"]))
        extra = {c.upper() for c in self.cfg.get("always_watch", ["USD"])}
        watch = {c for c in currencies if c} | extra
        if any(c and c not in CURRENCY_TERMS or c in ("XAU", "XAG", "OIL", "USIDX") for c in currencies):
            watch.add("USD")      # metals, oil, indices and US shares all move on US data
        return [e for e in self.events
                if e["impact"] in impacts and e["currency"] in watch and e["time"] - before <= now <= e["time"] + after]

    def events_ahead(self, hours=24, impacts=("High", "Medium"), now=None):
        now = now or datetime.now(timezone.utc)
        return [e for e in self.events
                if e["impact"] in impacts and now - timedelta(minutes=30) <= e["time"] <= now + timedelta(hours=hours)]

    def recent_headlines(self, n=20, now=None):
        now = now or datetime.now(timezone.utc)
        items = sorted(self.headlines, key=lambda h: h["time"], reverse=True)[:n]
        return [{**h, "scores": score_headline(h["title"])} for h in items]

    def assess(self, symbol, now=None):
        if not self.cfg.get("enabled", True):
            return NewsAssessment()
        now = now or datetime.now(timezone.utc)
        self.refresh()
        base, quote = symbol_currencies(symbol)
        out = NewsAssessment()
        for e in self.upcoming((base, quote), now):
            out.blackout = True
            mins = int((e["time"] - now).total_seconds() // 60)
            when = f"in {mins} min" if mins >= 0 else f"{-mins} min ago"
            out.reasons.append(f"{e['impact']} impact {e['currency']} '{e['title']}' {when}")
        scores = self.currency_scores(now)
        bias = scores.get(base, 0.0) - (scores.get(quote, 0.0) if quote else 0.0)
        out.bias = max(-1.0, min(1.0, bias))
        if abs(out.bias) >= 0.05:
            out.reasons.append(f"headline bias {out.bias:+.2f} ({base} {scores.get(base, 0):+.2f}"
                               + (f", {quote} {scores.get(quote, 0):+.2f}" if quote else "") + ")")
        return out
