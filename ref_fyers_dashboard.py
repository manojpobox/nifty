"""
Fyers API v3 -> Nifty & Sensex live dashboard in Excel
======================================================
Sheets written (created automatically, overwritten on every refresh):
    SUMMARY    - all key numbers. Column B = NIFTY, column C = SENSEX
    NIFTY_OC   - Nifty option chain, weekly expiry, ATM +/- 15 strikes, with IV and Greeks
    SENSEX_OC  - Sensex option chain, weekly expiry, ATM +/- 15 strikes, with IV and Greeks
    NIFTY_AD   - Nifty 50 stocks: advance / decline list
    SENSEX_AD  - Sensex 30 stocks: advance / decline list

Don't type in those five sheets. Add your own sheet and point formulas at them,
for example  =SUMMARY!B4

How to run (every day):
    cd C:\\Fyers
    python fyers_dashboard.py

First run only: it asks for your App ID, Secret ID and Redirect URL and saves them
in fyers_login.txt, so you never have to edit this file.

Files this script creates next to itself:
    fyers_login.txt   your app details
    token.txt         today's login
    iv_history.csv    one ATM IV value per day (used for IV percentile)
    nifty50.txt       list of Nifty 50 stocks   (edit when the index changes)
    sensex30.txt      list of Sensex 30 stocks  (edit when the index changes)
    fyers_dashboard.xlsx   the workbook (created if missing)
    debug_sample.txt  a small sample of the raw data from Fyers, for troubleshooting
"""

import csv
import math
import os
import time
from datetime import date, datetime, timedelta

import xlwings as xw
from fyers_apiv3 import fyersModel

# ============================== SETTINGS ==============================
WORKBOOK         = "fyers_dashboard.xlsx"
REFRESH_SECS     = 3      # main refresh. Keep 3 or more (Fyers rate limits)
AD_REFRESH_SECS  = 15     # advance/decline refresh
VOL_REFRESH_SECS = 60     # "biggest 1-minute volume" refresh
SHOW_STRIKES     = 15     # strikes shown above and below ATM
WIDE_STRIKES     = 50     # strikes used above and below ATM for the "Wide" numbers (max 50)
VOL_INTERVAL_MIN = "1"    # interval for biggest-volume burst: "1" or "5" minutes

INDICES = {
    "NIFTY":  {"spot": "NSE:NIFTY50-INDEX", "fut_prefix": "NSE:NIFTY",  "col": 2,
               "oc_sheet": "NIFTY_OC",  "ad_sheet": "NIFTY_AD",  "list_file": "nifty50.txt"},
    "SENSEX": {"spot": "BSE:SENSEX-INDEX",  "fut_prefix": "BSE:SENSEX", "col": 3,
               "oc_sheet": "SENSEX_OC", "ad_sheet": "SENSEX_AD", "list_file": "sensex30.txt"},
}
# Monthly future is found automatically and rolls to the next month after expiry.
# Only if it shows "not found", type the exact symbol here, e.g. "NSE:NIFTY26OCTFUT"
FUT_OVERRIDE = {"NIFTY": "", "SENSEX": ""}
VIX_SYMBOL = "NSE:INDIAVIX-INDEX"

# Starting stock lists (as of 30 Sep 2026). They are copied into nifty50.txt and
# sensex30.txt on the first run - edit those text files when an index changes.
DEFAULT_LISTS = {
    "nifty50.txt": """ADANIENT ADANIPORTS APOLLOHOSP ASIANPAINT AXISBANK BAJAJ-AUTO BAJFINANCE BAJAJFINSV
        BEL BHARTIARTL BSE CIPLA COALINDIA DRREDDY EICHERMOT ETERNAL GRASIM HCLTECH HDFCBANK HDFCLIFE
        HINDALCO HINDUNILVR ICICIBANK INDIGO INFY ITC JIOFIN JSWSTEEL KOTAKBANK LT M&M MARUTI MAXHEALTH
        NESTLEIND NTPC ONGC POWERGRID RELIANCE SBILIFE SBIN SHRIRAMFIN SUNPHARMA TCS TATACONSUM TMPV
        TATASTEEL TECHM TITAN TRENT ULTRACEMCO""",
    "sensex30.txt": """ADANIPORTS ASIANPAINT AXISBANK BAJFINANCE BAJAJFINSV BEL BHARTIARTL ETERNAL
        HCLTECH HDFCBANK HINDALCO HINDUNILVR ICICIBANK INDIGO INFY ITC KOTAKBANK LT M&M MARUTI NTPC
        POWERGRID RELIANCE SBIN SUNPHARMA TCS TATASTEEL TECHM TITAN ULTRACEMCO""",
}

LOGIN_FILE = "fyers_login.txt"
TOKEN_FILE = "token.txt"
IV_FILE    = "iv_history.csv"
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

HERE = os.path.dirname(os.path.abspath(__file__))


def path(name):
    return os.path.join(HERE, name)


def now_str():
    return datetime.now().strftime("%H:%M:%S")


def num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def tidy(k):
    """25000.0 -> 25000"""
    return int(k) if isinstance(k, float) and k.is_integer() else k


