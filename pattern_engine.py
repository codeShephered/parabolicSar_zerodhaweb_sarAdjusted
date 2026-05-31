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

# def _morning_doji_star(c1: Candle, c2: Candle, c3: Candle) -> bool:
#     return (
#         c1.is_bearish()
#         and _body(c1) > _range(c1) * 0.5
#         and _is_doji(c2)
#         and c2.high < c1.close
#         and c3.is_bullish()
#         and c3.close > (c1.open_price + c1.close) / 2
#     )

# def _evening_doji_star(c1: Candle, c2: Candle, c3: Candle) -> bool:
#     return (
#         c1.is_bullish()
#         and _body(c1) > _range(c1) * 0.5
#         and _is_doji(c2)
#         and c2.low > c1.close
#         and c3.is_bearish()
#         and c3.close < (c1.open_price + c1.close) / 2
#     )

#Changes made on 21-May-2026 for candle accuracy
# def _morning_doji_star(c1: Candle, c2: Candle, c3: Candle) -> bool:
#     c2_body_top = max(c2.open_price, c2.close)
#     #c2_body_bot = min(c2.open_price, c2.close)
#     return (
#         c1.is_bearish()
#         and _body(c1) > _range(c1) * 0.5
#         and _is_doji(c2)
#         and c2_body_top < c1.close          # doji body gaps below c1 close
#         and c3.is_bullish()
#         and c3.open_price > c2_body_top     # c3 gaps up above doji body
#         and c3.close > (c1.open_price + c1.close) / 2  # closes above c1 midpoint
#     )

# def _evening_doji_star(c1, c2, c3):
#     c2_body_top = max(c2.open_price, c2.close)
#     c2_body_bot = min(c2.open_price, c2.close)
#     c1_midpoint = (c1.open_price + c1.close) / 2
#     return (
#         c1.is_bullish()
#         and _body(c1) > _range(c1) * 0.5
#         and _is_doji(c2)
#         and c2_body_bot > c1.close           # ← body gaps, not wick
#         and c3.is_bearish()
#         and c3.open_price < c2_body_top      # ← C3 gaps down below C2 body
#         and c3.close < c1_midpoint           # ← closes below C1 midpoint
#     )
#Changes made on 23-May-2026 for more accuracy and bug fix
def _is_doji(c: Candle, threshold: float = 0.1) -> bool:
    """
    True doji: body ≤ 10% of range AND wicks exist on both sides.
    Rejects flat-line candles (range == 0).
    """
    r = _range(c)
    if r == 0:
        return False                            # flat line is NOT a doji ✅
    body_ratio   = _body(c) / r
    upper_wick   = c.high - max(c.open_price, c.close)
    lower_wick   = min(c.open_price, c.close) - c.low
    return (
        body_ratio <= threshold                 # body ≤ 10% of range
        and upper_wick > 0                      # upper wick must exist
        and lower_wick > 0                      # lower wick must exist
    )


# Changes done on 23-May-2026 — accurate Morning Doji Star
def _morning_doji_star(
    c1: Candle,
    c2: Candle,
    c3: Candle,
    preceding_candles: list[Candle] | None = None,
) -> bool:
    """
    Morning Doji Star — bullish reversal at bottom of downtrend.

    C1: Large bearish candle
    C2: True doji gapping below C1's close
    C3: Large bullish candle gapping above C2, closing above C1 midpoint
    """

    # ── 1. Downtrend context ─────────────────────────────────────────────────
    if preceding_candles and len(preceding_candles) >= 3:
        bearish_count = sum(1 for c in preceding_candles if c.is_bearish())
        in_downtrend  = bearish_count >= len(preceding_candles) * 0.6
    else:
        in_downtrend  = True                    # loose mode — early session

    # ── 2. C1: Large bearish candle ──────────────────────────────────────────
    c1_is_valid = (
        c1.is_bearish()
        and _body(c1) > _range(c1) * 0.5
    )

    # ── 3. C2: True doji gapping below C1 ───────────────────────────────────
    c1_body_low  = min(c1.open_price, c1.close)     # = c1.close for bearish
    c1_body_mid  = (c1.open_price + c1.close) / 2

    c2_body_top  = max(c2.open_price, c2.close)
    c2_body_bot  = min(c2.open_price, c2.close)

    c2_is_valid = (
        _is_doji(c2)                                # true doji with wicks
        and c2_body_top < c1_body_low               # entire C2 body gaps below C1
    )

    # ── 4. C3: Strong bullish candle gapping up from C2 ─────────────────────
    c3_is_valid = (
        c3.is_bullish()
        and _body(c3) > _range(c3) * 0.5           # strong bullish body
        and c3.open_price > c2_body_top             # gaps up above C2 body
        and c3.close > c1_body_mid                  # closes above C1 midpoint
    )

    return (
        in_downtrend
        and c1_is_valid
        and c2_is_valid
        and c3_is_valid
    )


