"""
Fyers API v3 -> Nifty & Sensex options dashboard in Excel
---------------------------------------------------------
Sheets written (created if missing):
    SUMMARY    - key numbers for Nifty (column B) and Sensex (column C)
    NIFTY_OC   - Nifty option chain, current expiry, with IV and Greeks
    SENSEX_OC  - Sensex option chain, current expiry, with IV and Greeks

Run:
    1. Open Excel, save a workbook as  option_chain.xlsx  in this folder, keep it open.
    2. Fill in CLIENT_ID, SECRET_KEY, REDIRECT_URI below.
    3. python fyers_options_dashboard.py
    4. Log in with the printed link. The login is saved in token.txt and reused
       while Fyers still accepts it; otherwise you are asked to log in again.
    5. Ctrl+C in the black window to stop.

Don't type in these three sheets - they are overwritten every refresh.
Build your own sheet with formulas pointing to them, e.g.  =SUMMARY!B2

IV and Greeks are calculated here with Black-Scholes (Fyers doesn't supply them),
so they can differ slightly from the numbers shown in broker apps.
"""

import math
import os
import time
from datetime import date, datetime

import xlwings as xw
from fyers_apiv3 import fyersModel

# ---------- 1. Your app credentials ----------
CLIENT_ID    = "J1AXE2DYZH-100"
SECRET_KEY   = "ZNTJDFJE6N"
REDIRECT_URI = "https://www.google.com"

# ---------- 2. Settings ----------
WORKBOOK     = "option_chain.xlsx"
REFRESH_SECS = 3        # keep 3 or more to stay inside Fyers rate limits
STRIKE_COUNT = 30       # strikes above AND below ATM (more strikes = more accurate Max Pain)
RISK_FREE    = 0.065    # interest rate used for IV / Greeks (6.5%)

INDICES = {
    "NIFTY":  {"spot": "NSE:NIFTY50-INDEX", "fut_prefix": "NSE:NIFTY",  "sheet": "NIFTY_OC",  "col": 2},
    "SENSEX": {"spot": "BSE:SENSEX-INDEX",  "fut_prefix": "BSE:SENSEX", "sheet": "SENSEX_OC", "col": 3},
}
# Futures symbol is detected automatically (current month, else next month).
# If it picks the wrong one, type the exact symbol here, e.g. "NSE:NIFTY26OCTFUT"
FUT_OVERRIDE = {"NIFTY": "", "SENSEX": ""}

VIX_SYMBOL = "NSE:INDIAVIX-INDEX"
TOKEN_FILE = "token.txt"
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

SUMMARY_LABELS = [
    "Spot", "Future", "Future symbol", "Future - Spot (premium)", "VWAP (Fut avg price)",
    "Fut OI", "Fut OI change", "Fut OI change %",
    "ATM strike", "ATM straddle (CE+PE)", "ATM IV %", "PCR (OI)", "Max Pain",
    "Highest Call OI strike", "Highest Call OI", "Highest Put OI strike", "Highest Put OI",
    "Highest Call Vol strike", "Highest Call Volume", "Highest Put Vol strike", "Highest Put Volume",
    "India VIX", "Expiry", "Days to expiry", "Updated",
]

CALL_COLS = ["IV %", "Delta", "Gamma", "Theta", "Vega", "OI", "Chg in OI", "Volume", "LTP", "Chg", "Bid", "Ask"]
PUT_COLS  = ["Bid", "Ask", "Chg", "LTP", "Volume", "Chg in OI", "OI", "Vega", "Theta", "Gamma", "Delta", "IV %"]
HEADER_ROW = 3
MAX_ROWS = 250


# ---------------------------------------------------------------- login ---
def token_works(token):
    """Ask Fyers for the profile to check the token is still accepted."""
    try:
        fy = fyersModel.FyersModel(client_id=CLIENT_ID, token=token, is_async=False, log_path="")
        return fy.get_profile().get("s") == "ok"
    except Exception:
        return False