# ================================ LOGIN ================================
def load_app_details():
    if os.path.exists(path(LOGIN_FILE)):
        vals = {}
        for line in open(path(LOGIN_FILE)):
            if "=" in line:
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip()
        if all(vals.get(k) for k in ("APP_ID", "SECRET_ID", "REDIRECT_URL")):
            return vals["APP_ID"], vals["SECRET_ID"], vals["REDIRECT_URL"]

    print("\nFirst-time setup. Enter the details of your app from myapi.fyers.in")
    app_id = input("App ID (looks like XXXXXXXXXX-100): ").strip()
    secret = input("Secret ID: ").strip()
    redirect = input("Redirect URL (press Enter for https://www.google.com): ").strip() or "https://www.google.com"
    with open(path(LOGIN_FILE), "w") as f:
        f.write(f"APP_ID={app_id}\nSECRET_ID={secret}\nREDIRECT_URL={redirect}\n")
    print(f"Saved in {LOGIN_FILE}. Delete that file if you ever need to re-enter them.\n")
    return app_id, secret, redirect


def token_works(app_id, token):
    try:
        fy = fyersModel.FyersModel(client_id=app_id, token=token, is_async=False, log_path="")
        return fy.get_profile().get("s") == "ok"
    except Exception:
        return False


def get_access_token(app_id, secret, redirect):
    if os.path.exists(path(TOKEN_FILE)):
        saved = open(path(TOKEN_FILE)).read().strip()
        if saved and token_works(app_id, saved):
            print("Using saved login.")
            return saved
        print("Saved login is no longer valid - please log in again.")

    session = fyersModel.SessionModel(
        client_id=app_id, secret_key=secret, redirect_uri=redirect,
        response_type="code", grant_type="authorization_code",
    )
    print("\nOpen this URL, log in, then copy auth_code from the redirected URL:\n")
    print(session.generate_authcode())
    session.set_token(input("\nPaste auth_code here: ").strip())
    resp = session.generate_token()
    if "access_token" not in resp:
        raise SystemExit(f"Login failed: {resp}\nIf the App ID or Secret is wrong, delete {LOGIN_FILE} and run again.")
    with open(path(TOKEN_FILE), "w") as f:
        f.write(resp["access_token"])
    return resp["access_token"]


# ====================== OPTION MATHS (Black-76) ======================
# IV and Greeks are calculated on the synthetic future (ATM strike + call - put),
# the same approach Sensibull describes. Fyers does not supply IV or Greeks.
def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _npdf(x):
    return math.exp(-x * x / 2) / math.sqrt(2 * math.pi)


def b76_price(F, K, T, vol, is_call):
    sq = vol * math.sqrt(T)
    d1 = (math.log(F / K) + vol * vol * T / 2) / sq
    d2 = d1 - sq
    return F * _ncdf(d1) - K * _ncdf(d2) if is_call else K * _ncdf(-d2) - F * _ncdf(-d1)


def implied_vol(price, F, K, T, is_call):
    if not price or price <= 0 or not F or not K or T <= 0:
        return None
    intrinsic = max(0.0, F - K) if is_call else max(0.0, K - F)
    if price <= intrinsic + 1e-9:
        return None
    lo, hi = 0.001, 5.0
    if b76_price(F, K, T, hi, is_call) < price:
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if b76_price(F, K, T, mid, is_call) < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def greeks(F, K, T, vol, is_call):
    sq = math.sqrt(T)
    d1 = (math.log(F / K) + vol * vol * T / 2) / (vol * sq)
    pdf = _npdf(d1)
    delta = _ncdf(d1) if is_call else _ncdf(d1) - 1
    gamma = pdf / (F * vol * sq)
    theta = -F * pdf * vol / (2 * sq) / 365      # per calendar day
    vega = F * pdf * sq / 100                    # per 1% change in IV
    return delta, gamma, theta, vega


def analytics(ltp, F, K, T, is_call, use_iv=None):
    """-> dict(iv, delta, theta, vega, gamma), values None when not computable.
    use_iv (in %) skips the IV search and prices the Greeks from that IV instead."""
    blank = {"iv": None, "delta": None, "theta": None, "vega": None, "gamma": None}
    if not T or not F:
        return blank
    iv = use_iv / 100 if use_iv else implied_vol(ltp, F, K, T, is_call)
    if iv is None:
        return blank
    d, g, th, v = greeks(F, K, T, iv, is_call)
    return {"iv": round(iv * 100, 2), "delta": round(d, 3), "theta": round(th, 2),
            "vega": round(v, 2), "gamma": round(g, 6)}


# ============================ DATA FETCH ============================
class Throttled:
    """Wraps the Fyers client so calls are spaced out (Fyers allows 10 per second)."""

    def __init__(self, client, gap=0.15):
        self._client, self._gap, self._last = client, gap, 0.0

    def __getattr__(self, name):
        fn = getattr(self._client, name)

        def call(*args, **kwargs):
            wait = self._gap - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            return fn(*args, **kwargs)
        return call


def fetch_chain(fyers, symbol):
    resp = fyers.optionchain(data={"symbol": symbol, "strikecount": WIDE_STRIKES, "timestamp": ""})
    if resp.get("s") != "ok":
        raise RuntimeError(f"option chain: {resp.get('message', resp)}")
    return resp["data"]


def fetch_quotes(fyers, symbols):
    """-> {symbol: v-dict} for symbols Fyers recognised"""
    out = {}
    for i in range(0, len(symbols), 50):
        resp = fyers.quotes(data={"symbols": ",".join(symbols[i:i + 50])})
        for item in resp.get("d") or []:
            v = item.get("v") or {}
            if num(v.get("lp")) is not None:
                out[item.get("n")] = v
    return out


