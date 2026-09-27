"""Broker adapters.

Equiti does not publish a public REST trading API, but every Equiti account can be
opened on MetaTrader 5, and MetaQuotes ships an official Python package
(`MetaTrader5`) that talks to a running MT5 terminal on the same machine.  That is
the supported way to automate an Equiti account: the bot logs in with your own
MT5 account number / password / server, and every trade it places shows up in the
Equiti app and MT5 mobile app exactly like a manual trade.

`PaperBroker` simulates fills locally (for backtests and dry runs).
"""
import itertools
import logging
from datetime import datetime, timezone

from .models import Bar, Position, SymbolInfo, BUY, SELL

log = logging.getLogger("broker")

TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]


class Broker:
    def connect(self): ...
    def shutdown(self): ...
    def bars(self, symbol, timeframe, count): raise NotImplementedError
    def symbol_info(self, symbol): raise NotImplementedError
    def quote(self, symbol): raise NotImplementedError          # (bid, ask)
    def equity(self): raise NotImplementedError
    def account(self): raise NotImplementedError             # dict for the app
    def positions(self, symbol=None): raise NotImplementedError
    def open(self, req): raise NotImplementedError               # returns Position or None
    def close(self, pos): raise NotImplementedError
    def modify(self, pos, sl, tp): raise NotImplementedError


