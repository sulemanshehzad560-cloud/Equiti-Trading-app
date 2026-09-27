from dataclasses import dataclass, field
from datetime import datetime

BUY, SELL, FLAT = 1, -1, 0


@dataclass
class Bar:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class Signal:
    side: int = FLAT          # +1 buy, -1 sell, 0 no trade
    strength: float = 0.0     # 0..1 confidence
    reason: str = ""


@dataclass
class SymbolInfo:
    name: str
    point: float = 0.00001
    digits: int = 5
    tick_size: float = 0.00001
    tick_value: float = 1.0       # account currency per 1 lot per tick
    contract_size: float = 100000
    volume_min: float = 0.01
    volume_max: float = 50.0
    volume_step: float = 0.01
    stops_level: int = 0          # minimum SL/TP distance in points


@dataclass
class Position:
    ticket: int
    symbol: str
    side: int
    volume: float
    entry: float
    sl: float = 0.0
    tp: float = 0.0
    opened: datetime | None = None
    comment: str = ""
    profit: float = 0.0
    price: float = 0.0            # current market price


@dataclass
class OrderRequest:
    symbol: str
    side: int
    volume: float
    sl: float
    tp: float
    comment: str = ""


@dataclass
class NewsAssessment:
    blackout: bool = False
    bias: float = 0.0             # -1 bearish .. +1 bullish for the symbol
    reasons: list = field(default_factory=list)