def get_access_token():
    if "XXXXXXXXXX" in CLIENT_ID or "XXXXXXXXXX" in SECRET_KEY:
        raise SystemExit("Open this file in Notepad and fill in CLIENT_ID, SECRET_KEY and "
                         "REDIRECT_URI near the top, then run it again.")

    if os.path.exists(TOKEN_FILE):
        saved = open(TOKEN_FILE).read().strip().split("\n")[-1].strip()
        if saved and token_works(saved):
            print("Using saved login.")
            return saved
        print("Saved login is no longer valid - please log in again.")

    session = fyersModel.SessionModel(
        client_id=CLIENT_ID, secret_key=SECRET_KEY, redirect_uri=REDIRECT_URI,
        response_type="code", grant_type="authorization_code",
    )
    print("\nOpen this URL, log in, then copy auth_code from the redirected URL:\n")
    print(session.generate_authcode())
    session.set_token(input("\nPaste auth_code here: ").strip())
    resp = session.generate_token()
    if "access_token" not in resp:
        raise SystemExit(f"Login failed: {resp}")
    with open(TOKEN_FILE, "w") as f:
        f.write(resp["access_token"])
    return resp["access_token"]


# ------------------------------------------------------- Black-Scholes ---
def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _npdf(x):
    return math.exp(-x * x / 2) / math.sqrt(2 * math.pi)


def bs_price(S, K, T, r, vol, is_call):
    sq = math.sqrt(T)
    d1 = (math.log(S / K) + (r + vol * vol / 2) * T) / (vol * sq)
    d2 = d1 - vol * sq
    if is_call:
        return S * _ncdf(d1) - K * math.exp(-r * T) * _ncdf(d2)
    return K * math.exp(-r * T) * _ncdf(-d2) - S * _ncdf(-d1)


def implied_vol(price, S, K, T, r, is_call):
    if not price or price <= 0 or not S or T <= 0:
        return None
    disc_k = K * math.exp(-r * T)
    intrinsic = max(0.0, S - disc_k) if is_call else max(0.0, disc_k - S)
    if price <= intrinsic:
        return None
    lo, hi = 0.001, 5.0
    if bs_price(S, K, T, r, hi, is_call) < price:
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if bs_price(S, K, T, r, mid, is_call) < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def greeks(S, K, T, r, vol, is_call):
    sq = math.sqrt(T)
    d1 = (math.log(S / K) + (r + vol * vol / 2) * T) / (vol * sq)
    d2 = d1 - vol * sq
    pdf = _npdf(d1)
    gamma = pdf / (S * vol * sq)
    vega = S * pdf * sq / 100                      # per 1% change in IV
    common = -S * pdf * vol / (2 * sq)
    if is_call:
        delta = _ncdf(d1)
        theta = common - r * K * math.exp(-r * T) * _ncdf(d2)
    else:
        delta = _ncdf(d1) - 1
        theta = common + r * K * math.exp(-r * T) * _ncdf(-d2)
    return delta, gamma, theta / 365, vega         # theta per calendar day


def option_analytics(opt, S, K, T, is_call):
    """Returns [IV%, Delta, Gamma, Theta, Vega] or blanks."""
    iv = implied_vol(opt.get("ltp"), S, K, T, RISK_FREE, is_call)
    if iv is None:
        return [None] * 5
    d, g, th, v = greeks(S, K, T, RISK_FREE, iv, is_call)
    return [round(iv * 100, 2), round(d, 3), round(g, 5), round(th, 2), round(v, 2)]


# ---------------------------------------------------------- data fetch ---
def num(x):
    return x if isinstance(x, (int, float)) else None


def fetch_chain(fyers, symbol):
    resp = fyers.optionchain(data={"symbol": symbol, "strikecount": STRIKE_COUNT, "timestamp": ""})
    if resp.get("s") != "ok":
        raise RuntimeError(f"option chain: {resp.get('message', resp)}")
    return resp["data"]


def fetch_depth(fyers, symbol):
    resp = fyers.depth(data={"symbol": symbol, "ohlcv_flag": "1"})
    if resp.get("s") != "ok":
        return None
    d = (resp.get("d") or {}).get(symbol)
    if not d or not d.get("ltp"):
        return None
    return d


def future_candidates(prefix):
    today = date.today()
    out = []
    for i in range(2):
        m = (today.month - 1 + i) % 12
        y = today.year + (today.month - 1 + i) // 12
        out.append(f"{prefix}{y % 100:02d}{MONTHS[m]}FUT")
    return out


_fut_symbol = {}


def fetch_future(fyers, name):
    """Find and fetch the near-month future. Returns (symbol, depth dict) or (None, None)."""
    cands = [FUT_OVERRIDE[name]] if FUT_OVERRIDE.get(name) else future_candidates(INDICES[name]["fut_prefix"])
    if _fut_symbol.get(name):
        d = fetch_depth(fyers, _fut_symbol[name])
        if d:
            return _fut_symbol[name], d
    for sym in cands:
        d = fetch_depth(fyers, sym)
        if d:
            _fut_symbol[name] = sym
            return sym, d
    return None, None


