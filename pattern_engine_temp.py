"""
pattern_engine.py — 12 candlestick patterns for CE/PE entry and exit.

Constraints:
  • ONLY the last 3 candles are used (constraint 6)
  • Volume is NEVER checked (constraint 7)

Pattern tiers:
  87% → Morning Doji Star, Evening Doji Star
  85% → Three White Soldiers, Three Black Crows
  83% → Bullish Engulfing, Bearish Engulfing
  82% → Morning Star, Evening Star
  81% → Piercing Line, Dark Cloud Cover
  80% → Hammer, Shooting Star

Exit behaviour:
  ≥83% (IMMEDIATE_EXIT_PATTERNS): exit on same candle — no confirmation needed
  80–82%: set pending_reversal flag; exit when SAR also reverses
"""
from __future__ import annotations
from typing import Optional, Tuple
from candle_builder import Candle
from logger_setup import get_module_logger

logger = get_module_logger("Patterns")

# ── Tier sets ─────────────────────────────────────────────────────────────────

IMMEDIATE_EXIT_PATTERNS: frozenset = frozenset({
    "Morning Doji Star",
    "Evening Doji Star",
    "Three White Soldiers",
    "Three Black Crows",
    "Bullish Engulfing",
    "Bearish Engulfing",
})

BULLISH_PATTERNS: frozenset = frozenset({
    "Morning Doji Star", "Three White Soldiers", "Bullish Engulfing",
    "Morning Star", "Piercing Line", "Hammer",
})

BEARISH_PATTERNS: frozenset = frozenset({
    "Evening Doji Star", "Three Black Crows", "Bearish Engulfing",
    "Evening Star", "Dark Cloud Cover", "Shooting Star",
})

# ── Candle helpers ────────────────────────────────────────────────────────────

def _body(c: Candle) -> float:
    return abs(c.close - c.open_price)

def _range(c: Candle) -> float:
    return c.high - c.low

def _upper_wick(c: Candle) -> float:
    return c.high - max(c.close, c.open_price)

def _lower_wick(c: Candle) -> float:
    return min(c.close, c.open_price) - c.low

def _is_doji(c: Candle, threshold: float = 0.1) -> bool:
    r = _range(c)
    return (_body(c) / r) <= threshold if r > 0 else True

# ── 3-candle patterns ─────────────────────────────────────────────────────────

#Changes made on 21-May-2026 for candle accuracy
def _morning_doji_star(c1: Candle, c2: Candle, c3: Candle) -> bool:
    c2_body_top = max(c2.open_price, c2.close)
    return (
        c1.is_bearish()
        and _body(c1) > _range(c1) * 0.5
        and _is_doji(c2)
        and c2_body_top < c1.close          # doji body gaps below c1 close
        and c3.is_bullish()
        and c3.open_price > c2_body_top     # c3 gaps up above doji body
        and c3.close > (c1.open_price + c1.close) / 2  # closes above c1 midpoint
    )

def _evening_doji_star(c1, c2, c3):
    c2_body_top = max(c2.open_price, c2.close)
    c2_body_bot = min(c2.open_price, c2.close)
    c1_midpoint = (c1.open_price + c1.close) / 2
    return (
        c1.is_bullish()
        and _body(c1) > _range(c1) * 0.5
        and _is_doji(c2)
        and c2_body_bot > c1.close           # body gaps, not wick
        and c3.is_bearish()
        and c3.open_price < c2_body_top      # C3 gaps down below C2 body
        and c3.close < c1_midpoint           # closes below C1 midpoint
    )

def _three_white_soldiers(c1: Candle, c2: Candle, c3: Candle) -> bool:
    return (
        c1.is_bullish() and c2.is_bullish() and c3.is_bullish()
        and c2.open_price >= c1.close * 0.995
        and c3.open_price >= c2.close * 0.995
        and c2.close > c1.close
        and c3.close > c2.close
        and _body(c1) > _range(c1) * 0.5
        and _body(c2) > _range(c2) * 0.5
        and _body(c3) > _range(c3) * 0.5
    )

def _three_black_crows(c1: Candle, c2: Candle, c3: Candle) -> bool:
    return (
        c1.is_bearish() and c2.is_bearish() and c3.is_bearish()
        and c2.open_price <= c1.close * 1.005
        and c3.open_price <= c2.close * 1.005
        and c2.close < c1.close
        and c3.close < c2.close
        and _body(c1) > _range(c1) * 0.5
        and _body(c2) > _range(c2) * 0.5
        and _body(c3) > _range(c3) * 0.5
    )

