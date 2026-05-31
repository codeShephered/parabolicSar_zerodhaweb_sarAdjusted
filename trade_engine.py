"""
trade_engine.py — Position lifecycle with Parabolic SAR trailing stop.

Stop-Loss (constraint 5):
  • Entry SL  = SAR value at entry
  • Trailing  = update SL to new SAR each candle close (favourable only)
    CE: SAR rises with price → SL tightens from below
    PE: SAR falls with price → SL tightens from above

Exit priority (constraint 6 + 11):
  Tick-level : SAR SL hit | 25% premium backstop
  Candle-close: SAR reversed | pattern reversal (immediate ≥83%, pending 80-82%)
  3:15 PM    : force-close all open trades (constraint 11)

Constraint 9 — Accurate premium display:
  entry_premium  = real Zerodha LTP at trade entry (BS only if LTP=0)
  current_premium= real Zerodha LTP updated every 30-second tick
  pnl            = (current_premium - entry_premium) × lot_size
"""
from __future__ import annotations
import threading, math
from datetime import datetime, date
from logger_setup import get_module_logger
from data_feed import get_nifty_expiry, get_banknifty_expiry, build_symbol, select_strike, bs_estimate
from pattern_engine import IMMEDIATE_EXIT_PATTERNS
import config

logger = get_module_logger("TradeEngine")


# ── Position ──────────────────────────────────────────────────────────────────

class Position:
    def __init__(self,
                 instrument:    str,
                 direction:     str,
                 option_type:   str,
                 strike:        float,
                 expiry:        date,
                 entry_premium: float,
                 sar_sl:        float,
                 pattern:       str,
                 lot_size:      int,
                 is_estimated:  bool = False):
        self.instrument    = instrument
        self.direction     = direction
        self.option_type   = option_type
        self.strike        = strike
        self.expiry        = expiry
        self.expiry_str    = expiry.strftime("%d-%b-%Y")

        # ── Premium (constraint 9) ────────────────────────────────────────────
        self.entry_premium   = max(entry_premium, 0.05)
        self.current_premium = self.entry_premium
        self.is_estimated    = is_estimated   # True when real LTP was 0 at entry
        self.pnl             = 0.0

        # ── SAR trailing SL ───────────────────────────────────────────────────
        #self.sar_sl          = round(sar_sl, 2)
        #self.initial_sar_sl  = round(sar_sl, 2)
        #self.sl_premium      = self.entry_premium * (1 - config.PREMIUM_SL_PCT)

        self.sar_sl          = round(sar_sl, 2)
        self.initial_sar_sl  = round(sar_sl, 2)

        self.pattern          = pattern
        self.lot_size         = lot_size
        self.entry_time       = datetime.now()
        self.is_open          = True
        self.pending_reversal = ""  # set when 80-82% pattern appears, cleared on SAR flip
        self._stale_ticks     = 0

        self.zerodha_symbol = build_symbol(instrument, expiry, strike, option_type)

    # ── Premium update ────────────────────────────────────────────────────────

    def update_premium(self, ltp: float, is_estimated: bool = False) -> None:
        """
        Update current premium from real Zerodha LTP.
        If ltp > 0 → use real value (preferred, constraint 9).
        If ltp = 0 → keep last known value; stale warning after 3 ticks.
        """
        if ltp > 0:
            self.current_premium = ltp
            self.is_estimated    = is_estimated
            self.pnl             = (ltp - self.entry_premium) * self.lot_size
            self._stale_ticks    = 0
        else:
            self._stale_ticks += 1
            if self._stale_ticks == 3:
                logger.warning(
                    f"{self.instrument} {self.option_type} {int(self.strike)}: "
                    f"real LTP unavailable for 3 ticks — "
                    f"displaying last known ₹{self.current_premium:.2f}. "
                    f"Symbol: {self.zerodha_symbol}"
                )

    # ── SAR trailing SL ───────────────────────────────────────────────────────

    def trail_sar(self, new_sar: float) -> None:
        """Move SL only in the favourable direction (constraint 5)."""
        new_sar = round(new_sar, 2)
        if self.option_type == "CE" and new_sar > self.sar_sl:
            logger.info(
                f"{self.instrument} CE SAR SL ↑ trailed "
                f"{self.sar_sl:.2f} → {new_sar:.2f}"
            )
            self.sar_sl = new_sar
        elif self.option_type == "PE" and new_sar < self.sar_sl:
            logger.info(
                f"{self.instrument} PE SAR SL ↓ trailed "
                f"{self.sar_sl:.2f} → {new_sar:.2f}"
            )
            self.sar_sl = new_sar

    def sar_sl_hit(self, spot: float) -> bool:
        """CE: price < SAR SL. PE: price > SAR SL."""
        return spot < self.sar_sl if self.option_type == "CE" else spot > self.sar_sl

    #def premium_sl_hit(self) -> bool:
    #    return self.current_premium <= self.sl_premium

    def to_dict(self) -> dict:
        pct = (self.current_premium - self.entry_premium) / self.entry_premium * 100 \
              if self.entry_premium else 0
        return {
            "instrument":      self.instrument,
            "direction":       self.direction,
            "option_type":     self.option_type,
            "strike":          self.strike,
            "expiry":          self.expiry_str,
            "pattern":         self.pattern,
            "entry_premium":   round(self.entry_premium, 2),
            "current_premium": round(self.current_premium, 2),
            "is_estimated":    self.is_estimated,
            "pnl":             round(self.pnl, 2),
            "pnl_pct":         round(pct, 2),
            #"sar_sl":          self.sar_sl,
            #"initial_sar_sl":  self.initial_sar_sl,
            #"sl_premium":      round(self.sl_premium, 2),
	    "sar_sl":          self.sar_sl,
            "initial_sar_sl":  self.initial_sar_sl,
            "entry_time":      self.entry_time.strftime("%H:%M:%S"),
            "zerodha_symbol":  self.zerodha_symbol,
            "pending_reversal":self.pending_reversal,
            "lot_size":        self.lot_size,
        }


