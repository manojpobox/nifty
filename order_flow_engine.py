"""
Order Flow Engine for NIFTYBOX
Implements real-time Level 2 Order Flow & Cumulative Volume Delta (CVD) tracking
using the FYERS API v3 WebSocket (Lee-Ready algorithm with Tick Rule fallback).
Thread-safe, non-blocking background daemon with JSON persistence and 15m macro aggregation.
"""

import os
import json
import time
import datetime
from datetime import timedelta
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config import DATA_DIR, load_env_vars, NIFTY_LOT_SIZE
from fyers_auth import FyersAuthManager
from oi_engine import get_nifty_future_symbol

try:
    from fyers_apiv3.FyersWebsocket import data_ws
except ImportError:
    data_ws = None

_ENGINE_INSTANCE = None
_ENGINE_LOCK = threading.Lock()

def get_5min_floor(dt: datetime.datetime) -> datetime.datetime:
    """Rounds a datetime object down to the nearest 5-minute interval."""
    discard = timedelta(minutes=dt.minute % 5, seconds=dt.second, microseconds=dt.microsecond)
    return dt - discard

class OrderFlowEngine:
    def __init__(self, lot_size: int = NIFTY_LOT_SIZE):
        self.lot_size = lot_size
        self.auth_manager = FyersAuthManager()
        self.symbol: Optional[str] = None
        self.ws = None
        self.ws_thread = None
        self.is_running = False
        self.is_connected = False
        self._lock = threading.Lock()

        # State tracking
        self.current_bar = {
            "start_time": None,
            "buy_volume": 0.0,
            "sell_volume": 0.0,
            "last_price": None,
            "bid": None,
            "ask": None
        }
        self.closed_bars: List[Dict] = []
        self._load_today_cached_bars()

    def _get_cache_filepath(self, date_str: str) -> Path:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        return DATA_DIR / f"order_flow_5m_{date_str}.json"

    def _load_today_cached_bars(self):
        """Loads already recorded bars from disk if available for today."""
        today_str = datetime.date.today().isoformat()
        fpath = self._get_cache_filepath(today_str)
        if fpath.exists():
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self.closed_bars = data
            except Exception as e:
                print(f"[OrderFlow] Error loading cached bars: {e}")

    def _save_bar_to_cache(self, bar_data: Dict):
        """Appends a closed bar to today's JSON storage."""
        date_str = bar_data.get("date_str", datetime.date.today().isoformat())
        fpath = self._get_cache_filepath(date_str)
        try:
            existing = []
            if fpath.exists():
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        existing = json.load(f)
                except Exception:
                    existing = []

            # Deduplicate by time_str
            filtered = [b for b in existing if b.get("time_str") != bar_data.get("time_str")]
            filtered.append(bar_data)
            filtered.sort(key=lambda x: x.get("time_str", ""))

            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(filtered, f, indent=2)
        except Exception as e:
            print(f"[OrderFlow] Error persisting bar: {e}")

    def process_trade_tick(self, trade_price: float, trade_qty: float) -> Tuple[str, float]:
        """
        Lee-Ready algorithm classification with Tick Rule fallback:
        1. trade_price >= ask -> Aggressive BUY
        2. trade_price <= bid -> Aggressive SELL
        3. Inside spread -> Tick Rule comparison against previous last_price
        """
        bid = self.current_bar.get("bid")
        ask = self.current_bar.get("ask")
        last_price = self.current_bar.get("last_price")

        # 1. Spread test
        if bid is not None and ask is not None and ask > bid:
            if trade_price >= ask:
                return "BUY", trade_qty
            elif trade_price <= bid:
                return "SELL", trade_qty

        # 2. Tick Rule Fallback
        if last_price is not None:
            if trade_price > last_price:
                return "BUY", trade_qty
            elif trade_price < last_price:
                return "SELL", trade_qty

        return "UNKNOWN", 0.0

    def _on_message(self, message):
        """Callback for incoming WebSocket trade ticks."""
        if not message:
            return

        packets = message if isinstance(message, list) else [message]

        with self._lock:
            for pkt in packets:
                if not isinstance(pkt, dict):
                    continue

                exch_time = pkt.get("last_traded_time") or pkt.get("exch_feed_time")
                if exch_time and isinstance(exch_time, (int, float)) and exch_time > 1000000000:
                    now = datetime.datetime.fromtimestamp(exch_time)
                else:
                    now = datetime.datetime.now()

                bar_start = get_5min_floor(now)

                if self.current_bar["start_time"] is None:
                    self.current_bar["start_time"] = bar_start

                # Candle Rollover Check
                if bar_start > self.current_bar["start_time"]:
                    b_vol = self.current_bar["buy_volume"]
                    s_vol = self.current_bar["sell_volume"]
                    tot_vol = b_vol + s_vol

                    buy_lots = round(b_vol / self.lot_size, 2)
                    sell_lots = round(s_vol / self.lot_size, 2)
                    delta_lots = round(buy_lots - sell_lots, 2)
                    buy_pct = round(100.0 * b_vol / tot_vol, 1) if tot_vol > 0 else 50.0

                    bar_data = {
                        "time_str": self.current_bar["start_time"].strftime("%H:%M"),
                        "date_str": self.current_bar["start_time"].strftime("%Y-%m-%d"),
                        "buy_volume": b_vol,
                        "sell_volume": s_vol,
                        "buy_lots": buy_lots,
                        "sell_lots": sell_lots,
                        "delta_lots": delta_lots,
                        "buy_pct": buy_pct
                    }
                    if b_vol > 0 or s_vol > 0:
                        self.closed_bars.append(bar_data)
                        self._save_bar_to_cache(bar_data)

                    self.current_bar = {
                        "start_time": bar_start,
                        "buy_volume": 0.0,
                        "sell_volume": 0.0,
                        "last_price": self.current_bar["last_price"],
                        "bid": self.current_bar["bid"],
                        "ask": self.current_bar["ask"]
                    }

                # Update quotes (bid / ask)
                bid_val = pkt.get("bid_price", pkt.get("bid"))
                ask_val = pkt.get("ask_price", pkt.get("ask"))
                if bid_val is not None:
                    try:
                        self.current_bar["bid"] = float(bid_val)
                    except Exception:
                        pass
                if ask_val is not None:
                    try:
                        self.current_bar["ask"] = float(ask_val)
                    except Exception:
                        pass

                # Process traded volume tick
                ltp_val = pkt.get("ltp", pkt.get("last_price"))
                qty_val = pkt.get("last_traded_qty")

                if ltp_val is not None and qty_val is not None:
                    try:
                        trade_price = float(ltp_val)
                        trade_qty = float(qty_val)

                        if trade_qty > 0:
                            side, qty = self.process_trade_tick(trade_price, trade_qty)
                            if side == "BUY":
                                self.current_bar["buy_volume"] += qty
                            elif side == "SELL":
                                self.current_bar["sell_volume"] += qty

                        self.current_bar["last_price"] = trade_price
                    except Exception:
                        pass

    def _on_connect(self):
        self.is_connected = True
        print(f"[OrderFlow] Connected to FYERS WebSocket. Subscribing to {self.symbol}...")
        try:
            self.ws.subscribe(symbols=[self.symbol], data_type="SymbolUpdate")
            print(f"[OrderFlow] Subscribed successfully to {self.symbol}")
        except Exception as e:
            print(f"[OrderFlow] Subscription error: {e}")

    def _on_close(self, message=None):
        self.is_connected = False
        print(f"[OrderFlow] WebSocket connection closed: {message}")

    def _on_error(self, message):
        print(f"[OrderFlow] WebSocket Error: {message}")

    def start(self):
        """Starts the WebSocket listener in a background daemon thread."""
        if self.is_running:
            return

        if data_ws is None:
            print("[OrderFlow] fyers_apiv3.FyersWebsocket not available.")
            return

        token_data = self.auth_manager._read_cached_token_raw()
        if not token_data or not token_data.get("access_token"):
            client, status = self.auth_manager.get_active_client()
            token_data = self.auth_manager._read_cached_token_raw()

        if not token_data or not token_data.get("access_token"):
            print("[OrderFlow] No valid token found for WebSocket.")
            return

        client_id = token_data.get("client_id", self.auth_manager.get_credentials().get("client_id", ""))
        access_token = token_data.get("access_token", "")
        formatted_token = f"{client_id}:{access_token}" if ":" not in access_token else access_token

        try:
            self.symbol = get_nifty_future_symbol()
        except Exception:
            self.symbol = "NSE:NIFTY26OCTFUT"

        print(f"[OrderFlow] Initializing OrderFlowEngine for {self.symbol} (Lot size: {self.lot_size})...")

        try:
            log_dir = Path("fyers_logs")
            log_dir.mkdir(parents=True, exist_ok=True)
            self.ws = data_ws.FyersDataSocket(
                access_token=formatted_token,
                log_path=str(log_dir.resolve()),
                litemode=False,
                reconnect=True,
                on_connect=self._on_connect,
                on_close=self._on_close,
                on_error=self._on_error,
                on_message=self._on_message
            )
        except Exception as e:
            print(f"[OrderFlow] Error instantiating FyersDataSocket: {e}")
            return

        def _run():
            self.is_running = True
            try:
                self.ws.connect()
                self.ws.keep_running()
                while self.is_running:
                    time.sleep(1)
            except Exception as ex:
                print(f"[OrderFlow] WebSocket runner error: {ex}")
            finally:
                self.is_running = False
                self.is_connected = False

        self.ws_thread = threading.Thread(target=_run, daemon=True, name="OrderFlowWSThread")
        self.ws_thread.start()
        print("[OrderFlow] WebSocket thread launched.")

    def get_5m_order_flow_map(self, target_date_str: Optional[str] = None) -> Dict[str, Dict]:
        """Returns map 'HH:MM' -> bar orderflow data for target date."""
        if not target_date_str:
            target_date_str = datetime.date.today().isoformat()

        res = {}
        fpath = self._get_cache_filepath(target_date_str)
        if fpath.exists():
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                    for item in cached:
                        t = item.get("time_str")
                        if t:
                            res[t] = item
            except Exception:
                pass

        if target_date_str == datetime.date.today().isoformat():
            with self._lock:
                for b in self.closed_bars:
                    t = b.get("time_str")
                    if t:
                        res[t] = b

                if self.current_bar["start_time"] is not None:
                    b_vol = self.current_bar["buy_volume"]
                    s_vol = self.current_bar["sell_volume"]
                    if b_vol > 0 or s_vol > 0:
                        active_t = self.current_bar["start_time"].strftime("%H:%M")
                        tot_vol = b_vol + s_vol
                        buy_lots = round(b_vol / self.lot_size, 2)
                        sell_lots = round(s_vol / self.lot_size, 2)
                        delta_lots = round(buy_lots - sell_lots, 2)
                        buy_pct = round(100.0 * b_vol / tot_vol, 1) if tot_vol > 0 else 50.0

                        res[active_t] = {
                            "time_str": active_t,
                            "date_str": target_date_str,
                            "buy_volume": b_vol,
                            "sell_volume": s_vol,
                            "buy_lots": buy_lots,
                            "sell_lots": sell_lots,
                            "delta_lots": delta_lots,
                            "buy_pct": buy_pct,
                            "is_live": True
                        }
        return res

    def get_15m_macro_order_flow_map(self, target_date_str: Optional[str] = None) -> Dict[str, Dict]:
        """Aggregates 5-minute order flow into standard 15-minute macro intervals."""
        five_map = self.get_5m_order_flow_map(target_date_str)
        if not five_map:
            return {}

        macro_15m = {}
        for t_str, b_data in five_map.items():
            try:
                hh, mm = map(int, t_str.split(":"))
                macro_mm = (mm // 15) * 15
                macro_key = f"{hh:02d}:{macro_mm:02d}"

                if macro_key not in macro_15m:
                    macro_15m[macro_key] = {
                        "time_str": macro_key,
                        "date_str": b_data.get("date_str", target_date_str),
                        "buy_volume": 0.0,
                        "sell_volume": 0.0,
                        "buy_lots": 0.0,
                        "sell_lots": 0.0,
                        "delta_lots": 0.0,
                        "buy_pct": 50.0,
                        "bars_count": 0
                    }

                m = macro_15m[macro_key]
                m["buy_volume"] += b_data.get("buy_volume", 0.0)
                m["sell_volume"] += b_data.get("sell_volume", 0.0)
                m["buy_lots"] += b_data.get("buy_lots", 0.0)
                m["sell_lots"] += b_data.get("sell_lots", 0.0)
                m["bars_count"] += 1
            except Exception:
                continue

        for m in macro_15m.values():
            m["buy_lots"] = round(m["buy_lots"], 2)
            m["sell_lots"] = round(m["sell_lots"], 2)
            m["delta_lots"] = round(m["buy_lots"] - m["sell_lots"], 2)
            tot_v = m["buy_volume"] + m["sell_volume"]
            m["buy_pct"] = round(100.0 * m["buy_volume"] / tot_v, 1) if tot_v > 0 else 50.0

        return macro_15m

def get_order_flow_singleton(lot_size: int = NIFTY_LOT_SIZE) -> OrderFlowEngine:
    """Thread-safe singleton getter."""
    global _ENGINE_INSTANCE
    with _ENGINE_LOCK:
        if _ENGINE_INSTANCE is None:
            _ENGINE_INSTANCE = OrderFlowEngine(lot_size=lot_size)
            try:
                _ENGINE_INSTANCE.start()
            except Exception as e:
                print(f"[OrderFlow] Startup notice: {e}")
        return _ENGINE_INSTANCE
