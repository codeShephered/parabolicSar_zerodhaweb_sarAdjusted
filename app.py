"""
app.py — SAR Options Trading System (Flask).

6-Layer pipeline (per WebSocket tick — replaces 30-second polling):
  L1 : Receive Zerodha KiteTicker WebSocket tick (every ~100 ms)
  L2 : Update 5-min candle builder with tick price
  L3 : Update Parabolic SAR (on candle close only)
  L4 : Exit checks — SAR SL (every tick) + candle-close exits
       Candle-close exits — SAR reversal + pattern reversal
  L5 : Entry — pattern + SAR alignment → CE/PE signal
       SAR position guard (Change 2): SAR must be strictly BELOW
       candle close for CE, strictly ABOVE candle close for PE
  L6 : Place paper / live trade

Constraints enforced:
  • No volume gate (7)
  • 3-candle window for patterns (6)
  • No new trade after 3:05 PM (11)
  • Force-close all by 3:15 PM (11)
  • NIFTY weekly / BANKNIFTY monthly (8)
  • Accurate real LTP displayed (9)

Change log:
  [Change 1] Replaced 30-second REST polling (_scheduler/_poll) with
             Zerodha KiteTicker WebSocket subscription. Ticks arrive
             every ~100 ms eliminating candle close detection drift.
             KiteTicker handles its own reconnect loop.
  [Change 2] Added strict SAR position guard at entry (L5):
             CE blocked when SAR >= candle close price (SAR not below price)
             PE blocked when SAR <= candle close price (SAR not above price)
             This is a price-level check on top of the existing sar_bull
             direction flag, preventing entries where SAR is ambiguous.
"""
from __future__ import annotations
import json, os, queue, threading
from datetime import datetime

from flask import (Flask, render_template, Response, jsonify,
                   request, redirect, url_for, send_file)

import config
from logger_setup import setup_logger, get_module_logger, get_log_entries, LOG_QUEUE
from data_feed import ZerodhaFeed, bs_estimate, get_nifty_expiry, get_banknifty_expiry
from candle_builder import CandleBuilder
from pattern_engine import PatternEngine
from parabolic_sar import ParabolicSAR
from trade_engine import TradeEngine

# ── NSE Index WebSocket instrument tokens (Zerodha standard) ─────────────────
# These are the permanent Zerodha tokens for NSE indices — do not change.
_NSE_TOKENS: dict[int, str] = {
    256265: "NIFTY",      # NSE:NIFTY 50
    260105: "BANKNIFTY",  # NSE:NIFTY BANK
}
_TOKEN_BY_INST: dict[str, int] = {v: k for k, v in _NSE_TOKENS.items()}

# Market-hours guard used in on_ticks to reject pre/post-market ticks
from datetime import time as _dtime
_MARKET_OPEN  = _dtime(9, 15)
_MARKET_CLOSE = _dtime(15, 30)

app = Flask(__name__)
app.secret_key = config.SECRET_KEY

_root = setup_logger("trading")
logger = get_module_logger("App")

# ── Shared state ──────────────────────────────────────────────────────────────
state: dict = {
    "mode":        config.TRADING_MODE,
    "running":     False,
    "connected":   False,
    "last_tick":   None,
    "market":      {"NIFTY": {}, "BANKNIFTY": {}},
    "signals":     {"NIFTY": {}, "BANKNIFTY": {}},
    "sar":         {"NIFTY": {}, "BANKNIFTY": {}},
    "candle":      {"NIFTY": {}, "BANKNIFTY": {}},
    "next_expiry": {},
}

zerodha_feed: ZerodhaFeed | None  = None
_ticker      = None                # KiteTicker WebSocket instance
_ticker_lock = threading.Lock()

candle_builders = {
    "NIFTY":     CandleBuilder("NIFTY"),
    "BANKNIFTY": CandleBuilder("BANKNIFTY"),
}
sar_trackers = {
    "NIFTY":     ParabolicSAR(),
    "BANKNIFTY": ParabolicSAR(),
}
sar_prev_bull: dict[str, bool | None] = {"NIFTY": None, "BANKNIFTY": None}

