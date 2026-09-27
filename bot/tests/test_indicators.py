import unittest

from trader import indicators as ind


class IndicatorTests(unittest.TestCase):
    def test_sma_ema(self):
        v = [1, 2, 3, 4, 5, 6]
        self.assertEqual(ind.sma(v, 3), [None, None, 2, 3, 4, 5])
        e = ind.ema(v, 3)
        self.assertEqual(e[:2], [None, None])
        self.assertAlmostEqual(e[2], 2)
        self.assertAlmostEqual(e[3], 3)   # linear series: EMA tracks SMA

    def test_rsi_extremes(self):
        up = [float(i) for i in range(40)]
        self.assertEqual(ind.rsi(up, 14)[-1], 100.0)
        down = [float(40 - i) for i in range(40)]
        self.assertAlmostEqual(ind.rsi(down, 14)[-1], 0.0)

    def test_atr_constant_range(self):
        h, l, c = [11.0] * 30, [9.0] * 30, [10.0] * 30
        self.assertAlmostEqual(ind.atr(h, l, c, 14)[-1], 2.0)

    def test_adx_strong_trend(self):
        c = [100 + i for i in range(80)]
        h, l = [x + 0.5 for x in c], [x - 0.5 for x in c]
        a, pdi, mdi = ind.adx(h, l, c, 14)
        self.assertGreater(a[-1], 50)
        self.assertGreater(pdi[-1], mdi[-1])

    def test_bollinger_and_donchian(self):
        lo, mid, up = ind.bollinger([5.0] * 25, 20)
        self.assertEqual((lo[-1], mid[-1], up[-1]), (5.0, 5.0, 5.0))
        dlo, dup = ind.donchian([1, 5, 3, 2], [0, 1, 1, 1], 3)
        self.assertEqual((dlo[-1], dup[-1]), (1, 5))
