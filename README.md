# SAR Options Trading System



Intraday NIFTY & BANKNIFTY options trading system combining **Parabolic SAR**
(start=0.02, increment=0.02, max=0.2) with **12 candlestick patterns** for CE/PE entry
and exit. Runs via a local web dashboard for both **Paper** and **Live** trading.

---

## Strategy Summary

| Component | Detail |
|---|---|
| Entry | SAR direction agrees with candlestick pattern direction |
| Stop Loss | Trailing Parabolic SAR (updated on every 5-min candle close) |
| Exit | SAR reversal OR candlestick reversal (≥83% immediate; 80–82% wait for SAR) |
| Volume gate | **Disabled** — patterns evaluated on shape + SAR only |
| Candle window | **3 candles only** (strict — never 5) |
| No new trade | After **3:05 PM** |
| Square off | All open trades closed by **3:15 PM** |
| NIFTY | Current **weekly** contract (Thursday expiry) |
| BANKNIFTY | **Monthly** contract (last Wednesday of month) |

---

## Prerequisites

| Item | Requirement |
|---|---|
| Python | 3.11 or 3.12 |
| OS | Windows 10/11 or Ubuntu 20.04+ |
| Zerodha | Active trading account with API access enabled |
| Internet | Stable connection during market hours (9:15 AM – 3:30 PM IST) |

---

## Installation

### Step 1 — Clone / Extract

```bash
# Windows PowerShell
Expand-Archive nifty_sar_system.zip -DestinationPath C:\trading\
cd C:\trading\nifty_sar_system

# Linux / macOS
unzip nifty_sar_system.zip -d ~/trading/
cd ~/trading/nifty_sar_system
```

### Step 2 — Create virtual environment (recommended)

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Linux / macOS
python3 -m venv venv
source venv/bin/activate
```

### Step 3 — Install dependencies

```bash
pip install -r requirements.txt
```

### Step 4 — Configure Zerodha credentials

Open `config.py` in a text editor and fill in your credentials:

```python
ZERODHA_API_KEY    = "your_api_key_here"
ZERODHA_API_SECRET = "your_api_secret_here"
# Leave ZERODHA_ACCESS_TOKEN blank — it is set via the dashboard login
```

> **How to get API credentials:**
> 1. Log in to [kite.zerodha.com](https://kite.zerodha.com)
> 2. Go to **My Account → API**
> 3. Create an app, set the redirect URL to `http://localhost:5000/zerodha/callback`
> 4. Copy the **API Key** and **API Secret** into `config.py`

---

## Running the System

### Option A — Run directly (foreground, for testing)

```bash
python app.py
```

Open your browser: **http://localhost:5000**

### Option B — Run as background service (recommended for trading)

```bash
# Install — survives desktop lock, starts on login automatically
python install_service.py

# Check status
python install_service.py status

# Remove
python install_service.py remove
```

The service runs completely in the background. The dashboard at
**http://localhost:5000** is available even when no terminal window is open.

---

## Daily Workflow

### Every trading day

1. **Open dashboard** → http://localhost:5000
2. **Click "Login with Zerodha"** → redirects to Zerodha → returns with token
   - OR paste today's access token directly into the **Option B** field
3. **Select mode**: Paper (simulate) or Live (real orders)
4. **Click ▶ Start** — system begins polling every 30 seconds
5. Monitor signals, positions, and P&L in real time
6. System automatically squares off all open trades at **3:15 PM**

> The Zerodha access token expires at midnight every day.
> You must login again each morning.

---

## Dashboard Sections

| Section | What it shows |
|---|---|
| Connection card | OAuth login, manual token entry, mode switch |
| Instrument cards | Live LTP, SAR value/direction/AF, current 5-min candle, signal |
| Performance | Total trades, wins, losses, net P&L today |
| Open Positions | Live positions with real option LTP and trailing SAR SL |
| Closed Trades | History with entry/exit premium, P&L, exit reason |
| System Log | Streaming log with filter and download |

---

## Premium Display (Accuracy)

- **Real LTP**: Fetched from Zerodha NFO (`kite.ltp`) every 30 seconds per position.
- **Estimated** tag (amber): Shown when Zerodha returns 0 for LTP (option not yet
  traded, pre-market, or symbol mismatch). A Black-Scholes estimate is used as a
  temporary fallback. The system logs a warning after 3 consecutive zero-LTP ticks
  and shows the last known real value when available.