pattern_engine = PatternEngine()
trade_engine   = TradeEngine()

_sse_clients: list[queue.Queue] = []
_sse_lock    = threading.Lock()
_stop_evt    = threading.Event()


# ── SSE broadcast ─────────────────────────────────────────────────────────────

def _broadcast(data: str) -> None:
    with _sse_lock:
        dead = [q for q in _sse_clients if not _try_put(q, data)]
        for q in dead:
            _sse_clients.remove(q)

def _try_put(q: queue.Queue, data: str) -> bool:
    try:
        q.put_nowait(data); return True
    except queue.Full:
        return False

def _log_loop() -> None:
    while True:
        try:
            _broadcast(LOG_QUEUE.get(timeout=1))
        except queue.Empty:
            pass


# ── Zerodha helpers ───────────────────────────────────────────────────────────

def _feed_ok() -> bool:
    return zerodha_feed is not None and zerodha_feed.is_connected()

def _real_ltp(pos, spot: float, instrument: str) -> float:
    """
    Constraint 9 — always try real LTP first.
    Falls back to last known value then BS estimate.
    """
    if _feed_ok():
        ltp = zerodha_feed.get_option_ltp(pos.zerodha_symbol)
        if ltp > 0:
            return ltp
    # Keep last known (do not disturb P&L with estimated value mid-trade)
    return pos.current_premium if pos.current_premium > 0 else bs_estimate(
        instrument, spot, pos.strike, pos.option_type,
        max((pos.expiry - __import__("datetime").date.today()).days, 1),
    )

def _reconnect() -> None:
    global zerodha_feed
    if zerodha_feed and config.ZERODHA_ACCESS_TOKEN:
        zerodha_feed.set_token(config.ZERODHA_ACCESS_TOKEN)
        state["connected"] = zerodha_feed.is_connected()


# ── Pipeline ──────────────────────────────────────────────────────────────────
# [Change 1] _process() is now called from the KiteTicker on_ticks callback
# instead of the 30-second scheduler. Each WebSocket tick (~100 ms) drives the
# candle builder. Candle close is detected when the slot changes — exactly as
# before — but the price that triggers the close is the first tick of the new
# slot, which arrives within ~100 ms of the candle boundary rather than up to
# 30 seconds later. This eliminates the candle close detection drift.