def fetch_depth(fyers, symbol):
    resp = fyers.depth(data={"symbol": symbol, "ohlcv_flag": "1"})
    if resp.get("s") != "ok":
        return None
    d = (resp.get("d") or {}).get(symbol)
    return d if d and d.get("ltp") else None


def contract_expired(depth):
    try:
        exp = datetime.fromtimestamp(int(depth.get("expiry")))
    except (TypeError, ValueError, OSError):
        return False
    now = datetime.now()
    if exp.date() < now.date():
        return True
    return exp.date() == now.date() and (now.hour, now.minute) >= (15, 30)


_fut_symbol = {}


def fetch_future(fyers, name):
    """Near-month future that has not expired. -> (symbol, depth) or (None, None)"""
    if FUT_OVERRIDE.get(name):
        return FUT_OVERRIDE[name], fetch_depth(fyers, FUT_OVERRIDE[name])
    cached = _fut_symbol.get(name)
    if cached:
        d = fetch_depth(fyers, cached)
        if d and not contract_expired(d):
            return cached, d
    today = date.today()
    for i in range(3):
        m = (today.month - 1 + i) % 12
        y = today.year + (today.month - 1 + i) // 12
        sym = f"{INDICES[name]['fut_prefix']}{y % 100:02d}{MONTHS[m]}FUT"
        d = fetch_depth(fyers, sym)
        if d and not contract_expired(d):
            _fut_symbol[name] = sym
            return sym, d
    return None, None


def biggest_volume_candle(fyers, symbol):
    """Largest single-interval volume of the latest trading day (today when the market is open).
    -> (volume, "HH:MM")  or  (volume, "01-Oct 14:32") when the day shown is not today."""
    if not symbol:
        return None, None
    today = date.today()
    resp = fyers.history(data={"symbol": symbol, "resolution": VOL_INTERVAL_MIN, "date_format": "1",
                               "range_from": (today - timedelta(days=7)).isoformat(),
                               "range_to": today.isoformat(), "cont_flag": "1"})
    candles = resp.get("candles") or []
    if not candles:
        return None, None
    last_day = datetime.fromtimestamp(candles[-1][0]).date()
    day = [c for c in candles if datetime.fromtimestamp(c[0]).date() == last_day]
    best = max(day, key=lambda c: c[5])
    when = datetime.fromtimestamp(best[0])
    return best[5], when.strftime("%H:%M" if last_day == today else "%d-%b %H:%M")


def vix_history(fyers):
    """One year of India VIX daily closes (fetched once)."""
    try:
        resp = fyers.history(data={"symbol": VIX_SYMBOL, "resolution": "D", "date_format": "1",
                                   "range_from": (date.today() - timedelta(days=364)).isoformat(),
                                   "range_to": date.today().isoformat(), "cont_flag": "1"})
        return [c[4] for c in resp.get("candles") or []]
    except Exception:
        return []


def load_stock_list(filename):
    p = path(filename)
    if not os.path.exists(p):
        with open(p, "w") as f:
            f.write("\n".join(DEFAULT_LISTS[filename].split()) + "\n")
    names = [ln.strip().upper() for ln in open(p) if ln.strip() and not ln.startswith("#")]
    return [n if ":" in n else f"NSE:{n}-EQ" for n in names]


# ============================ IV HISTORY ============================
def load_iv_history():
    hist = {}
    if os.path.exists(path(IV_FILE)):
        for row in csv.DictReader(open(path(IV_FILE))):
            hist[row["date"]] = {k: float(v) for k, v in row.items() if k != "date" and v}
    return hist