class MT5Broker(Broker):
    def __init__(self, login, password, server, terminal_path=None, magic=260927, deviation=20):
        try:
            import MetaTrader5 as mt5
        except ImportError as e:
            raise SystemExit(
                "The MetaTrader5 package is not installed or not supported here.\n"
                "It runs on Windows (or Wine) next to an installed MT5 terminal:\n"
                "  pip install MetaTrader5\n"
                "Use --mode paper to try the bot without MT5.") from e
        self.mt5 = mt5
        self.login, self.password, self.server = int(login), password, server
        self.path, self.magic, self.deviation = terminal_path, magic, deviation

    def connect(self):
        kw = dict(login=self.login, password=self.password, server=self.server, timeout=60000)
        if self.path:
            kw["path"] = self.path
        if not self.mt5.initialize(**kw):
            raise RuntimeError(f"MT5 initialize/login failed: {self.mt5.last_error()}")
        acc = self.mt5.account_info()
        log.info("connected to %s account %s (%s), balance %.2f %s", acc.server, acc.login,
                 "DEMO" if acc.trade_mode == self.mt5.ACCOUNT_TRADE_MODE_DEMO else "REAL", acc.balance, acc.currency)
        if not self.mt5.terminal_info().trade_allowed:
            log.warning("Algo Trading is OFF in the MT5 terminal - enable the 'Algo Trading' button")
        return acc

    def is_demo(self):
        return self.mt5.account_info().trade_mode == self.mt5.ACCOUNT_TRADE_MODE_DEMO

    def shutdown(self):
        self.mt5.shutdown()

    def _tf(self, tf):
        return getattr(self.mt5, "TIMEFRAME_" + tf)

    def bars(self, symbol, timeframe, count):
        self.mt5.symbol_select(symbol, True)
        # start_pos=1 skips the bar still forming, so strategies only see closed bars
        rates = self.mt5.copy_rates_from_pos(symbol, self._tf(timeframe), 1, count)
        if rates is None:
            raise RuntimeError(f"no data for {symbol}: {self.mt5.last_error()}")
        return [Bar(datetime.fromtimestamp(int(r["time"]), timezone.utc), float(r["open"]), float(r["high"]),
                    float(r["low"]), float(r["close"]), float(r["tick_volume"])) for r in rates]

    def symbol_info(self, symbol):
        s = self.mt5.symbol_info(symbol)
        if s is None:
            raise RuntimeError(f"unknown symbol {symbol} - check the exact name in MT5 Market Watch")
        return SymbolInfo(symbol, s.point, s.digits, s.trade_tick_size, s.trade_tick_value, s.trade_contract_size,
                          s.volume_min, s.volume_max, s.volume_step, s.trade_stops_level)

    def quote(self, symbol):
        t = self.mt5.symbol_info_tick(symbol)
        return t.bid, t.ask

    def equity(self):
        return self.mt5.account_info().equity

    def account(self):
        a = self.mt5.account_info()
        return {"login": a.login, "server": a.server, "currency": a.currency, "balance": a.balance,
                "equity": a.equity, "margin_free": getattr(a, "margin_free", None),
                "demo": a.trade_mode == self.mt5.ACCOUNT_TRADE_MODE_DEMO}

    def positions(self, symbol=None):
        raw = self.mt5.positions_get(symbol=symbol) if symbol else self.mt5.positions_get()
        out = []
        for p in raw or []:
            if p.magic != self.magic:
                continue   # never touch trades you placed by hand
            out.append(Position(p.ticket, p.symbol, BUY if p.type == self.mt5.POSITION_TYPE_BUY else SELL,
                                p.volume, p.price_open, p.sl, p.tp,
                                datetime.fromtimestamp(p.time, timezone.utc), p.comment, getattr(p, "profit", 0.0)))
        return out

    def _filling(self, symbol):
        fm = self.mt5.symbol_info(symbol).filling_mode
        if fm & 1:
            return self.mt5.ORDER_FILLING_FOK
        if fm & 2:
            return self.mt5.ORDER_FILLING_IOC
        return self.mt5.ORDER_FILLING_RETURN

    def _send(self, request):
        res = self.mt5.order_send(request)
        if res is None or res.retcode != self.mt5.TRADE_RETCODE_DONE:
            log.error("order rejected: %s %s", getattr(res, "retcode", None),
                      getattr(res, "comment", self.mt5.last_error()))
            return None
        return res

    def open(self, req):
        bid, ask = self.quote(req.symbol)
        price = ask if req.side == BUY else bid
        res = self._send({
            "action": self.mt5.TRADE_ACTION_DEAL, "symbol": req.symbol, "volume": float(req.volume),
            "type": self.mt5.ORDER_TYPE_BUY if req.side == BUY else self.mt5.ORDER_TYPE_SELL,
            "price": price, "sl": float(req.sl), "tp": float(req.tp), "deviation": self.deviation,
            "magic": self.magic, "comment": req.comment[:31], "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": self._filling(req.symbol)})
        if not res:
            return None
        return Position(res.order, req.symbol, req.side, req.volume, res.price or price, req.sl, req.tp,
                        datetime.now(timezone.utc), req.comment)

    def close(self, pos):
        bid, ask = self.quote(pos.symbol)
        return bool(self._send({
            "action": self.mt5.TRADE_ACTION_DEAL, "symbol": pos.symbol, "volume": float(pos.volume),
            "type": self.mt5.ORDER_TYPE_SELL if pos.side == BUY else self.mt5.ORDER_TYPE_BUY,
            "position": pos.ticket, "price": bid if pos.side == BUY else ask, "deviation": self.deviation,
            "magic": self.magic, "comment": "close", "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": self._filling(pos.symbol)}))

    def modify(self, pos, sl, tp):
        return bool(self._send({"action": self.mt5.TRADE_ACTION_SLTP, "symbol": pos.symbol,
                                "position": pos.ticket, "sl": float(sl), "tp": float(tp)}))


