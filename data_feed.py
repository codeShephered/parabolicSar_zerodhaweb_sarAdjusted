"""
data_feed.py — Zerodha KiteConnect data + option contract utilities.

Contract selection (constraint 8):
  NIFTY    : current weekly contract (Thursday expiry).
             If today IS Thursday (expiry day), use next Thursday.
  BANKNIFTY: monthly contract only (last Wednesday of the month).
             If today is past that date, use next month's last Wednesday.

Symbol formats (Zerodha NFO):
  Weekly  : NIFTY{YY}{M}{DD}{STRIKE}{TYPE}    e.g. NIFTY2651424300CE
  Monthly : BANKNIFTY{YY}{MMM}{STRIKE}{TYPE}  e.g. BANKNIFTY26MAY55800CE
"""
from __future__ import annotations
import math
from datetime import date, timedelta
from logger_setup import get_module_logger
import config

logger = get_module_logger("Feed")

# One-character month codes for weekly symbols
_MONTH_CHAR = {1:"1",2:"2",3:"3",4:"4",5:"5",6:"6",
               7:"7",8:"8",9:"9",10:"O",11:"N",12:"D"}
# Three-letter month names for monthly symbols
_MONTH_NAME = {1:"JAN",2:"FEB",3:"MAR",4:"APR",5:"MAY",6:"JUN",
               7:"JUL",8:"AUG",9:"SEP",10:"OCT",11:"NOV",12:"DEC"}


# ── Expiry helpers ─────────────────────────────────────────────────────────────
'''
def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    """Last occurrence of `weekday` (0=Mon … 6=Sun) in the given month."""
    if month == 12:
        last = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    offset = (last.weekday() - weekday) % 7
    return last - timedelta(days=offset)


def get_nifty_expiry() -> date:
    """
    Current NIFTY weekly expiry (Thursday).
    If today is Thursday (expiry day itself), return next Thursday.
    """
    today = date.today()
    delta = (3 - today.weekday()) % 7   # 3 = Thursday
    if delta == 0:
        delta = 7
    return today + timedelta(days=delta)


def get_banknifty_expiry() -> date:
    """
    BANKNIFTY monthly expiry = last Wednesday of the current month.
    If today is past that date, return next month's last Wednesday.
    """
    today = date.today()
    exp   = _last_weekday_of_month(today.year, today.month, 2)
    if today >= exp:
        if today.month == 12:
            exp = _last_weekday_of_month(today.year + 1, 1, 2)
        else:
            exp = _last_weekday_of_month(today.year, today.month + 1, 2)
    return exp
'''
# NSE trading holidays — Tuesday expiries that fall on these dates
# must use the preceding Monday instead.
# Add future holiday dates here as NSE announces them.
_NSE_HOLIDAYS: frozenset[date] = frozenset({
    date(2026, 10, 20),   # Tuesday — Diwali Laxmi Puja
    date(2026, 11, 10),   # Tuesday — Gurunanak Jayanti
    date(2026, 11, 24),   # Tuesday — add/remove as NSE calendar changes
})


def _adjust_for_holiday(expiry: date) -> date:
    """
    If the computed expiry Tuesday is an NSE holiday,
    return the preceding Monday instead.
    Keeps stepping back by one day until a non-holiday weekday is found
    (handles the rare case where Monday is also a holiday).
    """
    while expiry in _NSE_HOLIDAYS:
        expiry -= timedelta(days=1)   # step back to Monday (or earlier if needed)
    return expiry


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    """Last occurrence of `weekday` (0=Mon … 6=Sun) in the given month."""
    if month == 12:
        last = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    offset = (last.weekday() - weekday) % 7
    return last - timedelta(days=offset)


def get_nifty_expiry() -> date:
    """
    Current NIFTY weekly expiry (Tuesday).
    If today IS Tuesday (expiry day itself), use next Tuesday.
    If the Tuesday is an NSE holiday, use the preceding Monday.
    """
    today = date.today()
    delta = (1 - today.weekday()) % 7   # 1 = Tuesday
    if delta == 0:
        delta = 7                        # today is Tuesday → jump to next week
    expiry = today + timedelta(days=delta)
    return _adjust_for_holiday(expiry)


