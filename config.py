"""
config.py — Central configuration for SAR Options Trading System.

Strategy  : Parabolic SAR (start=0.02, increment=0.02, max=0.2) +
            12 candlestick patterns (3-candle window)
SL        : Trailing Parabolic SAR — updates every candle close
Volume    : NOT used at any point (constraint 7)
Contracts : NIFTY weekly (Thursday) | BANKNIFTY monthly (last Wednesday)
"""
import os

# ── Zerodha ───────────────────────────────────────────────────────────────────
#ZERODHA_API_KEY      = os.environ.get("ZERODHA_API_KEY",      "")
#ZERODHA_API_SECRET   = os.environ.get("ZERODHA_API_SECRET",   "")
#ZERODHA_ACCESS_TOKEN = os.environ.get("ZERODHA_ACCESS_TOKEN", "")

# ── Mode ──────────────────────────────────────────────────────────────────────
TRADING_MODE = "paper"   # "paper" | "live"

# ── Polling & candle ──────────────────────────────────────────────────────────
POLL_INTERVAL_SECONDS    = 30
CANDLE_TIMEFRAME_MINUTES = 5

# ── Market timing (constraint 11) ─────────────────────────────────────────────
NO_NEW_TRADE_HOUR     = 15
NO_NEW_TRADE_MINUTE   = 5
SQUARE_OFF_HOUR       = 15
SQUARE_OFF_MINUTE     = 15

# ── Instruments ───────────────────────────────────────────────────────────────
INSTRUMENTS = {
    "NIFTY": {
        "zerodha_symbol":  "NSE:NIFTY 50",
        "lot_size":        65,
        "strike_interval": 50,
        "expiry_type":     "weekly",   # current Thursday
        "expiry_weekday":  3,
        #"ce_strike_mode":  "itm",
        #"pe_strike_mode":  "itm",
        "ce_strike_mode":  "otm",
        "pe_strike_mode":  "otm",
        "profit_threshold": 50.0,     # ← NEW: minimum profit to exit on reversal
    },
    "BANKNIFTY": {
        "zerodha_symbol":  "NSE:NIFTY BANK",
        "lot_size":        30,
        "strike_interval": 100,
        "expiry_type":     "monthly",  # last Wednesday of month
        "expiry_weekday":  2,
        #"ce_strike_mode":  "atm",
        #"pe_strike_mode":  "atm",
        "ce_strike_mode":  "otm",
        "pe_strike_mode":  "otm",
        "profit_threshold": 90.0,     # ← NEW: minimum profit to exit on reversal
    },
}

# ── Parabolic SAR (constraint 4 & 5) ──────────────────────────────────────────
SAR_START       = 0.02   # initial acceleration factor
SAR_INCREMENT   = 0.02   # AF step per new extreme
SAR_MAX         = 0.20   # maximum AF
SAR_SEED_CANDLES = 5     # candles required before SAR is considered reliable

# ── Risk ──────────────────────────────────────────────────────────────────────
#PREMIUM_SL_PCT = 0.25    # 25 % drop backstop on option premium ##To avoid 25% premium backstop

# ── Implied volatility (Black-Scholes fallback when Zerodha LTP = 0) ──────────
OPTION_VOLATILITY = {"NIFTY": 0.14, "BANKNIFTY": 0.17}

# ── SMA for trend alignment pre-filter ────────────────────────────────────────
SMA_PERIOD        = 20
#TREND_FILTER_PCT  = 0.5    # reject signal if price > 0.5% against SMA direction
#changes on 24-May-2026
TREND_FILTER_BULLISH_PCT = 0.5 # block bullish if price > 0.5% below SMA20
TREND_FILTER_BEARISH_PCT = 1.0 

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_FILE        = "trading.log"
LOG_LEVEL       = "DEBUG"
MAX_MEMORY_LOGS = 1000

# ── Flask ─────────────────────────────────────────────────────────────────────
FLASK_HOST  = "0.0.0.0"
FLASK_PORT  = 5001
SECRET_KEY  = "sar_nifty_2026"