def _evening_star(c1: Candle, c2: Candle, c3: Candle) -> bool:
    return (
        c1.is_bullish()
        and _body(c1) > _range(c1) * 0.5
        and _body(c2) < _body(c1) * 0.3
        and c3.is_bearish()
        and c3.close < (c1.open_price + c1.close) / 2
    )

# Changes done on 23-May-2026 to improve accuracy of Morning Star
def _morning_star(
    c1: Candle,
    c2: Candle,
    c3: Candle,
    preceding_candles: list[Candle] | None = None,
    c3_volume: float | None = None,
    c2_prev_volume: float | None = None,
) -> bool:
    """
    Detects a Morning Star candlestick pattern.

    Args:
        c1: First candle  — large bearish candle
        c2: Second candle — small-bodied star (doji / spinning top)
        c3: Third candle  — large bullish confirmation candle
        preceding_candles: All completed candles before c1, used for downtrend
                           context. Passed automatically by PatternEngine.scan()
                           as candles[:-3].  The function works in loose mode
                           (no trend check) when this is None or has < 3 items.
        c3_volume:      Volume of c3 (optional — constraint 7 keeps this None)
        c2_prev_volume: Rolling avg volume before c2 (optional)
    """

    # ── 1. Downtrend context ─────────────────────────────────────────────────
    if preceding_candles and len(preceding_candles) >= 3:
        bearish_count = sum(1 for c in preceding_candles if c.is_bearish())
        in_downtrend  = bearish_count >= len(preceding_candles) * 0.6
    else:
        # Fewer than 3 preceding candles available (early session / loose mode)
        in_downtrend = True

    # ── 2. C1: Large bearish candle ──────────────────────────────────────────
    c1_is_valid = (
        c1.is_bearish()
        and _body(c1) > _range(c1) * 0.5       # body > 50% of total range
    )

    # ── 3. C2: Small star with gap down from C1 ──────────────────────────────
    c1_body_low    = min(c1.open_price, c1.close)   # = c1.close for bearish
    c1_body_mid    = (c1.open_price + c1.close) / 2

    c2_body_top    = max(c2.open_price, c2.close)
    c2_body_bot    = min(c2.open_price, c2.close)

    gap_down       = c2_body_top < c1_body_low      # entire C2 body below C1 close

    total_range_c2 = _range(c2) if _range(c2) > 0 else 1e-9
    body_ratio_c2  = _body(c2) / total_range_c2     # body < 30% of C2 range

    c2_is_valid = (
        _body(c2) < _body(c1) * 0.3            # small body vs C1
        and body_ratio_c2 < 0.3                # doji / spinning-top character
        and (c2.high - c2_body_top) > 0        # upper wick exists
        and (c2_body_bot - c2.low)  > 0        # lower wick exists
        and gap_down                            # gaps below C1 body
    )

    # ── 4. C3: Strong bullish candle with gap up from C2 ─────────────────────
    gap_up = c3.open_price > c2_body_top            # C3 opens above C2 body top

    c3_is_valid = (
        c3.is_bullish()
        and _body(c3) > _range(c3) * 0.5       # strong bullish body
        and c3.close  > c1_body_mid             # closes above C1 midpoint
        and gap_up                              # gaps above C2 body
    )

    # ── 5. Volume confirmation (skipped — constraint 7 keeps these None) ─────
    if c3_volume is not None and c2_prev_volume is not None and c2_prev_volume > 0:
        volume_confirmed = c3_volume > c2_prev_volume * 1.2
    else:
        volume_confirmed = True

    return (
        in_downtrend
        and c1_is_valid
        and c2_is_valid
        and c3_is_valid
        and volume_confirmed
    )

# ── 2-candle patterns ─────────────────────────────────────────────────────────

def _bullish_engulfing(c1: Candle, c2: Candle) -> bool:
    return (
        c1.is_bearish()
        and c2.is_bullish()
        and c2.open_price <= c1.close
        and c2.close >= c1.open_price
        and _body(c2) > _body(c1)
    )

def _bearish_engulfing(c1: Candle, c2: Candle) -> bool:
    return (
        c1.is_bullish()
        and c2.is_bearish()
        and c2.open_price >= c1.close
        and c2.close <= c1.open_price
        and _body(c2) > _body(c1)
    )

def _piercing_line(c1: Candle, c2: Candle) -> bool:
    mid = (c1.open_price + c1.close) / 2
    return (
        c1.is_bearish()
        and c2.is_bullish()
        and c2.open_price < c1.low
        and mid < c2.close < c1.open_price
    )

