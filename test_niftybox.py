import requests

base = 'http://127.0.0.1:8080'
tests = [
    ('/api/status', lambda d: f"Status: {d.get('status', {}).get('connected')} | User: {d.get('status', {}).get('fy_id')}"),
    ('/api/totp', lambda d: f"TOTP: {d.get('code')} (Remaining: {d.get('remaining_secs')}s)"),
    ('/api/auth/login-url', lambda d: f"Auth URL: {d.get('auth_url')[:60]}..."),
    ('/api/live', lambda d: f"Spot: {d.get('summary', {}).get('spot_price')} | ATM: {d.get('summary', {}).get('atm_strike')} | Corridor strikes: {len(d.get('corridor', []))}"),
    ('/api/optionchain', lambda d: f"Option Chain strikes count: {len(d.get('strikes', []))} | PCR: {d.get('summary', {}).get('pcr')}"),
    ('/api/macro-table?timeframe=5m', lambda d: f"Macro 5m bars: {len(d.get('rows', []))}"),
    ('/api/eod-radar', lambda d: f"EOD Verdict: {d.get('verdict', '').encode('ascii', 'ignore').decode()} | Range: {d.get('expected_range')}"),
    ('/api/candles?timeframe=5m', lambda d: f"Candles count: {len(d.get('candles', []))}"),
    ('/api/expiries', lambda d: f"Expiries count: {len(d.get('all_expiries', []))} | Fut: {d.get('futures_symbol')}"),
    ('/api/files', lambda d: f"Files catalog: {len(d.get('files', []))} files")
]

print("=== NIFTYBOX HEALTH TEST ===")
for path, fn in tests:
    try:
        r = requests.get(base + path, timeout=5)
        print(f"[{r.status_code}] {path:<28} -> {fn(r.json())}")
    except Exception as e:
        print(f"[FAIL] {path:<28} -> {e}")