# Changes done on 23-May-2026 — accurate Evening Doji Star
def _evening_doji_star(
    c1: Candle,
    c2: Candle,
    c3: Candle,
    preceding_candles: list[Candle] | None = None,
) -> bool:
    """
    Evening Doji Star — bearish reversal at top of uptrend.

    C1: Large bullish candle
    C2: True doji gapping above C1's close
    C3: Large bearish candle gapping below C2, closing below C1 midpoint
    """

    # ── 1. Uptrend context ───────────────────────────────────────────────────
    if preceding_candles and len(preceding_candles) >= 3:
        bullish_count = sum(1 for c in preceding_candles if c.is_bullish())
        in_uptrend    = bullish_count >= len(preceding_candles) * 0.6
    else:
        in_uptrend    = True                    # loose mode — early session

    # ── 2. C1: Large bullish candle ──────────────────────────────────────────
    c1_is_valid = (
        c1.is_bullish()
        and _body(c1) > _range(c1) * 0.5
    )

    # ── 3. C2: True doji gapping above C1 ───────────────────────────────────
    c1_body_high = max(c1.open_price, c1.close)     # = c1.close for bullish
    c1_body_mid  = (c1.open_price + c1.close) / 2

    c2_body_top  = max(c2.open_price, c2.close)
    c2_body_bot  = min(c2.open_price, c2.close)

    c2_is_valid = (
        _is_doji(c2)                                # true doji with wicks
        and c2_body_bot > c1_body_high              # entire C2 body gaps above C1
    )

    # ── 4. C3: Strong bearish candle gapping down from C2 ───────────────────
    c3_is_valid = (
        c3.is_bearish()
        and _body(c3) > _range(c3) * 0.5           # strong bearish body
        and c3.open_price < c2_body_bot             # ✅ gaps DOWN below C2 body bottom
        and c3.close < c1_body_mid                  # closes below C1 midpoint
    )

    return (
        in_uptrend
        and c1_is_valid
        and c2_is_valid
        and c3_is_valid
    )

# def _three_white_soldiers(c1: Candle, c2: Candle, c3: Candle) -> bool:
#     return (
#         c1.is_bullish() and c2.is_bullish() and c3.is_bullish()
#         and c2.open_price >= c1.close * 0.995
#         and c3.open_price >= c2.close * 0.995
#         and c2.close > c1.close
#         and c3.close > c2.close
#         and _body(c1) > _range(c1) * 0.5
#         and _body(c2) > _range(c2) * 0.5
#         and _body(c3) > _range(c3) * 0.5
#     )

# def _three_black_crows(c1: Candle, c2: Candle, c3: Candle) -> bool:
#     return (
#         c1.is_bearish() and c2.is_bearish() and c3.is_bearish()
#         and c2.open_price <= c1.close * 1.005
#         and c3.open_price <= c2.close * 1.005
#         and c2.close < c1.close
#         and c3.close < c2.close
#         and _body(c1) > _range(c1) * 0.5
#         and _body(c2) > _range(c2) * 0.5
#         and _body(c3) > _range(c3) * 0.5
#     )

# Changes made on 23-May-2026 for more accuracy
# Changes done on 23-May-2026 — accurate Three White Soldiers
def _three_white_soldiers(
    c1: Candle,
    c2: Candle,
    c3: Candle,
    preceding_candles: list[Candle] | None = None,
) -> bool:
    """
    Three White Soldiers — strong bullish reversal after a downtrend.

    Each candle:
      • Opens within the prior candle's body (not above its close)
      • Closes progressively higher
      • Has a strong body (> 50% of range)
      • Has a small upper wick (close near high — no selling pressure)
      • Body is at least 80% of prior candle's body (no shrinking momentum)
    """

    # ── 1. Downtrend context ─────────────────────────────────────────────────
    if preceding_candles and len(preceding_candles) >= 3:
        bearish_count = sum(1 for c in preceding_candles if c.is_bearish())
        in_downtrend  = bearish_count >= len(preceding_candles) * 0.6
    else:
        in_downtrend  = True                        # loose mode — early session

    # ── 2. All three bullish ─────────────────────────────────────────────────
    all_bullish = c1.is_bullish() and c2.is_bullish() and c3.is_bullish()

    # ── 3. Each opens WITHIN prior candle's body ─────────────────────────────
    # For bullish candle: body runs from open_price (bottom) to close (top)
    # C2 must open above C1's open AND at or below C1's close
    c2_opens_in_c1_body = c1.open_price <= c2.open_price <= c1.close
    c3_opens_in_c2_body = c2.open_price <= c3.open_price <= c2.close

    # ── 4. Progressive higher closes ─────────────────────────────────────────
    progressive_closes = c2.close > c1.close and c3.close > c2.close

    # ── 5. Strong bodies on all three ────────────────────────────────────────
    strong_bodies = (
        _body(c1) > _range(c1) * 0.5
        and _body(c2) > _range(c2) * 0.5
        and _body(c3) > _range(c3) * 0.5
    )

    # ── 6. Small upper wicks — close near high, no selling pressure ──────────
    small_upper_wicks = (
        _upper_wick(c1) <= _body(c1) * 0.25
        and _upper_wick(c2) <= _body(c2) * 0.25
        and _upper_wick(c3) <= _body(c3) * 0.25
    )

    # ── 7. Non-shrinking bodies — no weakening momentum ──────────────────────
    progressive_bodies = (
        _body(c2) >= _body(c1) * 0.8
        and _body(c3) >= _body(c2) * 0.8
    )

    return (
        in_downtrend
        and all_bullish
        and c2_opens_in_c1_body
        and c3_opens_in_c2_body
        and progressive_closes
        and strong_bodies
        and small_upper_wicks
        and progressive_bodies
    )