def _process(instrument: str, price: float) -> None:
    """
    Process one price tick for one instrument.
    Called from the KiteTicker on_ticks callback — NOT from a REST poll.

    Args:
        instrument : "NIFTY" or "BANKNIFTY"
        price      : last_price from the WebSocket tick
    """
    state["market"][instrument] = {"last_price": price}

    cb  = candle_builders[instrument]
    sar = sar_trackers[instrument]

    # ── L2: candle builder ────────────────────────────────────────────────────
    completed = cb.update(price)
    cur = cb.current()
    if cur:
        state["candle"][instrument] = cur.to_dict()

    # ── L4 (tick): SAR SL check — runs on EVERY tick ──────────────────────────
    if trade_engine.has_open(instrument):
        pos = trade_engine.get_pos(instrument)
        if pos:
            ltp = _real_ltp(pos, price, instrument)
            result = trade_engine.check_tick(instrument, price, ltp)
            if result:
                state["signals"][instrument] = {
                    "type": "exit", "reason": result["exit_reason"],
                    "ts":   result["exit_time"],
                }
                return

    # ── Only on candle close ───────────────────────────────────────────────────
    if completed is None:
        return

    # ── L3: Parabolic SAR — update on candle close only ───────────────────────
    sar_val, sar_bull = sar.update(completed)
    if sar_val is not None:
        state["sar"][instrument] = sar.to_dict()
        prev = sar_prev_bull[instrument]
        sar_reversed = prev is not None and prev != sar_bull
        sar_prev_bull[instrument] = sar_bull
    else:
        sar_val, sar_bull, sar_reversed = 0.0, True, False

    last3 = cb.get_last_n(3)   # constraint 6: EXACTLY 3 candles

    # ── L4 (candle): reversal pattern + SAR flip ──────────────────────────────
    if trade_engine.has_open(instrument):
        pos = trade_engine.get_pos(instrument)
        if pos:
            rev, _ = pattern_engine.is_reversal_of(last3, pos.direction)
            ltp = _real_ltp(pos, price, instrument)
            result = trade_engine.check_candle(
                instrument, ltp, sar_val, sar_reversed, rev or "")
            if result:
                state["signals"][instrument] = {
                    "type": "exit", "reason": result["exit_reason"],
                    "ts":   result["exit_time"],
                }
        return   # no new entry while position open

    # ── L5: entry — SAR + pattern ─────────────────────────────────────────────
    if not trade_engine.can_enter():
        return
    if not sar.is_ready or not cb.has_min(config.SAR_SEED_CANDLES):
        return

    # SMA20 trend alignment (prevents CE in downtrend, PE in uptrend)
    hist    = cb.get_last_n(25)
    sma_n   = min(20, len(hist))
    sma20   = sum(c.close for c in hist[-sma_n:]) / sma_n if sma_n else price
    below   = (sma20 - price) / sma20 * 100
    above   = (price - sma20) / sma20 * 100

    # Read filter thresholds — supports both config.py versions:
    #   Old: TREND_FILTER_PCT = 0.5  (symmetric)
    #   New: TREND_FILTER_BULLISH_PCT / TREND_FILTER_BEARISH_PCT (asymmetric)
    _sym   = getattr(config, "TREND_FILTER_PCT",         0.5)
    _bull  = getattr(config, "TREND_FILTER_BULLISH_PCT", _sym)
    _bear  = getattr(config, "TREND_FILTER_BEARISH_PCT", _sym)

    pattern, direction = pattern_engine.scan(last3)
    if not pattern:
        state["signals"][instrument] = {"type": "none"}
        return

    # Reject if direction opposes trend (no volume gate — shape + SAR only)
    if direction == "bullish" and below > _bull:
        logger.debug(f"{instrument}: {pattern} bullish blocked — {below:.1f}% below SMA20")
        state["signals"][instrument] = {"type": "none"}
        return
    if direction == "bearish" and above > _bear:
        logger.debug(f"{instrument}: {pattern} bearish blocked — {above:.1f}% above SMA20")
        state["signals"][instrument] = {"type": "none"}
        return

    # SAR direction gate — SAR must agree with the pattern signal
  '''
    if direction == "bullish" and not sar_bull:
        logger.debug(f"{instrument}: {pattern} bullish blocked — SAR is bearish")
        state["signals"][instrument] = {"type": "none"}
        return
    if direction == "bearish" and sar_bull:
        logger.debug(f"{instrument}: {pattern} bearish blocked — SAR is bullish")
        state["signals"][instrument] = {"type": "none"}
        return
        '''
    # ── Pattern-SAR alignment (CORRECTED for REVERSAL patterns) ──────────────
    #
    # REVERSAL PATTERNS (allowed OPPOSITE to SAR):
    #   • Morning Doji Star, Three White Soldiers, Morning Star, Bullish Engulfing, Piercing Line
    #     → Enter BULLISH even if SAR is bearish (bullish reversal at bottom of downtrend)
    #   • Evening Doji Star, Three Black Crows, Evening Star, Bearish Engulfing, Dark Cloud Cover
    #     → Enter BEARISH even if SAR is bullish (bearish reversal at top of uptrend)
    #
    # NON-REVERSAL PATTERNS (require SAR agreement):
    #   • Hammer (1-candle bullish) → requires sar_bull
    #   • Shooting Star (1-candle bearish) → requires not sar_bull
    #
    # Rationale: Reversals form AGAINST the trend. Hammer/Shooting Star form WITH the trend.
 
    REVERSAL_PATTERNS = {
        "Morning Doji Star", "Three White Soldiers", "Morning Star",
        "Bullish Engulfing",
        "Evening Doji Star", "Three Black Crows", "Evening Star",
        "Bearish Engulfing"
    }
 
    is_reversal_pattern = pattern in REVERSAL_PATTERNS
    
    if not is_reversal_pattern:
        # Non-reversal patterns (Hammer, Shooting Star) require SAR agreement
        if direction == "bullish" and not sar_bull:
            logger.debug(
                f"{instrument}: {pattern} bullish requires SAR bullish "
                f"(not reversal) — blocked"
            )
            state["signals"][instrument] = {"type": "none"}
            return
        if direction == "bearish" and sar_bull:
            logger.debug(
                f"{instrument}: {pattern} bearish requires SAR bearish "
                f"(not reversal) — blocked"
            )
            state["signals"][instrument] = {"type": "none"}
            return
    else:
        # Reversal patterns: allowed OPPOSITE to SAR, log the reversal nature
        if direction == "bullish" and not sar_bull:
            logger.debug(
                f"{instrument}: {pattern} (REVERSAL) — bullish signal despite SAR bearish ✓"
            )
        elif direction == "bearish" and sar_bull:
            logger.debug(
                f"{instrument}: {pattern} (REVERSAL) — bearish signal despite SAR bullish ✓"
            )

    # ── [Change 2] SAR position guard (strict price-level check) ─────────────
    # This is a second, independent guard that checks the ACTUAL SAR VALUE
    # against the completed candle's CLOSE PRICE — not just the direction flag.
    #
    # CE entry: SAR must be STRICTLY BELOW candle close
    #   SAR >= close means SAR is at or above price → market is not in an
    #   uptrend at this candle → CE entry is structurally invalid regardless
    #   of what the pattern signal says.
    #
    # PE entry: SAR must be STRICTLY ABOVE candle close
    #   SAR <= close means SAR is at or below price → market is not in a
    #   downtrend at this candle → PE entry is structurally invalid regardless
    #   of what the pattern signal says.
    #
    # Why this is needed on top of the sar_bull flag:
    #   sar_bull reflects the last SAR flip direction (can be from 3+ candles ago)
    #   sar_val is updated every candle — if SAR has crept close to price since
    #   the last flip, sar_bull is still True but the SAR is no longer clearly
    #   below price. This guard catches that ambiguous zone.
    candle_close = completed.close
    if direction == "bullish" and sar_val >= candle_close:
        logger.debug(
            f"{instrument}: {pattern} CE blocked — SAR ({sar_val:.2f}) "
            f">= candle close ({candle_close:.2f}) — SAR not below price"
        )
        state["signals"][instrument] = {"type": "none"}
        return
    if direction == "bearish" and sar_val <= candle_close:
        logger.debug(
            f"{instrument}: {pattern} PE blocked — SAR ({sar_val:.2f}) "
            f"<= candle close ({candle_close:.2f}) — SAR not above price"
        )
        state["signals"][instrument] = {"type": "none"}
        return

    # ── L6: place trade ────────────────────────────────────────────────────────
    signal = trade_engine.build_signal(instrument, direction, price, pattern, sar_val)

    # Real option LTP (constraint 9)
    real_ltp = zerodha_feed.get_option_ltp(signal["zerodha_symbol"]) if _feed_ok() else 0.0

    if state["mode"] == "live" and _feed_ok():
        trade_engine.place_live_order(signal, zerodha_feed)

    pos = trade_engine.enter(signal, real_ltp)

    state["signals"][instrument] = {
        "type":      "entry",
        "direction": direction,
        "pattern":   pattern,
        "opt_type":  signal["option_type"],
        "strike":    signal["strike"],
        "expiry":    signal["expiry_str"],
        "sar_sl":    signal["sar_sl"],
        "premium":   pos.entry_premium,
        "estimated": pos.is_estimated,
        "ts":        datetime.now().strftime("%H:%M:%S"),
    }


