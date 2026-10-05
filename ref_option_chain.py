"""
Fyers API v3 -> Nifty & Sensex option chain (current expiry) in Excel
---------------------------------------------------------------------
Run:
    1. Open Excel, save a workbook as  option_chain.xlsx  in this folder, keep it open.
    2. Fill in CLIENT_ID, SECRET_KEY, REDIRECT_URI below.
    3. python fyers_option_chain.py
    4. Log in with the printed link the first time each day. The token is saved
       in token.txt, so if you restart the script on the same day you won't
       be asked again.
    5. Ctrl+C in the black window to stop.

The script writes to sheets "NIFTY_OC" and "SENSEX_OC" (created if missing).
Don't type in those sheets - they are overwritten every refresh.
Build your own sheet and use formulas that point to them, e.g.  =NIFTY_OC!D8
"""

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
WORKBOOK      = "option_chain.xlsx"
REFRESH_SECS  = 3          # keep 3 or more to stay inside Fyers rate limits
STRIKE_COUNT  = 20         # strikes above AND below ATM (Fyers allows up to ~50)

CHAINS = {
    "NIFTY_OC":  "NSE:NIFTY50-INDEX",
    "SENSEX_OC": "BSE:SENSEX-INDEX",
}

TOKEN_FILE = "token.txt"

# Column layout (NSE style): CALLS | STRIKE | PUTS
CALL_COLS = ["OI", "Chg in OI", "Volume", "LTP", "Chg", "Chg %", "Bid", "Ask"]
PUT_COLS  = ["Bid", "Ask", "Chg %", "Chg", "LTP", "Volume", "Chg in OI", "OI"]
HEADER_ROW = 6   # rows 1-4 hold summary info, row 5 the CALLS/PUTS label


# ---------------------------------------------------------------- login ---
def get_access_token():
    today = date.today().isoformat()
    if os.path.exists(TOKEN_FILE):
        saved_date, saved_token = open(TOKEN_FILE).read().split("\n", 1)
        if saved_date == today and saved_token.strip():
            print("Using today's saved login.")
            return saved_token.strip()

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
        f.write(today + "\n" + resp["access_token"])
    return resp["access_token"]


# ---------------------------------------------------------- data fetch ---
def fetch_chain(fyers, symbol):
    resp = fyers.optionchain(data={
        "symbol": symbol,
        "strikecount": STRIKE_COUNT,
        "timestamp": "",          # empty = nearest (current) expiry
    })
    if resp.get("s") != "ok":
        raise RuntimeError(resp.get("message", resp))
    return resp["data"]


def num(x):
    return x if isinstance(x, (int, float)) else None


def build_rows(data):
    """Turn Fyers' flat list into one row per strike: CALL fields | strike | PUT fields."""
    spot = None
    strikes = {}
    for item in data.get("optionsChain", []):
        otype = item.get("option_type")
        if otype not in ("CE", "PE"):
            spot = item.get("ltp")          # the underlying index row
            continue
        strikes.setdefault(item["strike_price"], {})[otype] = item

    rows = []
    for strike in sorted(strikes):
        ce = strikes[strike].get("CE", {})
        pe = strikes[strike].get("PE", {})
        call = [num(ce.get(k)) for k in ("oi", "oich", "volume", "ltp", "ltpch", "ltpchp", "bid", "ask")]
        put  = [num(pe.get(k)) for k in ("bid", "ask", "ltpchp", "ltpch", "ltp", "volume", "oich", "oi")]
        rows.append(call + [strike] + put)
    return spot, rows


def expiry_label(data):
    exp = data.get("expiryData") or []
    return exp[0].get("date", "") if exp else ""


# ---------------------------------------------------------- excel write ---
def get_sheet(book, name):
    try:
        return book.sheets[name]
    except Exception:
        return book.sheets.add(name, after=book.sheets[-1])


def write_chain(sheet, title, data):
    spot, rows = build_rows(data)
    call_oi = sum(r[0] or 0 for r in rows)
    put_oi  = sum(r[-1] or 0 for r in rows)
    pcr = round(put_oi / call_oi, 3) if call_oi else None
    atm = min((r[len(CALL_COLS)] for r in rows), key=lambda s: abs(s - spot)) if rows and spot else None

    strike_col = len(CALL_COLS) + 1                         # 1-based column of STRIKE
    width = len(CALL_COLS) + 1 + len(PUT_COLS)

    sheet.range("A1").value = [
        [title, "Spot", spot, "ATM", atm, "Expiry", expiry_label(data)],
        ["", "Total Call OI", call_oi, "Total Put OI", put_oi, "PCR", pcr],
        ["", "Updated", datetime.now().strftime("%H:%M:%S"), "", "", "", ""],
    ]
    sheet.range((5, 1)).value = "CALLS"
    sheet.range((5, strike_col + 1)).value = "PUTS"
    sheet.range((HEADER_ROW, 1)).value = CALL_COLS + ["Strike"] + PUT_COLS

    # clear old rows (strike list can shift as spot moves), then write in one go
    sheet.range((HEADER_ROW + 1, 1), (HEADER_ROW + 200, width)).clear_contents()
    if rows:
        sheet.range((HEADER_ROW + 1, 1)).value = rows


def first_time_format(sheet):
    strike_col = len(CALL_COLS) + 1
    sheet.range((1, 1)).font.bold = True
    sheet.range((HEADER_ROW, 1), (HEADER_ROW, strike_col * 2 - 1)).font.bold = True
    sheet.range((HEADER_ROW + 1, strike_col), (HEADER_ROW + 200, strike_col)).font.bold = True
    sheet.range((HEADER_ROW + 1, strike_col), (HEADER_ROW + 200, strike_col)).color = (255, 242, 204)


# ----------------------------------------------------------------- main ---
if __name__ == "__main__":
    token = get_access_token()
    fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=token, is_async=False, log_path="")

    book = xw.Book(WORKBOOK)
    sheets = {name: get_sheet(book, name) for name in CHAINS}
    for s in sheets.values():
        first_time_format(s)

    print(f"Updating option chains every {REFRESH_SECS}s. Press Ctrl+C to stop.")
    while True:
        for name, symbol in CHAINS.items():
            try:
                write_chain(sheets[name], name.replace("_OC", ""), fetch_chain(fyers, symbol))
            except Exception as e:
                print(f"{datetime.now():%H:%M:%S} {name}: {e}")
        time.sleep(REFRESH_SECS)
