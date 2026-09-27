"""The trading loop: signals -> news filter -> risk checks -> orders."""
import json
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import indicators as ind
from .markets import classify, session_for
from .models import BUY, OrderRequest, NewsAssessment
from .risk import RiskManager
from .strategies import Ensemble

DAILY_TFS = ("D1", "W1", "MN1")


def owner_of(pos):
    """Strategy that opened a position, from its order comment ('xt:rsi2', legacy 'xt trend')."""
    m = re.match(r"xt[: ](\w+)", pos.comment or "")
    return m.group(1) if m else None

log = logging.getLogger("engine")


class Engine:
    def __init__(self, cfg, broker, news=None, journal_path=None):
        self.cfg = cfg
        self.broker = broker
        self.news = news
        self.risk = RiskManager(cfg.get("risk", {}))
        self.ensemble = Ensemble.from_config(cfg)
        self.symbols = cfg["symbols"]
        self.timeframe = cfg.get("timeframe", "M15")
        self.history = max(cfg.get("history_bars", 300), self.ensemble.min_bars() + 5)
        self.hours = cfg.get("trading_hours_utc", [0, 24])
        self.independent = self.ensemble.mode == "independent"
        self.daily = self.timeframe in DAILY_TFS
        self.session_overrides = cfg.get("sessions", {})
        self.ignore_sessions = cfg.get("ignore_sessions", False)     # the simulated demo market never closes
        self.daily_eval_min = cfg.get("daily_eval_minutes_before_close", 10)
        self._bars = {}                        # latest bars per symbol (the cross-sectional universe)
        self._daily_done = {}
        self.exit_on_opposite = cfg.get("exit_on_opposite_signal", True)
        ncfg = cfg.get("news", {})
        self.veto = ncfg.get("veto_threshold", 0.35)
        self.boost = ncfg.get("size_boost_max", 0.5)
        self.close_before_event = ncfg.get("close_positions_before_event", False)
        self.journal = Path(journal_path) if journal_path else None
        self._last_bar = {}
        self.lock = threading.RLock()          # broker calls from the app API and the loop never overlap
        self.stop_event = threading.Event()
        self.paused = False                    # set from the app: manage exits, but open nothing new
        self.mode = cfg.get("mode", "paper")
        self.started = None
        self.last_action = {}

    # ---------- helpers ----------
    def _log(self, **event):
        if self.journal:
            self.journal.parent.mkdir(parents=True, exist_ok=True)
            with self.journal.open("a") as f:
                f.write(json.dumps(event, default=lambda o: o.isoformat() if hasattr(o, "isoformat") else str(o)) + "\n")

    def _in_hours(self, now):
        start, end = self.hours
        h = now.hour + now.minute / 60
        return start <= h < end if start <= end else (h >= start or h < end)

    def _news_multiplier(self, side, news):
        """How the headline bias changes a trade: None = veto, else a size multiplier."""
        agreement = news.bias * side          # +1 news fully agrees with the trade, -1 fully against
        if agreement <= -self.veto:
            return None
        return max(0.5, min(1.0 + self.boost, 1.0 + self.boost * agreement))

    # ---------- core ----------
    def on_bar(self, symbol, bars, now=None, universe=None):
        """Evaluate one symbol on its newest closed bar. Returns a short action string."""
        now = now or datetime.now(timezone.utc)
        sess = None if self.ignore_sessions else session_for(symbol, self.session_overrides)
        ctx = {"symbol": symbol, "now": now, "session": sess,
               "universe": universe if universe is not None else {k: v for k, v in self._bars.items() if k != symbol}}
        sigs = self.ensemble.signals(bars, ctx)
        combined = None if self.independent else self.ensemble.combine(sigs)
        news = self.news.assess(symbol, now) if self.news else NewsAssessment()
        own = {s.name: sig for s, sig in sigs}

        # 1) manage open positions: news, the owning strategy's exit rule, opposite signal, trailing stop
        for pos in self.broker.positions(symbol):
            owner = self.ensemble.get(owner_of(pos))
            reason = None
            if news.blackout and self.close_before_event:
                reason = "news blackout: " + "; ".join(news.reasons)
            elif owner and owner.should_exit(bars, pos.side, ctx):
                reason = owner.should_exit(bars, pos.side, ctx)
            elif self.exit_on_opposite:
                opp = own.get(owner.name) if (self.independent and owner) else combined
                if opp is not None and opp.side == -pos.side:
                    reason = "opposite signal: " + opp.reason
            if reason:
                self.broker.close(pos)
                self._log(t=now, symbol=symbol, action="close", strategy=owner_of(pos), reason=reason)
                continue
            self._trail(pos, bars)

        # 2) entries: one combined signal (vote) or each strategy on its own (independent)
        wanted = [sig for _, sig in sigs if sig.side] if self.independent else ([combined] if combined.side else [])
        if not wanted:
            return "flat"
        return "; ".join(self._try_open(symbol, sig, bars, news, now, sess) for sig in wanted)

    def _try_open(self, symbol, signal, bars, news, now, sess):
        def skip(why):
            self._log(t=now, symbol=symbol, action="skip", why=why, strategy=signal.tag, signal=signal.side,
                      strength=round(signal.strength, 3), reason=signal.reason, news=news.reasons)
            return "skip: " + why

        positions = self.broker.positions(symbol)
        if self.paused:
            return skip("paused from the app")
        equity = self.broker.equity()
        if self.risk.daily_halt(equity, now):
            return skip("daily loss limit reached")
        if news.blackout:
            return skip("news blackout: " + "; ".join(news.reasons))
        if not self.daily and not self._in_hours(now):      # hours filter is for intraday timeframes
            return skip("outside trading hours")
        if sess and not self.daily and not sess.is_open(now):
            return skip(f"{sess.name} market closed")
        if self.independent and any(owner_of(p) == signal.tag for p in positions):
            return skip(f"already in a {signal.tag} position")
        if len(positions) >= self.risk.max_per_symbol:
            return skip("already in a position")
        if len(self.broker.positions()) >= self.risk.max_open:
            return skip("max open positions")
        mult = self._news_multiplier(signal.side, news)
        if mult is None:
            return skip(f"news against trade (bias {news.bias:+.2f})")

        info = self.broker.symbol_info(symbol)
        bid, ask = self.broker.quote(symbol)
        spread_pts = (ask - bid) / info.point
        if spread_pts > self.risk.max_spread_points:
            return skip(f"spread {spread_pts:.0f} pts too wide")
        if self.risk.max_spread_pct and bid and (ask - bid) / bid * 100 > self.risk.max_spread_pct:
            return skip(f"spread {(ask - bid) / bid * 100:.2f}% too wide")
        a = ind.atr([b.high for b in bars], [b.low for b in bars], [b.close for b in bars], 14)[-1]
        if not a:
            return skip("no ATR yet")
        price = ask if signal.side == BUY else bid
        sl, tp, sl_dist = self.risk.stops(signal.side, price, a, info, signal.sl_atr, signal.tp_atr, signal.sl_dist)
        volume = self.risk.volume(equity, sl_dist, info, mult * (0.5 + signal.strength / 2), price)
        if volume <= 0:
            return skip("position size below broker minimum")
        comment = f"xt:{signal.tag or 'ens'}"[:31]
        pos = self.broker.open(OrderRequest(symbol, signal.side, volume, sl, tp, comment))
        self._log(t=now, symbol=symbol, action="open" if pos else "rejected", strategy=signal.tag, side=signal.side,
                  volume=volume, price=price, sl=sl, tp=tp, reason=signal.reason, news_bias=round(news.bias, 3),
                  news=news.reasons, size_mult=round(mult, 2))
        side = "BUY" if signal.side == BUY else "SELL"
        return f"{'opened' if pos else 'rejected'} {side} {volume} lots ({signal.reason})"

    def _trail(self, pos, bars):
        if self.risk.trail_atr <= 0:
            return
        a = ind.atr([b.high for b in bars], [b.low for b in bars], [b.close for b in bars], 14)[-1]
        if not a:
            return
        info = self.broker.symbol_info(pos.symbol)
        new_sl = round(bars[-1].close - pos.side * a * self.risk.trail_atr, info.digits)
        better = (new_sl > pos.sl) if pos.side == BUY else (pos.sl == 0 or new_sl < pos.sl)
        in_profit = (new_sl - pos.entry) * pos.side > 0
        if better and in_profit:
            self.broker.modify(pos, new_sl, pos.tp)

    def _daily_at_close(self, symbol):
        """Daily strategies on exchange-traded CFDs act a few minutes before the cash close,
        using today's forming bar as the close (the market is shut at the daily bar roll)."""
        return self.daily and not self.ignore_sessions and session_for(symbol, self.session_overrides) is not None

    def _fetch(self, symbol):
        if self._daily_at_close(symbol):
            return self.broker.bars(symbol, self.timeframe, self.history, include_forming=True)
        return self.broker.bars(symbol, self.timeframe, self.history)

    def _is_due(self, symbol, bars):
        if self._daily_at_close(symbol):
            sess = session_for(symbol, self.session_overrides)
            now = datetime.now(timezone.utc)
            day = sess.trading_day(now)
            if sess.is_open(now) and sess.minutes_to_close(now) <= self.daily_eval_min and self._daily_done.get(symbol) != day:
                self._daily_done[symbol] = day
                return True
            return False
        if self._last_bar.get(symbol) == bars[-1].time:
            return False
        self._last_bar[symbol] = bars[-1].time
        return True

    # ---------- app controls ----------
    def close_all(self):
        with self.lock:
            closed = 0
            for pos in self.broker.positions():
                if self.broker.close(pos):
                    closed += 1
            self._log(t=datetime.now(timezone.utc), action="close_all", closed=closed, reason="from the app")
            return closed

    def status(self):
        with self.lock:
            acc = self.broker.account()
        start = self.risk._day_start_equity
        return {
            "mode": self.mode, "paused": self.paused, "started": self.started,
            "halted": bool(start) and (start - acc["equity"]) / start * 100 >= self.risk.max_daily_loss_pct,
            "symbols": self.symbols, "timeframe": self.timeframe, "account": acc,
            "day_pnl": round(acc["equity"] - start, 2) if start else 0.0,
            "last_action": self.last_action,
            "risk": {"risk_per_trade_pct": self.risk.risk_pct, "max_open": self.risk.max_open,
                     "max_per_symbol": self.risk.max_per_symbol, "max_daily_loss_pct": self.risk.max_daily_loss_pct,
                     "max_spread_points": self.risk.max_spread_points, "sl_atr": self.risk.sl_atr,
                     "tp_atr": self.risk.tp_atr},
            "strategies": [{**s.info(), "weight": self.ensemble.weights.get(s.name, 1.0),
                            "open": sum(1 for p in self.broker.positions() if owner_of(p) == s.name)}
                           for s in self.ensemble.strategies],
            "strategy_mode": self.ensemble.mode,
            "signal_threshold": self.ensemble.threshold,
            "news_enabled": self.news is not None,
            "news_veto": self.veto,
            "server_time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    def run(self, poll_seconds=None, once=False, connect=True):
        poll = poll_seconds or self.cfg.get("poll_seconds", 20)
        if connect:
            self.broker.connect()
        self.started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        log.info("running on %s %s", ", ".join(self.symbols), self.timeframe)
        try:
            while not self.stop_event.is_set():
                due = {}
                for symbol in self.symbols:          # fetch everything first: strategies may rank the universe
                    try:
                        with self.lock:
                            bars = self._fetch(symbol)
                        if bars:
                            self._bars[symbol] = bars
                            if self._is_due(symbol, bars):
                                due[symbol] = bars
                    except Exception:
                        log.exception("data error on %s", symbol)
                for symbol, bars in due.items():
                    try:
                        with self.lock:
                            result = self.on_bar(symbol, bars)
                        self.last_action[symbol] = {"t": bars[-1].time.isoformat(), "result": result}
                        log.info("%s %s", symbol, result)
                    except Exception:
                        log.exception("error on %s", symbol)
                if once:
                    break
                self.stop_event.wait(poll)
        except KeyboardInterrupt:
            log.info("stopped by user")
        finally:
            self.broker.shutdown()