# ── [Change 1] KiteTicker WebSocket feed ─────────────────────────────────────
# Replaces _scheduler() / _poll() / 30-second REST polling entirely.
# KiteTicker maintains a persistent WebSocket to Zerodha and pushes
# price ticks every ~100 ms. No polling loop, no drift.

def _start_ticker(api_key: str, access_token: str) -> None:
    """
    Start the KiteTicker WebSocket subscription for NIFTY and BANKNIFTY.
    Called ONLY from api_start (when user clicks Start on the dashboard).
    NOT called at login time — starting before market hours causes error 1006
    because Zerodha drops idle pre-market WebSocket connections.

    Fixes applied for error 1006:
      - connect_timeout=60   : longer handshake window (default 30 was too short
                               on slow networks / macOS SSL negotiation)
      - reconnect_max_delay=60: use Zerodha-tested default (not 5 — too aggressive)
      - reconnect_max_tries=300: maximise retry window for a full trading day
      - on_close auto-restart : if Zerodha server closes the connection (1006)
                                and reconnect fails, restart the ticker cleanly
      - Market-hours guard    : ignore ticks before 09:15 and after 15:30 to
                                avoid processing stale pre/post-market data
    """
    global _ticker

    with _ticker_lock:
        if _ticker is not None:
            try:
                _ticker.stop()
            except Exception:
                pass
            _ticker = None

    from kiteconnect import KiteTicker

    ticker = KiteTicker(
        api_key,
        access_token,
        reconnect           = True,
        reconnect_max_tries = 300,   # enough for a full 6.25-hour trading day
        reconnect_max_delay = 60,    # Zerodha-tested default — do not set below 5
        connect_timeout     = 60,    # longer window for slow networks + macOS SSL
    )
    tokens = list(_NSE_TOKENS.keys())   # [256265, 260105]

    def on_ticks(ws, ticks: list) -> None:
        """
        Receives price ticks from Zerodha WebSocket every ~100 ms.
        Market-hours guard prevents processing of pre/post-market noise.
        """
        if not state["running"]:
            return

        # Market-hours guard — ignore ticks outside NSE trading hours
        now = datetime.now().time()
        if now < _MARKET_OPEN or now > _MARKET_CLOSE:
            return

        state["last_tick"] = datetime.now().strftime("%H:%M:%S")
        state["connected"] = True

        for tick in ticks:
            token = tick.get("instrument_token")
            inst  = _NSE_TOKENS.get(token)
            if inst is None:
                continue
            price = float(tick.get("last_price", 0) or 0)
            if price <= 0:
                continue
            try:
                _process(inst, price)
            except Exception as exc:
                logger.error(f"Tick error {inst}: {exc}", exc_info=True)

        # 3:15 PM square-off (time-gated inside must_square_off)
        if trade_engine.must_square_off():
            for c in trade_engine.force_close_all():
                logger.info(
                    f"Square-off {c['instrument']} {c['option_type']}  "
                    f"PnL=₹{c['final_pnl']:.2f}"
                )

    def on_connect(ws, response) -> None:
        logger.info("KiteTicker WebSocket connected ✓ — subscribing to NIFTY + BANKNIFTY")
        ws.subscribe(tokens)
        ws.set_mode(ws.MODE_QUOTE, tokens)
        state["connected"] = True

    def on_reconnect(ws, attempts_count) -> None:
        logger.warning(f"KiteTicker reconnecting … attempt {attempts_count}")
        state["connected"] = False

    def on_noreconnect(ws) -> None:
        """
        All reconnect attempts exhausted.
        Try a clean restart before giving up entirely.
        """
        logger.error(
            "KiteTicker: all reconnect attempts exhausted.\n"
            "  Attempting a clean ticker restart …"
        )
        state["connected"] = False
        if state["running"] and config.ZERODHA_ACCESS_TOKEN:
            import threading as _t
            _t.Timer(5.0, _start_ticker,
                     args=(config.ZERODHA_API_KEY,
                           config.ZERODHA_ACCESS_TOKEN)).start()
        else:
            state["running"] = False
            logger.error("KiteTicker: could not restart — system stopped")

    def on_error(ws, code, reason) -> None:
        logger.error(f"KiteTicker error {code}: {reason}")
        # Error 1006 = server dropped TCP without WebSocket close handshake.
        # KiteTicker's built-in reconnect will handle it automatically.
        # No manual action needed — just log and let reconnect loop run.

    def on_close(ws, code, reason) -> None:
        logger.warning(f"KiteTicker closed: code={code}  reason={reason}")
        state["connected"] = False
        # If the system is still meant to be running (e.g. Zerodha server-side
        # 1006 drop during market hours), schedule a clean restart after 3 s.
        if state["running"] and code == 1006 and config.ZERODHA_ACCESS_TOKEN:
            logger.info("KiteTicker: scheduling restart in 3 s after 1006 close …")
            import threading as _t
            _t.Timer(3.0, _start_ticker,
                     args=(config.ZERODHA_API_KEY,
                           config.ZERODHA_ACCESS_TOKEN)).start()

    def on_order_update(ws, data) -> None:
        logger.info(f"Order update: {data}")

    ticker.on_ticks        = on_ticks
    ticker.on_connect      = on_connect
    ticker.on_reconnect    = on_reconnect
    ticker.on_noreconnect  = on_noreconnect
    ticker.on_error        = on_error
    ticker.on_close        = on_close
    ticker.on_order_update = on_order_update

    with _ticker_lock:
        _ticker = ticker

    ticker.connect(threaded=True)
    logger.info(
        f"KiteTicker started — tokens: NIFTY={tokens[0]} BANKNIFTY={tokens[1]}"
    )


