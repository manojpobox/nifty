import os
import json
import time
import asyncio
import datetime
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager


from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from config import (
    BASE_DIR, DATA_DIR, SERVER_HOST, SERVER_PORT,
    load_env_vars, save_env_vars, load_settings, save_settings
)
from fyers_auth import FyersAuthManager
from storage_manager import StorageManager
from oi_engine import OIEngine, get_fyers_expiries
from recorder_service import MarketRecorderService

# Core Service Instances
auth_mgr = FyersAuthManager()
storage_mgr = StorageManager(DATA_DIR)
oi_engine = OIEngine()
recorder = MarketRecorderService(auth_mgr, oi_engine, storage_mgr)

# Ensure static & templates directory exist
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR = BASE_DIR / "templates"
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("=" * 60)
    print("NIFTYBOX Terminal Server Initializing...")
    print(f"Localhost Web Dashboard: http://{SERVER_HOST}:{SERVER_PORT}")
    print("=" * 60)
    recorder.start()
    yield
    recorder.stop()

app = FastAPI(title="NIFTYBOX Terminal", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")



# ---------------------------------------------------------------------------
# HTML Single Page Dashboard & OAuth Callback
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    index_file = TEMPLATES_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>NIFTYBOX Terminal Loading...</h1>")

@app.get("/callback")
async def oauth_callback(
    auth_code: Optional[str] = Query(None),
    s: Optional[str] = Query(None),
    code: Optional[str] = Query(None)
):
    """
    Automatic OAuth Redirect Interception.
    Captures auth_code from Fyers, generates token, and redirects back to dashboard.
    """
    token_code = auth_code or code
    if not token_code:
        return RedirectResponse(url="/?auth_error=missing_code")

    res = auth_mgr.exchange_auth_code(token_code)
    if res.get("success"):
        return RedirectResponse(url="/?auth_success=1")
    else:
        err = res.get("error", "token_exchange_failed")
        return RedirectResponse(url=f"/?auth_error={err}")

# ---------------------------------------------------------------------------
# REST API Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/status")
async def get_status():
    client, conn_status = auth_mgr.get_active_client()
    now = datetime.datetime.now()
    market_open = (now.weekday() < 5) and (datetime.time(9, 15) <= now.time() <= datetime.time(15, 35))
    
    return {
        "status": conn_status,
        "market_open": market_open,
        "ist_time": now.strftime("%H:%M:%S"),
        "ist_date": now.strftime("%Y-%m-%d"),
        "is_weekend": now.weekday() >= 5
    }

@app.get("/api/totp")
async def get_totp():
    return auth_mgr.get_totp_code()

@app.get("/api/auth/login-url")
async def get_login_url():
    url = auth_mgr.get_auth_url()
    return {"auth_url": url}

@app.post("/api/auth/save-creds")
async def save_credentials(request: Request):
    data = await request.json()
    updated = auth_mgr.update_credentials(data)
    return {"success": True, "creds": updated}

@app.post("/api/auth/auto-login")
async def auto_login():
    res = auth_mgr.auto_generate_token()
    return res

@app.get("/api/live")
async def get_live_data(expiry_ts: Optional[str] = None):
    client, _ = auth_mgr.get_active_client()
    summary, corridor_df, _, shift = oi_engine.process_nifty_data(fyers_client=client, expiry_timestamp=expiry_ts)
    
    return {
        "summary": summary,
        "corridor": corridor_df.to_dict(orient="records"),
        "shift_event": shift
    }

@app.get("/api/optionchain")
async def get_option_chain(expiry_ts: Optional[str] = None):
    client, _ = auth_mgr.get_active_client()
    summary, _, full_chain, _ = oi_engine.process_nifty_data(fyers_client=client, expiry_timestamp=expiry_ts)

    # Group by strike for dual-sided table: { strike: { "CE": ..., "PE": ... } }
    grouped = {}
    for item in full_chain:
        s = item["strike_price"]
        opt_type = item["option_type"]
        if s not in grouped:
            grouped[s] = {"strike": s, "CE": {}, "PE": {}}
        grouped[s][opt_type] = item

    rows = [grouped[s] for s in sorted(grouped.keys())]
    
    return {
        "summary": summary,
        "strikes": rows,
        "count": len(rows)
    }

@app.get("/api/macro-table")
async def get_macro_table(timeframe: str = "5m"):
    client, _ = auth_mgr.get_active_client()
    table = oi_engine.get_macro_table(timeframe=timeframe, fyers_client=client)
    return {"timeframe": timeframe, "rows": table}

@app.get("/api/eod-radar")
async def get_eod_radar():
    client, _ = auth_mgr.get_active_client()
    summary, _, _, _ = oi_engine.process_nifty_data(fyers_client=client)
    radar = oi_engine.calculate_eod_radar(summary)
    return radar

@app.get("/api/candles")
async def get_candles(timeframe: str = "5m", count: int = 75):
    client, _ = auth_mgr.get_active_client()
    df = oi_engine.get_candlestick_data(timeframe=timeframe, count=count, fyers_client=client)
    return {
        "timeframe": timeframe,
        "candles": df.to_dict(orient="records")
    }

@app.get("/api/expiries")
async def get_expiries():
    client, _ = auth_mgr.get_active_client()
    exp_info = get_fyers_expiries(client)
    return exp_info

@app.get("/api/files")
async def list_files():
    catalog = storage_mgr.get_saved_files_catalog()
    return {"files": catalog}

@app.get("/api/download-file")
async def download_file(filename: str):
    fpath = DATA_DIR / filename
    if fpath.exists() and fpath.is_file():
        return FileResponse(path=str(fpath), filename=filename, media_type="text/csv")
    return JSONResponse(status_code=404, content={"error": "File not found"})

# ---------------------------------------------------------------------------
# Real-Time WebSocket Channel
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            client, conn_status = auth_mgr.get_active_client()
            summary, corridor_df, _, shift = oi_engine.process_nifty_data(fyers_client=client)
            totp = auth_mgr.get_totp_code()

            packet = {
                "type": "tick",
                "timestamp": datetime.datetime.now().strftime("%H:%M:%S"),
                "status": conn_status,
                "totp": totp,
                "summary": summary,
                "corridor": corridor_df.to_dict(orient="records"),
                "shift": shift
            }
            await websocket.send_text(json.dumps(packet))
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[WebSocket] Error: {e}")

if __name__ == "__main__":
    uvicorn.run("server:app", host=SERVER_HOST, port=SERVER_PORT, reload=False)
