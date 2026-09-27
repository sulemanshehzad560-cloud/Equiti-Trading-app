"""Exercises MT5Broker against a fake MetaTrader5 module (the real one is Windows-only)."""
import sys
import types
import unittest
from types import SimpleNamespace as NS

from trader.models import BUY, OrderRequest


def fake_mt5():
    m = types.ModuleType("MetaTrader5")
    m.TIMEFRAME_M15, m.ACCOUNT_TRADE_MODE_DEMO, m.POSITION_TYPE_BUY = 15, 0, 0
    m.TRADE_ACTION_DEAL, m.TRADE_ACTION_SLTP, m.ORDER_TYPE_BUY, m.ORDER_TYPE_SELL = 1, 6, 0, 1
    m.ORDER_TIME_GTC, m.ORDER_FILLING_FOK, m.ORDER_FILLING_IOC, m.ORDER_FILLING_RETURN = 0, 0, 1, 2
    m.TRADE_RETCODE_DONE = 10009
    m.sent = []
    m.initialize = lambda **kw: True
    m.shutdown = lambda: None
    m.last_error = lambda: (0, "ok")
    m.account_info = lambda: NS(server="Equiti-Demo", login=1, trade_mode=0, balance=1000.0, equity=1000.0, currency="USD")
    m.terminal_info = lambda: NS(trade_allowed=True)
    m.symbol_select = lambda s, on: True
    m.copy_rates_from_pos = lambda s, tf, start, n: [
        {"time": 1_700_000_000 + i * 900, "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15, "tick_volume": 5}
        for i in range(n)]
    m.symbol_info = lambda s: NS(point=1e-5, digits=5, trade_tick_size=1e-5, trade_tick_value=1.0,
                                 trade_contract_size=100000, volume_min=0.01, volume_max=100, volume_step=0.01,
                                 trade_stops_level=0, filling_mode=2)
    m.symbol_info_tick = lambda s: NS(bid=1.1000, ask=1.1001)
    m.positions_get = lambda **kw: [NS(ticket=7, symbol="EURUSD", type=0, volume=0.1, price_open=1.1, sl=1.09,
                                       tp=1.12, time=1_700_000_000, comment="", magic=260927),
                                    NS(ticket=8, symbol="EURUSD", type=1, volume=1, price_open=1.1, sl=0, tp=0,
                                       time=1_700_000_000, comment="manual", magic=0)]

    def order_send(req):
        m.sent.append(req)
        return NS(retcode=10009, order=99, price=req.get("price"), comment="done")
    m.order_send = order_send
    return m


class MT5AdapterTests(unittest.TestCase):
    def setUp(self):
        self.m = fake_mt5()
        sys.modules["MetaTrader5"] = self.m
        from trader.broker import MT5Broker
        self.b = MT5Broker("1", "pw", "Equiti-Demo")
        self.b.connect()

    def tearDown(self):
        sys.modules.pop("MetaTrader5", None)

    def test_bars_and_info(self):
        bars = self.b.bars("EURUSD", "M15", 3)
        self.assertEqual(len(bars), 3)
        self.assertEqual(self.b.symbol_info("EURUSD").digits, 5)
        self.assertTrue(self.b.is_demo())

    def test_ignores_manual_trades(self):
        self.assertEqual([p.ticket for p in self.b.positions("EURUSD")], [7])

    def test_open_close_modify(self):
        pos = self.b.open(OrderRequest("EURUSD", BUY, 0.1, 1.09, 1.12, "xt trend"))
        self.assertEqual(self.m.sent[-1]["price"], 1.1001)            # buys at ask
        self.assertEqual(self.m.sent[-1]["type_filling"], self.m.ORDER_FILLING_IOC)
        self.assertTrue(self.b.close(pos))
        self.assertEqual(self.m.sent[-1]["type"], self.m.ORDER_TYPE_SELL)
        self.assertEqual(self.m.sent[-1]["position"], 99)
        self.assertTrue(self.b.modify(pos, 1.095, 1.12))
        self.assertEqual(self.m.sent[-1]["action"], self.m.TRADE_ACTION_SLTP)


if __name__ == "__main__":
    unittest.main()