def fetch_vix(fyers):
    resp = fyers.quotes(data={"symbols": VIX_SYMBOL})
    try:
        return resp["d"][0]["v"]["lp"]
    except Exception:
        return None


# ------------------------------------------------------------ analysis ---
def expiry_info(data):
    exp = (data.get("expiryData") or [{}])[0]
    label = exp.get("date", "")
    try:
        exp_dt = datetime.strptime(label, "%d-%m-%Y").replace(hour=15, minute=30)
    except ValueError:
        return label, None, None
    secs = max((exp_dt - datetime.now()).total_seconds(), 60)
    return label, secs / (365 * 24 * 3600), round(secs / 86400, 2)


def parse_chain(data):
    spot, strikes = None, {}
    for item in data.get("optionsChain", []):
        otype = item.get("option_type")
        if otype in ("CE", "PE"):
            strikes.setdefault(item["strike_price"], {})[otype] = item
        else:
            spot = item.get("ltp")
    return spot, strikes


def best_strike(strikes, side, field):
    best_k, best_v = None, None
    for k, legs in strikes.items():
        v = num(legs.get(side, {}).get(field))
        if v is not None and (best_v is None or v > best_v):
            best_k, best_v = k, v
    return best_k, best_v


def max_pain(strikes):
    best_k, best_pain = None, None
    for K in strikes:
        pain = 0
        for s, legs in strikes.items():
            pain += (num(legs.get("CE", {}).get("oi")) or 0) * max(0, K - s)
            pain += (num(legs.get("PE", {}).get("oi")) or 0) * max(0, s - K)
        if best_pain is None or pain < best_pain:
            best_k, best_pain = K, pain
    return best_k


def build_index(fyers, name, vix_cache):
    cfg = INDICES[name]
    data = fetch_chain(fyers, cfg["spot"])
    spot, strikes = parse_chain(data)
    expiry_label, T, days = expiry_info(data)
    if not strikes or not spot:
        raise RuntimeError("empty option chain")

    atm = min(strikes, key=lambda k: abs(k - spot))

    # option chain rows with Greeks
    rows = []
    for K in sorted(strikes):
        ce, pe = strikes[K].get("CE", {}), strikes[K].get("PE", {})
        ce_g = option_analytics(ce, spot, K, T, True) if T else [None] * 5
        pe_g = option_analytics(pe, spot, K, T, False) if T else [None] * 5
        call = ce_g + [num(ce.get(f)) for f in ("oi", "oich", "volume", "ltp", "ltpch", "bid", "ask")]
        put = [num(pe.get(f)) for f in ("bid", "ask", "ltpch", "ltp", "volume", "oich", "oi")] + pe_g[::-1]
        rows.append(call + [K] + put)

    # summary numbers
    atm_ce, atm_pe = strikes[atm].get("CE", {}), strikes[atm].get("PE", {})
    straddle = (num(atm_ce.get("ltp")) or 0) + (num(atm_pe.get("ltp")) or 0)
    ivs = [x for x in (rows[sorted(strikes).index(atm)][0], rows[sorted(strikes).index(atm)][-1]) if x]
    atm_iv = round(sum(ivs) / len(ivs), 2) if ivs else None

    call_oi = num(data.get("callOi")) or sum(num(l.get("CE", {}).get("oi")) or 0 for l in strikes.values())
    put_oi = num(data.get("putOi")) or sum(num(l.get("PE", {}).get("oi")) or 0 for l in strikes.values())
    pcr = round(put_oi / call_oi, 3) if call_oi else None

    hc_k, hc_v = best_strike(strikes, "CE", "oi")
    hp_k, hp_v = best_strike(strikes, "PE", "oi")
    hcv_k, hcv_v = best_strike(strikes, "CE", "volume")
    hpv_k, hpv_v = best_strike(strikes, "PE", "volume")

    fut_sym, fut = fetch_future(fyers, name)
    fut_ltp = fut.get("ltp") if fut else None
    vwap = fut.get("atp") if fut else None
    fut_oi = fut.get("oi") if fut else None
    pdoi = fut.get("pdoi") if fut else None
    oi_chg = (fut_oi - pdoi) if (fut_oi is not None and pdoi) else None
    oi_chg_pct = round(oi_chg / pdoi * 100, 2) if (oi_chg is not None and pdoi) else None
    premium = round(fut_ltp - spot, 2) if fut_ltp else None

    vix = (data.get("indiavixData") or {}).get("ltp")
    if vix is None:
        if "vix" not in vix_cache:
            vix_cache["vix"] = fetch_vix(fyers)
        vix = vix_cache["vix"]

    summary = [
        spot, fut_ltp, fut_sym or "not found", premium, vwap,
        fut_oi, oi_chg, oi_chg_pct,
        atm, round(straddle, 2), atm_iv, pcr, max_pain(strikes),
        hc_k, hc_v, hp_k, hp_v, hcv_k, hcv_v, hpv_k, hpv_v,
        vix, expiry_label, days, datetime.now().strftime("%H:%M:%S"),
    ]
    return summary, rows, atm