def get_banknifty_expiry() -> date:
    """
    BANKNIFTY monthly expiry = last Tuesday of the current month.
    If today is past that date, use next month's last Tuesday.
    If that Tuesday is an NSE holiday, use the preceding Monday.
    """
    today = date.today()
    exp   = _last_weekday_of_month(today.year, today.month, 1)   # 1 = Tuesday
    if today >= exp:
        if today.month == 12:
            exp = _last_weekday_of_month(today.year + 1, 1, 1)
        else:
            exp = _last_weekday_of_month(today.year, today.month + 1, 1)
    return _adjust_for_holiday(exp)

def build_symbol(instrument: str, expiry: date,
                 strike: float, option_type: str) -> str:
    """
    Build the NFO trading symbol for Zerodha.

    NIFTY weekly:      NIFTY{YY}{M}{DD}{STRIKE}CE/PE
    BANKNIFTY monthly: BANKNIFTY{YY}{MMM}{STRIKE}CE/PE
    """
    yy   = str(expiry.year)[2:]
    cfg  = config.INSTRUMENTS.get(instrument, {})
    etype = cfg.get("expiry_type", "weekly")

    if etype == "monthly":
        mon = _MONTH_NAME[expiry.month]
        return f"{instrument}{yy}{mon}{int(strike)}{option_type}"
    else:
        m  = _MONTH_CHAR[expiry.month]
        dd = f"{expiry.day:02d}"
        return f"{instrument}{yy}{m}{dd}{int(strike)}{option_type}"


def select_strike(instrument: str, spot: float, option_type: str) -> float:
    """Choose ITM or ATM strike depending on instrument config."""
    cfg      = config.INSTRUMENTS[instrument]
    interval = cfg["strike_interval"]
    mode     = cfg.get(f"{'ce' if option_type=='CE' else 'pe'}_strike_mode", "atm")

    if mode == "itm":
        if option_type == "CE":
            base = math.floor(spot / interval) * interval
            if abs(spot - base) < 0.01:
                base -= interval
            return base
        else:
            base = math.ceil(spot / interval) * interval
            if abs(spot - base) < 0.01:
                base += interval
            return base
    # ATM
    return round(spot / interval) * interval


def bs_estimate(instrument: str, spot: float,
                strike: float, option_type: str, days: int) -> float:
    """
    Black-Scholes ATM approximation used as LTP fallback when Zerodha returns 0.
    Shows as 'estimated' in the UI. Constraint 9 — not the primary display value.

    Formula: premium = intrinsic + S×σ×√(T/252)×0.3989
    """
    sigma     = config.OPTION_VOLATILITY.get(instrument, 0.15)
    T         = max(days, 1) / 252
    atm_time  = spot * sigma * math.sqrt(T) * 0.3989
    intrinsic = max(spot - strike, 0) if option_type == "CE" else max(strike - spot, 0)
    return round(max(intrinsic + atm_time, 0.5), 2)


# ── Zerodha Feed ───────────────────────────────────────────────────────────────

