"""HTTP API for the Equiti Trader app (the PWA in ../public).

Runs inside the bot process on its own thread. Every /api/ call except /api/ping
needs `Authorization: Bearer <DASHBOARD_TOKEN>`. The same server also serves the
app itself, so http://localhost:8787 works on the trading PC with no hosting.

To reach it from your phone, put it behind HTTPS - e.g. a free Cloudflare tunnel:
    cloudflared tunnel --url http://localhost:8787
and paste the https://....trycloudflare.com address into the app's settings.
"""
import hmac
import json
import logging
import mimetypes
import threading
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from . import __version__
from .news import symbol_currencies

log = logging.getLogger("api")
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/javascript", ".js")


def _jsonable(o):
    if isinstance(o, datetime):
        return o.isoformat()
    if hasattr(o, "__dict__"):
        return o.__dict__
    return str(o)


def tail_jsonl(path, n):
    p = Path(path) if path else None
    if not p or not p.exists():
        return []
    with p.open() as f:
        lines = deque(f, maxlen=n)
    out = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


class TraderAPI:
    def __init__(self, engine, token, host="127.0.0.1", port=8787, static_dir=None, allow_origin="*"):
        if not token or len(token) < 16:
            raise ValueError("DASHBOARD_TOKEN must be at least 16 characters")
        self.engine, self.token, self.host, self.port = engine, token, host, port
        self.static = Path(static_dir).resolve() if static_dir and Path(static_dir).is_dir() else None
        self.allow_origin = allow_origin
        self.httpd = None

    # ---------- handlers ----------
    def positions(self):
        b = self.engine.broker
        with self.engine.lock:
            return [{"ticket": p.ticket, "symbol": p.symbol, "side": "BUY" if p.side > 0 else "SELL",
                     "volume": p.volume, "entry": p.entry, "sl": p.sl, "tp": p.tp, "profit": round(p.profit, 2),
                     "opened": p.opened, "comment": p.comment, "digits": b.symbol_info(p.symbol).digits}
                    for p in b.positions()]

    def news(self):
        ne = self.engine.news
        if not ne:
            return {"enabled": False, "symbols": [], "events": [], "currencies": {}, "headlines": []}
        now = datetime.now(timezone.utc)
        symbols = []
        for s in self.engine.symbols:
            a = ne.assess(s, now)
            symbols.append({"symbol": s, "blackout": a.blackout, "bias": round(a.bias, 3), "reasons": a.reasons,
                            "currencies": [c for c in symbol_currencies(s) if c]})
        return {"enabled": True, "symbols": symbols,
                "events": sorted(ne.events_ahead(now=now), key=lambda e: e["time"]),
                "currencies": {k: round(v, 3) for k, v in ne.currency_scores(now).items()},
                "headlines": ne.recent_headlines(20, now)}

    def route(self, method, path, query):
        e = self.engine
        if method == "GET":
            if path == "status":
                return e.status()
            if path == "positions":
                return self.positions()
            if path == "news":
                return self.news()
            if path == "journal":
                n = max(1, min(500, int(query.get("n", ["50"])[0])))
                return tail_jsonl(e.journal, n)
        if method == "POST":
            if path == "pause":
                e.paused = True
                log.warning("paused from the app")
                return {"paused": True}
            if path == "resume":
                e.paused = False
                log.warning("resumed from the app")
                return {"paused": False}
            if path == "close-all":
                n = e.close_all()
                log.warning("close-all from the app: %d closed", n)
                return {"closed": n}
        return None

    # ---------- server ----------
    def make_handler(self):
        api = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "EquitiTrader/" + __version__

            def log_message(self, fmt, *args):
                log.debug("%s " + fmt, self.address_string(), *args)

            def _cors(self):
                self.send_header("Access-Control-Allow-Origin", api.allow_origin)
                self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Private-Network", "true")
                self.send_header("Access-Control-Max-Age", "600")

            def _send(self, code, body, ctype="application/json"):
                data = body if isinstance(body, bytes) else json.dumps(body, default=_jsonable).encode()
                self.send_response(code)
                self._cors()
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def do_OPTIONS(self):
                self.send_response(204)
                self._cors()
                self.end_headers()

            def _api(self, method):
                url = urlparse(self.path)
                path = url.path[len("/api/"):].strip("/")
                if path == "ping":
                    return self._send(200, {"ok": True, "app": "equiti-trader", "version": __version__})
                auth = self.headers.get("Authorization", "")
                given = auth[7:] if auth.lower().startswith("bearer ") else ""
                if not hmac.compare_digest(given.encode(), api.token.encode()):
                    return self._send(401, {"error": "Wrong or missing access token"})
                try:
                    res = api.route(method, path, parse_qs(url.query))
                except Exception as ex:
                    log.exception("api error")
                    return self._send(500, {"error": str(ex)})
                if res is None:
                    return self._send(404, {"error": "Not found"})
                self._send(200, res)

            def do_POST(self):
                if self.path.startswith("/api/"):
                    return self._api("POST")
                self._send(404, {"error": "Not found"})

            def do_GET(self):
                if self.path.startswith("/api/"):
                    return self._api("GET")
                if not api.static:
                    return self._send(404, {"error": "Not found"})
                rel = urlparse(self.path).path.lstrip("/") or "index.html"
                f = (api.static / rel).resolve()
                inside = api.static in f.parents
                if not inside or not f.is_file():
                    f = api.static / "index.html"
                ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
                self._send(200, f.read_bytes(), ctype)

        return Handler

    def start(self):
        self.httpd = ThreadingHTTPServer((self.host, self.port), self.make_handler())
        threading.Thread(target=self.httpd.serve_forever, name="api", daemon=True).start()
        log.info("app + API on http://%s:%d", self.host, self.port)
        return self

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