def save_iv_history(hist):
    names = list(INDICES)
    with open(path(IV_FILE), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date"] + names)
        for d in sorted(hist):
            w.writerow([d] + [hist[d].get(n, "") for n in names])


def iv_percentile(hist, name, iv_now):
    """% of earlier days (up to 1 year) with IV below today's. -> (percentile, days)"""
    today = date.today().isoformat()
    past = [hist[d][name] for d in sorted(hist) if d < today and name in hist[d]][-252:]
    if not past or iv_now is None:
        return None, len(past)
    return round(100 * sum(1 for x in past if x < iv_now) / len(past), 1), len(past)


def write_debug_sample(fyers):
    """Saves a small sample of the raw data Fyers sends (no login details) to debug_sample.txt."""
    try:
        out = [f"Saved {datetime.now():%d-%b-%Y %H:%M:%S}"]
        for name, cfg in INDICES.items():
            data = fetch_chain(fyers, cfg["spot"])
            spot, strikes = parse_chain(data)
            atm = min(strikes, key=lambda k: abs(k - spot))
            out.append(f"\n[{name}] chain keys: {sorted(k for k in data if k != 'optionsChain')}")
            out.append(f"expiryData: {(data.get('expiryData') or [])[:2]}")
            out.append(f"underlying row: {[i for i in data['optionsChain'] if i.get('option_type') not in ('CE', 'PE')][:1]}")
            out.append(f"ATM call: {strikes[atm].get('CE')}")
            out.append(f"ATM put: {strikes[atm].get('PE')}")
            sym, depth = fetch_future(fyers, name)
            if depth:
                depth = {k: v for k, v in depth.items() if k not in ("bids", "ask")}
            out.append(f"future {sym}: {depth}")
        q = fyers.quotes(data={"symbols": ",".join([c["spot"] for c in INDICES.values()] + [VIX_SYMBOL])})
        out.append(f"\nindex quotes: {q.get('d')}")
        with open(path("debug_sample.txt"), "w") as f:
            f.write("\n".join(str(x) for x in out) + "\n")
    except Exception as e:
        print(f"{now_str()} could not write debug_sample.txt: {e}")


# ============================= ANALYSIS =============================
def expiry_info(data):
    exp = (data.get("expiryData") or [{}])[0]
    label = exp.get("date", "")
    try:
        exp_dt = datetime.strptime(label, "%d-%m-%Y").replace(hour=15, minute=30)
    except ValueError:
        return label, None, None
    secs = max((exp_dt - datetime.now()).total_seconds(), 60)
    return exp_dt.strftime("%d-%b-%Y"), secs / (365 * 24 * 3600), round(secs / 86400, 2)


def parse_chain(data):
    spot, strikes = None, {}
    for item in data.get("optionsChain", []):
        otype = item.get("option_type")
        if otype in ("CE", "PE"):
            strikes.setdefault(item["strike_price"], {})[otype] = item
        elif num(item.get("ltp")):
            spot = item["ltp"]
    return spot, strikes


def leg(strikes, k, side):
    return strikes.get(k, {}).get(side, {}) if k is not None else {}


def oi_change(opt):
    """-> (change, % change) using whichever fields Fyers sent"""
    oi, ch, prev = num(opt.get("oi")), num(opt.get("oich")), num(opt.get("prev_oi"))
    if ch is None and oi is not None and prev is not None:
        ch = oi - prev
    pct = num(opt.get("oichp"))
    if pct is None and ch is not None and oi is not None and (oi - ch) > 0:
        pct = ch / (oi - ch) * 100
    return ch, (round(pct, 2) if pct is not None else None)


def top_two(strikes, keys, side, field):
    """-> [(strike, value), (strike, value)] highest first"""
    vals = [(k, num(leg(strikes, k, side).get(field))) for k in keys]
    vals = sorted([x for x in vals if x[1]], key=lambda x: -x[1])
    return (vals + [(None, None), (None, None)])[:2]


def max_pain(strikes, keys):
    best_k, best = None, None
    for K in keys:
        pain = 0
        for s in keys:
            pain += (num(leg(strikes, s, "CE").get("oi")) or 0) * max(0, K - s)
            pain += (num(leg(strikes, s, "PE").get("oi")) or 0) * max(0, s - K)
        if best is None or pain < best:
            best_k, best = K, pain
    return best_k


def total(strikes, keys, side, field):
    return sum(num(leg(strikes, k, side).get(field)) or 0 for k in keys)


def oi_stats(strikes, keys):
    call_oi, put_oi = total(strikes, keys, "CE", "oi"), total(strikes, keys, "PE", "oi")
    return {
        "pcr": round(put_oi / call_oi, 3) if call_oi else None,
        "max_pain": tidy(max_pain(strikes, keys)),
        "put": top_two(strikes, keys, "PE", "oi"),
        "call": top_two(strikes, keys, "CE", "oi"),
    }


def bias(m):
    """Rule-based market reading. -> (label, score, [factor lines])"""
    score, lines = 0.0, []

    def add(points, text):
        nonlocal score
        score += points
        lines.append(f"{text}  [{points:+g}]")

    chp = m.get("spot_chp")
    if chp is None:
        add(0, "Price vs prev close: n/a")
    else:
        pts = 2 if chp >= 0.75 else 1 if chp >= 0.25 else -2 if chp <= -0.75 else -1 if chp <= -0.25 else 0
        add(pts, f"Price vs prev close: {chp:+.2f}%")

    if m.get("fut") and m.get("vwap"):
        diff = (m["fut"] - m["vwap"]) / m["vwap"] * 100
        pts = 1 if diff > 0.05 else -1 if diff < -0.05 else 0
        add(pts, f"Future vs VWAP: {diff:+.2f}%")
    else:
        add(0, "Future vs VWAP: n/a")

    pcr = m.get("pcr")
    if pcr is None:
        add(0, "PCR: n/a")
    else:
        pts = 1 if pcr >= 1.2 else -1 if pcr <= 0.8 else 0
        add(pts, f"PCR: {pcr:.2f}")

    p, c = m.get("put_oich"), m.get("call_oich")
    if p is None or c is None or (p == 0 and c == 0):
        add(0, "OI added today (puts vs calls): n/a")
    else:
        pts = 1 if p > c * 1.2 and p > 0 else -1 if c > p * 1.2 and c > 0 else 0
        add(pts, f"OI added today: puts {p:,.0f} vs calls {c:,.0f}")

    fch, foi = m.get("fut_ch"), m.get("fut_oi_chg")
    if fch is None or foi is None or fch == 0 or foi == 0:
        add(0, "Futures price + OI: n/a")
    elif fch > 0 and foi > 0:
        add(1, "Futures: long build-up (price up, OI up)")
    elif fch < 0 and foi > 0:
        add(-1, "Futures: short build-up (price down, OI up)")
    elif fch > 0:
        add(0.5, "Futures: short covering (price up, OI down)")
    else:
        add(-0.5, "Futures: long unwinding (price down, OI down)")

    adv, dec = m.get("adv"), m.get("dec")
    if adv is None or not (adv + dec):
        add(0, "Breadth: n/a")
    else:
        share = adv / (adv + dec)
        pts = 1 if share >= 0.65 else -1 if share <= 0.35 else 0
        add(pts, f"Breadth: {adv} up / {dec} down")

    sup, res = m.get("put_wall"), m.get("call_wall")      # (strike, OI) below / above ATM
    if sup and res and sup[1] and res[1]:
        pts = 1 if sup[1] > res[1] * 1.2 else -1 if res[1] > sup[1] * 1.2 else 0
        add(pts, f"Support {tidy(sup[0])} (Put OI {sup[1]:,.0f}) vs Resistance {tidy(res[0])} (Call OI {res[1]:,.0f})")
    else:
        add(0, "Support vs resistance OI: n/a")

    vchp = m.get("vix_chp")
    if vchp is None:
        add(0, "India VIX change: n/a")
    else:
        pts = -0.5 if vchp >= 3 else 0.5 if vchp <= -3 else 0
        add(pts, f"India VIX change: {vchp:+.2f}%")

    label = ("Strong Bullish" if score >= 4.5 else "Bullish" if score >= 2.5 else
             "Weak Bullish" if score >= 1 else "Strong Bearish" if score <= -4.5 else
             "Bearish" if score <= -2.5 else "Weak Bearish" if score <= -1 else "Neutral")
    return label, score, lines


# -------- chain table --------
CALL_COLS = ["Gamma", "Vega", "Theta", "Delta", "IV %", "LTP", "Volume", "OI Chg %", "OI Chg", "OI"]
PUT_COLS = CALL_COLS[::-1]
CHAIN_ROWS = SHOW_STRIKES * 2 + 1
CHAIN_WIDTH = len(CALL_COLS) * 2 + 1
STRIKE_COL = len(CALL_COLS) + 1
CHAIN_HEADER_ROW = 3


def chain_side(opt, an):
    ch, pct = oi_change(opt)
    return [an["gamma"], an["vega"], an["theta"], an["delta"], an["iv"],
            num(opt.get("ltp")), num(opt.get("volume")), pct, ch, num(opt.get("oi"))]


def build_index(fyers, name, quotes, state):
    """Fetch and calculate everything for one index. -> dict"""
    cfg = INDICES[name]
    data = fetch_chain(fyers, cfg["spot"])
    chain_spot, strikes = parse_chain(data)
    q = quotes.get(cfg["spot"], {})
    spot = num(q.get("lp")) or chain_spot
    if not strikes or not spot:
        raise RuntimeError("empty option chain")

    keys = sorted(strikes)
    atm_i = min(range(len(keys)), key=lambda i: abs(keys[i] - spot))
    atm = keys[atm_i]
    below = keys[atm_i - 1] if atm_i > 0 else None            # ATM-1
    above = keys[atm_i + 1] if atm_i + 1 < len(keys) else None  # ATM+1
    shown = keys[max(0, atm_i - SHOW_STRIKES): atm_i + SHOW_STRIKES + 1]

    expiry_label, T, days = expiry_info(data)
    ce_atm, pe_atm = num(leg(strikes, atm, "CE").get("ltp")), num(leg(strikes, atm, "PE").get("ltp"))
    synth = round(atm + ce_atm - pe_atm, 2) if ce_atm and pe_atm else None
    F = synth or spot

    # option chain rows (ATM +/- 15)
    rows, an_atm = [], {}
    for K in shown:
        ce, pe = leg(strikes, K, "CE"), leg(strikes, K, "PE")
        an_c = analytics(num(ce.get("ltp")), F, K, T, True)
        an_p = analytics(num(pe.get("ltp")), F, K, T, False)
        # In-the-money options trade thinly and their own IV is unreliable, so (as option
        # platforms do) each strike uses the IV of its out-of-the-money option for both sides.
        if K < atm and an_p["iv"]:
            an_c = analytics(None, F, K, T, True, use_iv=an_p["iv"])
        elif K > atm and an_c["iv"]:
            an_p = analytics(None, F, K, T, False, use_iv=an_c["iv"])
        if K == atm:
            an_atm = {"CE": an_c, "PE": an_p}
        rows.append(chain_side(ce, an_c) + [tidy(K)] + chain_side(pe, an_p)[::-1])
    # keep ATM on the same sheet row always (row 19): pad the top if fewer strikes exist below ATM
    rows = [[None] * CHAIN_WIDTH] * (SHOW_STRIKES - shown.index(atm)) + rows

    ivs = [x["iv"] for x in an_atm.values() if x.get("iv")]
    atm_iv = round(sum(ivs) / len(ivs), 2) if ivs else None

    wide, disp = oi_stats(strikes, keys), oi_stats(strikes, shown)

    # future
    fut_sym, fut = fetch_future(fyers, name)
    fut = fut or {}
    fut_ltp, vwap = num(fut.get("ltp")), num(fut.get("atp"))
    fut_oi, pdoi = num(fut.get("oi")), num(fut.get("pdoi"))
    fut_oi_chg = (fut_oi - pdoi) if (fut_oi and pdoi) else None
    fut_oi_pct = round(fut_oi_chg / pdoi * 100, 2) if fut_oi_chg is not None else None

    # VIX
    vq = quotes.get(VIX_SYMBOL, {})
    vix = num(vq.get("lp")) or num((data.get("indiavixData") or {}).get("ltp"))
    vix_hist = state.get("vix_hist") or []
    vix_pct = round(100 * sum(1 for x in vix_hist if x < vix) / len(vix_hist), 1) if vix and vix_hist else None

    # IV percentile from our own saved history
    hist = state["iv_hist"]
    today_key = date.today().isoformat()
    try:    # the future's last-trade time tells us whether the market traded today
        traded_today = datetime.fromtimestamp(int(fut.get("ltt"))).date() == date.today()
    except (TypeError, ValueError, OSError):
        traded_today = datetime.now().weekday() < 5
    if atm_iv and traded_today:
        hist.setdefault(today_key, {})[name] = atm_iv
        state["iv_dirty"] = True
    elif not traded_today and name in hist.get(today_key, {}):   # holiday: don't keep a stale value
        del hist[today_key][name]
        if not hist[today_key]:
            del hist[today_key]
        state["iv_dirty"] = True
    ivp, iv_days = iv_percentile(hist, name, atm_iv)

    # the six "near ATM" contracts used in points 7-15
    near = [("ATM Put", atm, "PE"), ("ATM Call", atm, "CE"),
            ("ATM-1 Put", below, "PE"), ("ATM+1 Call", above, "CE"),
            ("ATM+1 Put", above, "PE"), ("ATM-1 Call", below, "CE")]
    near_syms = [leg(strikes, k, side).get("symbol") for _, k, side in near]
    if state["burst_syms"].get(name) != near_syms:      # ATM moved -> refresh bursts now
        state["burst_syms"][name] = near_syms
        state["burst_due"][name] = 0
    if time.time() >= state["burst_due"].get(name, 0):
        try:
            state["burst"][name] = [biggest_volume_candle(fyers, s) for s in near_syms]
            state["burst_due"][name] = time.time() + VOL_REFRESH_SECS
        except Exception as e:
            print(f"{now_str()} {name} volume bursts: {e}")
            state["burst_due"][name] = time.time() + VOL_REFRESH_SECS
    bursts = state["burst"].get(name) or [(None, None)] * 6

    ad = state["ad"].get(name) or {}
    label, score, factors = bias({
        "spot": spot, "spot_chp": num(q.get("chp")), "fut": fut_ltp, "vwap": vwap,
        "pcr": wide["pcr"], "put_oich": sum(oi_change(leg(strikes, k, "PE"))[0] or 0 for k in keys),
        "call_oich": sum(oi_change(leg(strikes, k, "CE"))[0] or 0 for k in keys),
        "fut_ch": num(fut.get("ch")), "fut_oi_chg": fut_oi_chg,
        "adv": ad.get("adv"), "dec": ad.get("dec"),
        "call_wall": top_two(strikes, [k for k in keys if k >= atm], "CE", "oi")[0],
        "put_wall": top_two(strikes, [k for k in keys if k <= atm], "PE", "oi")[0],
        "vix_chp": num(vq.get("chp")),
    })

    S = []   # (label, value) rows for the SUMMARY sheet, in display order

    headers = []

    def sec(title):
        headers.append(title)
        S.append((title, None))

    def row(lbl, val):
        S.append((lbl, tidy(val) if isinstance(val, float) else val))

    def text(lbl, val):     # leading apostrophe = "keep as text" in Excel
        S.append((lbl, f"'{val}" if val else None))

    now = datetime.now()
    sec("PRICE")
    text("Date", now.strftime("%d-%b-%Y"))
    text("Time (last update)", now.strftime("%H:%M:%S"))
    row("Spot", spot)
    row("Spot change %", num(q.get("chp")))
    row("Day Open", num(q.get("open_price")))
    row("Day High", num(q.get("high_price")))
    row("Day Low", num(q.get("low_price")))
    row("Close (LTP until market closes)", num(q.get("lp")))
    row("Previous Close", num(q.get("prev_close_price")))
    sec("FUTURE (monthly)")
    row("Future price", fut_ltp)
    row("Future symbol", fut_sym or "not found")
    row("Future - Spot", round(fut_ltp - spot, 2) if fut_ltp else None)
    row("Future VWAP (avg price)", vwap)
    row("Future OI", fut_oi)
    row("Future OI change", fut_oi_chg)
    row("Future OI change %", fut_oi_pct)
    sec("WEEKLY EXPIRY")
    text("Weekly expiry date", expiry_label)
    row("Days to expiry", days)
    row("Synthetic weekly future", synth)
    row("ATM strike", atm)
    row("ATM straddle (Call + Put)", round(ce_atm + pe_atm, 2) if ce_atm and pe_atm else None)
    row("ATM IV %", atm_iv)
    row("IV percentile (own history)", ivp)
    row("IV history: days recorded", iv_days)
    row("India VIX", vix)
    row("India VIX 1-year percentile", vix_pct)
    for title, st in ((f"WIDE (ATM +/-{WIDE_STRIKES} strikes)", wide), (f"DISPLAYED (ATM +/-{SHOW_STRIKES} strikes)", disp)):
        tag = "Wide" if st is wide else "Displayed"
        sec(title)
        row(f"PCR ({tag})", st["pcr"])
        row(f"Max Pain ({tag})", st["max_pain"])
        for side, key in (("Put", "put"), ("Call", "call")):
            for rank, (k, v) in zip(("Highest", "2nd highest"), st[key]):
                row(f"{rank} {side} OI strike ({tag})", k)
                row(f"{rank} {side} OI ({tag})", v)
    sec("OI NEAR ATM")
    for lbl, k, side in near:
        row(f"{lbl} strike", k)
        row(f"{lbl} OI", num(leg(strikes, k, side).get("oi")))
    sec("VOLUME NEAR ATM")
    for lbl, k, side in near:
        row(f"{lbl} strike", k)
        row(f"{lbl} volume", num(leg(strikes, k, side).get("volume")))
    sec(f"BIGGEST {VOL_INTERVAL_MIN}-MINUTE VOLUME TODAY")
    for (lbl, k, side), (vol, when) in zip(near, bursts):
        row(f"{lbl} strike", k)
        row(f"{lbl} biggest {VOL_INTERVAL_MIN}-min volume", vol)
        text(f"{lbl} time of that volume", when)
    sec("ADVANCE / DECLINE")
    row("Advances", ad.get("adv"))
    row("Declines", ad.get("dec"))
    row("Unchanged", ad.get("unch"))
    row("A/D ratio", ad.get("ratio"))
    row("Sum of % change", ad.get("sum"))
    row("Average % change", ad.get("avg"))
    row("Stocks counted", ad.get("counted"))
    sec("MARKET BIAS (rule-based reading, not advice)")
    row("Bias", label)
    row("Score (-8.5 to +8.5)", score)
    for i, line in enumerate(factors, 1):
        row(f"Factor {i}", line)

    info = f"Spot {spot}   ATM {tidy(atm)}   Expiry {expiry_label}   Updated {now_str()}"
    return {"summary": S, "headers": headers, "rows": rows, "atm": tidy(atm), "info": info}


def build_ad(fyers, name):
    """Advance/decline for the index's stocks. -> (stats dict, table rows, missing symbols)"""
    symbols = load_stock_list(INDICES[name]["list_file"])
    quotes = fetch_quotes(fyers, symbols)
    table, missing = [], []
    for s in symbols:
        v = quotes.get(s)
        if not v or num(v.get("chp")) is None:
            missing.append(s)
            continue
        chp = v["chp"]
        table.append([s.split(":")[1].replace("-EQ", ""), v.get("lp"), num(v.get("ch")), chp,
                      "Advance" if chp > 0 else "Decline" if chp < 0 else "Unchanged"])
    table.sort(key=lambda r: -r[3])
    adv = sum(1 for r in table if r[3] > 0)
    dec = sum(1 for r in table if r[3] < 0)
    total_chp = sum(r[3] for r in table)
    stats = {
        "adv": adv, "dec": dec, "unch": len(table) - adv - dec,
        "ratio": round(adv / dec, 2) if dec else None,
        "sum": round(total_chp, 2), "avg": round(total_chp / len(table), 3) if table else None,
        "counted": f"{len(table)} of {len(symbols)}",
    }
    return stats, table, missing


# ============================== EXCEL ==============================
# Excel refuses outside commands while you are typing in a cell or a dialog is open.
BUSY_CODES = (-2146777998, -2147418111)


def excel_is_busy(err):
    return bool(getattr(err, "args", None)) and err.args[0] in BUSY_CODES


def excel_write(action, tries=5, wait=0.4):
    for attempt in range(tries):
        try:
            return action()
        except Exception as e:
            if not excel_is_busy(e) or attempt == tries - 1:
                raise
            time.sleep(wait)


def open_workbook():
    """Use the workbook if it is already open in Excel, else open it, else create it."""
    for app in xw.apps:
        for book in app.books:
            if book.name.lower() == WORKBOOK.lower():
                print(f"Using {WORKBOOK}, which is already open in Excel.")
                return book
    p = path(WORKBOOK)
    if os.path.exists(p):
        return xw.Book(p)
    book = xw.Book()
    book.save(p)
    print(f"Created {WORKBOOK}")
    return book


def get_sheet(book, name):
    try:
        return book.sheets[name]
    except Exception:
        return book.sheets.add(name, after=book.sheets[-1])


def setup_chain_sheet(sheet, name):
    sheet.range((1, 1)).value = f"{name} option chain (weekly expiry)"
    sheet.range((2, 1)).value = "CALLS"
    sheet.range((2, STRIKE_COL + 1)).value = "PUTS"
    sheet.range((CHAIN_HEADER_ROW, 1)).value = CALL_COLS + ["Strike"] + PUT_COLS
    sheet.range((1, 1), (CHAIN_HEADER_ROW, CHAIN_WIDTH)).font.bold = True
    first = CHAIN_HEADER_ROW + 1
    col = sheet.range((first, STRIKE_COL), (first + CHAIN_ROWS - 1, STRIKE_COL))
    col.font.bold = True
    col.color = (255, 242, 204)


def write_chain(sheet, result, state, name):
    first = CHAIN_HEADER_ROW + 1
    rows = result["rows"] + [[None] * CHAIN_WIDTH] * (CHAIN_ROWS - len(result["rows"]))
    sheet.range((first, 1)).value = rows
    sheet.range((1, 6)).value = result["info"]
    atm_row = first + [r[STRIKE_COL - 1] for r in result["rows"]].index(result["atm"])
    if state["atm_row"].get(name) != atm_row:            # move the green ATM highlight
        block = sheet.range((first, 1), (first + CHAIN_ROWS - 1, CHAIN_WIDTH))
        block.color = None
        sheet.range((first, STRIKE_COL), (first + CHAIN_ROWS - 1, STRIKE_COL)).color = (255, 242, 204)
        sheet.range((atm_row, 1), (atm_row, CHAIN_WIDTH)).color = (198, 239, 206)
        state["atm_row"][name] = atm_row


def write_summary(sheet, result, col, state):
    labels = [lbl for lbl, _ in result["summary"]]
    if state.get("summary_labels") != labels:            # first time, or layout changed
        sheet.range("A1").value = ["Metric", "NIFTY", "SENSEX"]
        sheet.range("A1:C1").font.bold = True
        sheet.range((2, 1)).value = [[lbl] for lbl in labels]
        sheet.range("A:A").column_width = 38
        sheet.range("B:C").column_width = 46
        sheet.range("B:C").api.HorizontalAlignment = -4131   # left aligned
        for i, (lbl, _) in enumerate(result["summary"]):
            if lbl in result["headers"]:
                sheet.range((i + 2, 1), (i + 2, 3)).font.bold = True
                sheet.range((i + 2, 1), (i + 2, 3)).color = (221, 235, 247)
        state["summary_labels"] = labels
    sheet.range((2, col)).value = [[val] for _, val in result["summary"]]


AD_TABLE_ROW = 10


def write_ad(sheet, name, stats, table, missing, max_rows):
    sheet.range("A1").value = [
        [f"{name} advance / decline", f"Updated {now_str()}"],
        ["Advances", stats["adv"]], ["Declines", stats["dec"]], ["Unchanged", stats["unch"]],
        ["A/D ratio", stats["ratio"]], ["Sum of % change", stats["sum"]],
        ["Average % change", stats["avg"]], ["Stocks counted", stats["counted"]],
        ["Not found (fix in the .txt list)", ", ".join(missing) if missing else "none"],
    ]
    sheet.range((AD_TABLE_ROW, 1)).value = ["Stock", "LTP", "Change", "Change %", "Status"]
    rows = table + [[None] * 5] * (max_rows - len(table))
    sheet.range((AD_TABLE_ROW + 1, 1)).value = rows


# =============================== MAIN ===============================
def new_state(fyers):
    return {"iv_hist": load_iv_history(), "iv_dirty": False, "iv_saved": 0,
            "vix_hist": vix_history(fyers), "ad": {}, "ad_due": 0,
            "burst": {}, "burst_due": {}, "burst_syms": {}, "atm_row": {}, "busy_warned": False}


def safe_excel(state, what, action):
    try:
        excel_write(action)
        state["busy_warned"] = False
    except Exception as e:
        if excel_is_busy(e):
            if not state["busy_warned"]:
                print(f"{now_str()} Excel is busy (a cell is being edited or a dialog is open). "
                      "Updates resume automatically when it is free.")
                state["busy_warned"] = True
        else:
            print(f"{now_str()} {what}: {e}")


def run_cycle(fyers, sheets, state):
    # 1. advance / decline (slower refresh)
    if time.time() >= state["ad_due"]:
        state["ad_due"] = time.time() + AD_REFRESH_SECS
        for name, cfg in INDICES.items():
            try:
                stats, table, missing = build_ad(fyers, name)
                state["ad"][name] = stats
                n = len(load_stock_list(cfg["list_file"]))
                safe_excel(state, f"{name} A/D sheet",
                           lambda: write_ad(sheets[cfg["ad_sheet"]], name, stats, table, missing, n))
            except Exception as e:
                print(f"{now_str()} {name} advance/decline: {e}")

    # 2. index + VIX quotes (one call), then each index
    try:
        quotes = fetch_quotes(fyers, [c["spot"] for c in INDICES.values()] + [VIX_SYMBOL])
    except Exception as e:
        print(f"{now_str()} index quotes: {e}")
        quotes = {}
    for name, cfg in INDICES.items():
        try:
            result = build_index(fyers, name, quotes, state)
        except Exception as e:
            print(f"{now_str()} {name}: {e}")
            continue
        safe_excel(state, f"{name} summary", lambda: write_summary(sheets["SUMMARY"], result, cfg["col"], state))
        safe_excel(state, f"{name} chain", lambda: write_chain(sheets[cfg["oc_sheet"]], result, state, name))

    # 3. save today's IV to the history file (at most once a minute)
    if state["iv_dirty"] and time.time() - state["iv_saved"] > 60:
        try:
            save_iv_history(state["iv_hist"])
            state["iv_dirty"], state["iv_saved"] = False, time.time()
        except Exception as e:
            print(f"{now_str()} could not save {IV_FILE}: {e}")


def main():
    app_id, secret, redirect = load_app_details()
    token = get_access_token(app_id, secret, redirect)
    fyers = Throttled(fyersModel.FyersModel(client_id=app_id, token=token, is_async=False, log_path=""))

    book = open_workbook()
    sheets = {"SUMMARY": get_sheet(book, "SUMMARY")}
    for name, cfg in INDICES.items():
        sheets[cfg["oc_sheet"]] = get_sheet(book, cfg["oc_sheet"])
        sheets[cfg["ad_sheet"]] = get_sheet(book, cfg["ad_sheet"])
        setup_chain_sheet(sheets[cfg["oc_sheet"]], name)

    state = new_state(fyers)
    write_debug_sample(fyers)
    print(f"Updating every {REFRESH_SECS}s. Press Ctrl+C to stop.")
    try:
        while True:
            run_cycle(fyers, sheets, state)
            time.sleep(REFRESH_SECS)
    except KeyboardInterrupt:
        if state["iv_dirty"]:
            save_iv_history(state["iv_hist"])
        print("\nStopped.")


if __name__ == "__main__":
    main()
