"""
parabolic_sar.py — Wilder Parabolic SAR implementation.

Parameters (constraint 4): start=0.02, increment=0.02, max=0.2

Algorithm:
  Uptrend (SAR below price):
    SAR(t) = SAR(t-1) + AF × [EP(t-1) − SAR(t-1)]
    SAR(t) ≤ min(Low(t-1), Low(t-2))
    If Low(t) < SAR(t)  →  reverse to downtrend

  Downtrend (SAR above price):
    SAR(t) = SAR(t-1) − AF × [SAR(t-1) − EP(t-1)]
    SAR(t) ≥ max(High(t-1), High(t-2))
    If High(t) > SAR(t)  →  reverse to uptrend

  Reversal:
    New SAR = prior EP
    New EP  = current period's extreme (High for uptrend, Low for downtrend)
    AF reset to start (0.02)

  EP update:
    New extreme in trend direction → AF += increment (capped at max)

Stop-Loss use (constraint 5):
  CE trade: SL = SAR value (SAR is below price in uptrend, rises with price)
  PE trade: SL = SAR value (SAR is above price in downtrend, falls with price)
  Each candle close: call update_sl(new_sar) — only moves favourably.
"""
from __future__ import annotations
from candle_builder import Candle
from logger_setup import get_module_logger
import config

logger = get_module_logger("SAR")


class ParabolicSAR:
    def __init__(self,
                 start:     float = None,
                 increment: float = None,
                 maximum:   float = None):
        self._start = start     or config.SAR_START
        self._inc   = increment or config.SAR_INCREMENT
        self._max   = maximum   or config.SAR_MAX
        self._reset()

    # ── internal state ────────────────────────────────────────────────────────

    def _reset(self) -> None:
        self._af:       float = self._start
        self._ep:       float = 0.0
        self._sar:      float = 0.0
        self._bullish:  bool  = True
        self._ready:    bool  = False
        self._highs:    list[float] = []
        self._lows:     list[float] = []

    # ── public properties ─────────────────────────────────────────────────────

    @property
    def value(self) -> float:
        return round(self._sar, 2)

    @property
    def is_bullish(self) -> bool:
        """SAR is BELOW price → uptrend → supports CE entry."""
        return self._bullish

    @property
    def is_bearish(self) -> bool:
        return not self._bullish

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def af(self) -> float:
        return round(self._af, 4)

    # ── update ────────────────────────────────────────────────────────────────

    def update(self, candle: Candle) -> tuple[float | None, bool | None]:
        """
        Feed one completed 5-minute candle.
        Returns (sar_value, is_bullish).
        Returns (None, None) while seeding.
        """
        h, l = candle.high, candle.low

	    ###Trading View SAR changes
        prior_lows  = list(self._lows[-2:])    # [Low(t-2), Low(t-1)]
        prior_highs = list(self._highs[-2:])   # [High(t-2), High(t-1)]

        self._highs.append(h)
        self._lows.append(l)
        if len(self._highs) > 3:
            self._highs.pop(0)
            self._lows.pop(0)

        # Need at least 2 periods to initialise
        if len(self._highs) < 2:
            return None, None

        if not self._ready:
            # Initialise: determine trend from first two candles
            if candle.close >= self._highs[-2]:
                self._bullish = True
                self._sar = min(self._lows)
                self._ep  = max(self._highs)
            else:
                self._bullish = False
                self._sar = max(self._highs)
                self._ep  = min(self._lows)
            self._af    = self._start
            self._ready = True
            return round(self._sar, 2), self._bullish

        prev_sar = self._sar
        prev_ep  = self._ep
        prev_af  = self._af

        if self._bullish:
            # ── Uptrend ───────────────────────────────────────────────────────
            new_sar = prev_sar + prev_af * (prev_ep - prev_sar)
            # Constraint: SAR cannot exceed prior two lows
	    # Trading View SAR
            #if len(self._lows) >= 2:
            #    new_sar = min(new_sar, self._lows[-2], self._lows[-1])
            #else:
            #    new_sar = min(new_sar, self._lows[-1])
            if len(prior_lows) >= 2:
                    new_sar = min(new_sar, prior_lows[-1], prior_lows[-2])
            elif len(prior_lows) == 1:
                new_sar = min(new_sar, prior_lows[-1])

            if l < new_sar:                          # price crossed SAR → reverse
                self._bullish = False
                self._sar     = prev_ep              # new SAR = prior highest high
                self._ep      = l
                self._af      = self._start
                logger.debug(f"SAR ↓ REVERSED to BEARISH  sar={self._sar:.2f}")
            else:
                self._sar = new_sar
                if h > prev_ep:
                    self._ep = h
                    self._af = min(prev_af + self._inc, self._max)
        else:
            # ── Downtrend ─────────────────────────────────────────────────────
            new_sar = prev_sar - prev_af * (prev_sar - prev_ep)
            # Constraint: SAR cannot go below prior two highs
	    # Trading View SAR
            #if len(self._highs) >= 2:
            #    new_sar = max(new_sar, self._highs[-2], self._highs[-1])
            #else:
            #    new_sar = max(new_sar, self._highs[-1])
            if len(prior_highs) >= 2:
                new_sar = max(new_sar, prior_highs[-1], prior_highs[-2])
            elif len(prior_highs) == 1:
                new_sar = max(new_sar, prior_highs[-1])

            if h > new_sar:                          # price crossed SAR → reverse
                self._bullish = True
                self._sar     = prev_ep              # new SAR = prior lowest low
                self._ep      = h
                self._af      = self._start
                logger.debug(f"SAR ↑ REVERSED to BULLISH  sar={self._sar:.2f}")
            else:
                self._sar = new_sar
                if l < prev_ep:
                    self._ep = l
                    self._af = min(prev_af + self._inc, self._max)

        logger.debug(
            f"SAR {'▲' if self._bullish else '▼'}  "
            f"sar={self._sar:.2f}  ep={self._ep:.2f}  af={self._af:.4f}"
        )
        return round(self._sar, 2), self._bullish

    def sl_hit(self, spot: float) -> bool:
        """True when price crosses the trailing SAR stop."""
        if not self._ready:
            return False
        return spot < self._sar if self._bullish else spot > self._sar

    def to_dict(self) -> dict:
        return {
            "value":   self.value,
            "bullish": self._bullish,
            "af":      self.af,
            "ep":      round(self._ep, 2),
            "ready":   self._ready,
        }
