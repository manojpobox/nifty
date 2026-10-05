import time
import datetime
import threading
from typing import Optional

from config import DATA_DIR
from fyers_auth import FyersAuthManager
from oi_engine import OIEngine
from storage_manager import StorageManager

class MarketRecorderService:
    """
    Background Market Data Recording Daemon for NIFTYBOX.
    Continuously snapshots and persists:
    - Spot & Futures ticks
    - 5-Strike ATM corridor OI snapshots
    - Complete full-market option chain (all strikes with IV/Greeks)
    - 1m, 5m, 15m OHLCV candles
    
    Adheres strictly to the Zero-Duplicate Rule (BUG-014 fix).
    """

    def __init__(self, auth_mgr: FyersAuthManager, oi_engine: OIEngine, storage_mgr: StorageManager):
        self.auth_mgr = auth_mgr
        self.oi_engine = oi_engine
        self.storage_mgr = storage_mgr
        self.is_running = False
        self._thread: Optional[threading.Thread] = None
        self.last_candle_log_time = 0
        self.last_tick_time = 0
        self.interval_sec = 4

    def is_market_hours(self) -> bool:
        """Checks if current time is within Indian market hours (09:15 to 15:35 IST on weekdays)."""
        now = datetime.datetime.now()
        if now.weekday() >= 5:  # Saturday or Sunday
            return False
        market_open = datetime.time(9, 15)
        market_close = datetime.time(15, 35)
        return market_open <= now.time() <= market_close

    def _loop(self):
        print("[RecorderService] Daemon loop started.")
        while self.is_running:
            try:
                client, status = self.auth_mgr.get_active_client()
                
                # Fetch live data
                summary, corridor_df, full_chain, shift_event = self.oi_engine.process_nifty_data(fyers_client=client)

                now_time = time.time()
                # Persist corridor snapshot & spot tick (Idempotent 1-per-minute handled by StorageManager)
                self.storage_mgr.log_oi_snapshot(summary, corridor_df, force=bool(shift_event))
                self.storage_mgr.log_futures_spot_tick(summary, force=bool(shift_event))

                if full_chain:
                    self.storage_mgr.log_full_option_chain_snapshot(
                        full_chain, spot_price=summary.get("spot_price", 0.0), force=bool(shift_event)
                    )

                # Persist candles every 60 seconds
                if now_time - self.last_candle_log_time >= 60:
                    for tf in ["1m", "5m", "15m"]:
                        c_df = self.oi_engine.get_candlestick_data(timeframe=tf, count=50, fyers_client=client)
                        self.storage_mgr.log_intraday_candles(c_df, timeframe=tf)
                    self.last_candle_log_time = now_time

            except Exception as e:
                print(f"[RecorderService] Cycle error: {e}")

            time.sleep(self.interval_sec)

    def start(self):
        if self.is_running:
            return
        self.is_running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="NiftyboxRecorderDaemon")
        self._thread.start()
        print("[RecorderService] Background recorder thread launched.")

    def stop(self):
        self.is_running = False