# ── Trade Engine ──────────────────────────────────────────────────────────────

class TradeEngine:
    def __init__(self):
        self._positions: dict[str, Position] = {}
        self._history:   list[dict]          = []
        self._lock       = threading.Lock()
        self.stats       = {"total": 0, "wins": 0, "losses": 0, "total_pnl": 0.0}

    # ── Queries ───────────────────────────────────────────────────────────────

    def has_open(self, instrument: str) -> bool:
        with self._lock:
            p = self._positions.get(instrument)
            return p is not None and p.is_open

    def get_pos(self, instrument: str) -> Position | None:
        with self._lock:
            return self._positions.get(instrument)

    def all_positions(self) -> list[dict]:
        with self._lock:
            return [p.to_dict() for p in self._positions.values() if p.is_open]

    def history(self) -> list[dict]:
        with self._lock:
            return list(self._history)

    def get_stats(self) -> dict:
        with self._lock:
            s = self.stats.copy()
            s["win_rate"] = round(s["wins"] / s["total"] * 100, 1) if s["total"] else 0.0
            return s

    # ── Timing helpers ────────────────────────────────────────────────────────

    @staticmethod
    def can_enter() -> bool:
        now = datetime.now()
        if now.weekday() >= 5:
            return False
        h, m = now.hour, now.minute
        return (h > 9 or (h == 9 and m >= 15)) and (
            h < config.NO_NEW_TRADE_HOUR or
            (h == config.NO_NEW_TRADE_HOUR and m < config.NO_NEW_TRADE_MINUTE)
        )

    @staticmethod
    def must_square_off() -> bool:
        now = datetime.now()
        return now.hour > config.SQUARE_OFF_HOUR or (
            now.hour == config.SQUARE_OFF_HOUR and
            now.minute >= config.SQUARE_OFF_MINUTE
        )

    # ── Build signal ──────────────────────────────────────────────────────────

    def build_signal(self, instrument: str, direction: str, spot: float,
                     pattern: str, sar_sl: float) -> dict:
        cfg         = config.INSTRUMENTS[instrument]
        opt_type    = "CE" if direction == "bullish" else "PE"
        strike      = select_strike(instrument, spot, opt_type)
        expiry      = (get_nifty_expiry() if cfg["expiry_type"] == "weekly"
                       else get_banknifty_expiry())
        symbol      = build_symbol(instrument, expiry, strike, opt_type)

        logger.info(
            f"SIGNAL  {instrument} {opt_type} {int(strike)}  "
            f"expiry={expiry.strftime('%d-%b-%Y')}  "
            f"pattern={pattern}  spot={spot:.2f}  SAR_SL={sar_sl:.2f}"
        )
        return {
            "instrument":   instrument,
            "direction":    direction,
            "option_type":  opt_type,
            "strike":       strike,
            "expiry":       expiry,
            "expiry_str":   expiry.strftime("%d-%b-%Y"),
            "pattern":      pattern,
            "spot":         spot,
            "sar_sl":       round(sar_sl, 2),
            "lot_size":     cfg["lot_size"],
            "zerodha_symbol": symbol,
        }

    # ── Entry ─────────────────────────────────────────────────────────────────

    def enter(self, signal: dict, ltp: float) -> Position:
        """
        Record a trade. Constraint 9 — always prefer real LTP.
        """
        is_est = ltp == 0
        if is_est:
            ltp = bs_estimate(
                signal["instrument"],
                signal["spot"],
                signal["strike"],
                signal["option_type"],
                max((signal["expiry"] - date.today()).days, 1),
            )
            logger.warning(
                f"{signal['instrument']} {signal['option_type']}: "
                f"real LTP unavailable — using BS estimate ₹{ltp:.2f}. "
                f"P&L may not be accurate until real LTP is received."
            )
        else:
            logger.info(
                f"{signal['instrument']} {signal['option_type']}: "
                f"entry LTP = ₹{ltp:.2f} (real)"
            )

        with self._lock:
            pos = Position(
                instrument    = signal["instrument"],
                direction     = signal["direction"],
                option_type   = signal["option_type"],
                strike        = signal["strike"],
                expiry        = signal["expiry"],
                entry_premium = ltp,
                sar_sl        = signal["sar_sl"],
                pattern       = signal["pattern"],
                lot_size      = signal["lot_size"],
                is_estimated  = is_est,
            )
            self._positions[signal["instrument"]] = pos
            self.stats["total"] += 1
        return pos

    def place_live_order(self, signal: dict, feed) -> None:
        from kiteconnect import KiteConnect
        feed.place_order(
            signal["zerodha_symbol"],
            signal["lot_size"],
            KiteConnect.TRANSACTION_TYPE_BUY,
        )

    # ── Tick exits ────────────────────────────────────────────────────────────

    def check_tick(self, instrument: str, spot: float, ltp: float) -> dict | None:
        """
        Called every 30 seconds.
        Checks: SAR SL | 25% premium backstop.
        """
        with self._lock:
            pos = self._positions.get(instrument)
            if not pos or not pos.is_open:
                return None
            pos.update_premium(ltp)

            #if pos.sar_sl_hit(spot):
            #    return self._close(pos, instrument,
            #                       f"SAR SL hit  spot={spot:.2f}  SL={pos.sar_sl:.2f}")

            #if pos.premium_sl_hit():
            #    return self._close(pos, instrument,
            #                       f"Premium SL  current=₹{pos.current_premium:.2f}  "
            #                       f"floor=₹{pos.sl_premium:.2f}")
            # 
            if pos.sar_sl_hit(spot):
                return self._close(pos, instrument,
                                   f"SAR SL hit  spot={spot:.2f}  SL={pos.sar_sl:.2f}")
        return None

    # ── Candle-close exits ────────────────────────────────────────────────────

    def check_candle(self, instrument: str, ltp: float,
                     sar_value: float, sar_reversed: bool,
                     rev_pattern: str = "") -> dict | None:
        """
        Called on each 5-minute candle close.
        1. Trail SAR SL
        2. Exit if SAR reversed
        3. Exit if high-confidence reversal pattern (≥83%)
        4. Set pending if moderate pattern (80-82%), wait for SAR confirm
        """
        with self._lock:
            pos = self._positions.get(instrument)
            if not pos or not pos.is_open:
                return None
            pos.update_premium(ltp)
            pos.trail_sar(sar_value)      # trail the SAR stop

            ##Changes on 21 May 2026 to honour reversal only when the ltp is > (50-for NIFTY; 90-for BANKNIFTY)entry preium
            # ── Compute current P&L ───────────────────────────────────────────────
            pnl        = ltp - pos.entry_premium
            pnl_pct    = pnl / pos.entry_premium * 100
            #premium_sl = pos.entry_premium * 0.75

            # ── Get profit threshold for this instrument ──────────────────────────
            profit_threshold = config.INSTRUMENTS[instrument].get("profit_threshold", 0)

            ############################

            # SAR reversal → exit
            if sar_reversed:
                return self._close(pos, instrument,
                                   f"Tier 1 exit: SAR reversed  new_sar={sar_value:.2f}")

            # # Tier 1 (≥83%) → immediate exit
            # if rev_pattern and rev_pattern in IMMEDIATE_EXIT_PATTERNS:
            #     return self._close(pos, instrument,
            #                        f"Reversal: {rev_pattern} (≥83% — immediate)")

            # Tier 2 (80-82%) → set pending, exit on next SAR confirm
            # if rev_pattern and not pos.pending_reversal:
            #     pos.pending_reversal = rev_pattern
            #     logger.info(
            #         f"{instrument}: pending reversal '{rev_pattern}' — "
            #         f"waiting for SAR confirmation"
            #     )
            #Changes made on 21-May-2026 to exit on Target while trend reversal is seen
            if rev_pattern and not pos.pending_reversal:
                if pnl < profit_threshold:
                    logger.info(
                        f"{instrument}: pending reversal '{pos.pending_reversal}' + Target flip "
                        f"but profit {pnl:+.2f} < threshold {profit_threshold:.2f} — holding position"
                    )
                    return None
                
                logger.info(
                    f"{instrument} Tier 2 exit: pending reversal '{pos.pending_reversal}' Target flip"
                    f"(profit threshold met). P&L={pnl:+.2f} ({pnl_pct:+.1f}%)"
                )
                return self._close(instrument, ltp, f"Reversal: {pos.pending_reversal} Target")

        return None

    # ── Close helper ──────────────────────────────────────────────────────────

    def _close(self, pos: Position, instrument: str, reason: str) -> dict:
        pos.is_open = False
        pnl = pos.pnl
        if pnl > 0:   self.stats["wins"]   += 1
        elif pnl < 0: self.stats["losses"] += 1
        self.stats["total_pnl"] += pnl
        record = {
            **pos.to_dict(),
            "exit_reason": reason,
            "exit_time":   datetime.now().strftime("%H:%M:%S"),
            "final_pnl":   round(pnl, 2),
        }
        self._history.append(record)
        del self._positions[instrument]
        sign = "+" if pnl >= 0 else ""
        logger.info(
            f"CLOSED  {pos.instrument} {pos.option_type} {int(pos.strike)}  "
            f"reason={reason}  PnL={sign}₹{pnl:.2f}"
        )
        return record

    def force_close_all(self, reason: str = "3:15 PM square-off") -> list[dict]:
        closed = []
        with self._lock:
            keys = list(self._positions.keys())
        for k in keys:
            with self._lock:
                pos = self._positions.get(k)
                if not pos or not pos.is_open:
                    continue
                r = self._close(pos, k, reason)
                closed.append(r)
        return closed
