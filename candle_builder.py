"""candle_builder.py — assembles 5-minute OHLCV candles from 30-second ticks."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
import config
from logger_setup import get_module_logger

logger = get_module_logger("Candle")


@dataclass
class Candle:
    timestamp:  datetime
    open_price: float
    high:       float = 0.0
    low:        float = float("inf")
    close:      float = 0.0
    volume:     int   = 0
    ticks:      int   = 0

    def update(self, price: float, volume: int = 0) -> None:
        if self.high == 0.0:
            self.high = price
        self.high   = max(self.high, price)
        self.low    = min(self.low,  price)
        self.close  = price
        self.volume += volume
        self.ticks  += 1

    def is_bullish(self) -> bool:
        return self.close > self.open_price

    def is_bearish(self) -> bool:
        return self.close < self.open_price

    def body(self) -> float:
        return abs(self.close - self.open_price)

    def candle_range(self) -> float:
        return self.high - self.low

    def upper_wick(self) -> float:
        return self.high - max(self.close, self.open_price)

    def lower_wick(self) -> float:
        return min(self.close, self.open_price) - self.low

    def to_dict(self) -> dict:
        return {
            "time":   self.timestamp.strftime("%H:%M"),
            "open":   round(self.open_price, 2),
            "high":   round(self.high, 2),
            "low":    round(self.low,  2),
            "close":  round(self.close, 2),
            "volume": self.volume,
            "bull":   self.is_bullish(),
        }


class CandleBuilder:
    def __init__(self, instrument: str, tf_minutes: int = 5):
        self.instrument   = instrument
        self.tf           = tf_minutes
        self._current: Candle | None    = None
        self._completed: list[Candle]   = []

    def _slot(self, ts: datetime) -> datetime:
        m = (ts.minute // self.tf) * self.tf
        return ts.replace(minute=m, second=0, microsecond=0)

    def update(self, price: float, volume: int = 0) -> Candle | None:
        """Feed one tick. Returns completed Candle if a new candle started."""
        now   = datetime.now()
        slot  = self._slot(now)
        done  = None

        if self._current is None:
            self._current = Candle(timestamp=slot, open_price=price)
        elif slot > self._current.timestamp:
            done = self._current
            self._completed.append(done)
            logger.debug(
                f"{self.instrument} candle {done.timestamp.strftime('%H:%M')}  "
                f"O={done.open_price:.2f} H={done.high:.2f} "
                f"L={done.low:.2f} C={done.close:.2f}"
            )
            self._current = Candle(timestamp=slot, open_price=price)

        self._current.update(price, volume)
        return done

    def get_last_n(self, n: int) -> list[Candle]:
        return self._completed[-n:]

    def current(self) -> Candle | None:
        return self._current

    def count(self) -> int:
        return len(self._completed)

    def has_min(self, n: int) -> bool:
        return len(self._completed) >= n