class PaperBroker(Broker):
    """Local simulation. Feed it bars with `on_bar` (backtest) or wrap another broker's
    data with `data_source` (dry run against live MT5 prices)."""

    def __init__(self, balance=10000.0, spread_points=10, symbols=None, data_source=None):
        self.balance = balance
        self.spread_points = spread_points
        self.infos = symbols or {}
        self.data = data_source
        self.last = {}               # symbol -> last Bar
        self.open_positions = []
        self.closed = []             # (Position, exit_price, exit_time, pnl, reason)
        self._ids = itertools.count(1)

    def connect(self):
        if self.data:
            self.data.connect()

    def shutdown(self):
        if self.data:
            self.data.shutdown()

    def bars(self, symbol, timeframe, count):
        bars = self.data.bars(symbol, timeframe, count)
        if bars:
            self.on_bar(symbol, bars[-1])
        return bars

    def symbol_info(self, symbol):
        if symbol in self.infos:
            return self.infos[symbol]
        if self.data:
            return self.data.symbol_info(symbol)
        return default_symbol_info(symbol)

    def quote(self, symbol):
        if self.data:
            return self.data.quote(symbol)
        info, c = self.symbol_info(symbol), self.last[symbol].close
        return c, c + self.spread_points * info.point

    def _pnl(self, pos, price):
        info = self.symbol_info(pos.symbol)
        return (price - pos.entry) * pos.side / info.tick_size * info.tick_value * pos.volume

    def equity(self):
        eq = self.balance
        for p in self.open_positions:
            if p.symbol in self.last:
                eq += self._pnl(p, self.last[p.symbol].close)
        return eq

    def account(self):
        return {"login": "PAPER", "server": "simulation", "currency": "USD", "balance": round(self.balance, 2),
                "equity": round(self.equity(), 2), "margin_free": None, "demo": True}

    def positions(self, symbol=None):
        out = [p for p in self.open_positions if symbol is None or p.symbol == symbol]
        for p in out:
            if p.symbol in self.last:
                p.profit = round(self._pnl(p, self.last[p.symbol].close), 2)
        return out

    def open(self, req):
        bid, ask = self.quote(req.symbol)
        price = ask if req.side == BUY else bid
        when = self.last[req.symbol].time if req.symbol in self.last else datetime.now(timezone.utc)
        pos = Position(next(self._ids), req.symbol, req.side, req.volume, price, req.sl, req.tp, when, req.comment)
        self.open_positions.append(pos)
        log.info("PAPER open %s %s %.2f @ %.5f sl %.5f tp %.5f", "BUY" if req.side == BUY else "SELL",
                 req.symbol, req.volume, price, req.sl, req.tp)
        return pos

    def _exit(self, pos, price, when, reason):
        pnl = self._pnl(pos, price)
        self.balance += pnl
        self.open_positions.remove(pos)
        self.closed.append((pos, price, when, pnl, reason))
        log.info("PAPER close %s #%d @ %.5f pnl %.2f (%s)", pos.symbol, pos.ticket, price, pnl, reason)

    def close(self, pos, reason="signal"):
        bid, ask = self.quote(pos.symbol)
        when = self.last[pos.symbol].time if pos.symbol in self.last else datetime.now(timezone.utc)
        self._exit(pos, bid if pos.side == BUY else ask, when, reason)
        return True

    def modify(self, pos, sl, tp):
        pos.sl, pos.tp = sl, tp
        return True

    def on_bar(self, symbol, bar):
        """Advance the simulation: check stop loss / take profit against the bar's range.
        If both are inside one bar the stop is assumed to hit first (conservative)."""
        self.last[symbol] = bar
        for pos in list(self.positions(symbol)):
            if pos.opened and bar.time <= pos.opened:
                continue
            if pos.side == BUY:
                if pos.sl and bar.low <= pos.sl:
                    self._exit(pos, min(pos.sl, bar.open), bar.time, "stop loss")
                elif pos.tp and bar.high >= pos.tp:
                    self._exit(pos, max(pos.tp, bar.open), bar.time, "take profit")
            else:
                if pos.sl and bar.high >= pos.sl:
                    self._exit(pos, max(pos.sl, bar.open), bar.time, "stop loss")
                elif pos.tp and bar.low <= pos.tp:
                    self._exit(pos, min(pos.tp, bar.open), bar.time, "take profit")


def default_symbol_info(symbol):
    """Reasonable contract specs for a USD account when no broker is attached."""
    s = symbol.upper()
    if s.startswith(("XAU", "GOLD")):
        return SymbolInfo(symbol, 0.01, 2, 0.01, 1.0, 100)
    if s.startswith(("XAG", "SILVER")):
        return SymbolInfo(symbol, 0.001, 3, 0.001, 5.0, 5000)
    if "JPY" in s:
        return SymbolInfo(symbol, 0.001, 3, 0.001, 0.67, 100000)   # tick value approx at USDJPY ~150
    return SymbolInfo(symbol, 0.00001, 5, 0.00001, 1.0, 100000)
