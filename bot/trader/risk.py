"""Position sizing and account-level safety limits."""
import math


class RiskManager:
    def __init__(self, cfg):
        self.risk_pct = cfg.get("risk_per_trade_pct", 0.5)
        self.max_open = cfg.get("max_open_positions", 3)
        self.max_per_symbol = cfg.get("max_positions_per_symbol", 1)
        self.max_daily_loss_pct = cfg.get("max_daily_loss_pct", 3.0)
        self.max_spread_points = cfg.get("max_spread_points", 30)
        self.max_spread_pct = cfg.get("max_spread_pct")          # handy for shares: 0.15 = 0.15% of price
        self.sizing = cfg.get("sizing", "risk")                    # risk: size from the stop | allocation: fixed % of equity
        self.allocation_pct = cfg.get("allocation_pct", 20)         # position value per trade when sizing=allocation
        self.max_notional_pct = cfg.get("max_notional_pct")       # optional leverage cap: position value as % of equity
        self.sl_atr = cfg.get("sl_atr_mult", 1.5)
        self.tp_atr = cfg.get("tp_atr_mult", 3.0)
        self.trail_atr = cfg.get("trail_atr_mult", 0.0)
        self.max_lots = cfg.get("max_lots_per_trade", 1.0)
        self._day, self._day_start_equity = None, None

    def daily_halt(self, equity, now):
        """True once today's loss reaches the limit. Resets at 00:00 UTC."""
        day = now.date()
        if day != self._day:
            self._day, self._day_start_equity = day, equity
        if not self._day_start_equity:
            return False
        loss_pct = (self._day_start_equity - equity) / self._day_start_equity * 100
        return loss_pct >= self.max_daily_loss_pct

    def stops(self, side, price, atr, info, sl_atr=None, tp_atr=None, sl_dist=None):
        """Stop and target prices. A strategy may override the defaults: its own ATR multiples,
        an explicit stop distance, or tp_atr=0 for no target (it exits by its own rule)."""
        min_dist = info.stops_level * info.point
        sl_atr = self.sl_atr if sl_atr is None else sl_atr
        tp_atr = self.tp_atr if tp_atr is None else tp_atr
        sl_d = max(sl_dist if sl_dist else atr * sl_atr, min_dist, info.point)
        sl = price - side * sl_d
        tp = price + side * max(atr * tp_atr, min_dist) if tp_atr > 0 else 0.0
        return round(sl, info.digits), (round(tp, info.digits) if tp else 0.0), sl_d

    def volume(self, equity, sl_distance, info, multiplier=1.0, price=None):
        """Lots so that hitting the stop loses risk_per_trade_pct of equity (times multiplier),
        capped so the position's value never exceeds max_notional_pct of equity."""
        if sl_distance <= 0 or info.tick_size <= 0 or info.tick_value <= 0:
            return 0.0
        if self.sizing == "allocation" and price:
            lots = equity * self.allocation_pct / 100 * multiplier / (price / info.tick_size * info.tick_value)
        else:
            risk_money = equity * self.risk_pct / 100 * multiplier
            loss_per_lot = sl_distance / info.tick_size * info.tick_value
            lots = risk_money / loss_per_lot
        if price and self.max_notional_pct:
            value_per_lot = price / info.tick_size * info.tick_value
            lots = min(lots, equity * self.max_notional_pct / 100 / value_per_lot)
        lots = min(lots, self.max_lots, info.volume_max)
        lots = math.floor(lots / info.volume_step + 1e-9) * info.volume_step
        if lots < info.volume_min:
            return 0.0          # the stop is too wide for the account - skip rather than over-risk
        return round(lots, 8)