class ZerodhaFeed:
    def __init__(self, api_key: str = "", access_token: str = ""):
        self._api_key = api_key
        self._token   = access_token
        self._kite    = None
        self._ok      = False
        if api_key and access_token:
            self._connect()

    def _connect(self) -> None:
        try:
            from kiteconnect import KiteConnect
            self._kite = KiteConnect(api_key=self._api_key)
            self._kite.set_access_token(self._token)
            p = self._kite.profile()
            self._ok = True
            logger.info(
                f"Zerodha ✓  user={p.get('user_name','?')}  "
                f"email={p.get('email','?')}"
            )
        except ImportError:
            logger.error("kiteconnect not installed — run: pip install kiteconnect")
        ###ZERODHA connection issue fix
        # except Exception as exc:
        #     self._ok = False
        #     if "TokenException" in str(exc) or "Invalid" in str(exc):
        #         logger.error("Zerodha: access token expired — regenerate via login")
        #     else:
        #         logger.error(f"Zerodha connect failed: {exc}")
        ##########################################
        ###ZERODHA connection issue fix
        except Exception as exc:
            self._ok = False
            msg      = str(exc)
            exc_type = type(exc).__name__
            # Zerodha raises TokenException but str(exc) contains the message body,
            # NOT the class name. The actual message is:
            #   "Incorrect `api_key` or `access_token`."
            # Check both the exception type and key phrases in the message.
            is_auth_error = (
                exc_type in ("TokenException", "InputException") or
                any(k in msg for k in
                    ("api_key", "access_token", "Incorrect", "TokenException"))
            )
            if is_auth_error:
                logger.error(
                    "Zerodha: access_token is invalid or expired.\n"
                    "  IMPORTANT — The access_token must be regenerated EVERY MORNING.\n"
                    "  It expires at midnight IST regardless of how recently it was used.\n"
                    "  The API key itself is permanent — the token is the issue.\n"
                    "  ACTION: Click 'Login with Zerodha' on the dashboard to get today's token.\n"
                    f"  Zerodha error detail: {msg}"
                )
            else:
                logger.error(f"Zerodha connect failed: {msg}")
            ######################################

    # ── Auth helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def login_url(api_key: str) -> str:
        return f"https://kite.zerodha.com/connect/login?v=3&api_key={api_key}"

    def generate_session(self, request_token: str, api_secret: str) -> str:
        try:
            if not self._kite:
                from kiteconnect import KiteConnect
                self._kite = KiteConnect(api_key=self._api_key)
            data  = self._kite.generate_session(request_token, api_secret=api_secret)
            self._token = data["access_token"]
            self._kite.set_access_token(self._token)
            self._ok = True
            logger.info("Zerodha OAuth session generated ✓")
            return self._token
        except Exception as exc:
            logger.error(f"Zerodha session generation: {exc}")
            return ""

    def set_token(self, token: str) -> bool:
        self._token = token
        self._connect()
        return self._ok

    def is_connected(self) -> bool:
        return self._ok

    # ── Market data ───────────────────────────────────────────────────────────

    def get_quote(self, instrument: str) -> dict:
        """Fetch index LTP, OHLC, and change."""
        if not self._ok:
            return {}
        sym = config.INSTRUMENTS[instrument]["zerodha_symbol"]
        try:
            raw  = self._kite.quote([sym])
            q    = raw.get(sym, {})
            ltp  = float(q.get("last_price", 0) or 0)
            if ltp == 0:
                return {}
            ohlc = q.get("ohlc", {})
            prev = float(ohlc.get("close", 0) or 0)
            chg  = round(ltp - prev, 2)
            return {
                "last_price": ltp,
                "open":       float(ohlc.get("open", 0) or 0),
                "high":       float(ohlc.get("high", 0) or 0),
                "low":        float(ohlc.get("low",  0) or 0),
                "prev_close": prev,
                "change":     chg,
                "pchange":    round(chg / prev * 100, 2) if prev else 0.0,
            }
        except Exception as exc:
            if "TokenException" in str(exc):
                self._ok = False
                logger.error("Zerodha token expired — reconnect via dashboard")
            else:
                logger.warning(f"quote({instrument}): {exc}")
            return {}

    def get_option_ltp(self, symbol: str) -> float:
        """
        Fetch REAL option last traded price from Zerodha NFO.
        Returns 0.0 if unavailable — caller uses BS estimate as fallback.
        Constraint 9: this is the primary premium source for the UI.
        """
        if not self._ok:
            return 0.0
        try:
            full = f"NFO:{symbol}"
            raw  = self._kite.ltp([full])
            ltp  = float(raw.get(full, {}).get("last_price", 0) or 0)
            if ltp == 0:
                # Fallback to quote (sometimes ltp() misses non-traded options)
                raw2 = self._kite.quote([full])
                ltp  = float(raw2.get(full, {}).get("last_price", 0) or 0)
            return ltp
        except Exception as exc:
            logger.debug(f"option LTP {symbol}: {exc}")
            return 0.0

    def place_order(self, symbol: str, qty: int,
                    transaction_type: str) -> str | None:
        if not self._ok:
            return None
        try:
            from kiteconnect import KiteConnect
            oid = self._kite.place_order(
                tradingsymbol    = symbol,
                exchange         = "NFO",
                transaction_type = transaction_type,
                quantity         = qty,
                order_type       = self._kite.ORDER_TYPE_MARKET,
                product          = self._kite.PRODUCT_MIS,
                variety          = self._kite.VARIETY_REGULAR,
            )
            logger.info(f"Live order placed: {symbol} qty={qty} → id={oid}")
            return str(oid)
        except Exception as exc:
            logger.error(f"Order failed {symbol}: {exc}")
            return None