def _stop_ticker() -> None:
    """Gracefully stop the KiteTicker WebSocket."""
    global _ticker
    with _ticker_lock:
        if _ticker is not None:
            try:
                _ticker.stop()
                logger.info("KiteTicker stopped")
            except Exception as exc:
                logger.warning(f"KiteTicker stop error: {exc}")
            _ticker = None
    state["connected"] = False


# ── Watchdog — monitors WebSocket health (replaces old _scheduler) ─────────
def _watchdog() -> None:
    """
    Lightweight watchdog thread. The heavy lifting (price ticks, candle
    building, signal detection) is now done by KiteTicker's own thread.
    This thread only:
      - Detects system sleep/wake (>90 s gap) and triggers ticker reconnect
      - Logs periodic heartbeats for monitoring
    """
    import time as _t
    last = _t.monotonic()
    while not _stop_evt.is_set():
        _stop_evt.wait(60)
        elapsed = _t.monotonic() - last
        last    = _t.monotonic()
        if elapsed > 90:
            logger.info(f"Woke from sleep (~{int(elapsed-60)}s) — restarting ticker")
            if _feed_ok() and config.ZERODHA_ACCESS_TOKEN:
                _start_ticker(config.ZERODHA_API_KEY, config.ZERODHA_ACCESS_TOKEN)


