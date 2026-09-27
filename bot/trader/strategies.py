"""Technical strategies. Each one looks at closed bars and votes on the last bar.

- TrendFollow:    EMA fast/slow alignment confirmed by ADX strength and DI direction.
- MeanReversion:  RSI extremes outside Bollinger Bands, only when the market is ranging (low ADX).
- Breakout:       Close through the prior Donchian channel with ATR-sized follow-through.

The Ensemble combines the votes with weights so no single strategy trades alone
unless it is very confident.
"""
from . import indicators as ind
from .models import BUY, SELL, FLAT, Signal


def _cols(bars):
    return ([b.high for b in bars], [b.low for b in bars], [b.close for b in bars])


class TrendFollow:
    name = "trend"

    def __init__(self, fast=20, slow=50, adx_period=14, adx_min=22):
        self.fast, self.slow, self.adx_period, self.adx_min = fast, slow, adx_period, adx_min

    def min_bars(self):
        return max(self.slow, self.adx_period * 2) + 2

    def evaluate(self, bars):
        if len(bars) < self.min_bars():
            return Signal()
        h, l, c = _cols(bars)
        f, s = ind.ema(c, self.fast)[-1], ind.ema(c, self.slow)[-1]
        a, pdi, mdi = (x[-1] for x in ind.adx(h, l, c, self.adx_period))
        if None in (f, s, a, pdi, mdi) or a < self.adx_min:
            return Signal()
        strength = min(1.0, 0.4 + (a - self.adx_min) / 30)
        if f > s and c[-1] > s and pdi > mdi:
            return Signal(BUY, strength, f"trend up ADX {a:.0f}")
        if f < s and c[-1] < s and mdi > pdi:
            return Signal(SELL, strength, f"trend down ADX {a:.0f}")
        return Signal()


class MeanReversion:
    name = "meanrev"

    def __init__(self, rsi_period=14, oversold=30, overbought=70, bb_period=20, bb_k=2.0, adx_max=20):
        self.rsi_period, self.oversold, self.overbought = rsi_period, oversold, overbought
        self.bb_period, self.bb_k, self.adx_max = bb_period, bb_k, adx_max

    def min_bars(self):
        return max(self.bb_period, self.rsi_period * 2 + 2, 30)

    def evaluate(self, bars):
        if len(bars) < self.min_bars():
            return Signal()
        h, l, c = _cols(bars)
        r = ind.rsi(c, self.rsi_period)[-1]
        lo, _, up = (x[-1] for x in ind.bollinger(c, self.bb_period, self.bb_k))
        a = ind.adx(h, l, c, 14)[0][-1]
        if None in (r, lo, up, a) or a > self.adx_max:
            return Signal()
        if r < self.oversold and c[-1] <= lo:
            return Signal(BUY, min(1.0, 0.5 + (self.oversold - r) / 30), f"oversold RSI {r:.0f}")
        if r > self.overbought and c[-1] >= up:
            return Signal(SELL, min(1.0, 0.5 + (r - self.overbought) / 30), f"overbought RSI {r:.0f}")
        return Signal()


class Breakout:
    name = "breakout"

    def __init__(self, channel=20, atr_period=14, min_atr_push=0.1):
        self.channel, self.atr_period, self.min_atr_push = channel, atr_period, min_atr_push

    def min_bars(self):
        return max(self.channel, self.atr_period) + 2

    def evaluate(self, bars):
        if len(bars) < self.min_bars():
            return Signal()
        h, l, c = _cols(bars)
        lo, up = ind.donchian(h[:-1], l[:-1], self.channel)   # channel of the bars BEFORE the current one
        a = ind.atr(h, l, c, self.atr_period)[-1]
        if None in (lo[-1], up[-1], a) or a == 0:
            return Signal()
        push_up, push_down = (c[-1] - up[-1]) / a, (lo[-1] - c[-1]) / a
        if push_up >= self.min_atr_push:
            return Signal(BUY, min(1.0, 0.5 + push_up / 2), f"breakout above {self.channel}-bar high")
        if push_down >= self.min_atr_push:
            return Signal(SELL, min(1.0, 0.5 + push_down / 2), f"breakdown below {self.channel}-bar low")
        return Signal()


STRATEGIES = {cls.name: cls for cls in (TrendFollow, MeanReversion, Breakout)}


class Ensemble:
    def __init__(self, strategies, weights=None, threshold=0.2):
        self.strategies = strategies
        self.weights = weights or {}
        self.threshold = threshold

    @classmethod
    def from_config(cls, cfg):
        strategies = []
        for name, params in cfg.get("strategies", {}).items():
            if params.get("enabled", True):
                kwargs = {k: v for k, v in params.items() if k not in ("enabled", "weight")}
                strategies.append(STRATEGIES[name](**kwargs))
        weights = {n: p.get("weight", 1.0) for n, p in cfg.get("strategies", {}).items()}
        return cls(strategies, weights, cfg.get("signal_threshold", 0.2))

    def min_bars(self):
        return max((s.min_bars() for s in self.strategies), default=0)

    def evaluate(self, bars):
        total_w, score, reasons = 0.0, 0.0, []
        for s in self.strategies:
            w = self.weights.get(s.name, 1.0)
            total_w += w
            sig = s.evaluate(bars)
            if sig.side:
                score += w * sig.side * sig.strength
                reasons.append(f"{s.name}: {sig.reason}")
        if not total_w:
            return Signal()
        score /= total_w
        if abs(score) < self.threshold:
            return Signal(FLAT, abs(score), "; ".join(reasons))
        return Signal(BUY if score > 0 else SELL, min(1.0, abs(score)), "; ".join(reasons))
