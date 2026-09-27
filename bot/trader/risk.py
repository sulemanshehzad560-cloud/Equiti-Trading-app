"""Position sizing and account-level safety limits."""
import math


class RiskManager:
    def __init__(self, cfg):
        self.risk_pct = cfg.get("risk_per_trade_pct", 0.5)
        self.max_open = cfg.get("max_open_positions", 3)
        self.max_per_symbol = cfg.get("max_positions_per_symbol", 1)
        self.max_daily_loss_pct = cfg.get("max_daily_loss_pct", 3.0)
        self.max_spread_points = cfg.get("max_spread_points", 30)
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

    def stops(self, side, price, atr, info):
        min_dist = info.stops_level * info.point
        sl_d, tp_d = max(atr * self.sl_atr, min_dist), max(atr * self.tp_atr, min_dist)
        sl = price - side * sl_d
        tp = price + side * tp_d if self.tp_atr > 0 else 0.0
        return round(sl, info.digits), (round(tp, info.digits) if tp else 0.0), sl_d

    def volume(self, equity, sl_distance, info, multiplier=1.0):
        """Lots so that hitting the stop loses risk_per_trade_pct of equity (times multiplier)."""
        if sl_distance <= 0 or info.tick_size <= 0 or info.tick_value <= 0:
            return 0.0
        risk_money = equity * self.risk_pct / 100 * multiplier
        loss_per_lot = sl_distance / info.tick_size * info.tick_value
        lots = risk_money / loss_per_lot
        lots = min(lots, self.max_lots, info.volume_max)
        lots = math.floor(lots / info.volume_step + 1e-9) * info.volume_step
        if lots < info.volume_min:
            return 0.0          # the stop is too wide for the account - skip rather than over-risk
        return round(lots, 2)