# ── Flask routes ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    cfg   = config.INSTRUMENTS
    expiry_info = {
        "NIFTY":     get_nifty_expiry().strftime("%d-%b-%Y"),
        "BANKNIFTY": get_banknifty_expiry().strftime("%d-%b-%Y"),
    }
    return render_template("index.html",
                           mode=state["mode"],
                           running=state["running"],
                           connected=state["connected"],
                           expiry_info=expiry_info)

@app.route("/api/save-key", methods=["POST"])
def api_save_key():
    """Store the API key before OAuth redirect so the callback can use it."""
    data = request.get_json(silent=True) or {}
    key  = data.get("api_key", "").strip()
    if not key:
        return jsonify({"error": "Empty API key"}), 400
    config.ZERODHA_API_KEY = key
    logger.info(f"API key saved for OAuth flow: {key[:6]}…")
    return jsonify({"status": "ok"})

@app.route("/stream")
def stream():
    def gen():
        cq = queue.Queue(maxsize=1000)
        with _sse_lock:
            _sse_clients.append(cq)
        for e in get_log_entries()[-100:]:
            yield f"data: {json.dumps(e)}\n\n"
        try:
            while True:
                try:
                    yield f"data: {cq.get(timeout=25)}\n\n"
                except queue.Empty:
                    yield ": ping\n\n"
        except GeneratorExit:
            with _sse_lock:
                try: _sse_clients.remove(cq)
                except ValueError: pass
    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/status")
