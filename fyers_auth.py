import os
import json
import time
import datetime
import hashlib
import requests
import pyotp
from pathlib import Path
from fyers_apiv3 import fyersModel

from config import (
    CONFIG_FILE, TOKEN_FILE, BASE_DIR,
    load_env_vars, save_env_vars, REDIRECT_URI
)

class FyersAuthManager:
    """
    Robust Fyers API v3 Authentication Manager.
    Features:
    - 1-Click Browser OAuth redirect capture via localhost callback
    - Automated Desktop Login via TOTP (pyotp) & PIN
    - 15-day refresh token auto-renewal engine
    - Live TOTP generator and countdown timer
    - Intelligent fallback to cached active tokens
    """

    def __init__(self):
        self.creds = load_env_vars()
        self._cached_client = None
        self._cached_client_time = 0
        self._cached_status = {"connected": False, "fy_id": "", "name": "", "app_id": ""}

    def get_credentials(self) -> dict:
        self.creds = load_env_vars()
        return self.creds

    def update_credentials(self, new_creds: dict):
        self.creds.update(new_creds)
        save_env_vars(self.creds)
        self._cached_client = None
        return self.creds

    def get_auth_url(self, state: str = "niftybox") -> str:
        """Generates the official Fyers v3 authorization URL."""
        creds = self.get_credentials()
        client_id = creds.get("client_id", "")
        redirect_uri = creds.get("redirect_uri", REDIRECT_URI)
        
        session = fyersModel.SessionModel(
            client_id=client_id,
            secret_key=creds.get("secret_key", ""),
            redirect_uri=redirect_uri,
            response_type="code",
            grant_type="authorization_code"
        )
        return session.generate_authcode()

    def get_totp_code(self) -> dict:
        """Generates current 6-digit TOTP code if key is present, else returns auto-pilot status."""
        creds = self.get_credentials()
        totp_key = creds.get("totp_key", "").strip().replace(" ", "")
        if not totp_key:
            return {
                "has_key": False,
                "mode": "auto_engine",
                "label": "15-DAY AUTO-RENEWAL",
                "status": "ACTIVE"
            }
        
        try:
            totp = pyotp.TOTP(totp_key)
            now = time.time()
            rem = int(30 - (now % 30))
            code = totp.now()
            return {"has_key": True, "code": code, "remaining_secs": rem}
        except Exception as e:
            return {"has_key": False, "mode": "auto_engine", "label": "15-DAY AUTO-RENEWAL", "status": "ACTIVE"}


    def exchange_auth_code(self, auth_code: str) -> dict:
        """Exchanges authorization code for access token and refresh token."""
        creds = self.get_credentials()
        client_id = creds.get("client_id", "")
        secret_key = creds.get("secret_key", "")
        redirect_uri = creds.get("redirect_uri", REDIRECT_URI)

        try:
            session = fyersModel.SessionModel(
                client_id=client_id,
                secret_key=secret_key,
                redirect_uri=redirect_uri,
                response_type="code",
                grant_type="authorization_code"
            )
            session.set_token(auth_code)
            resp = session.generate_token()

            if resp.get("s") == "ok" and "access_token" in resp:
                token_data = {
                    "client_id": client_id,
                    "access_token": resp["access_token"],
                    "refresh_token": resp.get("refresh_token", ""),
                    "created_at": datetime.datetime.now().isoformat(),
                    "date": datetime.date.today().isoformat()
                }
                self._save_token(token_data)
                self._cached_client = None
                return {"success": True, "data": resp}
            else:
                return {"success": False, "error": resp.get("message", str(resp))}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def renew_with_refresh_token(self) -> bool:
        """Renews access token using 15-day refresh token without user prompts."""
        token_data = self._read_cached_token_raw()
        if not token_data or not token_data.get("refresh_token"):
            return False

        creds = self.get_credentials()
        client_id = creds.get("client_id", "")
        secret_key = creds.get("secret_key", "")
        pin = creds.get("pin", "")

        app_id_hash = hashlib.sha256(f"{client_id}:{secret_key}".encode("utf-8")).hexdigest()
        url = "https://api-t1.fyers.in/api/v3/validate-refresh-token"
        payload = {
            "grant_type": "refresh_token",
            "appIdHash": app_id_hash,
            "refresh_token": token_data["refresh_token"],
            "pin": pin
        }

        try:
            r = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=10)
            data = r.json()
            if data.get("s") == "ok" and "access_token" in data:
                token_data["access_token"] = data["access_token"]
                token_data["created_at"] = datetime.datetime.now().isoformat()
                token_data["date"] = datetime.date.today().isoformat()
                self._save_token(token_data)
                self._cached_client = None
                return True
        except Exception as e:
            print(f"Error renewing refresh token: {e}")
        return False

    def auto_generate_token(self, client_id=None, secret_key=None, fy_id=None, pin=None, totp_key=None, redirect_uri=None) -> dict:
        """
        Automated desktop login via TOTP & PIN.
        1. Generates current TOTP code using pyotp
        2. Attempts direct headless authentication flow
        3. If broker presents web captcha, returns auth_url for 1-click browser callback
        """
        creds = self.get_credentials()
        client_id = client_id or creds.get("client_id")
        secret_key = secret_key or creds.get("secret_key")
        fy_id = fy_id or creds.get("fy_id")
        pin = pin or creds.get("pin")
        totp_key = (totp_key or creds.get("totp_key", "")).strip().replace(" ", "")
        redirect_uri = redirect_uri or creds.get("redirect_uri", REDIRECT_URI)

        if not totp_key:
            return {"success": False, "error": "TOTP secret key is required for automated login."}

        try:
            totp = pyotp.TOTP(totp_key)
            otp_val = totp.now()
        except Exception as e:
            return {"success": False, "error": f"Invalid TOTP key: {e}"}

        # Step 1: Send login OTP
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Content-Type": "application/json"
        })

        try:
            # Try modern Fyers v3 / vagator endpoints
            send_otp_url = "https://api-t2.fyers.in/vagator/v2/send_login_otp"
            r1 = session.post(send_otp_url, json={"fy_id": fy_id, "app_id": "2"}, timeout=10)
            d1 = r1.json()
            
            if d1.get("s") == "ok" and d1.get("request_key"):
                req_key = d1["request_key"]
                
                # Step 2: Verify TOTP
                verify_otp_url = "https://api-t2.fyers.in/vagator/v2/verify_otp"
                r2 = session.post(verify_otp_url, json={"request_key": req_key, "otp": otp_val}, timeout=10)
                d2 = r2.json()
                
                if d2.get("s") == "ok" and d2.get("request_key"):
                    req_key2 = d2["request_key"]
                    
                    # Step 3: Verify PIN
                    verify_pin_url = "https://api-t2.fyers.in/vagator/v2/verify_pin"
                    r3 = session.post(verify_pin_url, json={
                        "request_key": req_key2,
                        "identity_type": "pin",
                        "identifier": pin
                    }, timeout=10)
                    d3 = r3.json()
                    
                    if d3.get("s") == "ok" and d3.get("data", {}).get("token"):
                        bearer_token = d3["data"]["token"]
                        
                        # Step 4: Generate Auth Code
                        auth_endpoint = "https://api-t1.fyers.in/api/v3/generate-authcode"
                        params = {
                            "client_id": client_id,
                            "redirect_uri": redirect_uri,
                            "response_type": "code",
                            "state": "niftybox"
                        }
                        headers = {"Authorization": f"Bearer {bearer_token}"}
                        r4 = session.get(auth_endpoint, params=params, headers=headers, allow_redirects=False, timeout=10)
                        
                        location = r4.headers.get("Location") or (r4.json().get("Url") if r4.text.startswith("{") else "")
                        if location and "auth_code=" in location:
                            from urllib.parse import parse_qs, urlparse
                            parsed = parse_qs(urlparse(location).query)
                            auth_code = parsed.get("auth_code", [""])[0]
                            if auth_code:
                                return self.exchange_auth_code(auth_code)

            # If automated headless endpoint is blocked by broker captcha, return clear actionable redirect
            auth_url = self.get_auth_url()
            return {
                "success": False,
                "needs_browser": True,
                "auth_url": auth_url,
                "totp_code": otp_val,
                "error": "Headless login challenged by broker security. Use 1-Click Authorize button."
            }
        except Exception as e:
            return {"success": False, "error": str(e), "auth_url": self.get_auth_url()}

    def _save_token(self, token_data: dict):
        try:
            with open(TOKEN_FILE, "w") as f:
                json.dump(token_data, f, indent=2)
            # Also write plain text token for external script compatibility
            with open(BASE_DIR / "token.txt", "w") as f:
                f.write(f"{token_data.get('date', '')}\n{token_data.get('access_token', '')}")
        except Exception as e:
            print(f"Error saving token: {e}")

    def _read_cached_token_raw(self) -> dict:
        if TOKEN_FILE.exists():
            try:
                with open(TOKEN_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        
        # Check token.txt fallback
        txt_file = BASE_DIR / "token.txt"
        if txt_file.exists():
            try:
                content = txt_file.read_text().strip()
                lines = content.split("\n")
                token_str = lines[-1].strip()
                if token_str:
                    return {"access_token": token_str, "client_id": self.creds.get("client_id")}
            except Exception:
                pass

        # Check C:/Fyers/token.txt fallback
        fyers_dir_token = Path("C:/Fyers/token.txt")
        if fyers_dir_token.exists():
            try:
                content = fyers_dir_token.read_text().strip()
                lines = content.split("\n")
                token_str = lines[-1].strip()
                if token_str and len(token_str) > 50:
                    return {
                        "access_token": token_str,
                        "client_id": self.creds.get("fallback_client_id", "J1AXE2DYZH-100")
                    }
            except Exception:
                pass

        # Check ref_token.txt fallback
        ref_file = BASE_DIR / "ref_token.txt"
        if ref_file.exists():
            try:
                token_str = ref_file.read_text().strip()
                if token_str:
                    return {
                        "access_token": token_str,
                        "client_id": self.creds.get("fallback_client_id", "J1AXE2DYZH-100")
                    }
            except Exception:
                pass
        return {}


    def get_active_client(self):
        """
        Returns (fyers_client, status_dict).
        Validates token with get_profile() and caches active instance for 30s.
        """
        now = time.time()
        if self._cached_client and (now - self._cached_client_time) < 30:
            return self._cached_client, self._cached_status

        creds = self.get_credentials()
        primary_cid = creds.get("client_id", "")
        fallback_cid = creds.get("fallback_client_id", "J1AXE2DYZH-100")

        token_raw = self._read_cached_token_raw()
        access_token = token_raw.get("access_token", "").strip()

        if not access_token:
            self._cached_client = None
            self._cached_status = {"connected": False, "fy_id": "", "name": "", "app_id": primary_cid}
            return None, self._cached_status

        # Try client validation: first with token's client_id, then primary, then fallback
        cids_to_try = []
        if token_raw.get("client_id"):
            cids_to_try.append(token_raw["client_id"])
        if primary_cid and primary_cid not in cids_to_try:
            cids_to_try.append(primary_cid)
        if fallback_cid and fallback_cid not in cids_to_try:
            cids_to_try.append(fallback_cid)

        for cid in cids_to_try:
            try:
                fy = fyersModel.FyersModel(client_id=cid, token=access_token, is_async=False, log_path="")
                prof = fy.get_profile()
                if prof.get("s") == "ok" and prof.get("data"):
                    d = prof["data"]
                    self._cached_client = fy
                    self._cached_client_time = now
                    self._cached_status = {
                        "connected": True,
                        "fy_id": d.get("fy_id", creds.get("fy_id", "XM05617")),
                        "name": d.get("name", "MANOJ KUMAR"),
                        "email": d.get("email_id", ""),
                        "app_id": cid,
                        "totp_enabled": bool(d.get("totp", False))
                    }
                    return self._cached_client, self._cached_status
            except Exception:
                continue

        # If token was expired, try auto-renewal with refresh token
        if token_raw.get("refresh_token"):
            if self.renew_with_refresh_token():
                return self.get_active_client()

        self._cached_client = None
        self._cached_status = {"connected": False, "fy_id": "", "name": "", "app_id": primary_cid}
        return None, self._cached_status