# Changes done on 23-May-2026 — accurate Three Black Crows
def _three_black_crows(
    c1: Candle,
    c2: Candle,
    c3: Candle,
    preceding_candles: list[Candle] | None = None,
) -> bool:
    """
    Three Black Crows — strong bearish reversal after an uptrend.

    Each candle:
      • Opens within the prior candle's body (not below its close)
      • Closes progressively lower
      • Has a strong body (> 50% of range)
      • Has a small lower wick (close near low — no buying support)
      • Body is at least 80% of prior candle's body (no shrinking momentum)
    """

    # ── 1. Uptrend context ───────────────────────────────────────────────────
    if preceding_candles and len(preceding_candles) >= 3:
        bullish_count = sum(1 for c in preceding_candles if c.is_bullish())
        in_uptrend    = bullish_count >= len(preceding_candles) * 0.6
    else:
        in_uptrend    = True                        # loose mode — early session

    # ── 2. All three bearish ─────────────────────────────────────────────────
    all_bearish = c1.is_bearish() and c2.is_bearish() and c3.is_bearish()

    # ── 3. Each opens WITHIN prior candle's body ─────────────────────────────
    # For bearish candle: body runs from open_price (top) to close (bottom)
    # C2 must open below C1's open AND at or above C1's close
    c2_opens_in_c1_body = c1.close <= c2.open_price <= c1.open_price
    c3_opens_in_c2_body = c2.close <= c3.open_price <= c2.open_price

    # ── 4. Progressive lower closes ──────────────────────────────────────────
    progressive_closes = c2.close < c1.close and c3.close < c2.close

    # ── 5. Strong bodies on all three ────────────────────────────────────────
    strong_bodies = (
        _body(c1) > _range(c1) * 0.5
        and _body(c2) > _range(c2) * 0.5
        and _body(c3) > _range(c3) * 0.5
    )

    # ── 6. Small lower wicks — close near low, no buying support ─────────────
    small_lower_wicks = (
        _lower_wick(c1) <= _body(c1) * 0.25
        and _lower_wick(c2) <= _body(c2) * 0.25
        and _lower_wick(c3) <= _body(c3) * 0.25
    )

    # ── 7. Non-shrinking bodies — no weakening momentum ──────────────────────
    progressive_bodies = (
        _body(c2) >= _body(c1) * 0.8
        and _body(c3) >= _body(c2) * 0.8
    )

    return (
        in_uptrend
        and all_bearish
        and c2_opens_in_c1_body
        and c3_opens_in_c2_body
        and progressive_closes
        and strong_bodies
        and small_lower_wicks
        and progressive_bodies
    )

# def _morning_star(c1: Candle, c2: Candle, c3: Candle) -> bool:
#     return (
#         c1.is_bearish()
#         and _body(c1) > _range(c1) * 0.5
#         and _body(c2) < _body(c1) * 0.3
#         and c3.is_bullish()
#         and c3.close > (c1.open_price + c1.close) / 2
#     )

