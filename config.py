import os
import json
from pathlib import Path

# Base Paths
BASE_DIR = Path("C:/Nifty")
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = BASE_DIR / "fyers_config.json"
TOKEN_FILE = BASE_DIR / "token.json"
SETTINGS_FILE = BASE_DIR / "dashboard_settings.json"

# Core Nifty Market Parameters
NIFTY_STRIKE_STEP = 50
NIFTY_SPOT_SYMBOL = "NSE:NIFTY50-INDEX"
NIFTY_LOT_SIZE = 65  # Current NSE Nifty Lot Size

# Server configuration
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8080
REDIRECT_URI = f"http://{SERVER_HOST}:{SERVER_PORT}/callback"

# Default Credentials
DEFAULT_CONFIG = {
    "client_id": "O4IU017L3R-100",
    "secret_key": "12MAV54KZB",
    "redirect_uri": REDIRECT_URI,
    "fy_id": "XM05617",
    "pin": "3159",
    "totp_key": "",
    "fallback_client_id": "J1AXE2DYZH-100"
}

def load_env_vars():
    """Loads configuration from fyers_config.json, initializing defaults if missing."""
    config = DEFAULT_CONFIG.copy()
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                saved = json.load(f)
                config.update(saved)
        except Exception as e:
            print(f"Error loading {CONFIG_FILE}: {e}")
    else:
        save_env_vars(config)
    return config

def save_env_vars(config_data: dict):
    """Saves configuration securely to fyers_config.json."""
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config_data, f, indent=2)
    except Exception as e:
        print(f"Error saving {CONFIG_FILE}: {e}")

# Settings Manager
DEFAULT_SETTINGS = {
    "timeframe": "5m",
    "auto_refresh": True,
    "refresh_sec": 5,
    "selected_expiry": "",
    "sound_alerts": True,
    "sim_spot_manual": 22550.0
}

def load_settings():
    settings = DEFAULT_SETTINGS.copy()
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r") as f:
                settings.update(json.load(f))
        except Exception:
            pass
    else:
        save_settings(settings)
    return settings

def save_settings(settings_data: dict):
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings_data, f, indent=2)
    except Exception:
        pass