- P&L = `(current_premium − entry_premium) × lot_size`

---

## Parabolic SAR Parameters

| Parameter | Value | Effect |
|---|---|---|
| start (initial AF) | 0.02 | How quickly SAR reacts initially |
| increment | 0.02 | AF step per new extreme point |
| max | 0.20 | Maximum sensitivity (AF cap) |

SAR is computed on **completed 5-minute candles** only.
The system waits for 5 candles before considering SAR reliable.

---

## Contract Selection

### NIFTY (weekly)
- Picks the **current Thursday's expiry**.
- If today is Thursday (expiry day), uses **next Thursday**.
- Symbol format: `NIFTY{YY}{M}{DD}{STRIKE}{CE/PE}` e.g. `NIFTY2651424300CE`
- Strike: ITM (one step below spot for CE, one step above for PE)

### BANKNIFTY (monthly)
- Picks the **last Wednesday of the current month**.
- If today is past that date, uses **next month's last Wednesday**.
- Symbol format: `BANKNIFTY{YY}{MMM}{STRIKE}{CE/PE}` e.g. `BANKNIFTY26MAY55800CE`
- Strike: ATM (rounded to nearest 100)

---

## Configuration Reference (`config.py`)

| Setting | Default | Description |
|---|---|---|
| `SAR_START` | 0.02 | SAR initial acceleration factor |
| `SAR_INCREMENT` | 0.02 | AF increment per new extreme |
| `SAR_MAX` | 0.20 | Maximum AF |
| `POLL_INTERVAL_SECONDS` | 30 | How often to poll Zerodha |
| `NO_NEW_TRADE_HOUR/MINUTE` | 15:05 | Cut-off for new entries |
| `SQUARE_OFF_HOUR/MINUTE` | 15:15 | Force-close all positions |
| `PREMIUM_SL_PCT` | 0.25 | 25% premium drop backstop |
| `TREND_FILTER_PCT` | 0.5 | SMA20 alignment tolerance |
| `FLASK_PORT` | 5000 | Dashboard port |

---

## File Structure

```
nifty_sar_system/
├── app.py              ← Flask application + trading pipeline
├── config.py           ← All configuration (edit API keys here)
├── candle_builder.py   ← 5-minute OHLCV candle assembly
├── parabolic_sar.py    ← Parabolic SAR (Wilder algorithm)
├── pattern_engine.py   ← 12 candlestick patterns (no volume gate)
├── data_feed.py        ← Zerodha KiteConnect + contract selection
├── trade_engine.py     ← Position management + trailing SAR SL
├── logger_setup.py     ← Rotating log + SSE broadcast
├── install_service.py  ← Background service installer
├── templates/
│   └── index.html      ← Dashboard UI
├── requirements.txt
└── README.md
```

---

## Troubleshooting

**Dashboard not loading**
- Check that port 5000 is not blocked by firewall.
- Try `http://127.0.0.1:5000` instead.

**"Zerodha not connected" after login**
- Access token expires at midnight — re-login each morning.
- Verify `ZERODHA_API_KEY` and `ZERODHA_API_SECRET` in `config.py`.
- Redirect URL in your Zerodha app must be `http://localhost:5000/zerodha/callback`.

**P&L showing ₹0 or "Est" tag**
- Check that the option symbol is correct (visible in position row tooltip).
- Ensure your Zerodha account has NFO segment enabled.
- Real LTP may be 0 for deep ITM/OTM or illiquid strikes at market open.

**No signals firing**
- SAR needs at least 5 candles (25 minutes) after market open to initialise.
- Signals require SAR direction to agree with the candlestick pattern.
- Check the log for "blocked" messages explaining why a pattern was rejected.

**System not running after desktop lock**
- Use `python install_service.py` to register as an OS service.
- OS services survive lock, logout, and reboot without any terminal window.

---

## Disclaimer

This software is for educational and informational purposes only.
Trading in financial markets involves substantial risk of loss.
Past performance of any strategy does not guarantee future results.
The authors accept no liability for losses incurred through the use of this system.
Always paper-trade and verify thoroughly before using live funds.
