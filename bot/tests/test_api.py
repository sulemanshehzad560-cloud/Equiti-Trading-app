import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from tests.test_engine import CFG, uptrend
from trader.api import TraderAPI
from trader.broker import PaperBroker
from trader.engine import Engine

TOKEN = "unit-test-token-0123456789"


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        static = Path(cls.tmp.name) / "public"
        static.mkdir()
        (static / "index.html").write_text("<h1>app</h1>")
        (Path(cls.tmp.name) / "secret.txt").write_text("nope")
        bars = uptrend()
        cls.broker = PaperBroker(10000, 5)
        for b in bars:
            cls.broker.on_bar("EURUSD", b)
        cls.engine = Engine(CFG, cls.broker, journal_path=Path(cls.tmp.name) / "j.jsonl")
        cls.engine.on_bar("EURUSD", bars, now=bars[-1].time)          # opens a BUY
        cls.api = TraderAPI(cls.engine, TOKEN, "127.0.0.1", 0, static_dir=static).start()
        cls.base = f"http://127.0.0.1:{cls.api.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.api.stop()
        cls.tmp.cleanup()

    def setUp(self):
        if not self.broker.positions():
            bars = uptrend()
            self.engine.on_bar("EURUSD", bars, now=bars[-1].time)

    def call(self, path, method="GET", token=TOKEN):
        req = urllib.request.Request(self.base + path, method=method, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_ping_is_public_rest_needs_token(self):
        self.assertEqual(self.call("/api/ping", token="")[0], 200)
        self.assertEqual(self.call("/api/status", token="wrong")[0], 401)
        self.assertEqual(self.call("/api/status", token="")[0], 401)

    def test_status_positions_journal(self):
        code, body = self.call("/api/status")
        self.assertEqual(code, 200)
        s = json.loads(body)
        self.assertEqual(s["account"]["login"], "PAPER")
        self.assertFalse(s["paused"])
        pos = json.loads(self.call("/api/positions")[1])
        self.assertEqual(pos[0]["side"], "BUY")
        self.assertEqual(pos[0]["digits"], 5)
        self.assertGreater(pos[0]["price"], 0)
        self.assertEqual(s["risk"]["max_open"], 3)
        self.assertEqual({x["name"] for x in s["strategies"]}, {"trend", "breakout"})
        journal = json.loads(self.call("/api/journal?n=50")[1])
        self.assertIn("open", [j["action"] for j in journal])

    def test_pause_resume_and_close_all(self):
        self.assertTrue(json.loads(self.call("/api/pause", "POST")[1])["paused"])
        self.assertTrue(self.engine.paused)
        self.call("/api/resume", "POST")
        self.assertFalse(self.engine.paused)
        n = len(self.broker.positions())
        self.assertEqual(json.loads(self.call("/api/close-all", "POST")[1])["closed"], n)
        self.assertEqual(self.broker.positions(), [])

    def test_static_files_and_no_traversal(self):
        code, body = self.call("/")
        self.assertEqual((code, body), (200, b"<h1>app</h1>"))
        code, body = self.call("/../secret.txt")
        self.assertNotIn(b"nope", body)
        code, body = self.call("/%2e%2e/secret.txt")
        self.assertNotIn(b"nope", body)

    def test_news_disabled(self):
        self.assertFalse(json.loads(self.call("/api/news")[1])["enabled"])

    def test_paused_engine_opens_nothing(self):
        self.engine.paused = True
        try:
            for p in list(self.broker.positions()):
                self.broker.close(p)
            bars = uptrend()
            self.assertIn("paused", self.engine.on_bar("EURUSD", bars, now=bars[-1].time))
        finally:
            self.engine.paused = False