# def _evening_star(c1: Candle, c2: Candle, c3: Candle) -> bool:
#     return (
#         c1.is_bullish()
#         and _body(c1) > _range(c1) * 0.5
#         and _body(c2) < _body(c1) * 0.3
#         and c3.is_bearish()
#         and c3.close < (c1.open_price + c1.close) / 2
#     )
###Changes done on 23 May 2026 to improve accuracy of Morning, Evening star
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
        c1: First candle - should be a large bearish candle
        c2: Second candle - should be a small-bodied star (doji/spinning top)
        c3: Third candle - should be a large bullish candle
        preceding_candles: List of candles before c1 to verify downtrend (min 3 recommended)
        c3_volume: Volume of the third candle (optional, for volume confirmation)
        c2_prev_volume: Average volume before c2 (optional, for volume confirmation)
    """

    # ── 1. Downtrend context (preceding candles must show a downtrend) ──────────
    if preceding_candles and len(preceding_candles) >= 3:
        # Check that the majority of preceding candles are bearish (downtrend)
        bearish_count = sum(1 for c in preceding_candles if c.is_bearish())
        in_downtrend = bearish_count >= len(preceding_candles) * 0.6
    else:
        # If no preceding candles are provided, skip downtrend check (loose mode)
        in_downtrend = True

    # ── 2. C1: Large bearish candle ──────────────────────────────────────────────
    c1_is_valid = (
        c1.is_bearish()
        and _body(c1) > _range(c1) * 0.5          # Body > 50% of total range
    )

    # ── 3. C2: Small star candle with gap down from C1 ───────────────────────────
    c1_body_mid   = (c1.open_price + c1.close) / 2
    c1_body_low   = min(c1.open_price, c1.close)   # = c1.close for a bearish candle

    c2_body_top   = max(c2.open_price, c2.close)
    c2_body_bot   = min(c2.open_price, c2.close)
    #c2_body_mid   = (c2_body_top + c2_body_bot) / 2

    gap_down      = c2_body_top < c1_body_low      # Strict: C2's entire body below C1's close

    # Star body must be small AND centred (spinning-top / doji character)
    upper_wick    = c2.high - c2_body_top
    lower_wick    = c2_body_bot - c2.low
    total_range_c2 = _range(c2) if _range(c2) > 0 else 1e-9
    body_ratio_c2  = _body(c2) / total_range_c2    # Body occupies < 30 % of C2's range

    c2_is_valid = (
        _body(c2) < _body(c1) * 0.3               # Small body vs C1
        and body_ratio_c2 < 0.3                    # Doji / spinning-top character
        and upper_wick > 0                         # Has wicks on both sides
        and lower_wick > 0
        and gap_down                               # Gaps below C1's body
    )

    # ── 4. C3: Large bullish candle with gap up from C2 ─────────────────────────
    gap_up = c3.open_price > c2_body_top           # C3 opens above C2's body top

    c3_is_valid = (
        c3.is_bullish()
        and _body(c3) > _range(c3) * 0.5          # C3 also has a strong body
        and c3.close > c1_body_mid                 # Closes above C1's body midpoint
        and gap_up                                 # Gaps above C2's body
    )

    # ── 5. Volume confirmation (optional) ────────────────────────────────────────
    if c3_volume is not None and c2_prev_volume is not None and c2_prev_volume > 0:
        volume_confirmed = c3_volume > c2_prev_volume * 1.2   # C3 volume > 120% of prior avg
    else:
        volume_confirmed = True   # Skip check when volume data is unavailable

    return (
        in_downtrend
        and c1_is_valid
        and c2_is_valid
        and c3_is_valid
        and volume_confirmed
    )

# Changes done on 23-May-2026 to improve accuracy of Evening Star
def _evening_star(
    c1: Candle,
    c2: Candle,
    c3: Candle,
    preceding_candles: list[Candle] | None = None,
    c3_volume: float | None = None,
    c2_prev_volume: float | None = None,
) -> bool:
    """
    Detects an Evening Star candlestick pattern.

    Args:
        c1: First candle  — large bullish candle
        c2: Second candle — small-bodied star (doji / spinning top)
        c3: Third candle  — large bearish confirmation candle
        preceding_candles: All completed candles before c1, used for uptrend
                           context. Passed as candles[:-3] by PatternEngine.scan().
                           Falls back to loose mode when None or len < 3.
        c3_volume:      Volume of c3 (optional — constraint 7 keeps this None)
        c2_prev_volume: Rolling avg volume before c2 (optional)
    """

    # ── 1. Uptrend context ───────────────────────────────────────────────────
    if preceding_candles and len(preceding_candles) >= 3:
        bullish_count = sum(1 for c in preceding_candles if c.is_bullish())
        in_uptrend    = bullish_count >= len(preceding_candles) * 0.6
    else:
        # Fewer than 3 preceding candles — early session / loose mode
        in_uptrend = True

    # ── 2. C1: Large bullish candle ──────────────────────────────────────────
    c1_is_valid = (
        c1.is_bullish()
        and _body(c1) > _range(c1) * 0.5       # body > 50% of total range
    )

    # ── 3. C2: Small star with gap UP from C1 ───────────────────────────────
    c1_body_high   = max(c1.open_price, c1.close)   # = c1.close for bullish
    c1_body_mid    = (c1.open_price + c1.close) / 2

    c2_body_top    = max(c2.open_price, c2.close)
    c2_body_bot    = min(c2.open_price, c2.close)

    gap_up         = c2_body_bot > c1_body_high     # entire C2 body above C1 close

    total_range_c2 = _range(c2) if _range(c2) > 0 else 1e-9
    body_ratio_c2  = _body(c2) / total_range_c2     # body < 30% of C2's range

    c2_is_valid = (
        _body(c2) < _body(c1) * 0.3            # small body vs C1
        and body_ratio_c2 < 0.3                # doji / spinning-top character
        and (c2.high - c2_body_top) > 0        # upper wick exists
        and (c2_body_bot - c2.low)  > 0        # lower wick exists
        and gap_up                             # gaps above C1 body
    )

    # ── 4. C3: Strong bearish candle with gap DOWN from C2 ──────────────────
    gap_down = c3.open_price < c2_body_bot          # C3 opens below C2 body bottom

    c3_is_valid = (
        c3.is_bearish()
        and _body(c3) > _range(c3) * 0.5       # strong bearish body
        and c3.close  < c1_body_mid             # closes below C1 midpoint
        and gap_down                            # gaps below C2 body
    )

    # ── 5. Volume confirmation (skipped — constraint 7 keeps these None) ────
    if c3_volume is not None and c2_prev_volume is not None and c2_prev_volume > 0:
        volume_confirmed = c3_volume > c2_prev_volume * 1.2
    else:
        volume_confirmed = True

    return (
        in_uptrend
        and c1_is_valid
        and c2_is_valid
        and c3_is_valid
        and volume_confirmed
    )

# ── 2-candle patterns ─────────────────────────────────────────────────────────

# def _bullish_engulfing(c1: Candle, c2: Candle) -> bool:
#     return (
#         c1.is_bearish()
#         and c2.is_bullish()
#         and c2.open_price <= c1.close
#         and c2.close >= c1.open_price
#         and _body(c2) > _body(c1)
#     )

# def _bearish_engulfing(c1: Candle, c2: Candle) -> bool:
#     return (
#         c1.is_bullish()
#         and c2.is_bearish()
#         and c2.open_price >= c1.close
#         and c2.close <= c1.open_price
#         and _body(c2) > _body(c1)
#     )

# def _piercing_line(c1: Candle, c2: Candle) -> bool:
#     mid = (c1.open_price + c1.close) / 2
#     return (
#         c1.is_bearish()
#         and c2.is_bullish()
#         and c2.open_price < c1.low
#         and mid < c2.close < c1.open_price
#     )

# def _dark_cloud_cover(c1: Candle, c2: Candle) -> bool:
#     mid = (c1.open_price + c1.close) / 2
#     return (
#         c1.is_bullish()
#         and c2.is_bearish()
#         and c2.open_price > c1.high
#         and c1.open_price < c2.close < mid
#     )

#Changes made on 23-May2026 for more accuracy

# Changes done on 23-May-2026 — accurate Bullish Engulfing
def _bullish_engulfing(
    c1: Candle,
    c2: Candle,
    preceding_candles: list[Candle] | None = None,
) -> bool:
    """
    Bullish Engulfing — bullish reversal after a downtrend.

    C1: Bearish candle with a real body (not a doji)
    C2: Bullish candle whose body strictly engulfs C1's entire body,
        with a strong body and controlled upper wick
    """

    # ── 1. Downtrend context ─────────────────────────────────────────────────
    if preceding_candles and len(preceding_candles) >= 3:
        bearish_count = sum(1 for c in preceding_candles if c.is_bearish())
        in_downtrend  = bearish_count >= len(preceding_candles) * 0.6
    else:
        in_downtrend  = True

    # ── 2. Candle directions ─────────────────────────────────────────────────
    directions_valid = c1.is_bearish() and c2.is_bullish()

    # ── 3. C1 must have a real body — not a doji ─────────────────────────────
    c1_has_body = _body(c1) > _range(c1) * 0.3

    # ── 4. Strict engulfing — C2 body goes beyond C1 body on both ends ───────
    # Bearish C1: open_price = top, close = bottom
    # Bullish C2: open_price = bottom, close = top
    engulfs = (
        c2.open_price < c1.close        # C2 opens strictly below C1 body bottom
        and c2.close > c1.open_price    # C2 closes strictly above C1 body top
    )

    # ── 5. C2 must be a strong bullish candle ────────────────────────────────
    c2_strong = _body(c2) > _range(c2) * 0.5

    # ── 6. C2 body larger than C1 body ──────────────────────────────────────
    c2_larger = _body(c2) > _body(c1)

    # ── 7. Controlled upper wick on C2 — no rejection at the high ────────────
    c2_wick_ok = _upper_wick(c2) <= _body(c2) * 0.5

    return (
        in_downtrend
        and directions_valid
        and c1_has_body
        and engulfs
        and c2_strong
        and c2_larger
        and c2_wick_ok
    )


# Changes done on 23-May-2026 — accurate Bearish Engulfing
def _bearish_engulfing(
    c1: Candle,
    c2: Candle,
    preceding_candles: list[Candle] | None = None,
) -> bool:
    """
    Bearish Engulfing — bearish reversal after an uptrend.

    C1: Bullish candle with a real body (not a doji)
    C2: Bearish candle whose body strictly engulfs C1's entire body,
        with a strong body and controlled lower wick
    """

    # ── 1. Uptrend context ───────────────────────────────────────────────────
    if preceding_candles and len(preceding_candles) >= 3:
        bullish_count = sum(1 for c in preceding_candles if c.is_bullish())
        in_uptrend    = bullish_count >= len(preceding_candles) * 0.6
    else:
        in_uptrend    = True

    # ── 2. Candle directions ─────────────────────────────────────────────────
    directions_valid = c1.is_bullish() and c2.is_bearish()

    # ── 3. C1 must have a real body — not a doji ─────────────────────────────
    c1_has_body = _body(c1) > _range(c1) * 0.3

    # ── 4. Strict engulfing — C2 body goes beyond C1 body on both ends ───────
    # Bullish C1: open_price = bottom, close = top
    # Bearish C2: open_price = top, close = bottom
    engulfs = (
        c2.open_price > c1.close        # C2 opens strictly above C1 body top
        and c2.close < c1.open_price    # C2 closes strictly below C1 body bottom
    )

    # ── 5. C2 must be a strong bearish candle ────────────────────────────────
    c2_strong = _body(c2) > _range(c2) * 0.5

    # ── 6. C2 body larger than C1 body ──────────────────────────────────────
    c2_larger = _body(c2) > _body(c1)

    # ── 7. Controlled lower wick on C2 — no buying support at the low ────────
    c2_wick_ok = _lower_wick(c2) <= _body(c2) * 0.5

    return (
        in_uptrend
        and directions_valid
        and c1_has_body
        and engulfs
        and c2_strong
        and c2_larger
        and c2_wick_ok
    )


# Changes done on 23-May-2026 — accurate Piercing Line
def _piercing_line(
    c1: Candle,
    c2: Candle,
    preceding_candles: list[Candle] | None = None,
) -> bool:
    """
    Piercing Line — moderate bullish reversal after a downtrend.

    C1: Strong bearish candle
    C2: Bullish candle that opens below C1's low (gap down),
        then closes above C1's midpoint but below C1's open (body top)
    """

    # ── 1. Downtrend context ─────────────────────────────────────────────────
    if preceding_candles and len(preceding_candles) >= 3:
        bearish_count = sum(1 for c in preceding_candles if c.is_bearish())
        in_downtrend  = bearish_count >= len(preceding_candles) * 0.6
    else:
        in_downtrend  = True

    # ── 2. Candle directions ─────────────────────────────────────────────────
    directions_valid = c1.is_bearish() and c2.is_bullish()

    # ── 3. C1 must be a strong bearish candle ────────────────────────────────
    c1_strong = _body(c1) > _range(c1) * 0.5

    # ── 4. C2 must be a strong bullish candle ────────────────────────────────
    c2_strong = _body(c2) > _range(c2) * 0.5

    # ── 5. Gap down open — C2 opens below C1's entire range ──────────────────
    # Classical definition: C2 opens below C1's low (including wick)
    gap_down = c2.open_price < c1.low

    # ── 6. C2 closes above C1 midpoint but stays below C1 body top ───────────
    c1_body_top = c1.open_price             # bearish: open = top of body
    c1_body_bot = c1.close                  # bearish: close = bottom of body
    c1_mid      = (c1_body_top + c1_body_bot) / 2

    penetrates  = c1_mid < c2.close < c1_body_top

    return (
        in_downtrend
        and directions_valid
        and c1_strong
        and c2_strong
        and gap_down
        and penetrates
    )


# Changes done on 23-May-2026 — accurate Dark Cloud Cover
def _dark_cloud_cover(
    c1: Candle,
    c2: Candle,
    preceding_candles: list[Candle] | None = None,
) -> bool:
    """
    Dark Cloud Cover — moderate bearish reversal after an uptrend.

    C1: Strong bullish candle
    C2: Bearish candle that opens above C1's high (gap up),
        then closes below C1's midpoint (deeper = stronger signal)
    """

    # ── 1. Uptrend context ───────────────────────────────────────────────────
    if preceding_candles and len(preceding_candles) >= 3:
        bullish_count = sum(1 for c in preceding_candles if c.is_bullish())
        in_uptrend    = bullish_count >= len(preceding_candles) * 0.6
    else:
        in_uptrend    = True

    # ── 2. Candle directions ─────────────────────────────────────────────────
    directions_valid = c1.is_bullish() and c2.is_bearish()

    # ── 3. C1 must be a strong bullish candle ────────────────────────────────
    c1_strong = _body(c1) > _range(c1) * 0.5

    # ── 4. C2 must be a strong bearish candle ────────────────────────────────
    c2_strong = _body(c2) > _range(c2) * 0.5

    # ── 5. Gap up open — C2 opens above C1's entire range ────────────────────
    gap_up = c2.open_price > c1.high

    # ── 6. C2 closes below C1 midpoint — no unnecessary lower bound ──────────
    # Bullish C1: open_price = bottom, close = top of body
    c1_body_top = c1.close                  # bullish: close = top of body
    c1_body_bot = c1.open_price             # bullish: open = bottom of body
    c1_mid      = (c1_body_top + c1_body_bot) / 2

    # C2 must close below midpoint — deeper close = stronger bearish signal
    # Floor: c2.close > c1.low keeps it within a reasonable range
    penetrates  = c2.close < c1_mid and c2.close > c1.low

    return (
        in_uptrend
        and directions_valid
        and c1_strong
        and c2_strong
        and gap_up
        and penetrates
    )

# ── 1-candle patterns ─────────────────────────────────────────────────────────

# # Changes made on 21-May-2026 for candle accuracy

# def _hammer(c: Candle) -> bool:
#     r = _range(c)
#     if r == 0:
#         return False

#     body_bottom = min(c.open_price, c.close)   # lowest point of body
#     body_position = (body_bottom - c.low) / r
#     # body_position = 0.0 means body sits at very bottom
#     # body_position = 1.0 means body sits at very top
#     # Hammer needs body in TOP 70% → body_position >= 0.70

#     return (
#         r > 0
#         and _lower_wick(c) > 2 * _body(c)
#         and _upper_wick(c) <= _body(c) * 0.1
#         and _body(c) > 0
#         and body_position >= 0.80   # body in top 30% of range ✅
#     )


# def _shooting_star(c: Candle) -> bool:
#     r = _range(c)
#     if r == 0:
#         return False

#     body_top = max(c.open_price, c.close)      # highest point of body
#     body_position = (c.high - body_top) / r
#     # body_position = 0.0 means body sits at very top
#     # body_position = 1.0 means body sits at very bottom
#     # Shooting star needs body in BOTTOM 70% → body_position >= 0.70

#     return (
#         r > 0
#         and _upper_wick(c) > 2 * _body(c)
#         and _lower_wick(c) <= _body(c) * 0.1
#         and _body(c) > 0
#         and body_position >= 0.80   # body in bottom 30% of range ✅
#     )

# Changes made on 23-May-2026 for more accuracy
# Changes done on 23-May-2026 — accurate Hammer for 5-min intraday
def _hammer(
    c: Candle,
    preceding_candles: list[Candle] | None = None,
) -> bool:
    """
    Hammer — bullish reversal single candle at bottom of downtrend.

    Requirements:
      • Small body (< 35% of range) sitting in TOP 35% of total range
      • Long lower wick  (> 2× body) — buyers reclaimed the low
      • Tiny upper wick  (≤ 25% of body) — no selling pressure at top
      • Appears after a downtrend
    """

    # ── 1. Flat candle guard ─────────────────────────────────────────────────
    r = _range(c)
    if r == 0:
        return False

    # ── 2. Downtrend context ─────────────────────────────────────────────────
    if preceding_candles and len(preceding_candles) >= 3:
        bearish_count = sum(1 for c_ in preceding_candles if c_.is_bearish())
        in_downtrend  = bearish_count >= len(preceding_candles) * 0.6
    else:
        in_downtrend  = True                    # loose mode — early session

    # ── 3. Body must exist and be small relative to range ────────────────────
    body = _body(c)
    body_exists = body > 0
    body_small  = body < r * 0.35              # body < 35% of total range

    # ── 4. Body position — must sit in TOP 35% of the range ──────────────────
    # body_bottom is the lowest point of the real body
    # distance from c.low to body_bottom must be ≥ 65% of total range
    body_bottom   = min(c.open_price, c.close)
    body_position = (body_bottom - c.low) / r  # 0.0=bottom, 1.0=top
    body_high_up  = body_position >= 0.65       # body in top 35% of range ✅

    # ── 5. Long lower wick — buyers strongly reclaimed the low ───────────────
    long_lower_wick = _lower_wick(c) > 2 * body

    # ── 6. Tiny upper wick — no selling resistance at the top ────────────────
    # 0.25 tolerance handles 5-min intraday tick noise
    tiny_upper_wick = _upper_wick(c) <= body * 0.25

    return (
        in_downtrend
        and body_exists
        and body_small
        and body_high_up
        and long_lower_wick
        and tiny_upper_wick
    )


# Changes done on 23-May-2026 — accurate Shooting Star for 5-min intraday
def _shooting_star(
    c: Candle,
    preceding_candles: list[Candle] | None = None,
) -> bool:
    """
    Shooting Star — bearish reversal single candle at top of uptrend.

    Requirements:
      • Small body (< 35% of range) sitting in BOTTOM 35% of total range
      • Long upper wick  (> 2× body) — sellers pushed price back down
      • Tiny lower wick  (≤ 25% of body) — no buying support at bottom
      • Appears after an uptrend
    """

    # ── 1. Flat candle guard ─────────────────────────────────────────────────
    r = _range(c)
    if r == 0:
        return False

    # ── 2. Uptrend context ───────────────────────────────────────────────────
    if preceding_candles and len(preceding_candles) >= 3:
        bullish_count = sum(1 for c_ in preceding_candles if c_.is_bullish())
        in_uptrend    = bullish_count >= len(preceding_candles) * 0.6
    else:
        in_uptrend    = True                    # loose mode — early session

    # ── 3. Body must exist and be small relative to range ────────────────────
    body = _body(c)
    body_exists = body > 0
    body_small  = body < r * 0.35              # body < 35% of total range

    # ── 4. Body position — must sit in BOTTOM 35% of the range ───────────────
    # body_top is the highest point of the real body
    # distance from body_top to c.high must be ≥ 65% of total range
    body_top      = max(c.open_price, c.close)
    body_position = (c.high - body_top) / r    # 0.0=top, 1.0=bottom
    body_low_down = body_position >= 0.65       # body in bottom 35% of range ✅

    # ── 5. Long upper wick — sellers strongly pushed price back down ──────────
    long_upper_wick = _upper_wick(c) > 2 * body

    # ── 6. Tiny lower wick — no buying support at the bottom ─────────────────
    # 0.25 tolerance handles 5-min intraday tick noise
    tiny_lower_wick = _lower_wick(c) <= body * 0.25

    return (
        in_uptrend
        and body_exists
        and body_small
        and body_low_down
        and long_upper_wick
        and tiny_lower_wick
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
        #Changes done on May-23-2026 for candle accuracy
        # candles[:-3] = all candles before c1 → downtrend context window  
        # falls back to loose mode (in_downtrend=True) when len < 3 
        #Considers only last 5 candles and not all candles
        preceding = candles[-5:-3] if len(candles) > 3 else [] 
        # ── 3-candle (strongest first) ────────────────────────────────────────
        if n >= 3:
            c1, c2, c3 = candles[-3], candles[-2], candles[-1]
            

            #if _morning_doji_star(c1, c2, c3):
            if _morning_doji_star(c1, c2, c3, preceding_candles=preceding):
                logger.info("▲ Morning Doji Star (87%) — BULLISH")
                return "Morning Doji Star", "bullish"
            
            #if _evening_doji_star(c1, c2, c3):
            if _evening_doji_star(c1, c2, c3, preceding_candles=preceding):
                logger.info("▼ Evening Doji Star (82%) — BEARISH")
                return "Evening Doji Star", "bearish"
            
            #if _morning_star(c1, c2, c3):
            
            if _morning_star(c1, c2, c3, preceding_candles=preceding):
                logger.info("▲ Morning Star (82%) — BULLISH")
                return "Morning Star", "bullish"

            #if _evening_doji_star(c1, c2, c3):
            if _evening_star(c1, c2, c3, preceding_candles=preceding):
                logger.info("▼ Evening Star (87%) — BEARISH")
                return "Evening Star", "bearish"
            
            #if _three_white_soldiers(c1, c2, c3):
            if _three_white_soldiers(c1, c2, c3, preceding_candles=preceding):
                logger.info("▲ Three White Soldiers (85%) — BULLISH")
                return "Three White Soldiers", "bullish"

            #if _three_black_crows(c1, c2, c3):
            if _three_black_crows(c1, c2, c3, preceding_candles=preceding):
                logger.info("▼ Three Black Crows (85%) — BEARISH")
                return "Three Black Crows", "bearish"

            

        # ── 2-candle ──────────────────────────────────────────────────────────
        if n >= 2:
            c1, c2 = candles[-2], candles[-1]

            # if _bullish_engulfing(c1, c2):
            if _bullish_engulfing(c1, c2, preceding_candles=preceding):
                logger.info("▲ Bullish Engulfing (83%) — BULLISH")
                return "Bullish Engulfing", "bullish"

            # if _piercing_line(c1, c2):
            if _piercing_line(c1, c2, preceding_candles=preceding):
                logger.info("▲ Piercing Line (81%) — BULLISH")
                return "Piercing Line", "bullish"

            # if _bearish_engulfing(c1, c2):
            if _bearish_engulfing(c1, c2, preceding_candles=preceding):
                logger.info("▼ Bearish Engulfing (83%) — BEARISH")
                return "Bearish Engulfing", "bearish"

            # if _dark_cloud_cover(c1, c2):
            if _dark_cloud_cover(c1, c2, preceding_candles=preceding):
                logger.info("▼ Dark Cloud Cover (81%) — BEARISH")
                return "Dark Cloud Cover", "bearish"

        # ── 1-candle ──────────────────────────────────────────────────────────
        c = candles[-1]
        #if _hammer(c):
        if _hammer(c, preceding_candles=preceding):
            logger.info("▲ Hammer (80%) — BULLISH")
            return "Hammer", "bullish"
        #if _shooting_star(c):
        if _shooting_star(c, preceding_candles=preceding):
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