# ---------------------------------------------------------- excel write ---
def get_sheet(book, name):
    try:
        return book.sheets[name]
    except Exception:
        return book.sheets.add(name, after=book.sheets[-1])


def setup_summary(sheet):
    sheet.range("A1").value = ["Metric", "NIFTY", "SENSEX"]
    sheet.range("A1:C1").font.bold = True
    sheet.range("A2").value = [[label] for label in SUMMARY_LABELS]
    sheet.range("A:A").column_width = 26
    sheet.range("B:C").column_width = 20


def setup_chain(sheet, title):
    strike_col = len(CALL_COLS) + 1
    sheet.range((1, 1)).value = title
    sheet.range((2, 1)).value = "CALLS"
    sheet.range((2, strike_col + 1)).value = "PUTS"
    sheet.range((HEADER_ROW, 1)).value = CALL_COLS + ["Strike"] + PUT_COLS
    sheet.range((1, 1), (HEADER_ROW, strike_col * 2 - 1)).font.bold = True


def write_chain(sheet, rows, atm):
    strike_col = len(CALL_COLS) + 1
    width = strike_col * 2 - 1
    first = HEADER_ROW + 1
    full = sheet.range((first, 1), (first + MAX_ROWS, width))
    full.clear_contents()
    full.color = None
    if not rows:
        return
    sheet.range((first, 1)).value = rows
    sheet.range((first, strike_col), (first + len(rows) - 1, strike_col)).color = (255, 242, 204)
    atm_row = first + [r[len(CALL_COLS)] for r in rows].index(atm)
    sheet.range((atm_row, 1), (atm_row, width)).color = (198, 239, 206)
    sheet.range((1, 3)).value = f"ATM {atm} highlighted green.  Updated {datetime.now():%H:%M:%S}"


# Excel refuses outside commands while you are typing in a cell or a dialog is open.
BUSY_CODES = (-2146777998, -2147418111)   # 0x800AC472 "Excel busy", 0x80010001 "call rejected"


def excel_is_busy(err):
    return bool(getattr(err, "args", None)) and err.args[0] in BUSY_CODES


def excel_write(action, tries=5, wait=0.4):
    """Run an Excel write, quietly retrying for a moment if Excel is busy."""
    for attempt in range(tries):
        try:
            return action()
        except Exception as e:
            if not excel_is_busy(e) or attempt == tries - 1:
                raise
            time.sleep(wait)


# ----------------------------------------------------------------- main ---
if __name__ == "__main__":
    token = get_access_token()
    fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=token, is_async=False, log_path="")

    book = xw.Book(WORKBOOK)
    summary_sheet = get_sheet(book, "SUMMARY")
    setup_summary(summary_sheet)
    chain_sheets = {}
    for name, cfg in INDICES.items():
        chain_sheets[name] = get_sheet(book, cfg["sheet"])
        setup_chain(chain_sheets[name], f"{name} option chain")

    print(f"Updating every {REFRESH_SECS}s. Press Ctrl+C to stop.")
    busy_warned = False
    while True:
        vix_cache = {}
        for name, cfg in INDICES.items():
            try:
                summary, rows, atm = build_index(fyers, name, vix_cache)
            except Exception as e:
                print(f"{datetime.now():%H:%M:%S} {name}: {e}")
                continue
            try:
                excel_write(lambda: (
                    setattr(summary_sheet.range((2, cfg["col"])), "value", [[v] for v in summary]),
                    write_chain(chain_sheets[name], rows, atm),
                ))
                busy_warned = False
            except Exception as e:
                if excel_is_busy(e):
                    if not busy_warned:
                        print(f"{datetime.now():%H:%M:%S} Excel is busy (a cell is being edited or a "
                              "dialog is open). Updates resume automatically when it is free.")
                        busy_warned = True
                else:
                    print(f"{datetime.now():%H:%M:%S} {name}: {e}")
        time.sleep(REFRESH_SECS)
