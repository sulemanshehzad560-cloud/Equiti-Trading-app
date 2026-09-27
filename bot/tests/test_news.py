import json
import unittest
from datetime import datetime, timedelta, timezone

from trader.news import NewsEngine, parse_feed, score_headline, symbol_currencies

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)

RSS = f"""<?xml version="1.0"?><rss><channel>
<item><title>Fed signals rate hike as inflation runs hot</title><pubDate>{(NOW - timedelta(hours=1)).strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate></item>
<item><title>ECB turns dovish, euro slides</title><pubDate>{(NOW - timedelta(hours=2)).strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate></item>
<item><title>Old news: dollar plunges</title><pubDate>{(NOW - timedelta(days=3)).strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate></item>
</channel></rss>""".encode()

CAL = json.dumps([
    {"title": "Non-Farm Employment Change", "country": "USD", "date": (NOW + timedelta(minutes=20)).isoformat(), "impact": "High"},
    {"title": "German Buba Speech", "country": "EUR", "date": (NOW + timedelta(minutes=10)).isoformat(), "impact": "Low"},
    {"title": "BOJ Rate", "country": "JPY", "date": (NOW + timedelta(hours=5)).isoformat(), "impact": "High"},
]).encode()


def fake_fetch(url):
    return CAL if url.endswith(".json") else RSS


class NewsTests(unittest.TestCase):
    def test_symbol_currencies(self):
        self.assertEqual(symbol_currencies("EURUSD.m"), ("EUR", "USD"))
        self.assertEqual(symbol_currencies("xauusd"), ("XAU", "USD"))
        self.assertEqual(symbol_currencies("US30"), ("USIDX", None))

    def test_headline_subjects_and_polarity(self):
        s = score_headline("Gold rallies as dollar slumps")
        self.assertGreater(s["XAU"], 0)
        self.assertLess(s["USD"], 0)
        self.assertGreater(score_headline("Fed signals rate hike")["USD"], 0)
        self.assertLess(score_headline("Bank of England turns dovish")["GBP"], 0)
        self.assertLess(score_headline("Euro falls against the dollar")["EUR"], 0)
        self.assertNotIn("USD", score_headline("Euro falls against the dollar"))
        self.assertEqual(score_headline("Markets await data"), {})

    def test_negation_softens_and_flips(self):
        self.assertLess(score_headline("Fed is not expected to hike")["USD"], 0)

    def test_parse_atom(self):
        atom = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Yen surges</title>
        <updated>2026-09-25T10:00:00Z</updated></entry></feed>"""
        items = parse_feed(atom)
        self.assertEqual(items[0]["title"], "Yen surges")

    def _engine(self, **over):
        cfg = {"enabled": True, "calendar_url": "cal.json", "feeds": ["feed.xml"], **over}
        return NewsEngine(cfg, fetch=fake_fetch)

    def test_blackout_high_impact_only(self):
        ne = self._engine(always_watch=[])
        eurusd = ne.assess("EURUSD", NOW)
        self.assertTrue(eurusd.blackout)
        self.assertIn("Non-Farm", " ".join(eurusd.reasons))
        self.assertFalse(ne.assess("EURGBP", NOW).blackout)    # EUR event is low impact
        self.assertFalse(ne.assess("GBPJPY", NOW).blackout)    # BOJ is hours away
        self.assertTrue(ne.assess("XAUUSD", NOW).blackout)     # gold follows USD events

    def test_bias_direction(self):
        ne = self._engine(calendar_url=None)
        eurusd = ne.assess("EURUSD", NOW)
        self.assertLess(eurusd.bias, -0.3)        # hawkish Fed + dovish ECB => sell EURUSD
        self.assertGreater(ne.assess("USDJPY", NOW).bias, 0.1)

    def test_feed_failure_keeps_old_data(self):
        ne = self._engine()
        ne.refresh(force=True)
        n = len(ne.headlines)

        def broken(url):
            raise OSError("down")
        ne.fetch = broken
        ne.refresh(force=True)
        self.assertEqual(len(ne.headlines), n)
        self.assertTrue(ne.events)