def api_status():
    return jsonify({
        "mode":       state["mode"],
        "running":    state["running"],
        "connected":  state["connected"],
        "last_tick":  state["last_tick"],
        "market":     state["market"],
        "signals":    state["signals"],
        "sar":        state["sar"],
        "candle":     state["candle"],
        "positions":  trade_engine.all_positions(),
        "history":    trade_engine.history()[-20:],
        "stats":      trade_engine.get_stats(),
        "expiry":     {
            "NIFTY":     get_nifty_expiry().strftime("%d-%b-%Y"),
            "BANKNIFTY": get_banknifty_expiry().strftime("%d-%b-%Y"),
        },
    })


@app.route("/api/start", methods=["POST"])
def api_start():
    if state["running"]:
        return jsonify({"status": "already_running"})
    if not _feed_ok():
        return jsonify({"error": "Zerodha not connected — login first"}), 400
    state["running"] = True
    # [Change 1] Start WebSocket ticker when system is started
    _start_ticker(config.ZERODHA_API_KEY, config.ZERODHA_ACCESS_TOKEN)
    logger.info(f"System STARTED  mode={state['mode'].upper()}")
    return jsonify({"status": "started", "mode": state["mode"]})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    state["running"] = False
    # [Change 1] Stop WebSocket ticker when system is stopped
    _stop_ticker()
    logger.info("System STOPPED by user")
    return jsonify({"status": "stopped"})


@app.route("/api/mode", methods=["POST"])
def api_mode():
    if state["running"]:
        return jsonify({"error": "Stop the system before switching mode"}), 400
    data = request.get_json(silent=True) or {}
    m    = data.get("mode", "paper")
    if m not in ("paper", "live"):
        return jsonify({"error": "Invalid mode"}), 400
    state["mode"] = m
    config.TRADING_MODE = m
    logger.info(f"Mode switched → {m.upper()}")
    return jsonify({"status": "ok", "mode": m})


@app.route("/zerodha/login")
def zerodha_login():
    if not config.ZERODHA_API_KEY:
        return "Set ZERODHA_API_KEY in config.py first", 400
    return redirect(ZerodhaFeed.login_url(config.ZERODHA_API_KEY))


@app.route("/zerodha/callback")
@app.route("/callback")
def zerodha_callback():
    global zerodha_feed
    req_token = request.args.get("request_token", "")
    if not req_token:
        return "Missing request_token", 400
    if zerodha_feed is None:
        zerodha_feed = ZerodhaFeed(config.ZERODHA_API_KEY)
    token = zerodha_feed.generate_session(req_token, config.ZERODHA_API_SECRET)
    if token:
        config.ZERODHA_ACCESS_TOKEN = token
        state["connected"] = True
        logger.info("Zerodha OAuth login successful ✓")
        # Ticker is NOT started here — it starts only when user clicks Start.
        # Starting at login time causes error 1006 because the WebSocket
        # connects before market hours and Zerodha drops idle connections.
        return redirect(url_for("index"))
    logger.error(
        "Zerodha OAuth callback received but session generation failed.\n"
        "  Check that ZERODHA_API_SECRET in config.py is correct.\n"
        f"  request_token received: {req_token[:8]}…"
    )
    return (
        "<h2>Zerodha authentication failed</h2>"
        "<p>API secret may be wrong. Check <code>ZERODHA_API_SECRET</code> in config.py.</p>"
        "<p><a href='/'>← Back to dashboard</a></p>"
    )
    # if token:
    #     config.ZERODHA_ACCESS_TOKEN = token
    #     state["connected"] = True
    #     logger.info("Zerodha OAuth login successful ✓")
    #     return redirect(url_for("index"))
    # return "Authentication failed — check API key/secret in config.py", 400


