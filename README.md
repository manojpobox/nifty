# ⚡ NIFTYBOX — High-Performance Localhost Terminal

**NIFTYBOX** is a professional-grade quantitative options, futures, and spot trading terminal running on **localhost:8080** and integrated directly with **FYERS API v3**.

---

## 🚀 Key Features

1. **FYERS API v3 Integration with Dual Login Modes:**
   - **1-Click OAuth Redirect Interception:** Since NIFTYBOX listens directly on `http://127.0.0.1:8080`, clicking **"1-Click Authorize with FYERS"** launches the authorization flow and seamlessly captures the redirect code at `/callback`, generating both the daily `access_token` and the **15-day auto-renewal `refresh_token`** with 0 copy-pasting.
   - **Automated Desktop TOTP Login:** Implemented with `pyotp` for automatic headless desktop authentication using your TOTP secret key and PIN.
   - **Live 2FA TOTP Card:** Displays the current 6-digit TOTP code with a real-time 30-second countdown timer right in the dashboard header.

2. **Zero-Duplicate Intraday Data Hub:**
   - Saves all intraday market feeds into `C:\Nifty\data\`:
     - `NIFTY_OI_{date}.csv`: ATM $\pm 2$ corridor snapshots per minute.
     - `NIFTY_FULL_OPTIONS_{date}.csv`: Complete option chain across all strikes (with IV, Delta, Gamma, Theta, Vega, Volume, and OI).
     - `NIFTY_FUT_SPOT_{date}.csv`: Spot & Futures tick data (Time, Spot, Fut, Basis Spread, Net Delta, PCR, Signals).
     - `NIFTY_CANDLES_{1m,5m,15m}_{date}.csv`: Deduplicated OHLCV candles with Cumulative Volume Delta (CVD).
     - `corridor_oi_NIFTY_{date}.json`: Timestamped corridor boundary snapshots.
   - **Zero-Duplicate Protection:**
     - Enforces 1-per-minute write idempotency (resolves BUG-014 duplicate daemon writes).
     - Ingestion-level `.drop_duplicates(subset=["epoch"], keep="last")` (resolves BUG-013 Fyers chunk repetition).
     - Market hours isolation (excludes pre-market contamination BUG-019).

3. **Quantitative Signal Engines:**
   - **Signal 1 (Pure OI Buildup):** Classifies price direction vs corridor Call/Put writing into Long Build-up (🟢), Short Covering (🔵), Short Build-up (🔴), Long Unwinding (⚪), or Consolidation (⚪).
   - **Signal 2 (Institutional Smart Trap Alert):** Detects **⚡ Bear Traps** (Call Squeeze) and **⚡ Bull Traps** (Long Liquidation) by cross-referencing order flow delta and aggressive Futures Buy/Sell volume.
   - **Cumulative Volume Delta (CVD):** Real-time order flow tracking using the Lee-Ready algorithm, with automatic 09:15 AM morning resets and revised NSE 65-lot sizing.
   - **EOD Radar & Next-Day Overnight Bias:** Evaluates 4-Quadrant flow confluence, late-session CVD divergence, and ATM Straddle values to generate institutional overnight gap forecasts.

---

## 🛠️ Configuration Details

- **App ID:** `O4IU017L3R-100` (Fallback: `J1AXE2DYZH-100`)
- **Secret ID:** `12MAV54KZB`
- **Redirect URL:** `http://127.0.0.1:8080/callback`
- **Fyers ID (User ID):** `XM05617`
- **Localhost Port:** `8080`

---

## 🏃 Running NIFTYBOX

### 1-Click Launch:
Double-click `C:\Nifty\start_niftybox.bat`

### Or from Terminal:
```powershell
cd C:\Nifty
python server.py
```
Open **[http://127.0.0.1:8080](http://127.0.0.1:8080)** in any browser.
