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


class Strategy:
    """Base: evaluate() votes on the newest closed bar; should_exit() returns a reason to close
    a position this strategy owns (or None). Metadata feeds the app's Playbook panel."""
    name = "base"
    title = ""
    style = "swing"            # swing | intraday | calendar
    markets = "any"
    source = ""
    summary = ""

    def min_bars(self):
        return 0

    def evaluate(self, bars, ctx=None):
        return Signal()

    def should_exit(self, bars, side, ctx=None):
        return None

    def info(self):
        return {"name": self.name, "title": self.title, "style": self.style, "markets": self.markets,
                "source": self.source, "summary": self.summary}


class TrendFollow(Strategy):
    name = "trend"
    title = "EMA trend + ADX"
    markets = "FX, metals, indices"
    source = "Classic trend following (Wilder ADX, 1978)"
    summary = "EMA 20/50 alignment, confirmed by ADX strength and the DI lines."

    def __init__(self, fast=20, slow=50, adx_period=14, adx_min=22):
        self.fast, self.slow, self.adx_period, self.adx_min = fast, slow, adx_period, adx_min

    def min_bars(self):
        return max(self.slow, self.adx_period * 2) + 2

    def evaluate(self, bars, ctx=None):
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


class MeanReversion(Strategy):
    name = "meanrev"
    title = "RSI + Bollinger fade"
    markets = "FX in ranges"
    source = "Bollinger (2001); Wilder RSI"
    summary = "Fades RSI extremes outside the Bollinger Bands, only when ADX says the market is ranging."

    def __init__(self, rsi_period=14, oversold=30, overbought=70, bb_period=20, bb_k=2.0, adx_max=20):
        self.rsi_period, self.oversold, self.overbought = rsi_period, oversold, overbought
        self.bb_period, self.bb_k, self.adx_max = bb_period, bb_k, adx_max

    def min_bars(self):
        return max(self.bb_period, self.rsi_period * 2 + 2, 30)

    def evaluate(self, bars, ctx=None):
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


class Breakout(Strategy):
    name = "breakout"
    title = "Donchian breakout"
    markets = "FX, metals, indices"
    source = "Donchian channels; Turtle Traders (1983)"
    summary = "Close through the prior 20-bar high or low, with an ATR push filter."

    def __init__(self, channel=20, atr_period=14, min_atr_push=0.1):
        self.channel, self.atr_period, self.min_atr_push = channel, atr_period, min_atr_push

    def min_bars(self):
        return max(self.channel, self.atr_period) + 2

    def evaluate(self, bars, ctx=None):
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


from .strategies_research import RSI2Pullback, TimeSeriesMomentum, CrossSectionalMomentum, TurnOfMonth, \
    OpeningRangeBreakout, IntradayMomentum  # noqa: E402

STRATEGIES = {cls.name: cls for cls in (TrendFollow, MeanReversion, Breakout, RSI2Pullback, TimeSeriesMomentum,
                                        CrossSectionalMomentum, TurnOfMonth, OpeningRangeBreakout, IntradayMomentum)}


class Ensemble:
    def __init__(self, strategies, weights=None, threshold=0.2, mode="vote"):
        self.strategies = strategies
        self.weights = weights or {}
        self.threshold = threshold
        self.mode = mode           # vote: one combined signal | independent: each strategy trades on its own

    def get(self, name):
        return next((s for s in self.strategies if s.name == name), None)

    @classmethod
    def from_config(cls, cfg):
        strategies = []
        for name, params in cfg.get("strategies", {}).items():
            if params.get("enabled", True):
                kwargs = {k: v for k, v in params.items() if k not in ("enabled", "weight")}
                strategies.append(STRATEGIES[name](**kwargs))
        weights = {n: p.get("weight", 1.0) for n, p in cfg.get("strategies", {}).items()}
        return cls(strategies, weights, cfg.get("signal_threshold", 0.2), cfg.get("strategy_mode", "vote"))

    def min_bars(self):
        return max((s.min_bars() for s in self.strategies), default=0)

    def signals(self, bars, ctx=None):
        """Every strategy's own signal, tagged with its name (used by independent mode)."""
        out = []
        for s in self.strategies:
            sig = s.evaluate(bars, ctx) if len(bars) >= s.min_bars() else Signal()
            sig.tag = sig.tag or s.name
            if sig.side:
                sig.reason = f"{s.name}: {sig.reason}"
            out.append((s, sig))
        return out

    def evaluate(self, bars, ctx=None):
        return self.combine(self.signals(bars, ctx))

    def combine(self, sigs):
        total_w, score, reasons, lead = 0.0, 0.0, [], None
        for s, sig in sigs:
            w = self.weights.get(s.name, 1.0)
            total_w += w
            if sig.side:
                score += w * sig.side * sig.strength
                reasons.append(sig.reason)
                if lead is None or w * sig.strength > lead[0]:
                    lead = (w * sig.strength, sig)
        if not total_w:
            return Signal()
        score /= total_w
        if abs(score) < self.threshold:
            return Signal(FLAT, abs(score), "; ".join(reasons))
        side = BUY if score > 0 else SELL
        out = Signal(side, min(1.0, abs(score)), "; ".join(reasons))
        if lead and lead[1].side == side:      # the strongest agreeing strategy owns the trade and its stops
            ls = lead[1]
            out.tag, out.sl_atr, out.tp_atr, out.sl_dist = ls.tag, ls.sl_atr, ls.tp_atr, ls.sl_dist
        return out