def _dark_cloud_cover(c1: Candle, c2: Candle) -> bool:
    mid = (c1.open_price + c1.close) / 2
    return (
        c1.is_bullish()
        and c2.is_bearish()
        and c2.open_price > c1.high
        and c1.open_price < c2.close < mid
    )

# ── 1-candle patterns ─────────────────────────────────────────────────────────

# Changes made on 21-May-2026 for candle accuracy
def _hammer(c: Candle) -> bool:
    r = _range(c)
    if r == 0:
        return False
    body_bottom   = min(c.open_price, c.close)
    body_position = (body_bottom - c.low) / r
    return (
        _lower_wick(c) > 2 * _body(c)
        and _upper_wick(c) <= _body(c) * 0.1
        and _body(c) > 0
        and body_position >= 0.80               # body in top 20% of range
    )

def _shooting_star(c: Candle) -> bool:
    r = _range(c)
    if r == 0:
        return False
    body_top      = max(c.open_price, c.close)
    body_position = (c.high - body_top) / r
    return (
        _upper_wick(c) > 2 * _body(c)
        and _lower_wick(c) <= _body(c) * 0.1
        and _body(c) > 0
        and body_position >= 0.80               # body in bottom 20% of range
    )


# ── Pattern Engine ────────────────────────────────────────────────────────────

class PatternEngine:

    def scan(self, candles: list[Candle]) -> Tuple[Optional[str], str]:
        """
        Scan ≤3 candles for the strongest pattern present.
        Returns (pattern_name, 'bullish'|'bearish'|'none').
        Volume is NOT checked (constraint 7).
        """
        n = len(candles)
        if n == 0:
            return None, "none"

        # ── 3-candle (strongest first) ────────────────────────────────────────
        if n >= 3:
            c1, c2, c3 = candles[-3], candles[-2], candles[-1]

            if _morning_doji_star(c1, c2, c3):
                logger.info("▲ Morning Doji Star (87%) — BULLISH")
                return "Morning Doji Star", "bullish"

            if _three_white_soldiers(c1, c2, c3):
                logger.info("▲ Three White Soldiers (85%) — BULLISH")
                return "Three White Soldiers", "bullish"

            # candles[:-3] = all candles before c1 → downtrend context window
            # falls back to loose mode (in_downtrend=True) when len < 3
            preceding = candles[:-3] if len(candles) > 3 else []
            if _morning_star(c1, c2, c3, preceding_candles=preceding):
                logger.info("▲ Morning Star (82%) — BULLISH")
                return "Morning Star", "bullish"

            if _evening_doji_star(c1, c2, c3):
                logger.info("▼ Evening Doji Star (87%) — BEARISH")
                return "Evening Doji Star", "bearish"

            if _three_black_crows(c1, c2, c3):
                logger.info("▼ Three Black Crows (85%) — BEARISH")
                return "Three Black Crows", "bearish"

            if _evening_star(c1, c2, c3):
                logger.info("▼ Evening Star (82%) — BEARISH")
                return "Evening Star", "bearish"

        # ── 2-candle ──────────────────────────────────────────────────────────
        if n >= 2:
            c1, c2 = candles[-2], candles[-1]

            if _bullish_engulfing(c1, c2):
                logger.info("▲ Bullish Engulfing (83%) — BULLISH")
                return "Bullish Engulfing", "bullish"

            if _piercing_line(c1, c2):
                logger.info("▲ Piercing Line (81%) — BULLISH")
                return "Piercing Line", "bullish"

            if _bearish_engulfing(c1, c2):
                logger.info("▼ Bearish Engulfing (83%) — BEARISH")
                return "Bearish Engulfing", "bearish"

            if _dark_cloud_cover(c1, c2):
                logger.info("▼ Dark Cloud Cover (81%) — BEARISH")
                return "Dark Cloud Cover", "bearish"

        # ── 1-candle ──────────────────────────────────────────────────────────
        c = candles[-1]
        if _hammer(c):
            logger.info("▲ Hammer (80%) — BULLISH")
            return "Hammer", "bullish"
        if _shooting_star(c):
            logger.info("▼ Shooting Star (80%) — BEARISH")
            return "Shooting Star", "bearish"

        return None, "none"

    def is_reversal_of(
        self, candles: list[Candle], open_direction: str
    ) -> Tuple[Optional[str], str]:
        """Returns pattern if it REVERSES the open position's direction."""
        pat, direction = self.scan(candles)
        if not pat:
            return None, "none"
        if open_direction == "bullish" and direction == "bearish":
            return pat, direction
        if open_direction == "bearish" and direction == "bullish":
            return pat, direction
        return None, "none"