@app.route("/api/set-token", methods=["POST"])
def api_set_token():
    global zerodha_feed
    data  = request.get_json(silent=True) or {}
    token = data.get("access_token", "").strip()
    key   = data.get("api_key", config.ZERODHA_API_KEY).strip()
    if not token:
        return jsonify({"error": "Token is empty"}), 400
    if not key:
        return jsonify({"error": "API key not set — add to config.py or pass here"}), 400
    config.ZERODHA_API_KEY      = key
    config.ZERODHA_ACCESS_TOKEN = token
    if zerodha_feed is None:
        zerodha_feed = ZerodhaFeed(key, token)
    else:
        zerodha_feed._api_key = key
        zerodha_feed.set_token(token)
    state["connected"] = zerodha_feed.is_connected()
    if state["connected"]:
        logger.info("Zerodha: manual token accepted ✓")
        # Ticker starts only on api_start — not here.
        return jsonify({"status": "connected"})
    return jsonify({"status": "failed",
                    "message": "Token rejected — regenerate from kite.zerodha.com"}), 400


@app.route("/api/logs")
def api_logs():
    if not os.path.exists(config.LOG_FILE):
        return jsonify({"error": "Log file not found"}), 404
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(config.LOG_FILE, as_attachment=True,
                     download_name=f"sar_trading_{ts}.log")


@app.route("/health")
def health():
    return jsonify({"running": state["running"], "connected": state["connected"],
                    "last_tick": state["last_tick"]})


# ── Startup ───────────────────────────────────────────────────────────────────

def _init() -> None:
    global zerodha_feed
    logger.info("=" * 62)
    logger.info("  SAR Options Trading System")
    logger.info(f"  Mode          : {state['mode'].upper()}")
    logger.info(f"  SAR params    : start={config.SAR_START} "
                f"increment={config.SAR_INCREMENT} max={config.SAR_MAX}")
    logger.info(f"  No new trade  : after {config.NO_NEW_TRADE_HOUR}:{config.NO_NEW_TRADE_MINUTE:02d} PM")
    logger.info(f"  Square-off    : {config.SQUARE_OFF_HOUR}:{config.SQUARE_OFF_MINUTE:02d} PM")
    logger.info(f"  Volume gate   : DISABLED")
    logger.info(f"  Pattern window: 3 candles (strict)")
    logger.info(f"  Feed          : KiteTicker WebSocket (~100 ms ticks)")  # [Change 1]
    logger.info(f"  NIFTY expiry  : {get_nifty_expiry().strftime('%d-%b-%Y')} (weekly)")
    logger.info(f"  BANKNIFTY exp : {get_banknifty_expiry().strftime('%d-%b-%Y')} (monthly)")
    logger.info("=" * 62)

    if config.ZERODHA_API_KEY and config.ZERODHA_ACCESS_TOKEN:
        token_len = len(config.ZERODHA_ACCESS_TOKEN)
        if token_len < 10:
            logger.info("ZERODHA_ACCESS_TOKEN not set — use dashboard to login")
        else:
            zerodha_feed = ZerodhaFeed(config.ZERODHA_API_KEY,
                                       config.ZERODHA_ACCESS_TOKEN)
            state["connected"] = zerodha_feed.is_connected()
            if not state["connected"]:
                logger.warning(
                    "Zerodha auto-connect failed.\n"
                    "  Most likely cause: yesterday's access_token is still in config.py.\n"
                    "  ACTION: Use the dashboard 'Login with Zerodha' button — do not\n"
                    "  manually paste yesterday's token into config.py."
                )
    else:
        logger.info("Zerodha API key not set — add to config.py, then login via dashboard")

    # [Change 1] Start background threads.
    # _scheduler (30-second REST poll) is REMOVED — replaced by KiteTicker.
    # _watchdog only monitors for system sleep/wake and ticker health.
    threading.Thread(target=_log_loop,  daemon=True,  name="LogBroadcast").start()
    threading.Thread(target=_watchdog,  daemon=False, name="Watchdog").start()
    logger.info(f"Dashboard → http://localhost:{config.FLASK_PORT}")


_init()

if __name__ == "__main__":
    try:
        from waitress import serve
        logger.info("Waitress WSGI server starting")
        serve(app, host=config.FLASK_HOST, port=config.FLASK_PORT, threads=8)
    except ImportError:
        logger.warning("waitress not installed — using Flask dev server")
        app.run(host=config.FLASK_HOST, port=config.FLASK_PORT,
                debug=False, threaded=True, use_reloader=False)
